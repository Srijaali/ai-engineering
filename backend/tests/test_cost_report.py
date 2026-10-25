"""Historical attempts must not be double counted or assigned invented costs."""
from decimal import Decimal

from scripts.cost_report import aggregate, collect_calls


def test_canonical_cost_ledger_counts_each_attempt_once():
    calls = [c for c in collect_calls() if c['phase'] == 0]
    assert len(calls) == len({c['run_id'] for c in calls}) == 26
    assert sum(c['cost_usd'] is None for c in calls) == 12
    models = aggregate(calls)
    assert models['typesafe/jev-1.13']['known_cost_usd'] == '0.000191352'
    assert Decimal(models['cloudflare/clef']['known_cost_usd']) == Decimal('0.000165984')
    assert Decimal(models['openai/gpt-6-luna-decisions']['known_cost_usd']) == Decimal('0.0003722')
    assert models['anthropic/claude-opus-5.5']['calls'] == 5
    assert models['anthropic/claude-opus-5.5']['unreported_cost_calls'] == 5
    latest = [c for c in calls if c['run_id'].startswith('phase0-all-four-new-key-')]
    assert len(latest) == len({c['model_id'] for c in latest}) == 4


def test_missing_cost_never_becomes_zero_charge():
    result = aggregate([{'model_id': 'test', 'cost_usd': None},
                        {'model_id': 'test', 'cost_usd': '0.1'}])
    assert result['test'] == dict(calls=2, known_cost_usd='0.1', unreported_cost_calls=1)
