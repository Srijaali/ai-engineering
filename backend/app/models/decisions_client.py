"""Shared frozen question and options; only model changes across Decisions IDs."""
from .base import OpenRouterAgent,validate_observation
from .registry import MODELS
from ..prompts import instructions,OPTIONS


class DecisionsAgent(OpenRouterAgent):
    def __init__(self,model_id,*args):
        if model_id not in {m for m,_,_ in MODELS[:3]}:
            raise ValueError('Use an assigned Decisions model')
        super().__init__(model_id,*args)

    def payload(self,obs):
        validate_observation(obs)
        return '/api/alpha/decisions',dict(model=self.model_id,state=obs,
            questions={'next_move':dict(type='choice',instructions=instructions(),criteria=OPTIONS)})

    def parse(self,body):
        answer=body['answers']['next_move']
        return dict(move=answer['choice'],confidence=answer.get('confidence'),
                    probabilities=answer.get('probabilities')),None
