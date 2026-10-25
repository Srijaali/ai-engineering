"""Offline backend/API verification with explicit simulation and mocked HTTP."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import time
import uuid
import httpx
import pytest
from backend.app.config import Settings
from backend.app.models.accounting import Ledger
from backend.app.models.base import MoveDecision
from backend.app.models.registry import MODELS
from backend.app.storage import RunStore
from backend.app.benchmark import Workflow
from backend.app.runner import play_one,trial_id
from backend.app.stats import wilson,aggregate,random_baseline
from backend.app.cube.engine import SOLVED,apply
from backend.app.main import create_app


@pytest.fixture
def workflow(tmp_path):
    settings=Settings(api_key='offline-fixture',student_name='OFFLINE TEST',student_roll='OFFLINE TEST')
    ledger=Ledger(tmp_path/'ledger.sqlite3');store=RunStore(tmp_path/'results')
    def forbidden(*args,**kwargs):raise AssertionError('Simulation opened HTTP client')
    return Workflow(settings,ledger,store,client_factory=forbidden,mock_delay=0)


def submit(w,mode='SIMULATED',puzzles=None):
    return w.submit(uuid.uuid4().hex,'warmup',puzzles or ['warmup-001'],mode,True)[0]


async def finish(w):
    while w.tasks:await asyncio.gather(*list(w.tasks))


def test_simulated_four_model_race_jsonl_isolation_and_replay(workflow):
    run=submit(workflow);asyncio.run(workflow.execute(run['id']))
    saved=workflow.replay(run['id'])
    assert saved['run']['status']=='COMPLETED' and len(saved['trials'])==4
    assert all(t['mode']=='SIMULATED' and t['moves_used']==30 for t in saved['trials'])
    assert all(t['status']=='RAN_OUT_OF_MOVES' and t['cost_usd']=='0' for t in saved['trials'])
    assert len([e for e in saved['events'] if e['type']=='run'])==4
    path=workflow.store.directory(run['id'])
    assert path.parent.name=='SIMULATED'
    logs=[json.loads(s) for s in (path/'records.jsonl').read_text().splitlines()]
    assert len([r for r in logs if r['type']=='move'])==120
    assert len([r for r in logs if r['type']=='run'])==4
    assert all(r['config_snapshot']['max_moves']==30 and r['config_snapshot']['run_budget_s']==300 for r in logs)
    assert workflow.ledger.dashboard_data()['total']['requests']==0
    assert all(r['denominator']==0 for r in workflow.dashboard()['statistics']['warmup'])
    before=(path/'records.jsonl').read_bytes()
    for _ in range(3):workflow.replay(run['id']);workflow.dashboard()
    assert (path/'records.jsonl').read_bytes()==before


def test_submit_idempotency_and_resume_skips_finished_trials(workflow):
    request=uuid.uuid4().hex
    run,start=workflow.submit(request,'warmup',['warmup-001'],'SIMULATED',True)
    duplicate,second=workflow.submit(request,'warmup',['warmup-001'],'SIMULATED',True)
    assert run['id']==duplicate['id'] and start and not second
    asyncio.run(workflow.execute(run['id']))
    again,start=workflow.submit(request,'warmup',['warmup-001'],'SIMULATED',True)
    assert not start and again['status']=='COMPLETED'
    events=workflow.store.events(run['id'])
    again,start=workflow.submit(uuid.uuid4().hex,'warmup',['warmup-001'],'SIMULATED',True,resume_run_id=run['id'])
    assert not start and workflow.store.events(run['id'])==events


class FixtureAgent:
    def __init__(self,move=None,error=None,delay=0):self.move=move;self.error=error;self.delay=delay;self.calls=0
    async def decide(self,obs,ctx):
        self.calls+=1
        if ctx.first_request_perf is None:
            ctx.first_request_perf=time.perf_counter();ctx.first_request_wall=time.time()
            ctx.deadline=ctx.first_request_perf+self.budget
            if ctx.on_first_send:ctx.on_first_send(ctx.first_request_wall)
        await asyncio.sleep(self.delay)
        return MoveDecision(move=self.move,error_class=self.error,
                            forfeited=self.error=='MODEL_REFUSAL',decision_latency_ms=self.delay*1000)


def run_single(w,agent,puzzle=None,budget=300):
    run=submit(w)
    puzzle=puzzle or w.puzzles('warmup')[0]
    agent.budget=budget
    return asyncio.run(play_one(run,puzzle,MODELS[0][0],agent,replace(w.settings,run_budget_s=budget),w.store))


def test_solved_check_after_every_move_and_primary_gap(workflow):
    # Synthetic one-move test fixture only; never used as a live policy or saved benchmark.
    puzzle=dict(id='fixture',suite='primary',facelets=apply(SOLVED,'R'),exact_optimal=1,reference_upper_bound=None)
    agent=FixtureAgent(move="R'")
    result=run_single(workflow,agent,puzzle)
    assert result['status']=='SOLVED' and result['moves_used']==1 and result['gap']==0
    assert result['correct_stickers_final']==54 and agent.calls==1
    move=result['moves'][0]
    assert move['dist_before']==1 and move['dist_after']==0 and move['progress']=='progress'


def test_thirty_refusals_forfeit_without_three_strike_or_progress_heuristic(workflow):
    agent=FixtureAgent(error='MODEL_REFUSAL')
    result=run_single(workflow,agent)
    assert agent.calls==30 and result['status']=='RAN_OUT_OF_MOVES'
    assert result['moves_used']==result['refusals']==result['forfeits']==30
    assert result['initial_facelets']==result['final_facelets'] and result['gap'] is None


def test_wall_clock_is_fixed_from_first_request_not_reset_each_move(workflow):
    agent=FixtureAgent(move='U',delay=.02)
    result=run_single(workflow,agent,budget=.055)
    assert result['status']=='OUT_OF_TIME' and agent.calls==3
    assert result['moves_used']==2 and .055<=result['total_time_s']<.3


def test_stats_denominators_simulated_exclusion_wilson_and_progress(workflow):
    puzzle=dict(id='fixture',suite='primary',facelets=apply(SOLVED,'R'),exact_optimal=1,reference_upper_bound=None)
    trial=run_single(workflow,FixtureAgent(move="R'"),puzzle)
    live={**trial,'mode':'LIVE'}
    missing=dict(model_id=MODELS[0][0],mode='LIVE',suite='primary',status='NOT_RUN')
    infra={**live,'status':'INFRA_ERROR'}
    row=aggregate([trial,live,missing,infra])[0]
    assert row['planned']==3 and row['solved']==row['denominator']==1
    assert row['gap']['zero']==1 and row['gap']['mean']==0
    assert row['progress']['reduced']==1
    assert row['progress']['on_optimal_path_rate']==1 and row['progress']['no_progress']==0
    assert row['wilson_95'][0]<1 and row['wilson_95'][1]<=1
    assert wilson(0,0) is None


def test_random_baseline_reproducible_flagged_and_free(workflow):
    a=random_baseline(workflow.puzzles('warmup')[:1],repeats=100)
    b=random_baseline(workflow.puzzles('warmup')[:1],repeats=100)
    assert a==b and len(a['trials'])==100
    row=a['summaries']['warmup']
    assert row['baseline'] and row['denominator']==100 and row['latency_ms']['mean'] is None
    assert workflow.ledger.dashboard_data()['total']['requests']==0


def test_interrupted_run_requires_explicit_resume_and_keeps_terminal_trials(workflow):
    run=submit(workflow,puzzles=['warmup-001','warmup-002'])
    run['status']='RUNNING';workflow.store.save(run)
    workflow=Workflow(workflow.settings,workflow.ledger,workflow.store,mock_delay=0)
    assert workflow.store.get(run['id'])['status']=='INTERRUPTED'
    assert workflow.store.events(run['id'])==[]
    resumed,start=workflow.submit(uuid.uuid4().hex,'warmup',['warmup-001','warmup-002'],'SIMULATED',True,resume_run_id=run['id'])
    assert start
    asyncio.run(workflow.execute(resumed['id']))
    assert len(workflow.store.trials(run['id']))==8


def test_offline_plan_formula_worst_case_and_preserved_caps(workflow):
    plan=workflow.plan('warmup',['warmup-001'])
    o=plan['opus']
    expected=300/o['measured_seconds_per_call']*float(o['measured_cost_per_call_usd'])
    assert float(o['expected_per_trial_usd'])==pytest.approx(expected)
    assert float(o['worst_case_per_trial_usd'])>expected
    assert plan['max_spend_usd']==4 and plan['recommended_request_cap']==240
    assert workflow.ledger.dashboard_data()['total']['requests']==0


def test_fastapi_confirmed_simulation_sse_and_all_gets_are_read_only(workflow):
    app=create_app(workflow)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            cfg=(await client.get('/api/config')).json()
            headers={'X-Run-Consent':cfg['consent_token']}
            body=dict(request_id=uuid.uuid4().hex,suite='warmup',puzzle_ids=['warmup-001'],mode='SIMULATED')
            assert (await client.post('/api/race',json=body,headers=headers)).status_code==403
            body['confirm']=True
            started=await client.post('/api/race',json=body,headers=headers)
            assert started.status_code==200
            ident=started.json()['run_id']
            await finish(workflow)
            events=await client.get('/api/events/'+ident)
            assert events.status_code==200 and 'text/event-stream' in events.headers['content-type']
            assert 'event: end' in events.text and '"type": "move"' in events.text
            for path in ['/api/results','/api/dashboard','/api/replay/'+ident,'/api/config','/api/health','/','/dashboard.html']:
                assert (await client.get(path)).status_code==200
            return cfg
    cfg=asyncio.run(run())
    assert cfg['student_name']=='OFFLINE TEST'
    assert cfg['settings']['claude_max_tokens']==1024 and cfg['settings']['run_budget_s']==300
    assert workflow.ledger.dashboard_data()['total']['requests']==0


def test_api_requires_session_consent_and_rejects_cross_origin(workflow):
    app=create_app(workflow)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
            cfg=(await c.get('/api/config')).json()
            body=dict(request_id=uuid.uuid4().hex,suite='warmup',puzzle_ids=['warmup-001'],mode='LIVE',confirm=True)
            assert (await c.post('/api/race',json=body)).status_code==403
            assert (await c.post('/api/race',json=body,headers={'X-Run-Consent':cfg['consent_token'],'Origin':'http://other.test'})).status_code==403
            assert (await c.get('/api/replay/../../.env')).status_code in (404,400)
    asyncio.run(run())
    assert workflow.ledger.dashboard_data()['total']['requests']==0


def test_missing_identity_warns_and_blocks_live_but_not_simulation(workflow):
    workflow.settings=replace(workflow.settings,student_name='',student_roll='')
    app=create_app(workflow)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
            cfg=(await c.get('/api/config')).json();assert not cfg['identity_configured']
            body=dict(request_id=uuid.uuid4().hex,suite='warmup',puzzle_ids=['warmup-001'],mode='LIVE',confirm=True)
            r=await c.post('/api/race',json=body,headers={'X-Run-Consent':cfg['consent_token']})
            assert r.status_code==403
    asyncio.run(run())


def test_mocked_live_runner_refusals_and_global_credit_halt(tmp_path):
    calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        if body['model']==MODELS[1][0]:
            return httpx.Response(402,json={'error':{'message':'offline credit fixture'}})
        return httpx.Response(502,json={'error':{'message':'OpenAI refused to answer question next_move'}})
    factory=lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings=Settings(api_key='offline',student_name='OFFLINE TEST',student_roll='OFFLINE TEST')
    w=Workflow(settings,Ledger(tmp_path/'ledger'),RunStore(tmp_path/'runs'),client_factory=factory,mock_delay=0)
    run=submit(w,'LIVE');run['concurrent']=False;w.store.save(run)
    asyncio.run(w.execute(run['id']))
    trials=w.store.trials(run['id'])
    first=next(t for t in trials if t['model_id']==MODELS[0][0])
    assert first['status']=='RAN_OUT_OF_MOVES' and first['refusals']==30
    assert len(calls)==31 and len(trials)==4
    assert sum(t['status']=='NOT_RUN' for t in trials)==2
    assert w.ledger.dashboard_data()['total']['requests']==31
