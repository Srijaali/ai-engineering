"""Consent-gated orchestration and read-only planning/results; no auto-restart."""
import asyncio
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time
import httpx
from .config import ROOT,recommended_cap
from .cube.engine import to_net
from .models.registry import MODELS
from .models.base import redacted
from .models.chat_client import ChatAgent
from .models.decisions_client import DecisionsAgent
from .models.mock_agent import MockAgent
from .runner import race,trial_id,costs
from .stats import aggregate,random_baseline,summarize
from .storage import TERMINAL
from .stages import Stage
from .analysis_scope import primary_scope


class Workflow:
    def __init__(self,settings,ledger,store,puzzles_dir=None,client_factory=None,mock_delay=.1):
        self.settings,self.ledger,self.store=settings,ledger,store
        self.puzzles_dir=Path(puzzles_dir or ROOT/'data/puzzles')
        self.client_factory=client_factory or (lambda:httpx.AsyncClient(timeout=settings.request_timeout_s,follow_redirects=False))
        self.mock_delay=mock_delay;self.active=None;self.pending=None;self.permits={};self.tasks=set()
        self.store.recover()

    def puzzles(self,suite):
        if suite not in ('warmup','primary','extension'):raise ValueError('Unknown suite')
        return json.loads((self.puzzles_dir/(suite+'.json')).read_text())

    def select(self,suite,puzzle_ids):
        available={p['id']:p for p in self.puzzles(suite)}
        if not isinstance(puzzle_ids,list) or not puzzle_ids or len(puzzle_ids)!=len(set(puzzle_ids)):
            raise ValueError('Select distinct fixed puzzles')
        if any(p not in available for p in puzzle_ids):raise ValueError('Unknown puzzle')
        return [available[p] for p in puzzle_ids]

    def plan(self,suite='warmup',puzzle_ids=None):
        puzzle_ids=puzzle_ids or [self.puzzles(suite)[0]['id']]
        puzzles=self.select(suite,puzzle_ids)
        # Historical 1500-token sample is retained, never refreshed by a paid probe.
        evidence=ROOT/'docs/verification/phase3/summary.json'
        measured=None
        if evidence.exists():
            summary=json.loads(evidence.read_text())
            measured=next((r['attempts'][-1] for r in summary.get('results',[]) if r['model_id']==MODELS[-1][0] and r.get('attempts')),None)
        seconds=measured['latency_ms']/1000 if measured else None
        call_cost=Decimal(measured['cost_usd']) if measured and measured.get('cost_usd') is not None else None
        expected=Decimal(str(self.settings.run_budget_s))/Decimal(str(seconds))*call_cost if seconds and call_cost is not None else None
        # Conservative byte-count input bound plus framing at the documented price snapshot.
        from .prompts import observation
        agent=ChatAgent(MODELS[-1][0],self.settings,self.ledger,None)
        body=agent.payload(observation(puzzles[0]['facelets']))[1]
        input_bound=len(json.dumps(body).encode())+4096
        per_call_bound=Decimal(input_bound)*Decimal('.000004')+Decimal(self.settings.claude_max_tokens)*Decimal('.00002')
        factor=1+self.settings.max_transport_retries+self.settings.max_malformed_retries
        return dict(puzzles=len(puzzles),models=4,max_moves=30,run_budget_s=self.settings.run_budget_s,
            planned_initial_calls=len(puzzles)*4*30,recommended_request_cap=recommended_cap(len(puzzles),self.settings),
            max_requests=self.settings.max_requests,max_spend_usd=self.settings.max_spend_usd,
            fixed_config=self.settings.snapshot(),accounting=self.ledger.dashboard_data()['total'],
            opus=dict(measured_seconds_per_call=seconds,measured_cost_per_call_usd=str(call_cost) if call_cost is not None else None,
                measurement_output_cap=1500,current_output_cap=self.settings.claude_max_tokens,
                expected_formula='(RUN_BUDGET_S / measured_seconds_per_call) * measured_cost_per_call',
                expected_per_trial_usd=str(expected) if expected is not None else None,
                expected_selected_puzzles_usd=str(expected*len(puzzles)) if expected is not None else None,
                worst_case_per_trial_usd=str(per_call_bound*30*factor),
                worst_case_selected_puzzles_usd=str(per_call_bound*30*factor*len(puzzles)),
                worst_case_assumption='30 actions, all configured retries, full output ceiling; input upper bound UTF-8 bytes + 4096 framing at a $4/$20-per-million price snapshot. Harness still stops at $4.',
                caveat='Planning scenario from one 1500-token smoke; 1024-token runtime/cost is unmeasured. Not a bill guarantee.'))

    def stage_plan(self,definition):
        stage=Stage.from_dict(definition);puzzles=self.select(stage.suite,list(stage.puzzle_ids))
        if stage.request_cap>self.settings.max_requests:raise ValueError('Stage cap exceeds global request limit')
        summary=json.loads((ROOT/'docs/verification/phase3/summary.json').read_text())
        estimates={};known=[]
        for model in stage.models:
            sample=next((r['attempts'][-1] for r in summary.get('results',[]) if r['model_id']==model and r.get('attempts')),{})
            price=sample.get('cost_usd')
            estimates[model]=dict(measured_smoke_cost_per_call_usd=price,
                thirty_moves_per_puzzle_scenario_usd=str(Decimal(price)*30*len(puzzles)) if price is not None else None)
            if model==MODELS[-1][0] and price is not None and sample.get('latency_ms'):
                estimates[model]['wall_time_formula_scenario_usd']=str(Decimal(str(self.settings.run_budget_s))/(Decimal(str(sample['latency_ms']))/1000)*Decimal(price)*len(puzzles))
            if price is not None:known.append(Decimal(price)*30*len(puzzles))
        accounting=self.ledger.dashboard_data()['total']
        headroom=Decimal(str(self.settings.max_spend_usd))-Decimal(accounting['budget_spend_usd'])-Decimal(accounting['unknown_cost_holds_usd'])
        initial=len(puzzles)*len(stage.models)*30
        opus=self.plan(stage.suite,list(stage.puzzle_ids))['opus'] if MODELS[-1][0] in stage.models else None
        return dict(opus=opus,stage=stage.as_dict(),mode='LIVE',models=list(stage.models),puzzle_ids=list(stage.puzzle_ids),
            planned_initial_calls=initial,retry_envelope=initial*(1+self.settings.max_transport_retries+self.settings.max_malformed_retries),
            persistent_stage_request_cap=stage.request_cap,global_max_requests=self.settings.max_requests,
            max_spend_usd=self.settings.max_spend_usd,headroom_after_holds_usd=str(headroom),accounting=accounting,
            expected_cost_usd=str(sum(known)) if len(known)==len(stage.models) else None,
            cost_scenario_by_model=estimates,cost_caveat='Scenario: repeat one prior smoke price for 30 moves per model/puzzle, no retries. Not a guarantee; actual output, refusals, routing and retries vary.',
            fixed_config=self.settings.snapshot(),concurrent=True)

    def submit_stage(self,definition,confirmed=False):
        if confirmed is not True:raise PermissionError('Explicit per-stage confirmation required')
        stage=Stage.from_dict(definition);plan=self.stage_plan(stage.as_dict())
        if not all((self.settings.api_key,self.settings.student_name,self.settings.student_roll)):
            raise PermissionError('Configure backend key and student identity')
        if (self.settings.claude_reasoning_effort,self.settings.claude_max_tokens,self.settings.run_budget_s)!=('low',1024,300):
            raise PermissionError('Stage must use unchanged current fixed settings')
        ident=hashlib.sha256(('stage:'+stage.name).encode()).hexdigest()[:32]
        try:existing=self.store.get(ident)
        except KeyError:existing=None
        if existing:
            if existing.get('stage')!=stage.as_dict() or existing['config_snapshot']!=self.settings.snapshot():
                raise PermissionError('Saved stage definition/settings differ; no automatic replacement')
            return existing,False
        if self.active or self.pending:raise ValueError('Another race is active')
        puzzles=self.select(stage.suite,list(stage.puzzle_ids))
        permit=self.ledger.approve_stage(ident,stage.as_dict(),self.settings,True)
        run=dict(id=ident,selection=dict(suite=stage.suite,puzzle_ids=list(stage.puzzle_ids),mode='LIVE',concurrent=True),
                 stage=stage.as_dict(),model_ids=list(stage.models),request_cap=stage.request_cap,
                 mode='LIVE',suite=stage.suite,puzzle_ids=list(stage.puzzle_ids),puzzles=puzzles,concurrent=True,
                 status='QUEUED',config_snapshot=self.settings.snapshot(),created_at=time.time(),plan=plan)
        self.store.create(run);self.permits[ident]=permit;self.pending=ident
        return run,True

    def submit(self,request_id,suite,puzzle_ids,mode,confirm=False,concurrent=True,resume_run_id=None):
        if confirm is not True:raise PermissionError('Explicit per-run confirmation is required')
        if mode not in ('LIVE','SIMULATED'):raise ValueError('Use LIVE or SIMULATED')
        puzzles=self.select(suite,puzzle_ids)
        if not isinstance(request_id,str) or not 16<=len(request_id)<=128:raise ValueError('request_id required')
        selection=dict(suite=suite,puzzle_ids=puzzle_ids,mode=mode,concurrent=bool(concurrent))
        if mode=='LIVE':
            if not all((self.settings.api_key,self.settings.student_name,self.settings.student_roll)):
                raise PermissionError('Configure backend key, STUDENT_NAME and STUDENT_ROLL before live runs')
            if (self.settings.claude_reasoning_effort,self.settings.claude_max_tokens,self.settings.run_budget_s)!=( 'low',1024,300):
                raise PermissionError('Live settings must match the pre-declared low/1024/300 snapshot')
        ident=hashlib.sha256(request_id.encode()).hexdigest()[:32]
        if resume_run_id:
            if self.active or self.pending:raise ValueError('Another race is active')
            run=self.store.get(resume_run_id)
            if run['selection']!=selection or run['config_snapshot']!=self.settings.snapshot():
                raise PermissionError('Resume selection or fixed configuration differs')
            if run['status']=='COMPLETED':return run,False
            # Ambiguous interrupted trials were finalized INFRA_ERROR at recovery and skipped.
            permit=self.ledger.reauthorize(run['id'],self.settings,True) if mode=='LIVE' else {}
            self.permits[run['id']]=permit;run['status']='QUEUED';self.store.save(run)
            self.pending=run['id']
            return run,True
        try:
            existing=self.store.get(ident)
        except KeyError:existing=None
        if existing:
            if existing['selection']!=selection:raise ValueError('Idempotency key is bound to another selection')
            return existing,False
        if self.active or self.pending:raise ValueError('Another race is active; no automatic queue')
        permit=self.ledger.approve(ident,suite,len(puzzles),self.settings,True) if mode=='LIVE' else {}
        run=dict(id=ident,selection=selection,mode=mode,suite=suite,puzzle_ids=puzzle_ids,puzzles=puzzles,
                 concurrent=bool(concurrent),status='QUEUED',config_snapshot=self.settings.snapshot(),
                 created_at=time.time(),plan=self.plan(suite,puzzle_ids))
        self.store.create(run);self.permits[ident]=permit;self.pending=ident
        return run,True

    async def execute(self,run_id):
        if self.active:return
        lease=self.store.execution_lease()
        if lease is None:
            run=self.store.get(run_id);run['status']='INTERRUPTED';self.store.save(run);self.pending=None;return
        self.active=run_id
        run=self.store.get(run_id)
        if run['status']!='QUEUED':lease.close();self.active=None;return
        run['status']='RUNNING';self.store.save(run)
        try:
            async def play(agents):
                for puzzle in run['puzzles']:
                    await race(run,puzzle,agents,self.settings,self.store,
                               self.ledger if run['mode']=='LIVE' else None,self.permits.get(run_id))
            if run['mode']=='LIVE':
                async with self.client_factory() as client:
                    selected=set(run.get('model_ids',[m for m,_,_ in MODELS]))
                    agents={m:(ChatAgent if c=='LLM' else DecisionsAgent)(m,self.settings,self.ledger,client) for m,_,c in MODELS if m in selected}
                    await play(agents)
            else:
                agents={m:MockAgent(i,self.mock_delay,self.settings.run_budget_s) for i,(m,_,_) in enumerate(MODELS)}
                await play(agents)
            reason=self.ledger.stage_stop_reason(run_id) if run.get('stage') else None
            run['status']='STOPPED' if reason else 'COMPLETED'
            if reason:run['error_class']=reason
        except asyncio.CancelledError:
            run['status']='INTERRUPTED';raise
        except Exception as exc:
            run['status']='INTERRUPTED';run['error_class']=type(exc).__name__
            if run.get('stage'):self.ledger.stop_stage(self.permits[run_id],type(exc).__name__)
        finally:
            run['completed_at']=time.time();self.store.save(run)
            self.store.append(run_id,dict(type='complete',status=run['status'],mode=run['mode']))
            self.active=None;self.pending=None;lease.close();self.permits.pop(run_id,None)

    def start(self,run_id):
        task=asyncio.create_task(self.execute(run_id));self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def results(self):
        return [dict(id=r['id'],mode=r['mode'],suite=r['suite'],puzzle_ids=r['puzzle_ids'],status=r['status'],
                     created_at=r['created_at'],config_snapshot=r['config_snapshot'],stage=r.get('stage'),
                     model_ids=r.get('model_ids',[m for m,_,_ in MODELS])) for r in self.store.runs()]

    def replay(self,run_id):
        run=self.store.get(run_id)
        return dict(run=run,trials=self.store.trials(run_id),events=self.store.events(run_id))

    def dashboard(self):
        runs=[r for r in self.store.runs() if r['mode']=='LIVE']
        trials=[]
        for run in runs:
            saved=self.store.trials(run['id']);present={t['trial_id'] for t in saved}
            trials.extend(saved)
            for puzzle in run['puzzles']:
                for model in run.get('model_ids',[m for m,_,_ in MODELS]):
                    ident=trial_id(puzzle['id'],model)
                    if ident not in present:
                        trials.append(dict(model_id=model,mode='LIVE',suite=puzzle['suite'],status='NOT_RUN',moves=[],reason='Not executed yet'))
        # Billing is joined from the durable attempt ledger, including interrupted requests.
        bill=self.ledger.dashboard_data()
        baseline_path=self.store.root/'BASELINE/random.json'
        baseline=json.loads(baseline_path.read_text()) if baseline_path.exists() else None
        scope=primary_scope(trials)
        summaries={s:aggregate(scope['paired'] if s=='primary' else [t for t in trials if t['suite']==s]) for s in ('warmup','primary','extension')}
        for suite,rows in summaries.items():
            for row in rows:
                records=[r for r in self.ledger.saved_attempts() if r['suite']==suite and r['model_id']==row['model_id'] and (suite!='primary' or r['trial_id'] in {t['trial_id'] for t in scope['paired']})]
                row['cost']=costs(records)
            if baseline and baseline.get('summaries',{}).get(suite):
                matched=set(scope['paired_puzzles']) if suite=='primary' else {p['id'] for run in runs if run['suite']==suite for p in run['puzzles']}
                candidates=[t for t in baseline['trials'] if t['suite']==suite and (not matched or t['puzzle_id'] in matched)]
                b=summarize(candidates,'random_baseline','Random baseline (free)',True);b['cost']=dict(known_cost_usd='0',unknown_cost_attempts=0,cost_usd='0',requests=0)
                summaries[suite].append(b)
        return dict(primary_scope=dict(paired_puzzles=scope['paired_puzzles'],additional_completed=scope['additional_completed'],partial_trials=scope['partial_trials'],partial_stages=[dict(stage=r.get('stage',{}).get('name'),status=r['status'],error_class=r.get('error_class'),run_id=r['id']) for r in runs if r['suite']=='primary' and r['status']!='COMPLETED']),statistics=summaries,accounting=bill,simulated_runs_excluded=sum(r['mode']=='SIMULATED' for r in self.store.runs()),
                    baseline_available=baseline is not None,live_runs=[dict(run_id=r['id'],suite=r['suite'],mode=r['mode'],stage=r.get('stage'),status=r['status'],model_ids=r.get('model_ids',[m for m,_,_ in MODELS])) for r in runs])

    def build_baseline(self):
        puzzles=[p for s in ('warmup','primary','extension') for p in self.puzzles(s)]
        data=random_baseline(puzzles)
        self.store.atomic(self.store.root/'BASELINE/random.json',data)
        return data
