"""Phase 3 fixed settings; no credentials in public snapshots."""
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
APPROVED_MAX_SPEND_USD = 4.0


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default='', repr=False)
    student_name: str = ''
    student_roll: str = ''
    claude_reasoning_effort: str = 'low'
    claude_max_tokens: int = 1024
    request_timeout_s: float = 120
    run_budget_s: float = 300
    max_transport_retries: int = 1
    max_malformed_retries: int = 0
    max_requests: int = 1000
    max_spend_usd: float = 4.0
    max_moves: int = 30

    def __post_init__(self):
        if not isfinite(self.max_spend_usd) or not 0 < self.max_spend_usd <= APPROVED_MAX_SPEND_USD:
            raise ValueError('MAX_SPEND_USD exceeds the user-approved USD 4 harness ceiling')
        if self.claude_reasoning_effort not in ('low', 'medium', 'high', 'xhigh', 'max'):
            raise ValueError('Claude reasoning must remain enabled')
        if type(self.claude_max_tokens) is not int or self.claude_max_tokens < 1:
            raise ValueError('CLAUDE_MAX_TOKENS must be a positive integer')
        if type(self.max_requests) is not int or self.max_requests < 1:
            raise ValueError('MAX_REQUESTS must be a positive integer')
        if self.max_moves != 30:
            raise ValueError('Assignment requires MAX_MOVES=30')
        if any(type(v) is not int or not 0 <= v <= 2 for v in
               (self.max_transport_retries, self.max_malformed_retries)):
            raise ValueError('Retry limits must be integers from 0 to 2')
        if any(not isfinite(v) or v <= 0 for v in (self.request_timeout_s, self.run_budget_s)):
            raise ValueError('Timeouts and RUN_BUDGET_S must be finite and positive')

    def snapshot(self):
        return {name: getattr(self, name) for name in (
            'claude_reasoning_effort', 'claude_max_tokens', 'request_timeout_s', 'run_budget_s',
            'max_transport_retries', 'max_malformed_retries', 'max_requests', 'max_spend_usd', 'max_moves')}

    # Historical probe helpers are retained for reading, but their CLIs are disabled.
    opus_effort = property(lambda self: self.claude_reasoning_effort)
    opus_max_tokens = property(lambda self: self.claude_max_tokens)
    opus_temperature = property(lambda self: 0)


def load_settings(require_identity=True, require_key=True):
    v = dotenv_values(ROOT / '.env')
    required = (['OPENROUTER_API_KEY'] if require_key else []) + (['STUDENT_NAME', 'STUDENT_ROLL'] if require_identity else [])
    missing = [key for key in required if not v.get(key)]
    if missing:
        raise ValueError('Missing required .env variables: ' + ', '.join(missing))
    return Settings(api_key=v.get('OPENROUTER_API_KEY') or '', student_name=v.get('STUDENT_NAME') or '',
                    student_roll=v.get('STUDENT_ROLL') or '',
                    claude_reasoning_effort=v.get('CLAUDE_REASONING_EFFORT') or 'low',
                    claude_max_tokens=int(v.get('CLAUDE_MAX_TOKENS') or 1024),
                    request_timeout_s=float(v.get('REQUEST_TIMEOUT_S') or 120),
                    run_budget_s=float(v.get('RUN_BUDGET_S') or 300),
                    max_transport_retries=int(v.get('MAX_TRANSPORT_RETRIES') or 0) if v.get('MAX_TRANSPORT_RETRIES') is not None else 1,
                    max_malformed_retries=int(v.get('MAX_MALFORMED_RETRIES') or 0),
                    max_requests=int(v.get('MAX_REQUESTS') or 1000),
                    max_spend_usd=float(v.get('MAX_SPEND_USD') or 4),
                    max_moves=int(v.get('MAX_MOVES') or 30))


def recommended_cap(puzzles, settings, model_count=4):
    if type(puzzles) is not int or puzzles < 1 or model_count != 4:
        raise ValueError('Use positive puzzle count and all four models')
    return puzzles * model_count * 30 * (1 + settings.max_transport_retries + settings.max_malformed_retries)
