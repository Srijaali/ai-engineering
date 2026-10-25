"""Explicit pre-experiment five-call Opus calibration; never runs a benchmark."""
import argparse
import asyncio
from dataclasses import asdict,replace
from decimal import Decimal,ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from backend.app.config import ROOT,load_settings
from backend.app.models.accounting import Ledger
from backend.app.models.base import DecisionContext
from backend.app.models.chat_client import ChatAgent
from backend.app.models.registry import MODELS
from backend.app.cube.engine import apply_moves
from backend.app.cube.puzzles import generate_scramble
from backend.app.prompts import observation

RUN_ID='opus-calibration-20261025-001'
OUTPUT=ROOT/'data/results/CALIBRATION'/RUN_ID


def states():
    suites={s:json.loads((ROOT/f'data/puzzles/{s}.json').read_text()) for s in ('warmup','primary','extension')}
    warmup=suites['warmup'];fixed={p['facelets'] for rows in suites.values() for p in rows}
    records=[dict(id=f'calibration-{i+1:03d}',source_puzzle=p['id'],extra_moves=[],seed=None,
                  observation=observation(p['facelets'])) for i,p in enumerate(warmup)]
    seen={r['observation']['facelets'] for r in records}
    for source,length in zip(warmup,(5,10)):
        seed=20261025+length
        while True:
            moves=generate_scramble(seed,length);facelets=apply_moves(source['facelets'],moves)
            if facelets not in seen and facelets not in fixed:break
            seed+=1
        seen.add(facelets)
        records.append(dict(id=f'calibration-{len(records)+1:03d}',source_puzzle=source['id'],extra_moves=moves,seed=seed,
                            observation=observation(facelets,moves,length)))
    assert len(records)==len(seen)==5
    return records


def recommendation(records):
    values=[r['tokens_out'] for r in records if r.get('tokens_out') is not None]
    if not values:return None
    target=max(Decimal(1500),Decimal(2*max(values)))
    rounded=int((target/250).quantize(Decimal(1),rounding=ROUND_HALF_UP))*250
    return dict(largest_observed_output=max(values),ceiling=rounded,
                rule='max(1500, 2 * maximum completion tokens), rounded to nearest 250; ties up',
                tentative=any(r.get('finish_reason')=='length' for r in records) or len(values)<5,
                caveat='Any length-truncated sample is censored; recommendation is not guaranteed sufficient.')


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');tmp.replace(path)


async def calibrate(settings,ledger,output=OUTPUT,confirmed=False,client_factory=None,run_id=RUN_ID):
    if confirmed is not True:raise PermissionError('Explicit five-call calibration consent required')
    if ledger.saved_attempts(run_id):raise PermissionError('Saved calibration exists; no automatic replay of paid calls')
    calibrated=replace(settings,claude_reasoning_effort='low',claude_max_tokens=3000,
                       max_transport_retries=0,max_malformed_retries=0)
    records=states();output=Path(output)
    if output.exists():raise PermissionError('Calibration output already exists; inspect before any further calls')
    permit=ledger.approve_calibration(run_id,calibrated,True)
    plan=dict(run_id=run_id,suite='calibration',excluded_from_benchmark_statistics=True,
              owner_tentative_config=settings.snapshot(),calibration_only_config=calibrated.snapshot(),
              requested_model=MODELS[-1][0],calls=5,retries=0,states=records,
              frozen_prompt_sha256=hashlib.sha256((ROOT/'backend/app/prompts.py').read_bytes()).hexdigest())
    write(output/'plan.json',plan)
    factory=client_factory or (lambda:httpx.AsyncClient(timeout=settings.request_timeout_s,follow_redirects=False))
    results=[];stop_reason=None
    async with factory() as client:
        agent=ChatAgent(MODELS[-1][0],calibrated,ledger,client)
        for index,state in enumerate(records,1):
            decision=await agent.decide(state['observation'],DecisionContext(permit,state['id'],attempt_limit=1))
            last=decision.attempts[-1] if decision.attempts else {}
            row=dict(call=index,state_id=state['id'],suite='calibration',model_id=MODELS[-1][0],
                config_snapshot=calibrated.snapshot(),http_status=last.get('http_status'),
                resolved_model=last.get('resolved_model'),provider=last.get('provider'),
                tokens_in=last.get('tokens_in'),reasoning_tokens=last.get('reasoning_tokens'),tokens_out=last.get('tokens_out'),
                finish_reason=last.get('finish_reason'),latency_ms=last.get('latency_ms'),cost_usd=last.get('cost_usd'),
                request_id=last.get('request_id'),move=decision.move,error_class=decision.error_class,
                attempts=len(decision.attempts),raw_response=last.get('raw_response'),
                excluded_from_benchmark_statistics=True)
            results.append(row)
            with (output/'attempts.jsonl').open('a') as stream:stream.write(json.dumps(row,allow_nan=False)+'\n')
            write(output/'results.json',results)
            print(json.dumps({k:v for k,v in row.items() if k not in ('raw_response','config_snapshot')}),flush=True)
            if decision.error_class=='CREDIT_ERROR':stop_reason='402: all paid calls stopped';break
            if not decision.attempts or last.get('permanent_failure'):
                stop_reason=decision.error_class or 'No HTTP attempt permitted';break
    known=sum((Decimal(r['cost_usd']) for r in results if r['cost_usd'] is not None),Decimal(0))
    unknown=sum(r['cost_usd'] is None for r in results)
    summary=dict(run_id=run_id,suite='calibration',excluded_from_benchmark_statistics=True,
        status='STOPPED' if stop_reason else 'COMPLETE',stop_reason=stop_reason,calls=sum(r['attempts'] for r in results),
        results=results,known_cost_usd=str(known),unknown_cost_attempts=unknown,cost_usd=None if unknown else str(known),
        recommendation=recommendation(results),owner_tentative_config=settings.snapshot(),
        calibration_only_config=calibrated.snapshot(),accounting=ledger.dashboard_data())
    write(output/'summary.json',summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--confirm-five-opus-calls',action='store_true')
    args=parser.parse_args();settings=load_settings(False,False)
    print(json.dumps(dict(suite='calibration',model=MODELS[-1][0],calls=5,max_tokens=3000,reasoning='low',retries=0,
                         owner_env_max_tokens=settings.claude_max_tokens,max_spend_usd=settings.max_spend_usd,
                         states=[dict(id=r['id'],source_puzzle=r['source_puzzle'],extra_moves=r['extra_moves'],seed=r['seed']) for r in states()]),indent=2),flush=True)
    if not args.execute:return
    if not args.confirm_five_opus_calls:raise SystemExit('Missing explicit five-call confirmation flag')
    load_settings()  # Require actual local credentials and identity; never print them.
    ledger=Ledger(ROOT/'data/results/phase3/ledger.sqlite3')
    summary=asyncio.run(calibrate(settings,ledger,confirmed=True))
    print(json.dumps({k:v for k,v in summary.items() if k not in ('results','accounting')},indent=2),flush=True)

if __name__=='__main__':main()
