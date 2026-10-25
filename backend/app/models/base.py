"""Common Agent contract, strict observation boundary and audited HTTP attempts."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import re
import time
from typing import Protocol
import httpx
from .accounting import BudgetStop, NotRun
from .registry import MODELS
from ..prompts import MOVES, observation as canonical_observation

OBSERVATION_FIELDS = frozenset(('facelets','facelet_order','net','recent_moves','moves_used','moves_remaining'))
PERMANENT = {'BAD_REQUEST','AUTH_ERROR','PERMISSION_ERROR','MODEL_UNAVAILABLE','SCHEMA_ERROR','API_ERROR'}
MALFORMED = {'MALFORMED_OUTPUT','INVALID_MOVE','LENGTH_OUTPUT'}


@dataclass
class DecisionContext:
    permit: dict = field(repr=False)
    trial_id: str = ''
    deadline: float = None
    attempt_limit: int = None
    first_request_perf: float = None
    first_request_wall: float = None
    on_first_send: object = field(default=None,repr=False)


@dataclass
class MoveDecision:
    move: str = None
    error_class: str = None
    forfeited: bool = False
    status: str = 'DECISION'
    confidence: object = None
    probabilities: object = None
    attempts: list = field(default_factory=list)
    decision_latency_ms: float = None

    @property
    def valid(self):
        return self.error_class is None and self.move in MOVES


class Agent(Protocol):
    async def decide(self, observation: dict, context: DecisionContext) -> MoveDecision: ...


def validate_observation(obs):
    if not isinstance(obs,dict) or set(obs)!=OBSERVATION_FIELDS:
        raise ValueError('Only the six approved observation fields may be sent')
    used=obs['moves_used']; recent=obs['recent_moves']
    if type(used) is not int or not 0<=used<30 or type(obs['moves_remaining']) is not int:
        raise ValueError('Observation must be within the fixed 30-move limit')
    if not isinstance(recent,list) or len(recent)>6 or any(type(m) is not str or m not in MOVES for m in recent):
        raise ValueError('Recent history must contain at most six exact legal move strings')
    if obs!=canonical_observation(obs['facelets'],recent,used,30):
        raise ValueError('State, net, order and move counts must agree exactly')
    return obs


def accounting(body):
    usage=body.get('usage',{}) if isinstance(body,dict) else {}
    usage=usage if isinstance(usage,dict) else {}
    details=usage.get('completion_tokens_details') or {}
    details=details if isinstance(details,dict) else {}
    def count(v):
        return v if type(v) is int and v>=0 else None
    try:
        amount=Decimal(str(usage.get('cost')))
        cost=str(amount) if amount.is_finite() and amount>=0 else None
    except InvalidOperation:
        cost=None
    return dict(cost_usd=cost,tokens_in=count(usage.get('input_tokens',usage.get('prompt_tokens'))),
                tokens_out=count(usage.get('output_tokens',usage.get('completion_tokens'))),
                reasoning_tokens=count(details.get('reasoning_tokens',usage.get('reasoning_tokens'))),
                total_tokens=count(usage.get('total_tokens')))


def error_class(status,body):
    error=body.get('error') if isinstance(body,dict) else None
    code=error.get('code') if isinstance(error,dict) else None
    if status==200 and type(code) is int and code>=400:
        status=code
    message=str(error.get('message','') if isinstance(error,dict) else error or '').lower()
    if status==402:
        return 'CREDIT_ERROR'
    if status==401:
        return 'AUTH_ERROR'
    if status==404:
        return 'MODEL_UNAVAILABLE'
    if 'refused to answer' in message:
        return 'MODEL_REFUSAL'
    if status in (400,422):
        return 'SCHEMA_ERROR' if any(k in message for k in ('schema','parameter','response_format')) else 'BAD_REQUEST'
    if status==403:
        return 'PERMISSION_ERROR'
    if status==429 or 500<=status<=599:
        return 'TRANSPORT_ERROR'
    if status!=200 or error:
        return 'API_ERROR'
    return None


def redacted(value,key):
    if isinstance(value,dict):
        return {k:('[REDACTED_ACCOUNT_ID]' if k=='user_id' else redacted(v,key)) for k,v in value.items()}
    if isinstance(value,list):
        return [redacted(v,key) for v in value]
    if isinstance(value,str):
        if key:
            value=value.replace(key,'[REDACTED]')
        value=re.sub(r'sk-or-v1-[A-Za-z0-9_-]+','[REDACTED]',value)
        return re.sub(r'(/keys/)[A-Za-z0-9_-]+',r'\1[REDACTED_KEY_ID]',value)
    if isinstance(value,float) and not math.isfinite(value):
        return None
    return value


class OpenRouterAgent:
    def __init__(self,model_id,settings,ledger,client):
        if model_id not in {m for m,_,_ in MODELS}:
            raise ValueError('Unassigned model ID; no substitution permitted')
        self.model_id,self.settings,self.ledger,self.client=model_id,settings,ledger,client

    async def decide(self,obs,context):
        validate_observation(obs)
        endpoint,payload=self.payload(obs)
        wire=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        # Payload is built once; retries use identical bytes and fixed settings.
        decision=MoveDecision()
        transport_retries=malformed_retries=0
        first_send=None
        while True:
            remaining=(context.deadline-time.perf_counter() if context.deadline is not None
                       else self.settings.run_budget_s)
            if remaining<=0:
                decision.error_class='OUT_OF_TIME';decision.status='OUT_OF_TIME';break
            try:
                request_id=self.ledger.reserve(context.permit,context.trial_id,self.model_id,self.settings)
            except (BudgetStop,NotRun) as exc:
                decision.error_class='BUDGET_EXHAUSTED' if isinstance(exc,BudgetStop) else 'MODEL_STOPPED'
                decision.status='NOT_RUN' if not decision.attempts else 'INFRA_ERROR'
                self.ledger.mark_not_run(context.permit['run_id'],context.trial_id,self.model_id,str(exc))
                break
            send=time.perf_counter()
            if context.first_request_perf is None:
                context.first_request_perf=send;context.first_request_wall=time.time()
                if context.deadline is None:context.deadline=send+self.settings.run_budget_s
                if context.on_first_send:context.on_first_send(context.first_request_wall)
            remaining=context.deadline-time.perf_counter()
            if first_send is None:first_send=send
            body={};status=None;finish=None;answer={};raw_text=None
            try:
                response=await asyncio.wait_for(self.client.post('https://openrouter.ai'+endpoint,content=wire,
                    headers={'Authorization':'Bearer '+self.settings.api_key,'Content-Type':'application/json'},
                    timeout=min(remaining,self.settings.request_timeout_s)),timeout=min(remaining,self.settings.request_timeout_s))
                status=response.status_code
                try:
                    body=response.json()
                except ValueError:
                    raw_text=response.text
                kind=error_class(status,body)
                if kind is None:
                    try:
                        answer,finish=self.parse(body)
                        if finish=='length':kind='LENGTH_OUTPUT'
                        elif type(answer.get('move')) is not str or answer['move'] not in MOVES:kind='INVALID_MOVE'
                    except (KeyError,IndexError,TypeError,ValueError,AttributeError):
                        kind='MALFORMED_OUTPUT'
            except (httpx.TransportError,asyncio.TimeoutError):
                kind='TRANSPORT_ERROR'
            latency=(time.perf_counter()-send)*1000
            billing=accounting(body)
            is_permanent=kind in PERMANENT
            will_retry=False;backoff=0
            if kind=='TRANSPORT_ERROR' and transport_retries<self.settings.max_transport_retries:
                transport_retries+=1;backoff=(1,3)[transport_retries-1];will_retry=True
            elif kind in MALFORMED and malformed_retries<self.settings.max_malformed_retries:
                malformed_retries+=1;will_retry=True
            allowance_suppressed = bool(will_retry and context.attempt_limit is not None
                                        and len(decision.attempts)+1 >= context.attempt_limit)
            if allowance_suppressed: will_retry=False;backoff=0
            record=dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),http_status=status,
                        resolved_model=body.get('model') if isinstance(body,dict) else None,
                        provider=body.get('provider') if isinstance(body,dict) else None,
                        error_class=kind,permanent_failure=is_permanent,latency_ms=latency,
                        finish_reason=finish,move=answer.get('move') if kind is None else None,
                        retry_requested=will_retry,retry_backoff_s=backoff,
                        retry_suppressed_by_allowance=allowance_suppressed,
                        retry_index=len(decision.attempts),config_snapshot=self.settings.snapshot(),
                        observation=obs,request_body=payload,raw_response=body,unparsed_response=raw_text,**billing)
            record=redacted(record,self.settings.api_key)
            self.ledger.finish(request_id,record)
            decision.attempts.append(dict(request_id=request_id,**record))
            decision.error_class=kind
            if kind is None:
                decision.move=answer['move'];decision.confidence=redacted(answer.get('confidence'),self.settings.api_key)
                decision.probabilities=redacted(answer.get('probabilities'),self.settings.api_key)
                break
            if not will_retry:
                break
            if context.deadline-time.perf_counter()<=backoff:
                break
            if backoff:await asyncio.sleep(backoff)
        if first_send is not None:
            decision.decision_latency_ms=(time.perf_counter()-first_send)*1000
        if decision.error_class in MALFORMED or decision.error_class=='MODEL_REFUSAL':
            decision.forfeited=True
        if decision.error_class=='TRANSPORT_ERROR':decision.status='INFRA_ERROR'
        elif decision.error_class in PERMANENT or decision.error_class=='CREDIT_ERROR':decision.status='ERROR_API'
        return decision
