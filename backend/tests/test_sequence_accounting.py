"""Owner-approved refusal estimates and sequence stop; no live requests."""
import json
import pytest
from backend.app.models.accounting import Ledger,units,BudgetStop
from backend.app.config import Settings
from backend.app.models.registry import MODELS
from backend.tests.test_stages import definition

LUNA=MODELS[1][0]

def refusal():
    return dict(http_status=502,error_class='MODEL_REFUSAL',cost_usd=None,
                raw_response={'error':{'message':'OpenAI refused to answer question next_move'}})


def test_only_exact_luna_no_usage_refusals_get_lower_hold():
    r=refusal()
    assert Ledger.effective_hold(LUNA,units('.1'),json.dumps(r))==units('.01')
    variants=[{**r,'http_status':503},{**r,'error_class':'TRANSPORT_ERROR'},
              {**r,'raw_response':{'error':{'message':'other'}}},
              {**r,'raw_response':{'error':r['raw_response']['error'],'usage':{'output_tokens':3}}}]
    for other in variants:assert Ledger.effective_hold(LUNA,units('.1'),json.dumps(other))==units('.1')
    assert Ledger.effective_hold(MODELS[-1][0],units('.1'),json.dumps(r))==units('.1')


def test_refusal_hold_changes_without_rewriting_attempts(tmp_path):
    ledger=Ledger(tmp_path/'budget.db');s=Settings()
    permit=ledger.approve('test','primary',1,s,True)
    rid=ledger.reserve(permit,'one',LUNA,s);ledger.finish(rid,refusal())
    with ledger.connection() as db:assert db.execute('SELECT hold FROM attempts').fetchone()[0]==units('.1')
    assert ledger.dashboard_data()['total']['unknown_cost_holds_usd']=='0.01'
    assert Ledger(ledger.path).dashboard_data()['total']['unknown_cost_holds_usd']=='0.01'


def test_sequence_stops_at_reconciled_basis_plus_new_known_cost(tmp_path):
    ledger=Ledger(tmp_path/'budget.db');settings=Settings()
    a=definition(name='C2');b=definition(name='C3')
    ledger.reconcile('.11',{},confirmed=True)
    with pytest.raises(PermissionError):ledger.approve_sequence('s',[a,b],'2.50')
    ledger.approve_sequence('s',[a,b],'2.50',confirmed=True)
    with pytest.raises(PermissionError):ledger.approve_sequence('s',[a,b],'2.50',confirmed=True)
    pa=ledger.approve_stage('a',a,settings,True)
    rid=ledger.reserve(pa,'a',LUNA,settings)
    ledger.finish(rid,dict(cost_usd='2.40',error_class=None))
    assert ledger.dashboard_data()['total']['budget_spend_usd']=='2.51'
    assert 'SEQUENCE_SPEND_STOP' in ledger.stage_stop_reason('a')
    pb=ledger.approve_stage('b',b,settings,True)
    with pytest.raises(BudgetStop):Ledger(ledger.path).reserve(pb,'b',LUNA,settings)
    assert ledger.dashboard_data()['total']['requests']==1
