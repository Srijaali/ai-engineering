"""Authorized Phase 0 diagnostic: one POST per frozen Decisions ID, no retries."""
import asyncio
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import load_settings
from backend.app.models.registry import MODELS
from backend.app.prompts import instructions, OPTIONS, smoke_observation
from scripts.smoke_openrouter import shape


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume-evidence', type=Path)
    args = parser.parse_args()
    settings = load_settings()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = ROOT / 'docs/verification' / f'decisions-direct-{stamp}.json'
    shared = {'state': smoke_observation(), 'questions': {'next_move': {
        'type': 'choice', 'instructions': instructions(), 'criteria': OPTIONS}}}
    report = {'phase': 0, 'timestamp_utc': stamp, 'benchmark_data': False,
              'endpoint': 'https://openrouter.ai/api/alpha/decisions',
              'shared_request_fields': shared, 'calls': [], 'retries': 0,
              'opus_probed': False}
    if args.resume_evidence:
        previous = json.loads(args.resume_evidence.read_text())
        assert previous['shared_request_fields'] == shared
        assert previous['endpoint'] == report['endpoint']
        assert all(c['http_status'] not in (400, 402, 404, None) for c in previous['calls'])
        report['calls'] = previous['calls']
        report['continued_from'] = str(args.resume_evidence)

    def sanitized(value):
        result = json.dumps(value, indent=2, ensure_ascii=True)
        result = result.replace(settings.api_key, '[REDACTED]')
        return re.sub(r'sk-or-v1-[A-Za-z0-9_-]+', '[REDACTED]', result)

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(sanitized(report) + '\n')

    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            for model, _, _ in MODELS[:3]:
                if any(c['model_id'] == model for c in report['calls']):
                    continue
                record = {'model_id': model, 'http_status': None, 'latency_ms': None,
                          'resolved_model': None, 'provider': None, 'usage_cost': None,
                          'confidence_present': False, 'probabilities_present': False,
                          'response_shape': None}
                report['calls'].append(record)
                save()
                started = time.perf_counter()

                async def exchange():
                    response = await client.post(report['endpoint'],
                        headers={'Authorization': 'Bearer ' + settings.api_key},
                        json={'model': model, **shared})
                    record['http_status'] = response.status_code
                    try:
                        body = response.json()
                    except ValueError:
                        body = {'unparsed_response_text': response.text}
                    return body

                try:
                    body = await asyncio.wait_for(exchange(), 120)
                finally:
                    record['latency_ms'] = (time.perf_counter() - started) * 1000
                    save()
                record['response'] = body
                record['response_shape'] = shape(body)
                if isinstance(body, dict):
                    record['resolved_model'] = body.get('model')
                    record['provider'] = body.get('provider')
                    usage = body.get('usage')
                    record['usage_cost'] = usage.get('cost') if isinstance(usage, dict) else None
                    answers = body.get('answers')
                    answer = answers.get('next_move') if isinstance(answers, dict) else None
                    if isinstance(answer, dict):
                        record['confidence_present'] = 'confidence' in answer
                        record['probabilities_present'] = 'probabilities' in answer
                save()
                print(sanitized({k: v for k, v in record.items() if k != 'response'}), flush=True)
                if record['http_status'] in (400, 402, 404):
                    report['stopped_reason'] = f'HTTP {record["http_status"]}; no retries or remaining model calls'
                    save()
                    return 1
            report['complete'] = True
            save()
            return 0
    except Exception as exc:
        report['stopped_reason'] = type(exc).__name__ + ': ' + str(exc)
        save()
        print(sanitized({'stopped_reason': report['stopped_reason']}), flush=True)
        return 1
    finally:
        print('Evidence:', output.relative_to(ROOT), flush=True)


if __name__ == '__main__':
    raise SystemExit('Direct legacy probes are disabled. Use scripts/phase3_smoke.py with explicit paid consent and the persistent ledger.')
