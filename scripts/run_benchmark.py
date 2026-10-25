"""Offline planning by default; a stage executes only with explicit named consent."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.config import ROOT,load_settings
from backend.app.models.accounting import Ledger
from backend.app.storage import RunStore
from backend.app.benchmark import Workflow
from backend.app.stages import Stage
from backend.app.stats import describe
from backend.app.runner import costs
from scripts.cost_report import collect_calls


def stage_report(workflow,run_id):
    run=workflow.store.get(run_id);trials=workflow.store.trials(run_id)
    attempts=workflow.ledger.saved_attempts(run_id);rows=[]
    for model in run['model_ids']:
        selected=[t for t in trials if t['model_id']==model]
        billing=[a for a in attempts if a['model_id']==model]
        latencies=[m['successful_latency_ms'] for t in selected for m in t.get('moves',[]) if m.get('successful_latency_ms') is not None]
        rows.append(dict(model_id=model,statuses=[t['status'] for t in selected],moves_used=sum(t['moves_used'] for t in selected),
                         successful_attempt_latency_ms=describe(latencies),all_attempt_latency_ms=describe([a['latency_ms'] for a in billing if a.get('latency_ms') is not None]),
                         refusals=sum(t.get('refusals',0) for t in selected),forfeits=sum(t.get('forfeits',0) for t in selected),
                         truncated_responses=sum(t.get('truncated_responses',0) for t in selected),
                         other_malformed_outputs=sum(t.get('other_malformed_outputs',0) for t in selected),
                         retries=sum(t.get('retries',0) for t in selected),**costs(billing)))
    return dict(run_id=run_id,stage=run['stage'],suite=run['suite'],mode=run['mode'],status=run['status'],
                error_class=run.get('error_class'),models=rows,stage_cost=costs(attempts),accounting=workflow.ledger.dashboard_data())


async def execute_stage(workflow,stage):
    run,start=workflow.submit_stage(stage.as_dict(),confirmed=True)
    if start:
        print(json.dumps(dict(event='STAGE_STARTED',stage=stage.name,run_id=run['id'])),flush=True)
        task=asyncio.create_task(workflow.execute(run['id']))
        while not task.done():
            await asyncio.wait({task},timeout=10)
            if not task.done():
                print(json.dumps(dict(event='PROGRESS',stage=stage.name,attempts=len(workflow.ledger.saved_attempts(run['id'])),stop_reason=workflow.ledger.stage_stop_reason(run['id']))),flush=True)
        await task
    else:print(json.dumps(dict(event='SAVED_ONLY_NO_RERUN',run_id=run['id'])),flush=True)
    report=stage_report(workflow,run['id'])
    workflow.store.atomic(workflow.store.directory(run['id'])/'stage-report.json',report)
    print(json.dumps(report,indent=2),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',choices=['warmup','primary','extension'],default='warmup')
    parser.add_argument('--puzzles',type=int,default=1)
    parser.add_argument('--build-baseline',action='store_true')
    parser.add_argument('--stage',type=Path)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--confirm-stage')
    args=parser.parse_args()
    if args.execute and not args.stage:raise SystemExit('Paid execution requires a stage definition')
    if args.stage and args.build_baseline:raise SystemExit('Keep stage execution separate from baseline preparation')
    ledger=Ledger(ROOT/'data/results/phase3/ledger.sqlite3');ledger.import_history(collect_calls())
    settings=load_settings(False,False)
    workflow=Workflow(settings,ledger,RunStore(ROOT/'data/results'),mock_delay=0)
    if args.stage:
        stage=Stage.from_dict(json.loads(args.stage.read_text()))
        print(json.dumps(workflow.stage_plan(stage.as_dict()),indent=2),flush=True)
        if args.execute:
            if args.confirm_stage!=stage.name:raise SystemExit('Explicit --confirm-stage '+stage.name+' required')
            asyncio.run(execute_stage(workflow,stage))
        return
    puzzles=workflow.puzzles(args.suite)
    if not 1<=args.puzzles<=len(puzzles):raise SystemExit('Invalid puzzle count')
    print(json.dumps(workflow.plan(args.suite,[p['id'] for p in puzzles[:args.puzzles]]),indent=2))
    if args.build_baseline:
        data=workflow.build_baseline()
        print('Free random baseline written:',len(data['trials']),'trials; zero inference/cost.')


if __name__=='__main__':main()
