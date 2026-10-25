"""Shared 30-action, first-send-deadline runner; BFS is scoring only."""
import asyncio
from decimal import Decimal
import hashlib
import time
from .cube.engine import SOLVED,is_solved
from .cube.optimal import exact_distance
from .env import CubeEnv
from .models.base import DecisionContext,PERMANENT,MALFORMED
from .storage import TERMINAL


def trial_id(puzzle_id,model_id):
    return hashlib.md5((puzzle_id+'\0'+model_id).encode()).hexdigest()


def costs(attempts,simulated=False):
    known=sum((Decimal(a['cost_usd']) for a in attempts if a.get('cost_usd') is not None),Decimal(0))
    unknown=sum(a.get('cost_usd') is None for a in attempts)
    return dict(known_cost_usd=str(known),unknown_cost_attempts=unknown,
                cost_usd=None if unknown else str(known),requests=len(attempts),
                cost_source='SIMULATED_NO_INFERENCE' if simulated else 'usage.cost')


def output_counts(attempts):
    """Count responses, including retried/late responses, not just forfeited moves."""
    return dict(truncated_responses=sum(a.get('finish_reason')=='length' for a in attempts),
                other_malformed_outputs=sum(a.get('error_class') in MALFORMED and a.get('finish_reason')!='length' for a in attempts))


def rank(trials):
    attempted=[t for t in trials if t['status']!='NOT_RUN']
    attempted.sort(key=lambda t:(0,t['moves_used'],t['total_time_s']) if t['status']=='SOLVED'
                   else (1,-t['correct_stickers_final'],t['total_time_s']))
    ranks={t['trial_id']:i+1 for i,t in enumerate(attempted)}
    return ranks


async def play_one(run,puzzle,model,agent,settings,store,ledger=None,permit=None):
    ident=trial_id(puzzle['id'],model)
    old=store.trial(run['id'],ident)
    if old and old['status'] in TERMINAL:return old
    env=CubeEnv();env.reset(puzzle)
    trial=dict(run_id=run['id'],trial_id=ident,puzzle_id=puzzle['id'],suite=puzzle['suite'],
               model_id=model,mode=run['mode'],concurrent=run['concurrent'],status='RUNNING',
               initial_facelets=env.facelets,final_facelets=env.facelets,moves_used=0,
               total_time_s=0,elapsed_s=0,sum_model_latency_s=0,first_request_at=None,
               exact_optimal=puzzle['exact_optimal'],gap=None,reference_upper_bound=puzzle['reference_upper_bound'],
               reference_len=(puzzle['reference_upper_bound'] or {}).get('length'),
               correct_stickers_final=sum(a==b for a,b in zip(env.facelets,SOLVED)),
               forfeits=0,refusals=0,truncated_responses=0,other_malformed_outputs=0,retries=0,invalid_outputs=0,moves=[],error_class=None,
               config_snapshot=run['config_snapshot'],**costs([],run['mode']=='SIMULATED'))
    store.save_trial(run['id'],trial)
    def first_send(wall):
        trial['first_request_at']=wall
        store.append(run['id'],dict(type='started',trial_id=ident,model_id=model,puzzle_id=puzzle['id'],
                                    first_request_at=wall,run_budget_s=settings.run_budget_s))
    context=DecisionContext(permit or {},ident,on_first_send=first_send)
    all_attempts=[]
    status='SOLVED' if is_solved(env.facelets) else None
    while status is None and env.moves_used<30:
        if run.get('stage') and ledger.stage_stop_reason(run['id']):
            status='NOT_RUN' if context.first_request_perf is None else 'INFRA_ERROR'
            trial['error_class']='STAGE_STOPPED: '+ledger.stage_stop_reason(run['id'])
            ledger.mark_not_run(run['id'],ident,model,trial['error_class']);break
        if run['mode']=='LIVE' and ledger.stop_reason(model):
            status='NOT_RUN' if context.first_request_perf is None else 'INFRA_ERROR'
            trial['error_class']=ledger.stop_reason(model)
            ledger.mark_not_run(run['id'],ident,model,trial['error_class'])
            break
        if context.deadline is not None and time.perf_counter()>=context.deadline:
            status='OUT_OF_TIME';break
        obs=env.observation()
        store.append(run['id'],dict(type='thinking',trial_id=ident,puzzle_id=puzzle['id'],model_id=model,
                                    facelets=env.facelets,moves_used=env.moves_used,
                                    first_request_at=context.first_request_wall,mode=run['mode']))
        try:
            decision=await agent.decide(obs,context)
        except Exception as exc:
            status='ERROR_MODEL';trial['error_class']=type(exc).__name__
            if run.get('stage'):
                ledger.stop_stage(permit,type(exc).__name__)
                all_attempts=[a for a in ledger.saved_attempts(run['id']) if a['trial_id']==ident and a['model_id']==model]
                status='INFRA_ERROR' if all_attempts else 'NOT_RUN'
                trial.update(**costs(all_attempts),**output_counts(all_attempts))
                trial['sum_model_latency_s']=sum(a.get('latency_ms',0) for a in all_attempts)/1000
                trial['retries']=sum(a.get('retry_index',0)>0 for a in all_attempts)
            break
        all_attempts.extend(decision.attempts)
        trial.update(**output_counts(all_attempts))
        if run.get('stage') and decision.error_class not in {None,'MODEL_REFUSAL','OUT_OF_TIME',*MALFORMED}:
            ledger.stop_stage(permit,decision.error_class)
        halted=ledger.stage_stop_reason(run['id']) if run.get('stage') else None
        before=exact_distance(env.facelets)
        expired=context.deadline is not None and time.perf_counter()>=context.deadline
        applied=False
        if halted:
            status='INFRA_ERROR';trial['error_class']='STAGE_STOPPED: '+halted
        elif expired or decision.error_class=='OUT_OF_TIME':
            status='OUT_OF_TIME'
        elif decision.valid or decision.forfeited:
            env.step(decision.move if decision.valid else None);applied=True
            if decision.forfeited:
                trial['forfeits']+=1
                trial['refusals']+=decision.error_class=='MODEL_REFUSAL'
                trial['invalid_outputs']+=decision.error_class!='MODEL_REFUSAL'
            if is_solved(env.facelets):status='SOLVED'
        else:
            status=('NOT_RUN' if context.first_request_perf is None else 'INFRA_ERROR')
            trial['error_class']=decision.error_class
        after=exact_distance(env.facelets)
        last=decision.attempts[-1] if decision.attempts else {}
        attempted_forfeit=applied and decision.forfeited
        record=dict(type='move',run_id=run['id'],trial_id=ident,puzzle_id=puzzle['id'],suite=puzzle['suite'],
                    model_id=model,mode=run['mode'],concurrent=run['concurrent'],move_index=env.moves_used,
                    observation=obs,facelets=env.facelets,move=decision.move,valid=decision.valid,
                    applied=applied,forfeited=attempted_forfeit,attempts=len(decision.attempts),
                    attempt_latencies_ms=[a['latency_ms'] for a in decision.attempts],
                    request_ids=[a['request_id'] for a in decision.attempts],
                    attempt_usage=[{k:a.get(k) for k in ('cost_usd','tokens_in','tokens_out','reasoning_tokens','error_class','http_status','retry_requested','finish_reason')} for a in decision.attempts],
                    successful_latency_ms=last.get('latency_ms') if decision.valid else None,
                    decision_latency_ms=decision.decision_latency_ms,
                    resolved_model=last.get('resolved_model'),provider=last.get('provider'),
                    tokens_in=last.get('tokens_in'),tokens_out=last.get('tokens_out'),reasoning_tokens=last.get('reasoning_tokens'),
                    confidence=decision.confidence,probabilities=decision.probabilities,
                    finish_reason=last.get('finish_reason'),error_class=decision.error_class,
                    dist_before=before,dist_after=after,
                    progress=('unknown' if not isinstance(before,int) or not isinstance(after,int)
                              else 'progress' if after<before else 'no-progress'),
                    config_snapshot=run['config_snapshot'],timestamp=time.time(),
                    first_request_at=context.first_request_wall,**costs(decision.attempts,run['mode']=='SIMULATED'))
        store.append(run['id'],record);trial['moves'].append(record)
        trial['sum_model_latency_s']+=sum(a['latency_ms'] for a in decision.attempts)/1000
        trial['retries']+=max(0,len(decision.attempts)-1)
        trial.update(moves_used=env.moves_used,final_facelets=env.facelets,
                     correct_stickers_final=sum(a==b for a,b in zip(env.facelets,SOLVED)),
                     elapsed_s=time.perf_counter()-context.first_request_perf if context.first_request_perf is not None else 0,
                     **costs(all_attempts,run['mode']=='SIMULATED'))
        store.save_trial(run['id'],trial)
    trial['status']=status or 'RAN_OUT_OF_MOVES'
    trial['moves_used']=env.moves_used
    trial['final_facelets']=env.facelets
    trial['correct_stickers_final']=sum(a==b for a,b in zip(env.facelets,SOLVED))
    trial['total_time_s']=time.perf_counter()-context.first_request_perf if context.first_request_perf is not None else 0
    trial['gap']=(env.moves_used-puzzle['exact_optimal'] if trial['status']=='SOLVED' and puzzle['suite']=='primary' and puzzle['exact_optimal'] is not None else None)
    trial['completed_at']=time.time()
    store.save_trial(run['id'],trial);store.append(run['id'],dict(type='run',**trial))
    return trial


async def race(run,puzzle,agents,settings,store,ledger=None,permit=None):
    args=[(model,agent) for model,agent in agents.items()]
    async def one(item):
        try:return await play_one(run,puzzle,item[0],item[1],settings,store,ledger,permit)
        except Exception as exc:
            if not run.get('stage'):raise
            ledger.stop_stage(permit,type(exc).__name__)
            ident=trial_id(puzzle['id'],item[0]);saved=store.trial(run['id'],ident)
            attempts=[a for a in ledger.saved_attempts(run['id']) if a['trial_id']==ident and a['model_id']==item[0]]
            fallback=dict(run_id=run['id'],trial_id=ident,puzzle_id=puzzle['id'],suite=puzzle['suite'],model_id=item[0],
                mode='LIVE',concurrent=run['concurrent'],moves_used=0,moves=[],total_time_s=0,sum_model_latency_s=0,
                first_request_at=None,initial_facelets=puzzle['facelets'],final_facelets=puzzle['facelets'],
                correct_stickers_final=sum(a==b for a,b in zip(puzzle['facelets'],SOLVED)),exact_optimal=puzzle['exact_optimal'],
                gap=None,reference_upper_bound=puzzle['reference_upper_bound'],reference_len=None,forfeits=0,refusals=0,
                retries=0,invalid_outputs=0,config_snapshot=run['config_snapshot'])
            trial=saved or fallback
            trial.update(status='INFRA_ERROR' if attempts else 'NOT_RUN',error_class=type(exc).__name__,completed_at=time.time(),**costs(attempts),**output_counts(attempts))
            trial['total_time_s']=max(trial.get('elapsed_s',0),time.time()-trial['first_request_at']) if trial.get('first_request_at') else trial.get('elapsed_s',0)
            store.save_trial(run['id'],trial);store.append(run['id'],dict(type='run',**trial));return trial
    if run['concurrent']:
        trials=await asyncio.gather(*(one(item) for item in args))
    else:
        trials=[]
        for item in args:trials.append(await one(item))
    ranks=rank(trials)
    for trial in trials:
        trial['rank']=ranks.get(trial['trial_id']);store.save_trial(run['id'],trial)
    store.append(run['id'],dict(type='ranking',puzzle_id=puzzle['id'],ranks=ranks))
    return trials
