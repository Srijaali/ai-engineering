"""Phase 3 only. Default: local plan/report. Explicit flag: one approved smoke.

No stage B/C/D, solver, game runner, web server or automatic paid recovery.
"""
import argparse
import asyncio
from dataclasses import asdict,replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import httpx
from backend.app.config import load_settings,recommended_cap
from backend.app.models.accounting import Ledger
from backend.app.models.base import DecisionContext
from backend.app.models.chat_client import ChatAgent
from backend.app.models.decisions_client import DecisionsAgent
from backend.app.models.registry import MODELS
from backend.app.prompts import observation
from scripts.cost_report import collect_calls

RUN_ID='phase3-smoke-001'
OUTPUT=ROOT/'docs/verification/phase3'


def projection(ledger,settings):
    records=ledger.saved_attempts(RUN_ID)
    rows={}
    for model,_,_ in MODELS:
        samples=[r for r in records if r['model_id']==model]
        # Calibration is not trial data. Use last reported per-call cost only as a scenario.
        latest=samples[-1] if samples else {}
        cost=latest.get('cost_usd')
        rows[model]=dict(measured_smoke_cost_usd=cost,
                        B_3_puzzles_90_moves_usd=str(Decimal(cost)*90) if cost is not None else None,
                        C_5_puzzles_150_moves_usd=str(Decimal(cost)*150) if cost is not None else None,
                        D_primary6to10_150_moves_usd=str(Decimal(cost)*150) if cost is not None else None,
                        D_extension_150_moves_usd=str(Decimal(cost)*150) if cost is not None else None,
                        ablation_per_30_move_trial_usd=str(Decimal(cost)*30) if cost is not None else None)
    return dict(by_model=rows,
                recommended_requests=dict(B=recommended_cap(3,settings),C=recommended_cap(5,settings),
                    D_primary6to10=recommended_cap(5,settings),D_extension=recommended_cap(5,settings)),
                no_retry_maximum_requests=dict(B=360,C=600,D_primary6to10=600,D_extension=600),
                assumptions='Scenario only: 30 actions/trial, repeat the last observed smoke per-call cost. '
                            'Per-call input/output, solve length, routing and retries can differ. '
                            'Multiply cost scenario by 1+configured retries for a retry-envelope scenario; '
                            'missing costs remain unknown. Effort ablation pricing/usage is unmeasured.',
                configured_retry_multiplier=1+settings.max_transport_retries+settings.max_malformed_retries)


async def smoke(settings,ledger,confirmed=False,client_factory=None,output=OUTPUT):
    if confirmed is not True:
        raise PermissionError('Explicit paid-smoke confirmation is required')
    # A persisted attempt is never resubmitted after rerun/crash, even if it has unknown cost.
    existing=ledger.saved_attempts(RUN_ID)
    if existing:
        return dict(status='SAVED_ONLY',attempts=existing,accounting=ledger.dashboard_data(),
                    projections=projection(ledger,settings))
    puzzle=json.loads((ROOT/'data/puzzles/warmup.json').read_text())[0]
    obs=observation(puzzle['facelets'],[],0,30)
    permit=ledger.approve(RUN_ID,'phase3_smoke',1,settings,confirmed=True,smoke=True)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    factory=client_factory or (lambda:httpx.AsyncClient(timeout=settings.request_timeout_s,follow_redirects=False))
    results=[];calibrated=settings;halt=False
    async with factory() as client:
        for model,_,category in MODELS:
            if halt:
                ledger.mark_not_run(RUN_ID,puzzle['id'],model,'CREDIT_ERROR; smoke stopped')
                continue
            agent=(ChatAgent if category=='LLM' else DecisionsAgent)(model,settings,ledger,client)
            decision=await agent.decide(obs,DecisionContext(permit,puzzle['id'],attempt_limit=1))
            entry=dict(model_id=model,**asdict(decision))
            results.append(entry)
            # Each result is printed before the next authorized request is sent.
            print(json.dumps(dict(model_id=model,status=decision.status,error_class=decision.error_class,
                 move=decision.move,confidence=decision.confidence,
                 attempts=[{k:r.get(k) for k in ('http_status','latency_ms','resolved_model','provider','tokens_in','tokens_out','reasoning_tokens','finish_reason','cost_usd')}
                           for r in decision.attempts])),flush=True)
            (output/'smoke-results.json').write_text(json.dumps(results,indent=2,allow_nan=False)+'\n')
            halt=decision.error_class=='CREDIT_ERROR'
            if category=='LLM' and decision.attempts and decision.attempts[-1]['finish_reason']=='length':
                observed=decision.attempts[-1].get('tokens_out') or settings.claude_max_tokens
                higher=2*max(settings.claude_max_tokens,observed)
                ledger.grant_calibration(permit,higher)
                calibrated=replace(settings,claude_max_tokens=higher)
                extra=await ChatAgent(model,calibrated,ledger,client).decide(obs,
                    DecisionContext(permit,puzzle['id']+':pre-experiment-calibration',attempt_limit=1))
                results.append(dict(model_id=model,pre_experiment_calibration=True,**asdict(extra)))
                print(json.dumps(dict(model_id=model,calibration=True,error_class=extra.error_class,
                    attempts=[{k:r.get(k) for k in ('http_status','latency_ms','tokens_out','reasoning_tokens','finish_reason','cost_usd')}
                              for r in extra.attempts])),flush=True)
                (output/'smoke-results.json').write_text(json.dumps(results,indent=2,allow_nan=False)+'\n')
                halt=extra.error_class=='CREDIT_ERROR'
    final=dict(status='STOPPED_402' if halt else 'SMOKE_REVIEW_REQUIRED',run_id=RUN_ID,
               puzzle_id=puzzle['id'],observation=obs,results=results,
               frozen_config_snapshot=calibrated.snapshot(),
               calibration_live_validated=bool(results and results[-1].get('move')),
               accounting=ledger.dashboard_data(),projections=projection(ledger,calibrated))
    (output/'summary.json').write_text(json.dumps(final,indent=2,allow_nan=False)+'\n')
    # This records a fixed candidate; later stages must explicitly approve this snapshot.
    (output/'frozen-config.json').write_text(json.dumps(dict(config=calibrated.snapshot(),
        live_validated=final['calibration_live_validated'],benchmark_started=False),indent=2)+'\n')
    return final


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--confirm-paid-smoke',action='store_true',help='Confirm the explicitly approved four initial calls and one length-only Opus calibration, maximum 6')
    args=parser.parse_args()
    settings=load_settings()
    ledger=Ledger(ROOT/'data/results/phase3/ledger.sqlite3')
    ledger.import_history(collect_calls())
    report=dict(planned_initial_calls=4,optional_opus_length_only_calls=1,absolute_smoke_cap=6,
                max_requests=settings.max_requests,max_spend_usd=settings.max_spend_usd,
                recommended_full_one_puzzle_cap=recommended_cap(1,settings),
                settings=settings.snapshot(),accounting={k:v for k,v in ledger.dashboard_data().items() if k in ('total','by_model')})
    print(json.dumps(report,indent=2),flush=True)
    if not args.execute:
        return
    if not args.confirm_paid_smoke:
        raise SystemExit('No paid calls: add --confirm-paid-smoke only after explicit authorization')
    summary=asyncio.run(smoke(settings,ledger,confirmed=True))
    print(json.dumps(dict(status=summary['status'],accounting=summary['accounting']['total'],
                          by_model=summary['accounting']['by_model'],projections=summary['projections']),indent=2))


if __name__=='__main__':
    main()
