"""Phase 0 only: free preflight, 20 floor probes, then one call per model.

No automatic model substitutions, retries, trimming, or move correction.
Evidence is separate from benchmark results. No request headers are stored.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import statistics
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import load_settings
from backend.app.models.registry import MODELS
from backend.app.prompts import MOVES, OPTIONS, instructions, smoke_observation, system_prompt

BASE = 'https://openrouter.ai'


def shape(value):
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return type(value).__name__


def request_body(model_id, settings):
    observation = smoke_observation()
    if model_id != MODELS[-1][0]:
        return '/api/alpha/decisions', {
            'model': model_id, 'state': observation,
            'questions': {'next_move': {'type': 'choice',
                                      'instructions': instructions(), 'criteria': OPTIONS}},
        }
    return '/api/v1/chat/completions', {
        'model': model_id,
        'messages': [{'role': 'system', 'content': system_prompt()},
                     {'role': 'user', 'content': json.dumps(observation)}],
        'response_format': {'type': 'json_schema', 'json_schema': {
            'name': 'move', 'strict': True, 'schema': {
                'type': 'object', 'properties': {'move': {'type': 'string', 'enum': list(MOVES)}},
                'required': ['move'], 'additionalProperties': False}}},
        'provider': {'require_parameters': True},
        'reasoning': {'effort': settings.opus_effort},
        'max_tokens': settings.opus_max_tokens,
        'temperature': settings.opus_temperature,
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    settings = load_settings()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out = ROOT / 'docs/verification' / f'phase0-{stamp}.json'
    report = {'phase': 0, 'timestamp_utc': stamp, 'benchmark_data': False,
              'timeout_s': settings.request_timeout_s,
              'config': {'opus_effort': settings.opus_effort,
                         'opus_max_tokens': settings.opus_max_tokens,
                         'opus_temperature': settings.opus_temperature,
                         'max_spend_usd': settings.max_spend_usd},
              'network_floor': [], 'calls': []}

    def safe_json(obj):
        text = json.dumps(obj, indent=2, ensure_ascii=True)
        text = text.replace(settings.api_key, '[REDACTED]')
        return re.sub(r'sk-or-v1-[A-Za-z0-9_-]+', '[REDACTED]', text)

    def save():
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(safe_json(report) + '\n')

    async def fetch(client, method, path, **kwargs):
        async def exchange():
            response = await client.request(method, BASE + path, **kwargs)
            if method == 'HEAD':
                return response.status_code, None
            return response.status_code, response.json()
        started = time.perf_counter()
        status, body = await asyncio.wait_for(exchange(), settings.request_timeout_s)
        return status, body, (time.perf_counter() - started) * 1000

    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_s,
                                     follow_redirects=False) as client:
            headers = {'Authorization': 'Bearer ' + settings.api_key}
            for name, path in [('key', '/api/v1/key'), ('credits', '/api/v1/credits')]:
                status, body, latency = await fetch(client, 'GET', path, headers=headers)
                # Allowlist numeric budget metadata; omit labels and key hashes.
                data = body.get('data', {}) if isinstance(body, dict) else {}
                fields = ('limit', 'limit_remaining', 'usage', 'total_credits', 'total_usage')
                report[name] = {'http_status': status, 'latency_ms': latency,
                                'data': {k: data[k] for k in fields if k in data}}
                save()
                if status != 200:
                    raise RuntimeError(f'{name} preflight HTTP {status}; stop before paid calls')
            status, body, latency = await fetch(client, 'GET', '/api/v1/models')
            if status != 200:
                raise RuntimeError(f'Model catalog HTTP {status}')
            catalog = {m['id']: m for m in body['data']}
            report['models'] = {model: {
                'present': model in catalog,
                'pricing': catalog.get(model, {}).get('pricing'),
                'supported_parameters': catalog.get(model, {}).get('supported_parameters'),
            } for model, _, _ in MODELS}
            save()
            if any(not entry['present'] for entry in report['models'].values()):
                raise RuntimeError('Frozen model absent from live catalog; stop for review')
            for _ in range(20):
                status, _, latency = await fetch(client, 'HEAD', '/')
                report['network_floor'].append({'http_status': status, 'latency_ms': latency})
            times = [r['latency_ms'] for r in report['network_floor']]
            report['network_summary_ms'] = dict(mean=statistics.mean(times),
                median=statistics.median(times), p95=sorted(times)[18],
                definition='20 sequential unauthenticated HEAD / requests; warm connection; '
                           'nearest-rank p95. HTTP request latency, not ICMP RTT or confirmed origin geography.')
            save()
            if args.preflight_only:
                print(safe_json(report))
                return 0
            spent = 0.0
            for model, _, _ in MODELS:
                path, payload = request_body(model, settings)
                pricing = report['models'][model]['pricing']
                # Conservative input byte allowance plus framing; provider charges may vary.
                reserve = ((len(json.dumps(payload).encode()) + 512) * float(pricing['prompt'])
                           + (settings.opus_max_tokens if model == MODELS[-1][0] else 512)
                           * float(pricing['completion']) + float(pricing.get('request') or 0))
                if spent + reserve > settings.max_spend_usd:
                    raise RuntimeError('Probe cost estimate exceeds MAX_SPEND_USD')
                status, body, latency = await fetch(client, 'POST', path,
                                                    headers=headers, json=payload)
                record = {'model_id': model, 'endpoint': path, 'http_status': status,
                          'latency_ms': latency, 'resolved_model': body.get('model'),
                          'provider': body.get('provider'), 'response_shape': shape(body),
                          'response': body, 'request_body': payload}
                report['calls'].append(record)
                usage = body.get('usage', {})
                cost = usage.get('cost')
                record['cost_usd'] = cost
                if isinstance(cost, (int, float)):
                    spent += cost
                report['measured_total_cost_usd'] = spent
                save()
                if status != 200:
                    raise RuntimeError(f'{model} HTTP {status}; stop and review saved response')
                if model == MODELS[-1][0]:
                    choice = body['choices'][0]
                    record['finish_reason'] = choice.get('finish_reason')
                    answer = json.loads(choice['message']['content'])
                    valid = (isinstance(answer, dict) and set(answer) == {'move'}
                             and isinstance(answer['move'], str) and answer['move'] in MOVES
                             and choice.get('finish_reason') != 'length')
                    record['move'] = answer.get('move') if isinstance(answer, dict) else None
                else:
                    answer = body.get('answers', {}).get('next_move', {})
                    record['move'] = answer.get('choice')
                    valid = isinstance(record['move'], str) and record['move'] in MOVES
                record['valid'] = valid
                save()
                print(safe_json({k: record.get(k) for k in (
                    'model_id', 'resolved_model', 'provider', 'http_status', 'latency_ms',
                    'cost_usd', 'move', 'valid', 'finish_reason')}))
                if not valid or cost is None:
                    raise RuntimeError('Invalid answer or missing measured cost; stop for review')
            report['complete'] = True
            save()
            return 0
    except Exception as exc:
        report['complete'] = False
        report['blocker'] = {'type': type(exc).__name__, 'message': str(exc)}
        save()
        print(safe_json(report['blocker']))
        return 1
    finally:
        print('Evidence:', out.relative_to(ROOT))


if __name__ == '__main__':
    raise SystemExit('Direct legacy probes are disabled. Use scripts/phase3_smoke.py with explicit paid consent and the persistent ledger.')
