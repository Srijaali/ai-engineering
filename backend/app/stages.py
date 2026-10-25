"""Explicit reusable execution scope; stage definitions never confer consent."""
from dataclasses import dataclass,asdict
import re
from .models.registry import MODELS


@dataclass(frozen=True)
class Stage:
    name:str
    suite:str
    puzzle_ids:tuple
    models:tuple
    request_cap:int

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,dict) or set(value)!={'name','suite','puzzle_ids','models','request_cap'}:
            raise ValueError('Stage requires name, suite, puzzle_ids, models and request_cap')
        if not isinstance(value['name'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',value['name']):
            raise ValueError('Invalid stage name')
        if value['suite'] not in ('warmup','primary','extension'):raise ValueError('Invalid stage suite')
        for key in ('puzzle_ids','models'):
            items=value[key]
            if not isinstance(items,(list,tuple)) or not items or not all(isinstance(i,str) for i in items) or len(items)!=len(set(items)):
                raise ValueError('Stage requires distinct nonempty '+key)
        if not set(value['models'])<={m for m,_,_ in MODELS}:raise ValueError('Unassigned stage model ID')
        if type(value['request_cap']) is not int or value['request_cap']<1:raise ValueError('Invalid stage request cap')
        return cls(value['name'],value['suite'],tuple(value['puzzle_ids']),tuple(value['models']),value['request_cap'])

    def as_dict(self):
        value=asdict(self);value['puzzle_ids']=list(self.puzzle_ids);value['models']=list(self.models);return value
