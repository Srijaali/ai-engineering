"""Offline owner-reported spend reconciliation; historical attempts stay intact."""
import json
from decimal import Decimal
import pytest
from backend.app.config import Settings
from backend.app.models.accounting import Ledger,BudgetStop
from backend.app.models.registry import MODELS


def fixture(tmp_path):
    ledger=Ledger(tmp_path/'ledger.sqlite3');settings=Settings(max_spend_usd=4)
    permit=ledger.approve('offline','warmup',1,settings,confirmed=True)
    for i,cost in enumerate(['0.027262678',None]):
        request=ledger.reserve(permit,str(i),MODELS[0][0],settings)
        ledger.finish(request,dict(cost_usd=cost,status='FINISHED'))
    return ledger,settings,permit


def test_reconciliation_preserves_history_and_releases_only_covered_holds(tmp_path):
    ledger,settings,permit=fixture(tmp_path)
    before=ledger.saved_attempts()
    total=ledger.reconcile('0.11',{'source':'owner-reported OpenRouter','scope':'unknown'},confirmed=True)
    assert ledger.saved_attempts()==before
    assert total['requests']==2 and total['unknown_cost_attempts']==1 and total['cost_usd'] is None
    assert total['known_cost_usd']=='0.027262678'
    assert total['budget_spend_usd']=='0.11' and total['unknown_cost_holds_usd']=='0'
    assert total['historical_unknown_cost_holds_usd']=='0.1'
    assert Decimal('4')-Decimal(total['budget_spend_usd'])==Decimal('3.89')
    ledger=Ledger(ledger.path)
    request=ledger.reserve(permit,'new',MODELS[0][0],settings)
    assert ledger.dashboard_data()['total']['unknown_cost_holds_usd']=='0.1'
    ledger.finish(request,dict(cost_usd='0.02',status='FINISHED'))
    assert ledger.dashboard_data()['total']['budget_spend_usd']=='0.13'
    assert ledger.dashboard_data()['by_model'][MODELS[0][0]]['known_cost_usd']=='0.047262678'


def test_reconciliation_consent_pending_and_monotonic_spend(tmp_path):
    ledger,settings,permit=fixture(tmp_path)
    with pytest.raises(PermissionError):ledger.reconcile('0.11',{})
    for value in ['NaN','-1','0.0000000001','0.01']:
        with pytest.raises(ValueError):ledger.reconcile(value,{},confirmed=True)
    ledger.reconcile('0.11',{},confirmed=True)
    with pytest.raises(ValueError):ledger.reconcile('0.10',{},confirmed=True)
    ledger.reconcile('0.12',{},confirmed=True)
    with ledger.connection() as db:assert db.execute('SELECT COUNT(*) FROM reconciliations').fetchone()[0]==2
    ledger.reserve(permit,'pending',MODELS[0][0],settings)
    with pytest.raises(ValueError):ledger.reconcile('0.20',{},confirmed=True)


def test_authoritative_spend_enforces_unchanged_cap(tmp_path):
    ledger,settings,permit=fixture(tmp_path)
    ledger.reconcile('3.95',{},confirmed=True)
    with pytest.raises(BudgetStop):ledger.reserve(permit,'blocked',MODELS[0][0],settings)
    assert ledger.dashboard_data()['total']['requests']==2
