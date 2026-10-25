"""Phase 3 offline verification. Mock HTTP only; socket connections are blocked."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time
import httpx
import pytest
from backend.app import config,prompts
from backend.app.config import Settings,recommended_cap
from backend.app.models.accounting import Ledger,BudgetStop,NotRun
from backend.app.models.base import DecisionContext,OBSERVATION_FIELDS,accounting
from backend.app.models.chat_client import ChatAgent
from backend.app.models.decisions_client import DecisionsAgent
from backend.app.models.registry import MODELS
from scripts.phase3_smoke import smoke,RUN_ID

MODEL=MODELS[0][0]
OPUS=MODELS[-1][0]
ROOT=Path(__file__).resolve().parents[2]


def setup(tmp_path,settings=None,smoke_only=False):
    settings=settings or Settings(api_key='offline-placeholder')
    ledger=Ledger(tmp_path/'budget.sqlite3')
    permit=ledger.approve('test','primary',1,settings,confirmed=True,smoke=smoke_only)
    return settings,ledger,DecisionContext(permit,'primary-001')


def response(model,move='U',cost=.00001):
    body={'model':model,'provider':'offline-fixture','usage':{'cost':cost,'input_tokens':123,'output_tokens':9}}
    if model==OPUS:
        body['choices']=[{'finish_reason':'stop','message':{'content':json.dumps({'move':move})}}]
        body['usage']={'cost':cost,'prompt_tokens':123,'completion_tokens':9,
                       'completion_tokens_details':{'reasoning_tokens':2}}
    else:
        body['answers']={'next_move':{'choice':move,'confidence':.1,'probabilities':{'U':.1}}}
    return body


def run_agent(settings,ledger,context,handler,model=MODEL,obs=None):
    calls=[]
    def dispatch(req):
        calls.append(req)
        return handler(req)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(dispatch)) as client:
            cls=ChatAgent if model==OPUS else DecisionsAgent
            result=await cls(model,settings,ledger,client).decide(obs or prompts.smoke_observation(),context)
        return result
    return asyncio.run(run()),calls


def test_exact_validated_prompt_is_untouched_and_four_models_share_observation():
    expected=json.loads((ROOT/'docs/verification/net-n2.request.json').read_text())
    state=expected['state']
    bodies=[]
    for model,_,category in MODELS:
        cls=ChatAgent if category=='LLM' else DecisionsAgent
        _,body=cls(model,Settings(),None,None).payload(state)
        bodies.append(body)
    assert prompts.instructions()==expected['questions']['next_move']['instructions']
    for body in bodies[:3]:
        assert body['questions']==expected['questions']
        assert body['state']==state and set(body['state'])==OBSERVATION_FIELDS
    assert json.loads(bodies[-1]['messages'][1]['content'])==state
    assert bodies[-1]['messages'][0]['content']==prompts.system_prompt()
    assert bodies[-1]['reasoning']=={'effort':'low'} and bodies[-1]['max_tokens']==1024
    assert bodies[-1]['provider']=={'require_parameters':True}
    schema=bodies[-1]['response_format']['json_schema']
    assert schema['strict'] and schema['schema']['properties']['move']['enum']==list(prompts.MOVES)
    assert schema['schema']['additionalProperties'] is False
    assert all('temperature' not in b for b in bodies)
    for body in bodies:
        wire=json.dumps(body)
        for key in ('scramble','solution','exact_optimal','dist_before','puzzle_id','seed','reference_upper_bound'):
            assert key not in wire


@pytest.mark.parametrize('field',['scramble','solution','exact_optimal','puzzle_id','seed','reference_upper_bound'])
@pytest.mark.parametrize('model',[m for m,_,_ in MODELS])
def test_payload_rejects_every_metadata_leak_before_http(tmp_path,field,model):
    settings,ledger,ctx=setup(tmp_path)
    obs={**prompts.smoke_observation(),field:'forbidden'}
    with pytest.raises(ValueError,match='six approved'):
        run_agent(settings,ledger,ctx,lambda r:pytest.fail('HTTP called'),model,obs)
    assert ledger.dashboard_data()['total']['requests']==0


@pytest.mark.parametrize('mutate',[
    lambda o:o.update(facelet_order='UDFRLB'),lambda o:o.update(net='bad'),
    lambda o:o.update(moves_used=31,moves_remaining=-1),lambda o:o.update(moves_used=1,moves_remaining=30),
    lambda o:o.update(recent_moves=['U']*7)])
def test_inconsistent_observation_is_not_transmitted(tmp_path,mutate):
    settings,ledger,ctx=setup(tmp_path);obs=prompts.smoke_observation();mutate(obs)
    with pytest.raises(ValueError):run_agent(settings,ledger,ctx,lambda r:pytest.fail('HTTP'),obs=obs)


def test_no_consent_no_request_and_settings_cannot_drift(tmp_path):
    settings,ledger,ctx=setup(tmp_path)
    with pytest.raises(PermissionError):ledger.approve('bad','primary',1,settings)
    with pytest.raises(PermissionError):
        run_agent(settings,ledger,DecisionContext({'run_id':'test','token':'bad'},'x'),lambda r:pytest.fail('HTTP'))
    with pytest.raises(PermissionError):
        run_agent(replace(settings,claude_max_tokens=6000),ledger,ctx,lambda r:pytest.fail('HTTP'))
    assert ledger.dashboard_data()['total']['requests']==0


@pytest.mark.parametrize('status',[429,500,502,503,524,529])
def test_default_one_transport_retry_same_bytes_and_cost_per_attempt(tmp_path,monkeypatch,status):
    settings,ledger,ctx=setup(tmp_path)
    async def no_sleep(seconds):assert seconds==1
    monkeypatch.setattr('backend.app.models.base.asyncio.sleep',no_sleep)
    result,calls=run_agent(settings,ledger,ctx,lambda r:httpx.Response(status,json={'usage':{'cost':.0001}}))
    assert len(calls)==2 and calls[0].content==calls[1].content
    assert result.status=='INFRA_ERROR' and not result.forfeited
    assert result.attempts[0]['retry_requested'] is True
    assert result.attempts[1]['retry_index']==1
    assert ledger.dashboard_data()['total']['known_cost_usd']=='0.0002'


def test_network_timeout_retry_and_zero_retry_setting(tmp_path,monkeypatch):
    async def no_sleep(seconds):pass
    monkeypatch.setattr('backend.app.models.base.asyncio.sleep',no_sleep)
    def timeout(req):raise httpx.ReadTimeout('offline')
    for retries,expected in ((0,1),(1,2),(2,3)):
        settings,ledger,ctx=setup(tmp_path/str(retries),Settings(max_transport_retries=retries))
        result,calls=run_agent(settings,ledger,ctx,timeout)
        assert len(calls)==expected and result.status=='INFRA_ERROR'
        assert ledger.dashboard_data()['total']['unknown_cost_attempts']==expected


@pytest.mark.parametrize('status,kind',[(400,'BAD_REQUEST'),(401,'AUTH_ERROR'),(404,'MODEL_UNAVAILABLE'),(422,'BAD_REQUEST')])
def test_permanent_errors_are_not_retried_and_later_trials_not_run(tmp_path,status,kind):
    settings,ledger,ctx=setup(tmp_path)
    result,calls=run_agent(settings,ledger,ctx,lambda r:httpx.Response(status,json={'error':{'message':'invalid model or request'}}))
    assert len(calls)==1 and result.error_class==kind
    ctx.trial_id='primary-002'
    later,calls=run_agent(settings,ledger,ctx,lambda r:pytest.fail('Must not repeat permanent failure'))
    assert not calls and later.status=='NOT_RUN'
    assert ledger.dashboard_data()['not_run'][0]['trial_id']=='primary-002'
    # Block survives a new ledger object; other models remain eligible.
    fresh=Ledger(ledger.path)
    good,calls=run_agent(settings,fresh,ctx,lambda r:httpx.Response(200,json=response(MODELS[1][0])),MODELS[1][0])
    assert good.valid and len(calls)==1


def test_schema_rejection_stops_model_but_bad_answer_only_forfeits(tmp_path):
    settings,ledger,ctx=setup(tmp_path)
    result,calls=run_agent(settings,ledger,ctx,lambda r:httpx.Response(400,json={'error':{'message':'unsupported JSON schema'}}))
    assert result.error_class=='SCHEMA_ERROR' and len(calls)==1 and not result.forfeited
    other=MODELS[1][0]
    for _ in range(4):
        result,calls=run_agent(settings,ledger,ctx,lambda r:httpx.Response(200,json={}),other)
        assert result.forfeited and result.error_class=='MALFORMED_OUTPUT' and len(calls)==1


def test_refusal_exception_never_retries_or_trips_circuit(tmp_path):
    settings,ledger,ctx=setup(tmp_path)
    for _ in range(4):
        result,calls=run_agent(settings,ledger,ctx,lambda r:httpx.Response(502,json={'error':{'message':'OpenAI refused to answer question "next_move"'}}))
        assert len(calls)==1 and result.error_class=='MODEL_REFUSAL' and result.forfeited
        assert not result.attempts[0]['retry_requested']
    assert ledger.dashboard_data()['total']['requests']==4


def test_402_halts_all_models_and_survives_restart(tmp_path):
    settings,ledger,ctx=setup(tmp_path)
    result,calls=run_agent(settings,ledger,ctx,lambda r:httpx.Response(402,json={'error':{'message':'credits'}}))
    assert len(calls)==1 and result.error_class=='CREDIT_ERROR'
    for model,_,_ in MODELS:
        later,calls=run_agent(settings,Ledger(ledger.path),ctx,lambda r:pytest.fail('402 halt bypassed'),model)
        assert calls==[] and later.status=='NOT_RUN'
    assert ledger.dashboard_data()['total']['requests']==1


@pytest.mark.parametrize('content',[" U",'U\n','R_prime','r','X',None,2])
def test_move_equality_never_repairs_output(tmp_path,content):
    settings,ledger,ctx=setup(tmp_path)
    result,calls=run_agent(settings,ledger,ctx,lambda r:httpx.Response(200,json=response(MODEL,content)))
    assert result.error_class=='INVALID_MOVE' and result.forfeited and len(calls)==1


@pytest.mark.parametrize('text',['not json','{"move":"U","move":"R"}','{"move":"U","extra":1}','[]'])
def test_strict_chat_json_and_no_malformed_retry_by_default(tmp_path,text):
    settings,ledger,ctx=setup(tmp_path)
    body=response(OPUS);body['choices'][0]['message']['content']=text
    result,calls=run_agent(settings,ledger,ctx,lambda r:httpx.Response(200,json=body),OPUS)
    assert len(calls)==1 and result.forfeited and result.error_class=='MALFORMED_OUTPUT'


def test_configurable_malformed_retry_is_identical_and_counted(tmp_path):
    settings,ledger,ctx=setup(tmp_path,Settings(max_malformed_retries=1))
    result,calls=run_agent(settings,ledger,ctx,lambda r:httpx.Response(200,json={}))
    assert len(calls)==2 and calls[0].content==calls[1].content
    assert result.forfeited and ledger.dashboard_data()['total']['requests']==2


def test_usage_accounting_covers_decisions_chat_truncation_unknown_and_redaction(tmp_path):
    settings,ledger,ctx=setup(tmp_path,Settings(api_key='private-offline-secret'))
    body=response(OPUS);body['choices'][0]['finish_reason']='length';body['choices'][0]['message']['content']=''
    body['usage']['completion_tokens']=1500;body['usage']['completion_tokens_details']['reasoning_tokens']=1500
    body['error_private_echo']=settings.api_key
    result,calls=run_agent(settings,ledger,ctx,lambda r:httpx.Response(200,json=body),OPUS)
    row=ledger.saved_attempts('test')[0]
    assert row['reasoning_tokens']==row['tokens_out']==1500 and row['tokens_in']==123
    assert result.error_class=='LENGTH_OUTPUT' and result.forfeited and len(calls)==1
    assert row['cost_usd']=='0.00001'
    assert settings.api_key not in json.dumps(row)
    result,calls=run_agent(settings,ledger,ctx,lambda r:httpx.Response(200,json={'answers':{'next_move':{'choice':'U'}}}))
    total=ledger.dashboard_data()['total']
    assert total['requests']==2 and total['unknown_cost_attempts']==1 and total['cost_usd'] is None
    assert accounting({'usage':{'cost':'nan'}})['cost_usd'] is None
    assert accounting({})['tokens_out'] is None


def test_request_limit_and_unknown_cost_holds_are_atomic_and_durable(tmp_path):
    settings,ledger,ctx=setup(tmp_path,Settings(max_requests=4))
    def reserve(i):
        try:return ledger.reserve(ctx.permit,str(i),MODEL,settings)
        except BudgetStop:return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(v is not None for v in pool.map(reserve,range(12)))==4
    assert Ledger(ledger.path).dashboard_data()['total']['requests']==4
    with pytest.raises(BudgetStop):Ledger(ledger.path).reserve(ctx.permit,'x',MODEL,settings)
    s,l,c=setup(tmp_path/'spend',Settings(max_spend_usd=.1))
    l.reserve(c.permit,'x',MODEL,s)
    with pytest.raises(BudgetStop):l.reserve(c.permit,'y',MODEL,s)
    assert l.dashboard_data()['total']['unknown_cost_holds_usd']=='0.1'


def test_known_cost_limit_blocks_before_next_http(tmp_path):
    s,l,c=setup(tmp_path,Settings(max_spend_usd=.1))
    result,calls=run_agent(s,l,c,lambda r:httpx.Response(200,json=response(MODEL,cost=.1)))
    assert result.valid and len(calls)==1
    result,calls=run_agent(s,l,c,lambda r:pytest.fail('budget exhausted'))
    assert result.error_class=='BUDGET_EXHAUSTED' and calls==[]


def test_replay_dashboard_payload_building_and_imports_never_request_http(tmp_path):
    s,l,c=setup(tmp_path)
    result,calls=run_agent(s,l,c,lambda r:httpx.Response(200,json=response(MODEL)))
    before=l.dashboard_data()
    for _ in range(3):
        assert l.replay('test')
        assert l.dashboard_data()==before
        DecisionsAgent(MODEL,s,l,None).payload(prompts.smoke_observation())
    assert len(calls)==1


def test_counter_history_is_idempotent_and_missing_initialized_ledger_fails_closed(tmp_path):
    s,l,c=setup(tmp_path)
    historical=[dict(run_id='old',model_id=MODEL,cost_usd=None)]
    l.import_history(historical);l.import_history(historical)
    assert l.dashboard_data()['total']['requests']==1
    assert l.dashboard_data()['by_suite']['diagnostic']['cost_usd'] is None
    l.path.unlink()
    with pytest.raises(RuntimeError,match='restore'):Ledger(l.path)


def test_deadline_no_request_and_recommended_caps_do_not_change_limits(tmp_path):
    s,l,c=setup(tmp_path);c.deadline=time.perf_counter()-1
    result,calls=run_agent(s,l,c,lambda r:pytest.fail('expired'))
    assert result.error_class=='OUT_OF_TIME' and calls==[]
    assert recommended_cap(3,s)==720 and recommended_cap(5,s)==1200
    assert s.max_requests==1000 and s.max_moves==30
    with pytest.raises(ValueError):replace(s,claude_reasoning_effort='none')


def test_smoke_four_calls_exact_first_state_and_no_resume_inference(tmp_path):
    ledger=Ledger(tmp_path/'smoke.db');calls=[]
    def handler(req):
        body=json.loads(req.content);calls.append(body)
        return httpx.Response(200,json=response(body['model']))
    factory=lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler))
    s=Settings(api_key='offline-placeholder')
    first=asyncio.run(smoke(s,ledger,True,factory,tmp_path/'output'))
    assert len(calls)==4 and {b['model'] for b in calls}=={m for m,_,_ in MODELS}
    puzzle=json.loads((ROOT/'data/puzzles/warmup.json').read_text())[0]
    assert first['observation']==prompts.observation(puzzle['facelets'])
    assert first['accounting']['total']['requests']==4
    again=asyncio.run(smoke(s,ledger,True,factory,tmp_path/'output'))
    assert again['status']=='SAVED_ONLY' and len(calls)==4


def test_smoke_402_stops_immediately_and_marks_remaining_not_run(tmp_path):
    ledger=Ledger(tmp_path/'smoke.db');calls=[]
    def handler(req):calls.append(req);return httpx.Response(402,json={'error':{'message':'credits'}})
    factory=lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result=asyncio.run(smoke(Settings(),ledger,True,factory,tmp_path/'output'))
    assert result['status']=='STOPPED_402' and len(calls)==1
    assert len(result['accounting']['not_run'])==3


def test_smoke_suppresses_transport_retry_under_one_call_authorization(tmp_path):
    ledger=Ledger(tmp_path/'smoke.db');calls=[]
    def handler(req):calls.append(req);return httpx.Response(503,json={})
    factory=lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result=asyncio.run(smoke(Settings(),ledger,True,factory,tmp_path/'output'))
    assert len(calls)==4
    assert all(r['attempts'][0]['retry_suppressed_by_allowance'] for r in result['results'])


def test_smoke_allows_one_length_only_opus_calibration_then_freezes(tmp_path):
    ledger=Ledger(tmp_path/'smoke.db');calls=[]
    def handler(req):
        body=json.loads(req.content);calls.append(body);result=response(body['model'])
        if body['model']==OPUS and body['max_tokens']==1500:
            result['choices'][0]['finish_reason']='length'
            result['usage']['completion_tokens']=1500
        return httpx.Response(200,json=result)
    factory=lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result=asyncio.run(smoke(Settings(claude_max_tokens=1500),ledger,True,factory,tmp_path/'output'))
    assert len(calls)==5 and calls[-1]['max_tokens']==3000
    assert result['frozen_config_snapshot']['claude_max_tokens']==3000
    frozen=json.loads((tmp_path/'output/frozen-config.json').read_text())
    assert frozen['live_validated'] and not frozen['benchmark_started']
