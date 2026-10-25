"""Deterministic offline results from preserved LIVE JSONL and billing evidence.

Two RESULTS marker pairs contain the headline and full results respectively.
No .env, HTTP client, clock, solver policy or mutable runtime ledger is used.
"""
from collections import Counter
from decimal import Decimal
import argparse
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.analysis_scope import primary_scope
from backend.app.models.registry import MODELS
from backend.app.stats import summarize,random_baseline,ELIGIBLE,describe
from backend.app.runner import costs

ROOT=Path(__file__).resolve().parents[1]
START='<!-- RESULTS:START -->';END='<!-- RESULTS:END -->'


def load(root=ROOT):
    runs=[];trials=[]
    for path in sorted((root/'data/results/LIVE').glob('*/run.json')):
        run=json.loads(path.read_text());assert run['mode']=='LIVE'
        latest={}
        for line in (path.parent/'records.jsonl').read_text().splitlines():
            record=json.loads(line)
            if record['type']=='run':latest[record['trial_id']]=record
        runs.append(run);trials.extend(latest.values())
    billing=json.loads((root/'data/evidence/ledger.json').read_text())
    attempts=[dict(json.loads(r['body']),request_id=r['id'],run_id=r['run_id'],trial_id=r['trial_id'],model_id=r['model_id'],suite=r['suite']) for r in billing['tables']['attempts']]
    return runs,trials,attempts,billing


def f(value,places=3):return 'N/A' if value is None else f'{value:.{places}f}'
def interval(row):return 'N/A' if row['wilson_95'] is None else '–'.join(f'{100*v:.2f}%' for v in row['wilson_95'])
def table(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join('---' for _ in headers)+'|']+['| '+' | '.join(str(x).replace('\n',' ') for x in r)+' |' for r in rows])
def short(model):return next((n for m,n,_ in MODELS if m==model),model)
def bill_for(attempts,ids):return costs([a for a in attempts if a['trial_id'] in ids])
def latency(row):return ' / '.join(f(row['latency_ms'][k]) for k in ('mean','median','p95'))


def render(root=ROOT):
    runs,trials,attempts,billing=load(root);scope=primary_scope(trials);paired=scope['paired']
    summaries=[summarize(paired,m,n) for m,n,_ in MODELS]
    headline=['### Results at a glance — paired primary set',
        f"**Paired sample: {len(scope['paired_puzzles'])} primary puzzles ({', '.join(scope['paired_puzzles'])}); one trial per model per puzzle.** C1 is the recorded race. C2 is included under the same frozen protocol. C3 is reported separately below. The recommended five-puzzle minimum was **NOT met**, due to spending constraints and time. No population-wide ranking is claimed.",
        table(['Model','Solved k/n','Wilson 95%','Mean move latency ms','Mean total s','Mean moves all / solved','Solved-primary gap mean'],
            [[r['name'],f"{r['solved']}/{r['denominator']}",interval(r),f(r['latency_ms']['mean']),f(r['total_s']['mean']),f(r['moves_all']['mean'])+' / '+f(r['moves_solved']['mean']),f(r['gap']['mean'])] for r in summaries])]
    full=['### 7. Full results — generated from saved evidence',
        'Source: each `data/results/LIVE/<run_id>/records.jsonl` run record, matched to `run.json`; billing: `data/evidence/ledger.json`. These files are preserved for the owner to commit; no claim is made that uncommitted files already exist in Git history. SIMULATED runs and replay are excluded. Baselines have separate labelled rows.',
        f"Paired set: **{', '.join(scope['paired_puzzles'])}**, n={len(scope['paired_puzzles'])} per model. A paired puzzle requires all four eligible terminal outcomes under identical saved settings. SOLVED, RAN_OUT_OF_MOVES, OUT_OF_TIME and ERROR_MODEL are eligible experimental outcomes; INFRA_ERROR and NOT_RUN are not eligible success denominators. C3's INFRA_ERROR is a saved terminal error, not a completed evaluation outcome."]
    def trial_table(items):
        rows=[]
        for t in sorted(items,key=lambda t:(t['puzzle_id'],[m for m,_,_ in MODELS].index(t['model_id']))):
            r=summarize([t],t['model_id'],short(t['model_id']));r['latency_ms']=describe([m['successful_latency_ms'] for m in t.get('moves',[]) if m.get('successful_latency_ms') is not None]);cost=bill_for(attempts,{t['trial_id']})
            run=next(r for r in runs if r['id']==t['run_id'])
            rows.append([f"[{run['stage']['name']} / {t['puzzle_id']}](data/results/LIVE/{t['run_id']}/records.jsonl)",short(t['model_id']),t['status'],t['moves_used'],f(t['total_time_s']),latency(r),t['gap'] if t.get('gap') is not None else 'N/A',t.get('refusals',0),t.get('forfeits',0),t.get('truncated_responses',0),'$'+cost['known_cost_usd'],cost['unknown_cost_attempts']])
        return table(['Stage / puzzle','Model','Status','Moves','Total s','Latency mean / median / p95 ms','Gap','Refusals','Forfeits','Truncated','Known USD','Unknown attempts'],rows)
    full+=['#### Paired live trials',trial_table(paired),
        f"Across all saved LIVE trials: {sum(t.get('retries',0) for t in trials)} retries, {sum(t.get('other_malformed_outputs',0) for t in trials)} other malformed outputs, {sum(t.get('truncated_responses',0) for t in trials)} truncated responses and {sum(t.get('refusals',0) for t in trials)} refusals.",
        'Latency is successful-attempt send-to-parsed-body time; invalid/refused/truncated attempts are excluded from this headline latency but remain in costs, total time and attempt logs. p95 is nearest rank. Moves include forfeits; C1 Opus used 16 actions including one forfeit. Gap is actions minus exact shortest distance and is defined only for solved primary trials. All paired puzzles have exact optimum 4.',
        '#### Paired aggregates',table(['Model','Solved k/n','Wilson 95%','Latency mean / median / p95 ms','Total mean all / solved s','Moves mean all / solved','Move distribution','Gap mean / min / zero count / histogram','Final correct stickers mean','Refusals / forfeits / truncated'],
        [[r['name'],f"{r['solved']}/{r['denominator']}",interval(r),latency(r),f(r['total_s']['mean'])+' / '+f(r['solved_total_s']['mean']),f(r['moves_all']['mean'])+' / '+f(r['moves_solved']['mean']),json.dumps(r['move_histogram'],sort_keys=True),f(r['gap']['mean'])+' / '+f(r['gap']['minimum'])+' / '+str(r['gap']['zero'])+' / '+json.dumps(r['gap']['histogram'],sort_keys=True),f(r['correct_stickers_mean']),f"{r['refusals']} / {r['forfeits']} / {r['truncated_responses']}"] for r in summaries])]
    partial=scope['partial_trials']
    full+=['#### Partial C3 stage — additional completed trials and error outcome',
        'C2 and C3 were launched headlessly, sequentially, by `scripts/run_primary_sequence.py --execute --confirm-sequence primary-C2-C5-owner-20261025`, under the owner’s explicit C2–C5 authorization recorded in D-145. Each puzzle’s four models ran concurrently. C3 stopped on saved `TRANSPORT_ERROR`; Opus is `INFRA_ERROR` at approximately 300 s. The adapter combines deadline timeout with transport errors, and the runner checks fatal stage errors before deadline expiry. This likely misclassified the run deadline; it has **not been silently corrected**. No 402 or $2.50 crossing occurred. See [D-146](decisions.md), [execution log](docs/verification/c2-c5/execution.log), and the [saved C3 stage report](data/results/LIVE/912193241d4b3efe90f950d3e2e13df6/stage-report.json).',
        '**Additional completed C3 trials, excluded from the paired headline:**',trial_table(scope['additional_completed']),
        '**C3 error/non-completed evaluation outcome, excluded from denominators:**',trial_table([t for t in partial if t['status'] not in ELIGIBLE]),
        table(['Model','Eligible primary outcomes retained overall','Paired headline n','Additional completed C3','Excluded C3 error'],[[n,sum(t['model_id']==m and t['suite']=='primary' and t['status'] in ELIGIBLE for t in trials),len([t for t in paired if t['model_id']==m]),sum(t['model_id']==m for t in scope['additional_completed']),sum(t['model_id']==m and t['status'] not in ELIGIBLE for t in partial)] for m,n,_ in MODELS]),
        '**C4, C5 and the extension suite: NOT RUN.** Other generated primary/warm-up puzzles were not evaluated live. Trials are not fabricated for unstarted stages.']
    warmup=[t for t in trials if t['suite']=='warmup']
    full+=['#### Warm-up W — separate adapter verification',trial_table(warmup),
        'Only Jev, Luna and Clef were authorized in W. Opus was NOT RUN in warm-up and has no fabricated trial/denominator. Warm-up is excluded from primary comparisons.']
    puzzles=json.loads((root/'data/puzzles/primary.json').read_text());selected=[p for p in puzzles if p['id'] in scope['paired_puzzles']]
    baseline=random_baseline(selected)
    b=baseline['summaries']['primary']
    full+=['#### Random baseline — free, separate from model results',
        table(['Policy / scope','Runs per puzzle','Solved k/n','Wilson 95%','Moves mean','Correct stickers mean','Cost'],[['Random baseline; same paired puzzles',100,f"{b['solved']}/{b['denominator']}",interval(b),f(b['moves_all']['mean']),f(b['correct_stickers_mean']),'$0; no inference']]),
        'Seed 20261024; 100 independent random-policy runs per paired puzzle. Recomputed offline using the same engine. Baseline latency is N/A and is not mixed into the model sample size.',
        '#### Per-move progress — post-hoc exact-distance scoring',
        table(['Model','Known-distance applied moves','Distance-reducing moves / known','On-optimal-path rate','Unknown-distance applied moves','Immediate inverses / pairs','Mean probability entropy nats'],
        [[r['name'],r['progress']['known'],f"{r['progress']['reduced']}/{r['progress']['known']}",f(r['progress']['on_optimal_path_rate']),r['progress']['unknown'],f"{r['immediate_inverses']['count']}/{r['immediate_inverses']['pairs']}",f(r['entropy_nats']['mean'])] for r in summaries]),
        'Only applied moves with both endpoints in the depth-five table enter the exact progress denominator; leaving the table is unknown (distance at least 6), not evidence of improvement. Forfeited unchanged-state actions follow the logged applied flag. Immediate inverse rates use consecutive valid applied moves, excluding forfeits. BFS was never available to live decisions.',
        '#### Confidence calibration — descriptive only',
        table(['Model','Known-progress choices with confidence','Mean confidence when distance reduced','Mean confidence otherwise'],
        [[r['name'],len(r['confidence_progress']),f(sum(x['confidence'] for x in r['confidence_progress'] if x['reduced'])/sum(x['reduced'] for x in r['confidence_progress'])) if any(x['reduced'] for x in r['confidence_progress']) else 'N/A',f(sum(x['confidence'] for x in r['confidence_progress'] if not x['reduced'])/sum(not x['reduced'] for x in r['confidence_progress'])) if any(not x['reduced'] for x in r['confidence_progress']) else 'N/A'] for r in summaries])]
    stage_rows=[]
    for run in sorted(runs,key=lambda r:r['created_at']):
        records=[a for a in attempts if a['run_id']==run['id']];c=costs(records)
        stage_rows.append([run['stage']['name'],run['suite'],run['status'],c['requests'],'$'+c['known_cost_usd'],c['unknown_cost_attempts']])
    for suite in ('diagnostic','phase3_smoke'):
        records=[a for a in attempts if a['suite']==suite];c=costs(records)
        stage_rows.append([suite,'route/adapter probes','Not benchmark trials',c['requests'],'$'+c['known_cost_usd'],c['unknown_cost_attempts']])
    allcost=costs(attempts);stage_rows.append(['LEDGER TOTAL','All stages/accounts','Known subtotal only',len(attempts),'$'+allcost['known_cost_usd'],allcost['unknown_cost_attempts']])
    checkpoint=billing['tables']['reconciliations'][-1];watermark=checkpoint['through_attempt_id']
    new=[a for a in attempts if a['request_id']>watermark];newcost=costs(new)
    reconciled=Decimal(checkpoint['amount'])/10**9+Decimal(newcost['known_cost_usd'])
    full+=['#### Cost and request reconciliation — all stages retained',table(['Stage','Suite','Status','Requests','Known USD','Unknown-cost attempts'],stage_rows),
        'The stage request sum includes diagnostic and adapter probes and equals the ledger total. C4/C5/extension made zero requests. Phase 0 and adapter-probe timings never enter benchmark statistics.',
        table(['Model','Lifetime requests','Lifetime known USD','Unknown-cost attempts'],[[n,costs([a for a in attempts if a['model_id']==m])['requests'],'$'+costs([a for a in attempts if a['model_id']==m])['known_cost_usd'],costs([a for a in attempts if a['model_id']==m])['unknown_cost_attempts']] for m,n,_ in MODELS]),
        f"Owner-reported OpenRouter cumulative spend at the reconciliation checkpoint: **$0.11**, approximately 90K tokens (actual dashboard as-of time/view/date range unspecified). At attempt {watermark}, known costs were $0.027262678; the unexplained difference **$0.082737322** remains unallocated. The checkpoint covers the original 27 unknown costs for budget accounting, not an invented per-model allocation. Adding subsequent known charges gives a **${reconciled} reconciled spending basis**, plus {newcost['unknown_cost_attempts']} post-checkpoint unknown charges. This is not a final invoice. Missing charges are never zero. See [reconciliation note](results/RECONCILIATION-2026-10-25.md).",
        'Current active holds: **$0.60** (50 classified Luna refusals × $0.01 and one unpriced Opus timeout × $0.10). These are owner-approved estimates, not billed costs. The $0.01 estimate was justified using the earlier gap/27 average, not a proven individual upper bound. Headroom after holds under the unchanged $4 cap: **$2.439371194**. The whole-project owner ceiling remains $5; the harness cap remains $4 and C2–C5 additionally had a $2.50 known/reconciled-spend stop.',
        '#### Observed routes and paired token accounting',
        table(['Assigned model ID','API surface','Observed resolved strings','Observed providers','Paired input tokens reported / unknown attempts','Paired output tokens reported / unknown attempts','Paired reasoning tokens reported / unknown attempts'],
        [[m,'POST /api/v1/chat/completions' if c=='LLM' else 'POST /api/alpha/decisions',', '.join(sorted({str(a.get('resolved_model')) for a in attempts if a['model_id']==m and a.get('resolved_model')})) or 'unknown',', '.join(sorted({str(a.get('provider')) for a in attempts if a['model_id']==m and a.get('provider')})) or 'unknown',*[str(sum(a.get(k) or 0 for a in attempts if a['model_id']==m and a['trial_id'] in {t['trial_id'] for t in paired}))+' / '+str(sum(a.get(k) is None for a in attempts if a['model_id']==m and a['trial_id'] in {t['trial_id'] for t in paired})) for k in ('tokens_in','tokens_out','reasoning_tokens')]] for m,n,c in MODELS]),
        'Reported token sums cover only attempts with that field; unknown counts are shown separately. Opus output tokens include reasoning tokens where reported; do not add reasoning tokens again. No billing-only paid requests were made.',
        '![Final dashboard: paired primary scope](docs/img/dashboard-primary.png)',
        '![Recorded C1 race completion, saved real run](docs/img/race-completion.jpg)']
    return '\n\n'.join(headline)+'\n','\n\n'.join(full)+'\n'


def update(text,blocks):
    pattern=re.compile('^'+re.escape(START)+r'\n.*?^'+re.escape(END)+'$',re.S|re.M)
    matches=list(pattern.finditer(text))
    if len(matches)!=len(blocks):raise ValueError('README requires exactly two RESULTS marker pairs')
    out=text
    for match,block in reversed(list(zip(matches,blocks))):out=out[:match.start()]+START+'\n'+block+END+out[match.end():]
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    path=ROOT/'README.md';original=path.read_text();generated=update(original,render())
    if args.check:
        if generated!=original:raise SystemExit('README result blocks differ from preserved evidence')
        print('PASS: both README results blocks reproduce exactly')
    else:path.write_text(generated);print('Updated both README results blocks from preserved evidence')

if __name__=='__main__':main()
