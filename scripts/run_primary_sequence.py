"""Owner-approved C2-C5: plan offline, then explicit headless sequence consent."""
import argparse
import asyncio
from decimal import Decimal
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.config import ROOT,load_settings
from backend.app.models.accounting import Ledger
from backend.app.models.registry import MODELS
from backend.app.storage import RunStore
from backend.app.benchmark import Workflow
from backend.app.stages import Stage
from scripts.run_benchmark import stage_report

SEQUENCE='primary-C2-C5-owner-20261025'

async def execute(workflow,definitions):
    workflow.ledger.approve_sequence(SEQUENCE,definitions,'2.50',confirmed=True)
    reports=[]
    try:
        for definition in definitions:
            total=workflow.ledger.dashboard_data()['total']
            if Decimal(total['budget_spend_usd'])>=Decimal('2.50') or workflow.ledger.stop_reason():
                print(json.dumps(dict(event='SEQUENCE_STOPPED',reason='spend threshold or global paid halt')),flush=True);break
            run,start=workflow.submit_stage(definition,confirmed=True)
            if not start:
                print(json.dumps(dict(event='SEQUENCE_STOPPED',reason='Stage already exists; no automatic rerun',stage=definition['name'])),flush=True);break
            print(json.dumps(dict(event='STAGE_STARTED',stage=definition['name'],run_id=run['id'])),flush=True)
            task=asyncio.create_task(workflow.execute(run['id']))
            while not task.done():
                await asyncio.wait({task},timeout=10)
                if not task.done():print(json.dumps(dict(event='PROGRESS',stage=definition['name'],requests=len(workflow.ledger.saved_attempts(run['id'])),budget=workflow.ledger.dashboard_data()['total'])),flush=True)
            await task
            report=stage_report(workflow,run['id']);reports.append(report)
            workflow.store.atomic(workflow.store.directory(run['id'])/'stage-report.json',report)
            print(json.dumps(dict(event='STAGE_FINISHED',stage=definition['name'],status=report['status'],models=report['models'],cost=report['stage_cost'])),flush=True)
            if report['status']!='COMPLETED' or workflow.ledger.stage_stop_reason(run['id']):
                print(json.dumps(dict(event='SEQUENCE_STOPPED',reason=workflow.ledger.stage_stop_reason(run['id']) or report['status'])),flush=True);break
    finally:
        target=ROOT/'docs/verification/c2-c5';target.mkdir(parents=True,exist_ok=True)
        workflow.store.atomic(target/'report.json',dict(reports=reports,accounting=workflow.ledger.dashboard_data()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true');parser.add_argument('--confirm-sequence')
    args=parser.parse_args()
    settings=load_settings();ledger=Ledger(ROOT/'data/results/phase3/ledger.sqlite3')
    expected=dict(claude_reasoning_effort='low',claude_max_tokens=1024,run_budget_s=300,max_moves=30,max_spend_usd=4)
    if any(getattr(settings,k)!=v for k,v in expected.items()):raise SystemExit('Frozen settings differ; stop')
    definitions=[Stage.from_dict(json.loads((ROOT/f'stages/C{i}.json').read_text())).as_dict() for i in range(2,6)]
    workflow=Workflow(settings,ledger,RunStore(ROOT/'data/results'))
    plans=[workflow.stage_plan(d) for d in definitions]
    print(json.dumps(dict(event='PLAN',sequence=SEQUENCE,stop_spend_usd='2.50',plans=plans),indent=2),flush=True)
    if args.execute:
        if args.confirm_sequence!=SEQUENCE:raise SystemExit('Explicit sequence confirmation required')
        asyncio.run(execute(workflow,definitions))

if __name__=='__main__':main()
