"""Saved live statistics and separately flagged free random baseline; no HTTP."""
from collections import Counter
import hashlib
import math
import random
import statistics
from .cube.engine import SOLVED,MOVES,apply,inverse_move
from .models.registry import MODELS

ELIGIBLE={'SOLVED','RAN_OUT_OF_MOVES','OUT_OF_TIME','ERROR_MODEL'}


def describe(values):
    values=sorted(values)
    return dict(n=len(values),mean=statistics.mean(values) if values else None,
                median=statistics.median(values) if values else None,
                p95=values[max(0,math.ceil(len(values)*.95)-1)] if values else None)


def wilson(k,n):
    if not n:return None
    z=1.959963984540054;p=k/n
    centre=(p+z*z/(2*n))/(1+z*z/n)
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return [centre-half,centre+half]


def summarize(trials,model,name,baseline=False):
    planned=[t for t in trials if t['model_id']==model]
    eligible=[t for t in planned if t['status'] in ELIGIBLE]
    solved=[t for t in eligible if t['status']=='SOLVED']
    moves=[m for t in eligible for m in t.get('moves',[])]
    gaps=[t['gap'] for t in solved if t.get('gap') is not None]
    known=[m for m in moves if m.get('applied') and isinstance(m.get('dist_before'),int) and isinstance(m.get('dist_after'),int)]
    inverses=0;pair_count=0;entropies=[]
    for t in eligible:
        valid=[m['move'] for m in t.get('moves',[]) if m['valid'] and m.get('applied')]
        pair_count+=max(0,len(valid)-1)
        inverses+=sum(inverse_move(a)==b for a,b in zip(valid,valid[1:]))
    for m in moves:
        vector=m.get('probabilities')
        values=list(vector.values()) if isinstance(vector,dict) else vector
        if isinstance(values,list) and len(values)==18 and all(type(p) in (int,float) and math.isfinite(p) and 0<=p<=1 for p in values) and abs(sum(values)-1)<.02:
            entropies.append(-sum(p*math.log(p) for p in values if p>0))
    return dict(model_id=model,name=name,baseline=baseline,planned=len(planned),eligible=len(eligible),
                statuses=dict(Counter(t['status'] for t in planned)),solved=len(solved),denominator=len(eligible),
                success_rate=len(solved)/len(eligible) if eligible else None,wilson_95=wilson(len(solved),len(eligible)),
                latency_ms=describe([m['successful_latency_ms'] for m in moves if m.get('successful_latency_ms') is not None]),
                decision_latency_ms=describe([m['decision_latency_ms'] for m in moves if m.get('decision_latency_ms') is not None]),
                total_s=describe([t['total_time_s'] for t in eligible if t.get('total_time_s') is not None]),solved_total_s=describe([t['total_time_s'] for t in solved if t.get('total_time_s') is not None]),
                moves_all=describe([t['moves_used'] for t in eligible]),moves_solved=describe([t['moves_used'] for t in solved]),
                move_histogram=dict(Counter(t['moves_used'] for t in eligible)),
                gap=dict(mean=statistics.mean(gaps) if gaps else None,minimum=min(gaps) if gaps else None,
                         zero=gaps.count(0),histogram=dict(Counter(gaps))),
                correct_stickers_mean=statistics.mean([t['correct_stickers_final'] for t in eligible]) if eligible else None,
                refusals=sum(t.get('refusals',0) for t in planned),forfeits=sum(t.get('forfeits',0) for t in planned),
                truncated_responses=sum(t.get('truncated_responses',sum(m.get('finish_reason')=='length' for m in t.get('moves',[]))) for t in planned),
                other_malformed_outputs=sum(t.get('other_malformed_outputs',sum(m.get('error_class') not in (None,'MODEL_REFUSAL','LENGTH_OUTPUT') and m.get('forfeited',False) for m in t.get('moves',[]))) for t in planned),
                invalid_outputs=sum(t.get('invalid_outputs',0) for t in planned),retries=sum(t.get('retries',0) for t in planned),
                progress=dict(known=len(known),reduced=sum(m['dist_after']<m['dist_before'] for m in known),
                              no_progress=sum(m['dist_after']>=m['dist_before'] for m in known),
                              on_optimal_path_rate=sum(m['dist_after']<m['dist_before'] for m in known)/len(known) if known else None,
                              unknown=sum(m.get('applied') and not (isinstance(m.get('dist_before'),int) and isinstance(m.get('dist_after'),int)) for m in moves)),
                immediate_inverses=dict(count=inverses,pairs=pair_count,rate=inverses/pair_count if pair_count else None),
                confidence_progress=[dict(confidence=m['confidence'],reduced=m['dist_after']<m['dist_before']) for m in known if m.get('confidence') is not None],
                entropy_nats=describe(entropies),
                extension_reference=[dict(puzzle_id=t['puzzle_id'],moves=t['moves_used'],status=t['status'],reference_upper_bound=t.get('reference_upper_bound')) for t in eligible if t['suite']=='extension'])


def aggregate(trials):
    return [summarize([t for t in trials if t['mode']=='LIVE'],m,n) for m,n,_ in MODELS]


def random_baseline(puzzles,repeats=100,seed=20261024):
    """Exactly 100 seeded free trials/puzzle by default; no BFS-guided policy."""
    trials=[]
    for puzzle in puzzles:
        for index in range(repeats):
            derived=int.from_bytes(hashlib.sha256(f'{seed}:{puzzle["id"]}:{index}'.encode()).digest()[:8],'big')
            rng=random.Random(derived);state=puzzle['facelets'];used=0
            while used<30 and state!=SOLVED:
                state=apply(state,rng.choice(MOVES));used+=1
            solved=state==SOLVED
            trials.append(dict(model_id='random_baseline',puzzle_id=puzzle['id'],suite=puzzle['suite'],mode='BASELINE',
                status='SOLVED' if solved else 'RAN_OUT_OF_MOVES',moves_used=used,total_time_s=None,moves=[],
                correct_stickers_final=sum(a==b for a,b in zip(state,SOLVED)),
                gap=used-puzzle['exact_optimal'] if solved and puzzle['suite']=='primary' else None,
                refusals=0,forfeits=0,retries=0,cost_usd='0'))
    return dict(seed=seed,repeats_per_puzzle=repeats,puzzles=[p['id'] for p in puzzles],trials=trials,
                summaries={s:summarize([t for t in trials if t['suite']==s],'random_baseline','Random baseline (free)',True)
                           for s in ('warmup','primary','extension')})
