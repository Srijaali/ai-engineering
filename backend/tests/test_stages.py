"""Generic stages and narrow W validation, using mocked HTTP only."""
import asyncio
from dataclasses import replace
import json
import uuid
import httpx
import pytest
from backend.app.config import Settings
from backend.app.models.registry import MODELS
from backend.app.models.accounting import Ledger,BudgetStop
from backend.app.storage import RunStore
from backend.app.benchmark import Workflow
from backend.app.stages import Stage


def definition(name='W',cap=100,puzzles=None,models=None,suite='warmup'):
    return dict(name=name,suite=suite,puzzle_ids=puzzles or ['warmup-001'],models=models or [m for m,_,_ in MODELS[:3]],request_cap=cap)


def workflow(tmp_path,handler):
    settings=Settings(api_key='offline-fixture',student_name='offline',student_roll='offline')
    ledger=Ledger(tmp_path/'ledger.db');store=RunStore(tmp_path/'results')
    factory=lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Workflow(settings,ledger,store,client_factory=factory)


def response(req,refusal=False):
    model=json.loads(req.content)['model']
    if refusal:return httpx.Response(502,json={'model':model,'error':{'message':'OpenAI refused to answer'},'usage':{'cost':.00001}})
    return httpx.Response(200,json={'model':model,'provider':'fixture','answers':{'next_move':{'choice':'U','confidence':.2}},'usage':{'cost':.00001,'input_tokens':100,'output_tokens':1}})


def test_w_never_constructs_opus_and_membership_is_three(tmp_path,monkeypatch):
    calls=[]
    def handler(req):calls.append(json.loads(req.content)['model']);return response(req)
    w=workflow(tmp_path,handler)
    def forbidden(*args,**kwargs):raise AssertionError('Opus adapter constructed for W')
    monkeypatch.setattr('backend.app.benchmark.ChatAgent',forbidden)
    plan=w.stage_plan(definition());assert plan['planned_initial_calls']==90 and plan['persistent_stage_request_cap']==100
    assert plan['retry_envelope']==180
    with pytest.raises(PermissionError):w.submit_stage(definition())
    run,start=w.submit_stage(definition(),True);assert start
    asyncio.run(w.execute(run['id']))
    trials=w.store.trials(run['id']);assert len(trials)==3 and all(t['moves_used']==30 for t in trials)
    assert len(calls)==90 and set(calls)==set(definition()['models'])
    data=w.dashboard();assert data['statistics']['warmup'][3]['planned']==0
    assert data['live_runs'][0]['mode']=='LIVE' and data['live_runs'][0]['stage']['name']=='W'
    assert len(w.ledger.saved_attempts(run['id']))==90
    again,start=w.submit_stage(definition(),True);assert not start and again['id']==run['id'] and len(calls)==90


def test_stage_allowlist_and_persistent_cap_survive_restart(tmp_path):
    w=workflow(tmp_path,response);d=definition(cap=3)
    run,_=w.submit_stage(d,True);permit=w.permits[run['id']]
    with pytest.raises(PermissionError):w.ledger.reserve(permit,'forbidden',MODELS[-1][0],w.settings)
    for i in range(3):
        request=w.ledger.reserve(permit,str(i),MODELS[0][0],w.settings)
        w.ledger.finish(request,dict(cost_usd='.001',error_class=None,permanent_failure=False))
    restarted=Ledger(w.ledger.path)
    with pytest.raises(BudgetStop):restarted.reserve(permit,'fourth',MODELS[0][0],w.settings)
    assert restarted.dashboard_data()['total']['requests']==3
    changed={**d,'request_cap':4}
    with pytest.raises(PermissionError):w.submit_stage(changed,True)


@pytest.mark.parametrize('status,message',[(402,'credits'),(400,'invalid schema'),(404,'invalid model')])
def test_fatal_errors_halt_after_already_inflight_wave(tmp_path,status,message):
    calls=[]
    def handler(req):calls.append(req);return httpx.Response(status,json={'error':{'message':message},'usage':{'cost':0}})
    w=workflow(tmp_path,handler);run,_=w.submit_stage(definition(puzzles=['warmup-001','warmup-002']),True)
    asyncio.run(w.execute(run['id']))
    assert len(calls)==3 and w.store.get(run['id'])['status']=='STOPPED'
    trials=w.store.trials(run['id']);assert len(trials)==6
    assert sum(t['status']=='NOT_RUN' for t in trials)==3
    assert w.ledger.stage_stop_reason(run['id'])


def test_model_refusals_are_thirty_forfeits_and_do_not_stop(tmp_path):
    calls=[]
    def handler(req):calls.append(req);return response(req,refusal=True)
    w=workflow(tmp_path,handler);run,_=w.submit_stage(definition(),True)
    asyncio.run(w.execute(run['id']))
    assert len(calls)==90 and w.store.get(run['id'])['status']=='COMPLETED'
    assert all(t['forfeits']==t['refusals']==30 for t in w.store.trials(run['id']))


def test_unclassified_agent_exception_stops_stage(tmp_path,monkeypatch):
    calls=[]
    def handler(req):calls.append(req);return response(req)
    w=workflow(tmp_path,handler)
    async def crash(*args):raise RuntimeError('offline-fixture')
    monkeypatch.setattr('backend.app.models.decisions_client.DecisionsAgent.decide',crash)
    run,_=w.submit_stage(definition(),True);asyncio.run(w.execute(run['id']))
    assert not calls and w.store.get(run['id'])['status']=='STOPPED'
    assert len(w.store.trials(run['id']))==3


def test_generic_stage_allows_later_names_without_authorizing_execution(tmp_path):
    w=workflow(tmp_path,response)
    for name,suite,puzzles in [('C1','primary',['primary-001']),('C5','primary',['primary-005']),('E','extension',['extension-001'])]:
        d=definition(name=name,suite=suite,puzzles=puzzles,models=[m for m,_,_ in MODELS])
        assert Stage.from_dict(d).name==name
        assert w.stage_plan(d)['planned_initial_calls']==120
        with pytest.raises(PermissionError):w.submit_stage(d)
    assert w.ledger.dashboard_data()['total']['requests']==0


def test_retry_policy_unchanged_and_stage_never_exceeds_cap(tmp_path,monkeypatch):
    calls=[]
    def handler(req):calls.append(req);return httpx.Response(503,json={'error':{'message':'temporary'},'usage':{'cost':0}})
    w=workflow(tmp_path,handler)
    # Configured transport retry remains one; only the test clock removes real backoff waiting.
    async def fast_sleep(delay):return None
    monkeypatch.setattr('backend.app.models.base.asyncio.sleep',fast_sleep)
    run,_=w.submit_stage(definition(cap=1),True);asyncio.run(w.execute(run['id']))
    assert len(calls)==1 and len(w.ledger.saved_attempts(run['id']))==1
    assert w.settings.max_transport_retries==1 and w.settings.max_malformed_retries==0


def test_unclassified_runner_exception_stops_future_puzzles_and_keeps_billing(tmp_path,monkeypatch):
    calls=[]
    def handler(req):calls.append(req);return response(req)
    w=workflow(tmp_path,handler)
    def crash(*args):raise RuntimeError('offline runner failure')
    monkeypatch.setattr('backend.app.runner.exact_distance',crash)
    run,_=w.submit_stage(definition(puzzles=['warmup-001','warmup-002']),True)
    asyncio.run(w.execute(run['id']))
    assert len(calls)==3 and len(w.store.trials(run['id']))==6
    assert w.store.get(run['id'])['status']=='STOPPED'
    assert w.ledger.dashboard_data()['total']['known_cost_usd']=='0.00003'
    assert sum(t['status']=='NOT_RUN' for t in w.store.trials(run['id']))==3
