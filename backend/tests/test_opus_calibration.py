"""Calibration consent, hard cap, no retries, separation and exact frozen input."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import httpx
import pytest
from backend.app.config import Settings,ROOT
from backend.app.models.accounting import Ledger,BudgetStop
from backend.app.models.registry import MODELS
from backend.app.prompts import system_prompt
from scripts.calibrate_opus import states,calibrate,recommendation


def test_five_distinct_states_deterministic_and_not_primary_extension():
    samples=states();assert samples==states() and len({s['observation']['facelets'] for s in samples})==5
    primary={p['facelets'] for suite in ('primary','extension') for p in json.loads((ROOT/f'data/puzzles/{suite}.json').read_text())}
    assert not any(s['observation']['facelets'] in primary for s in samples)
    assert [len(s['extra_moves']) for s in samples]==[0,0,0,5,10]
    assert [s['observation']['moves_used'] for s in samples]==[0,0,0,5,10]


def test_calibration_gate_five_opus_only_no_global_changes(tmp_path):
    ledger=Ledger(tmp_path/'ledger.db');s=replace(Settings(),claude_max_tokens=3000,max_transport_retries=0)
    with pytest.raises(PermissionError):ledger.approve_calibration('fixture',s)
    with pytest.raises(PermissionError):ledger.approve_calibration('fixture',Settings(),True)
    permit=ledger.approve_calibration('fixture',s,True)
    with pytest.raises(PermissionError):ledger.reserve(permit,'bad-model',MODELS[0][0],s)
    for i in range(5):
        ident=ledger.reserve(permit,str(i),MODELS[-1][0],s)
        ledger.finish(ident,dict(cost_usd='.01',error_class=None,permanent_failure=False))
    with pytest.raises(BudgetStop):ledger.reserve(permit,'sixth',MODELS[-1][0],s)
    assert s.max_spend_usd==4 and s.max_requests==1000
    with pytest.raises(PermissionError):ledger.approve_calibration('fixture',s,True)


def test_exactly_five_calls_frozen_prompt_and_separate_accounting(tmp_path):
    ledger=Ledger(tmp_path/'ledger.db');calls=[]
    def handler(req):
        body=json.loads(req.content);calls.append(body)
        assert body['model']==MODELS[-1][0] and body['max_tokens']==3000 and body['reasoning']=={'effort':'low'}
        assert body['messages'][0]['content']==system_prompt()
        return httpx.Response(200,json=dict(model=MODELS[-1][0],provider='fixture',choices=[dict(message=dict(content='{"move":"R"}'),finish_reason='stop')],usage=dict(prompt_tokens=1000,completion_tokens=800,completion_tokens_details=dict(reasoning_tokens=780),cost=.02)))
    factory=lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result=asyncio.run(calibrate(Settings(),ledger,tmp_path/'output',True,factory,'fixture'))
    assert result['calls']==len(calls)==5 and result['known_cost_usd']=='0.10'
    assert result['recommendation']['ceiling']==1500
    assert result['owner_tentative_config']['claude_max_tokens']==1024
    assert set(ledger.dashboard_data()['by_suite'])=={'calibration'}
    assert result['excluded_from_benchmark_statistics']
    with pytest.raises(PermissionError):asyncio.run(calibrate(Settings(),ledger,tmp_path/'output',True,factory,'fixture'))
    assert len(calls)==5


@pytest.mark.parametrize('status',[402,503])
def test_402_stops_all_and_503_never_retries(tmp_path,status):
    ledger=Ledger(tmp_path/'ledger.db');calls=[]
    def handler(req):calls.append(req);return httpx.Response(status,json={'error':{'message':'fixture'},'usage':{'cost':0}})
    factory=lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result=asyncio.run(calibrate(Settings(),ledger,tmp_path/'output',True,factory,'fixture'))
    assert len(calls)==(1 if status==402 else 5)
    assert all(r['attempts']==1 for r in result['results'])
    if status==402:assert ledger.stop_reason()=='CREDIT_ERROR'


def test_recommendation_nearest_250_and_censored_flag():
    rows=[dict(tokens_out=1500,finish_reason='stop') for _ in range(5)]
    assert recommendation(rows)['ceiling']==3000
    rows[-1]=dict(tokens_out=3000,finish_reason='length')
    assert recommendation(rows)['ceiling']==6000 and recommendation(rows)['tentative']
