"""Published evidence and paired scope reproduce offline, without paid requests."""
import json
from pathlib import Path
from decimal import Decimal
from scripts.make_readme_results import ROOT,load,render,update
from backend.app.analysis_scope import primary_scope
from backend.app.models.accounting import Ledger


def test_readme_generated_blocks_reproduce_exactly():
    original=(ROOT/'README.md').read_text()
    assert update(original,render())==original
    assert update(update(original,render()),render())==original


def test_real_scope_retains_c2_and_separates_partial_c3():
    runs,trials,attempts,billing=load();scope=primary_scope(trials)
    assert scope['paired_puzzles']==['primary-001','primary-002']
    assert len(scope['paired'])==8 and len(scope['additional_completed'])==3
    assert len(scope['partial_trials'])==4
    assert [t['status'] for t in scope['partial_trials']].count('INFRA_ERROR')==1
    assert len(attempts)==458 and len({a['request_id'] for a in attempts})==458
    assert sum(Decimal(a['cost_usd']) for a in attempts if a.get('cost_usd') is not None)==Decimal('0.877891484')
    assert len([a for a in attempts if a.get('cost_usd') is None])==78


def test_public_snapshot_bootstrap_preserves_counters_not_consent(tmp_path):
    ledger=Ledger(tmp_path/'test.db');snapshot=ROOT/'data/evidence/ledger.json'
    ledger.import_submission_snapshot(snapshot)
    total=ledger.dashboard_data()['total']
    assert total['requests']==458 and total['budget_spend_usd']=='0.960628806'
    assert total['unknown_cost_holds_usd']=='0.6'
    with ledger.connection() as db:assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]==0
    ledger.import_submission_snapshot(snapshot)
    assert ledger.dashboard_data()['total']['requests']==458


def test_dashboard_primary_matches_readme_and_lists_c3_separately(tmp_path):
    import shutil
    from backend.app.benchmark import Workflow
    from backend.app.storage import RunStore
    from backend.app.config import Settings
    results=tmp_path/'results';shutil.copytree(ROOT/'data/results/LIVE',results/'LIVE')
    ledger=Ledger(tmp_path/'ledger.db');ledger.import_submission_snapshot(ROOT/'data/evidence/ledger.json')
    workflow=Workflow(Settings(),ledger,RunStore(results))
    data=workflow.dashboard()
    assert data['primary_scope']['paired_puzzles']==['primary-001','primary-002']
    assert [r['denominator'] for r in data['statistics']['primary']]==[2,2,2,2]
    assert [r['solved'] for r in data['statistics']['primary']]==[0,0,0,1]
    assert len(data['primary_scope']['additional_completed'])==3
    assert len(data['primary_scope']['partial_trials'])==4
    assert data['primary_scope']['partial_stages'][0]['error_class']=='TRANSPORT_ERROR'
    assert [r['cost']['requests'] for r in data['statistics']['primary']]==[60,60,60,46]
