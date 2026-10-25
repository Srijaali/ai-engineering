"""Phase 3 durable consent, request counters, cost holds and circuit breakers.

No HTTP imports or automatic reruns. Raw secrets/provider errors are not stored.
"""
from contextlib import contextmanager
from decimal import Decimal
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from .registry import MODELS
from ..config import ROOT, recommended_cap

REQUEST_HOLD_USD = Decimal('0.10')
LUNA_REFUSAL_HOLD_USD = Decimal('0.01')


class BudgetStop(Exception):
    pass


class NotRun(Exception):
    pass


def units(amount):
    return int(Decimal(str(amount)) * 1_000_000_000)


def money(amount):
    return str(Decimal(amount) / 1_000_000_000)


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.marker = self.path.with_suffix('.initialized')
        if self.marker.exists() and not self.path.exists():
            raise RuntimeError('Initialized budget ledger is missing; restore backup, never reset')
        with self.connection() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS runs(
                id TEXT PRIMARY KEY, suite TEXT, config TEXT, cap INTEGER,
                consent_hash TEXT, model_cap INTEGER, calibration_tokens INTEGER);
              CREATE TABLE IF NOT EXISTS attempts(
                id INTEGER PRIMARY KEY, run_id TEXT, trial_id TEXT, model_id TEXT, suite TEXT,
                origin TEXT, hold INTEGER, cost INTEGER, body TEXT);
              CREATE UNIQUE INDEX IF NOT EXISTS history_once ON attempts(origin) WHERE origin IS NOT NULL;
              CREATE TABLE IF NOT EXISTS sequence_guards(
                name TEXT PRIMARY KEY, definition TEXT, stop_spend INTEGER, stop_reason TEXT);
              CREATE TABLE IF NOT EXISTS reconciliations(
                id INTEGER PRIMARY KEY, created_utc TEXT, amount INTEGER,
                through_attempt_id INTEGER, known_at_checkpoint INTEGER, evidence TEXT);
              CREATE TABLE IF NOT EXISTS stages(run_id TEXT PRIMARY KEY, name TEXT UNIQUE, definition TEXT, stop_reason TEXT);
              CREATE TABLE IF NOT EXISTS blocks(model_id TEXT PRIMARY KEY, reason TEXT);
              CREATE TABLE IF NOT EXISTS control(key TEXT PRIMARY KEY,value TEXT);
              CREATE TABLE IF NOT EXISTS not_run(
                run_id TEXT,trial_id TEXT,model_id TEXT,reason TEXT,
                PRIMARY KEY(run_id,trial_id,model_id));
            ''')
        self.marker.touch(exist_ok=True)
        if self.path.resolve()==(ROOT/'data/results/phase3/ledger.sqlite3').resolve():
            self.import_submission_snapshot(ROOT/'data/evidence/ledger.json')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def import_submission_snapshot(self,path):
        """Seed a clean checkout once; never overwrite counters or restore consent."""
        path=Path(path)
        if not path.exists():return
        snapshot=json.loads(path.read_text())
        if snapshot.get('format_version')!=1:raise ValueError('Unsupported evidence snapshot')
        allowed=('attempts','reconciliations','stages','control','blocks','sequence_guards')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]:return
            for table in allowed:
                columns=[r[1] for r in db.execute('PRAGMA table_info('+table+')')]
                for row in snapshot['tables'][table]:
                    if set(row)!=set(columns):raise ValueError('Evidence schema differs')
                    db.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in columns)+')',[row[k] for k in columns])

    def import_history(self, records):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in records:
                if row.get('phase',0) != 0:
                    continue
                cost = row.get('cost_usd')
                db.execute('''INSERT OR IGNORE INTO attempts(run_id,trial_id,model_id,suite,origin,hold,cost,body)
                              VALUES(?,?,?,?,?,?,?,?)''',
                           ('historical',row['run_id'],row['model_id'],'diagnostic',row['run_id'],
                            units(REQUEST_HOLD_USD),None if cost is None else units(cost),json.dumps(row)))

    @staticmethod
    def effective_hold(model, hold, encoded):
        record=json.loads(encoded)
        raw=record.get('raw_response')
        if (model=='openai/gpt-6-luna-decisions' and record.get('http_status')==502
            and record.get('error_class')=='MODEL_REFUSAL' and isinstance(raw,dict)
            and raw.get('usage') in (None,{})
            and 'refused to answer' in json.dumps(raw.get('error',{})).lower()):
            return units(LUNA_REFUSAL_HOLD_USD)
        return hold

    def _budget(self, db):
        checkpoint=db.execute('SELECT id,created_utc,amount,through_attempt_id,known_at_checkpoint,evidence FROM reconciliations ORDER BY id DESC LIMIT 1').fetchone()
        watermark=checkpoint[3] if checkpoint else 0
        covered_known=db.execute('SELECT COALESCE(SUM(cost),0) FROM attempts WHERE id<=?',(watermark,)).fetchone()[0]
        rows=db.execute('SELECT model_id,cost,hold,body FROM attempts WHERE id>?',(watermark,)).fetchall()
        new_known=sum(cost or 0 for _,cost,_,_ in rows)
        new_holds=sum(self.effective_hold(model,hold,body) for model,cost,hold,body in rows if cost is None)
        basis=max(checkpoint[2],covered_known) if checkpoint else 0
        return basis+new_known,new_holds,checkpoint

    def approve_sequence(self,name,stages,stop_spend_usd,confirmed=False):
        """Owner-scoped additional stop threshold; never changes global limits."""
        if confirmed is not True:raise PermissionError('Explicit sequence consent required')
        encoded=json.dumps(stages,sort_keys=True)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM sequence_guards WHERE name=?',(name,)).fetchone():
                raise PermissionError('Sequence already exists; no automatic restart')
            if units(stop_spend_usd)<=0:raise ValueError('Invalid sequence stop')
            db.execute('INSERT INTO sequence_guards VALUES(?,?,?,NULL)',(name,encoded,units(stop_spend_usd)))

    def _check_sequence(self,db,run_id):
        stage=db.execute('SELECT name,definition FROM stages WHERE run_id=?',(run_id,)).fetchone()
        if not stage:return None
        spend,_,_=self._budget(db)
        for name,encoded,limit,stopped in db.execute('SELECT * FROM sequence_guards').fetchall():
            definitions=json.loads(encoded)
            if stage[0] not in {d['name'] for d in definitions}:continue
            if json.loads(stage[1]) not in definitions:return 'SEQUENCE_DEFINITION_MISMATCH'
            if stopped:return stopped
            if spend>=limit:return 'SEQUENCE_SPEND_STOP: '+money(spend)+' >= '+money(limit)
        return None

    def reconcile(self, amount_usd, evidence, confirmed=False):
        """Append an owner-reported cumulative checkpoint; never rewrite attempts."""
        if confirmed is not True:raise PermissionError('Explicit reconciliation authorization required')
        amount=Decimal(str(amount_usd))
        if not amount.is_finite() or amount<0 or amount!=Decimal(units(amount))/1_000_000_000:
            raise ValueError('Invalid reconciliation amount')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if any(json.loads(r[0]).get('status')=='PENDING' for r in db.execute('SELECT body FROM attempts')):
                raise ValueError('Cannot reconcile in-flight attempts')
            spend,_,_=self._budget(db)
            if units(amount)<spend:raise ValueError('Reconciliation cannot reduce known or previously reconciled spend')
            watermark,known=db.execute('SELECT COALESCE(MAX(id),0),COALESCE(SUM(cost),0) FROM attempts').fetchone()
            db.execute('INSERT INTO reconciliations(created_utc,amount,through_attempt_id,known_at_checkpoint,evidence) VALUES(?,?,?,?,?)',
                (datetime.now(timezone.utc).isoformat(),units(amount),watermark,known,json.dumps(evidence,allow_nan=False)))
        return self.dashboard_data()['total']

    def approve(self, run_id, suite, puzzles, settings, confirmed=False, smoke=False):
        if confirmed is not True:
            raise PermissionError('Explicit paid-run confirmation is required')
        cap = 6 if smoke else recommended_cap(puzzles, settings)
        snapshot = json.dumps(settings.snapshot(),sort_keys=True)
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise PermissionError('Run already exists; inspect saved records, do not rerun automatically')
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,NULL)',
                       (run_id,suite,snapshot,cap,digest,1 if smoke else cap))
        return dict(run_id=run_id,token=token)

    def approve_stage(self,run_id,stage,settings,confirmed=False):
        from ..stages import Stage
        stage=Stage.from_dict(stage)
        if confirmed is not True:raise PermissionError('Stage requires explicit confirmation')
        if stage.request_cap>settings.max_requests:raise PermissionError('Stage cap exceeds unchanged global request limit')
        token=secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone() or db.execute('SELECT 1 FROM stages WHERE name=?',(stage.name,)).fetchone():
                raise PermissionError('Stage already exists; no automatic rerun')
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,NULL)',
                (run_id,stage.suite,json.dumps(settings.snapshot(),sort_keys=True),stage.request_cap,
                 hashlib.sha256(token.encode()).hexdigest(),stage.request_cap))
            db.execute('INSERT INTO stages VALUES(?,?,?,NULL)',(run_id,stage.name,json.dumps(stage.as_dict(),sort_keys=True)))
        return dict(run_id=run_id,token=token)

    def stop_stage(self,permit,reason):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');self._run(db,permit)
            db.execute('UPDATE stages SET stop_reason=COALESCE(stop_reason,?) WHERE run_id=?',(reason,permit['run_id']))

    def stage_stop_reason(self,run_id):
        with self.connection() as db:
            row=db.execute('SELECT stop_reason FROM stages WHERE run_id=?',(run_id,)).fetchone()
        return row[0] if row else None

    def approve_calibration(self, run_id, settings, confirmed=False):
        """Owner-authorized five-call Opus-only calibration; no global limit change."""
        if confirmed is not True:
            raise PermissionError('Explicit five-call calibration consent required')
        if (settings.claude_reasoning_effort,settings.claude_max_tokens,
            settings.max_transport_retries,settings.max_malformed_retries)!=('low',3000,0,0):
            raise PermissionError('Calibration requires fixed low/3000 and zero retries')
        token=secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise PermissionError('Calibration already exists; no automatic rerun')
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,NULL)',
                (run_id,'calibration',json.dumps(settings.snapshot(),sort_keys=True),5,
                 hashlib.sha256(token.encode()).hexdigest(),5))
        return dict(run_id=run_id,token=token)

    def _run(self, db, permit):
        row = db.execute('SELECT suite,config,cap,consent_hash,model_cap,calibration_tokens FROM runs WHERE id=?',
                         (permit.get('run_id'),)).fetchone()
        token = permit.get('token')
        if not row or not isinstance(token,str) or not secrets.compare_digest(
                row[3],hashlib.sha256(token.encode()).hexdigest()):
            raise PermissionError('Missing or invalid paid-call consent')
        return row

    def grant_calibration(self, permit, new_tokens):
        opus = MODELS[-1][0]
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._run(db,permit)
            rows = db.execute('SELECT body FROM attempts WHERE run_id=? AND model_id=?',
                              (permit['run_id'],opus)).fetchall()
            if run[4] != 1 or run[5] is not None or len(rows)!=1:
                raise PermissionError('Only one pre-experiment calibration extension is authorized')
            body = json.loads(rows[0][0])
            if body.get('finish_reason')!='length' or new_tokens <= json.loads(run[1])['claude_max_tokens']:
                raise PermissionError('Calibration requires observed length truncation and a higher cap')
            db.execute('UPDATE runs SET calibration_tokens=? WHERE id=?',(new_tokens,permit['run_id']))

    def reserve(self, permit, trial_id, model_id, settings):
        if model_id not in {m for m,_,_ in MODELS}:
            raise ValueError('Only the four assigned model IDs are allowed')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._run(db,permit)
            stage=db.execute('SELECT definition,stop_reason FROM stages WHERE run_id=?',(permit['run_id'],)).fetchone()
            if stage:
                if model_id not in json.loads(stage[0])['models']:raise PermissionError('Model not authorized by stage consent')
                if stage[1]:raise NotRun('STAGE_STOPPED: '+stage[1])
                sequence_stop=self._check_sequence(db,permit['run_id'])
                if sequence_stop:raise BudgetStop(sequence_stop)
            if run[0]=='calibration' and model_id!=MODELS[-1][0]:
                raise PermissionError('Only Opus is authorized for calibration')
            expected = json.loads(run[1]); actual = settings.snapshot()
            if model_id == MODELS[-1][0] and run[5] is not None:
                expected['claude_max_tokens'] = run[5]
            if expected != actual:
                raise PermissionError('Settings changed after approval; benchmark settings must stay fixed')
            halt = db.execute("SELECT value FROM control WHERE key='paid_halt'").fetchone()
            block = db.execute('SELECT reason FROM blocks WHERE model_id=?',(model_id,)).fetchone()
            if halt or block:
                raise NotRun(halt[0] if halt else block[0])
            count = db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
            spend,active_holds,_ = self._budget(db)
            run_count = db.execute('SELECT COUNT(*) FROM attempts WHERE run_id=?',(permit['run_id'],)).fetchone()[0]
            model_count = db.execute('SELECT COUNT(*) FROM attempts WHERE run_id=? AND model_id=?',
                                     (permit['run_id'],model_id)).fetchone()[0]
            model_limit = run[4] + (1 if model_id == MODELS[-1][0] and run[5] is not None else 0)
            if count >= settings.max_requests or run_count >= run[2] or model_count >= model_limit:
                raise BudgetStop('Persistent request limit or approved call allowance exhausted')
            if spend >= units(settings.max_spend_usd) or spend + active_holds + units(REQUEST_HOLD_USD) > units(settings.max_spend_usd):
                raise BudgetStop('Reconciled/known spend plus active unknown-cost holds exhaust the spending allowance')
            cursor = db.execute('''INSERT INTO attempts(run_id,trial_id,model_id,suite,hold,body)
                                   VALUES(?,?,?,?,?,?)''',
                                (permit['run_id'],trial_id,model_id,run[0],units(REQUEST_HOLD_USD),
                                 json.dumps({'status':'PENDING','cost_usd':None,'config_snapshot':actual})))
            return cursor.lastrowid

    def finish(self, request_id, record):
        cost = record.get('cost_usd')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE attempts SET cost=?,body=? WHERE id=?',
                       (None if cost is None else units(cost),json.dumps(record,allow_nan=False),request_id))
            current_run=db.execute('SELECT run_id FROM attempts WHERE id=?',(request_id,)).fetchone()[0]
            sequence_stop=self._check_sequence(db,current_run)
            if sequence_stop:
                db.execute('UPDATE stages SET stop_reason=COALESCE(stop_reason,?) WHERE run_id=?',(sequence_stop,current_run))
                stage_name=db.execute('SELECT name FROM stages WHERE run_id=?',(current_run,)).fetchone()[0]
                for name,encoded in db.execute('SELECT name,definition FROM sequence_guards').fetchall():
                    if stage_name in {d['name'] for d in json.loads(encoded)}:
                        db.execute('UPDATE sequence_guards SET stop_reason=COALESCE(stop_reason,?) WHERE name=?',(sequence_stop,name))
            if record.get('error_class')=='CREDIT_ERROR':
                db.execute("INSERT OR REPLACE INTO control VALUES('paid_halt','CREDIT_ERROR')")
            if record.get('error_class')=='CREDIT_ERROR' or record.get('permanent_failure'):
                run=db.execute('SELECT run_id FROM attempts WHERE id=?',(request_id,)).fetchone()[0]
                db.execute('UPDATE stages SET stop_reason=COALESCE(stop_reason,?) WHERE run_id=?',(record['error_class'],run))
            if record.get('permanent_failure'):
                model = db.execute('SELECT model_id FROM attempts WHERE id=?',(request_id,)).fetchone()[0]
                db.execute('INSERT OR REPLACE INTO blocks VALUES(?,?)',(model,record['error_class']))

    def mark_not_run(self, run_id, trial_id, model_id, reason):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO not_run VALUES(?,?,?,?)',(run_id,trial_id,model_id,reason))

    def saved_attempts(self, run_id=None):
        with self.connection() as db:
            sql='SELECT id,run_id,trial_id,model_id,suite,body FROM attempts'
            rows=db.execute(sql+(' WHERE run_id=?' if run_id else '')+' ORDER BY id',
                            (run_id,) if run_id else ()).fetchall()
        return [dict(request_id=i,run_id=r,trial_id=t,model_id=m,suite=s,**json.loads(body))
                if r!='historical' else dict(json.loads(body),request_id=i,run_id=r,trial_id=t,model_id=m,suite=s)
                for i,r,t,m,s,body in rows]

    def replay(self, run_id):
        return self.saved_attempts(run_id)

    def dashboard_data(self):
        with self.connection() as db:
            rows=db.execute('SELECT run_id,model_id,suite,cost,hold,body FROM attempts').fetchall()
            spend,active_holds,checkpoint=self._budget(db)
            not_run=[dict(run_id=r,trial_id=t,model_id=m,status='NOT_RUN',reason=why)
                     for r,t,m,why in db.execute('SELECT * FROM not_run').fetchall()]
        def summarize(items):
            known=sum(row[3] or 0 for row in items)
            unknown=sum(row[3] is None for row in items)
            return dict(requests=len(items),known_cost_usd=money(known),unknown_cost_attempts=unknown,
                        cost_usd=None if unknown else money(known),
                        unknown_cost_holds_usd=money(sum(self.effective_hold(row[1],row[4],row[5]) for row in items if row[3] is None)))
        total=summarize(rows)
        total.update(budget_spend_usd=money(spend),historical_unknown_cost_holds_usd=total['unknown_cost_holds_usd'],
                     unknown_cost_holds_usd=money(active_holds),reconciliation=None if checkpoint is None else
                     dict(id=checkpoint[0],created_utc=checkpoint[1],authoritative_spend_usd=money(checkpoint[2]),
                          through_attempt_id=checkpoint[3],known_at_checkpoint_usd=money(checkpoint[4]),evidence=json.loads(checkpoint[5])))
        return dict(total=total,by_model={m:summarize([r for r in rows if r[1]==m]) for m,_,_ in MODELS},
                    by_run={k:summarize([r for r in rows if r[0]==k]) for k in sorted({r[0] for r in rows})},
                    by_suite={k:summarize([r for r in rows if r[2]==k]) for k in sorted({r[2] for r in rows})},not_run=not_run)

    def stop_reason(self, model_id=None):
        with self.connection() as db:
            row=db.execute("SELECT value FROM control WHERE key='paid_halt'").fetchone()
            if row:return row[0]
            if model_id:
                row=db.execute('SELECT reason FROM blocks WHERE model_id=?',(model_id,)).fetchone()
                if row:return row[0]
        return None

    def reauthorize(self,run_id,settings,confirmed=False):
        """Explicit resume only; counts/caps/blocks are NEVER reset."""
        if confirmed is not True:raise PermissionError('Resume needs fresh paid consent')
        token=secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT config FROM runs WHERE id=?',(run_id,)).fetchone()
            if not row or json.loads(row[0])!=settings.snapshot():
                raise PermissionError('Resume configuration differs from fixed run snapshot')
            db.execute('UPDATE runs SET consent_hash=? WHERE id=?',
                (hashlib.sha256(token.encode()).hexdigest(),run_id))
        return dict(run_id=run_id,token=token)
