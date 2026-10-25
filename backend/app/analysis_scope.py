"""Read-only paired primary scope: complete experimental outcomes, no infra trials."""
from collections import defaultdict
from .models.registry import MODELS
from .stats import ELIGIBLE


def primary_scope(trials):
    groups=defaultdict(list)
    for t in trials:
        if t.get('mode')=='LIVE' and t.get('suite')=='primary':groups[t['puzzle_id']].append(t)
    assigned={m for m,_,_ in MODELS};paired=[]
    for puzzle,items in groups.items():
        if (len(items)==4 and {t['model_id'] for t in items}==assigned
            and all(t['status'] in ELIGIBLE for t in items)
            and len({str(sorted(t.get('config_snapshot',{}).items())) for t in items})==1):paired.append(puzzle)
    paired=sorted(paired)
    return dict(paired_puzzles=paired,paired=[t for p in paired for t in groups[p]],
                additional_completed=[t for p in sorted(groups) if p not in paired for t in groups[p] if t['status'] in ELIGIBLE],
                partial_trials=[t for p in sorted(groups) if p not in paired for t in groups[p]])
