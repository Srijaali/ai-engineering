"""Offline audit of unique Phase 0 inference attempts; never queries accounts.

Canonical sources avoid double counting resumed/combined evidence. An absent
usage.cost stays null. Each diagnostic call is labelled as its own diagnostic
run, not a completed cube trial. Phase 3 adapter attempts are read from their durable ledger without HTTP.
"""
import csv
from decimal import Decimal
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs/verification'


def collect_calls() -> list[dict]:
    calls = []
    response_ids = set()

    def add(filename, record, timestamp, account, label):
        body = record.get('response', {})
        response_id = body.get('id') if isinstance(body, dict) else None
        if response_id:
            if response_id in response_ids:
                raise ValueError('Duplicate response ID in canonical cost registry')
            response_ids.add(response_id)
        usage = body.get('usage', {}) if isinstance(body, dict) else {}
        cost = usage.get('cost')
        calls.append(dict(run_id=label, run_kind='diagnostic_single_request', phase=0,
                          model_id=record['model_id'], timestamp_utc=timestamp,
                          account_context=account, http_status=record.get('http_status'),
                          cost_usd=None if cost is None else str(Decimal(str(cost))),
                          cost_source='usage.cost' if cost is not None else 'not_reported',
                          evidence=f'docs/verification/{filename}', response_id=response_id))

    filename = 'decisions-direct-20261024T152058163666Z.json'
    root = json.loads((EVIDENCE / filename).read_text())
    for record in root['calls']:
        add(filename, record, root['timestamp_utc'], 'initial_friend_account_user_reported',
            'phase0-initial-' + record['model_id'].replace('/', '-'))
    filename = 'own-account-luna-20261024T153150135800Z.json'
    root = json.loads((EVIDENCE / filename).read_text())
    add(filename, root['luna'], root['timestamp_utc'], 'intermediate_account_reported_own_at_time',
        'phase0-intermediate-key-luna')
    filename = 'luna-key3-20261024T155339633678Z.json'
    root = json.loads((EVIDENCE / filename).read_text())
    for record in root['calls']:
        add(filename, record, root['timestamp_utc'], 'current_key_friend_account_user_reported',
            'phase0-third-key-unchanged-luna')
    batches = [
        ('ladder-20261024-next-', ('jev', 'clef', 'luna-a', 'luna-b', 'opus')),
        ('luna-ladder-l-20261024-', ('L1', 'L2', 'NET')),
        ('net-', ('n1', 'n2', 'n3')),
        ('final-representation-', ('jev', 'clef', 'luna')),
    ]
    for prefix, stages in batches:
        for stage in stages:
            filename = prefix + stage + '.json'
            record = json.loads((EVIDENCE / filename).read_text())
            add(filename, record, record['timestamp_utc'], 'current_key_friend_account_user_reported',
                'phase0-' + prefix.rstrip('-') + '-' + stage)
    filename = 'opus-final-retry.json'
    if (EVIDENCE / filename).exists():
        record = json.loads((EVIDENCE / filename).read_text())
        add(filename, record, record['timestamp_utc'], 'current_key_friend_account_user_reported',
            'phase0-opus-final-retry')
    for stage in ('jev', 'luna', 'clef', 'opus'):
        filename = f'all-four-new-key-{stage}.json'
        if (EVIDENCE / filename).exists():
            record = json.loads((EVIDENCE / filename).read_text())
            add(filename, record, record['timestamp_utc'], 'new_key_account_not_independently_verified',
                'phase0-all-four-new-key-' + stage)
    filename = 'opus-small-budget.json'
    if (EVIDENCE / filename).exists():
        record = json.loads((EVIDENCE / filename).read_text())
        add(filename, record, record['timestamp_utc'], 'current_key_account_not_independently_verified',
            'phase0-opus-small-budget')
    filename = 'opus-63-token.json'
    if (EVIDENCE / filename).exists():
        record = json.loads((EVIDENCE / filename).read_text())
        add(filename, record, record['timestamp_utc'], 'current_key_account_not_independently_verified',
            'phase0-opus-63-token')
    runtime = ROOT / 'data/results/phase3/ledger.sqlite3'
    if runtime.exists():
        import sqlite3
        db = sqlite3.connect('file:' + str(runtime) + '?mode=ro', uri=True)
        try:
            rows = db.execute("SELECT id,run_id,trial_id,model_id,suite,body FROM attempts WHERE origin IS NULL ORDER BY id").fetchall()
        finally:
            db.close()
        for request_id,run_id,trial_id,model,suite,encoded in rows:
            item = json.loads(encoded)
            calls.append(dict(run_id=f'{run_id}:request-{request_id}', run_kind='pre_experiment_calibration' if suite=='calibration' else 'phase3_adapter_attempt', phase='calibration' if suite=='calibration' else 3,
                              model_id=model, timestamp_utc=item.get('timestamp_utc'),
                              account_context='Friend-funded account, user-reported',
                              http_status=item.get('http_status'), cost_usd=item.get('cost_usd'),
                              cost_source='usage.cost' if item.get('cost_usd') is not None else 'not_reported',
                              evidence=f'data/results/phase3/ledger.sqlite3 run={run_id} trial={trial_id} request={request_id}',
                              response_id=None))
    return calls


def aggregate(calls: list[dict]) -> dict:
    result = {}
    for record in calls:
        row = result.setdefault(record['model_id'], dict(calls=0, known_cost_usd=Decimal(0),
                                                         unreported_cost_calls=0))
        row['calls'] += 1
        if record['cost_usd'] is None:
            row['unreported_cost_calls'] += 1
        else:
            row['known_cost_usd'] += Decimal(record['cost_usd'])
    return {model: {**row, 'known_cost_usd': str(row['known_cost_usd'])}
            for model, row in sorted(result.items())}


def main():
    calls = collect_calls()
    models = aggregate(calls)
    total = sum(Decimal(r['known_cost_usd']) for r in models.values())
    part0 = [c for c in calls if 'final-representation-' in c['evidence']]
    report = dict(approved_max_spend_usd='5.00', harness_max_spend_usd='4.00', opus_allowed=False,
                  totals_are_reported_costs_not_invoice_reconciliation=True,
                  account_attribution='User-reported account/key epochs; no key/owner IDs retained',
                  model_totals=models, part0_model_totals=aggregate(part0),
                  known_cost_total_usd=str(total), unreported_cost_calls=sum(c['cost_usd'] is None for c in calls),
                  inference_calls=len(calls), diagnostic_runs=calls, phase2_api_calls=0,
                  phase3_attempts=sum(c['phase']==3 for c in calls), calibration_attempts=sum(c['phase']=='calibration' for c in calls))
    output = ROOT / 'results'
    output.mkdir(exist_ok=True)
    (output / 'COST_LEDGER.json').write_text(json.dumps(report, indent=2) + '\n')
    with (output / 'COST_LEDGER.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(calls[0]))
        writer.writeheader()
        writer.writerows(calls)
    lines = ['# Reported API costs', '',
             'Amounts are returned usage.cost in USD. Missing charges remain unknown; '
             'this is not an invoice reconciliation. Phase 0 timings are excluded from statistics. '
             'Every listed run is one diagnostic request, not a cube-game trial. '
             'Free read-only credit/catalog requests are excluded from inference totals.', '',
             '| Model | Calls | Known USD | Cost not reported (calls) |',
             '| --- | ---: | ---: | ---: |']
    lines += [f'| {m} | {r["calls"]} | {r["known_cost_usd"]} | {r["unreported_cost_calls"]} |'
              for m, r in models.items()]
    lines += ['', f'Known total: USD {total}; {report["unreported_cost_calls"]} calls with unknown cost.',
              '', 'Whole-project ceiling: USD 5.00; hard harness stop: USD 4.00. All paid runs paused. Phase 2/4/5/6 API calls: 0.', '',
              'Per-request/per-diagnostic-run details: COST_LEDGER.csv and COST_LEDGER.json. '
              'Account attribution follows user reports and is not independently verified. '
              'Paid requests are read from the persistent ledger; per-game run costs and suite totals are also available from the saved-run API/dashboard. No live game trials were executed in the offline Phase 4–6 block.']
    (output / 'COSTS.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'diagnostic_runs'}, indent=2))


if __name__ == '__main__':
    main()
