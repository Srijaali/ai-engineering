"""Assigned Opus, mandatory reasoning, unchanged shared text and strict schema."""
import json
from .base import OpenRouterAgent,validate_observation
from .registry import MODELS
from ..prompts import MOVES,system_prompt


def unique_object(pairs):
    result={}
    for k,v in pairs:
        if k in result:raise ValueError('Duplicate JSON key')
        result[k]=v
    return result


class ChatAgent(OpenRouterAgent):
    def __init__(self,model_id,*args):
        if model_id!=MODELS[-1][0]:raise ValueError('Only assigned Opus 5.5 is permitted')
        super().__init__(model_id,*args)

    def payload(self,obs):
        validate_observation(obs)
        return '/api/v1/chat/completions',{
            'model':self.model_id,
            'messages':[{'role':'system','content':system_prompt()},
                        {'role':'user','content':json.dumps(obs)}],
            'reasoning':{'effort':self.settings.claude_reasoning_effort},
            'max_tokens':self.settings.claude_max_tokens,
            'provider':{'require_parameters':True},
            'response_format':{'type':'json_schema','json_schema':{'name':'move','strict':True,
                'schema':{'type':'object','properties':{'move':{'type':'string','enum':list(MOVES)}},
                          'required':['move'],'additionalProperties':False}}}}

    def parse(self,body):
        choice=body['choices'][0];finish=choice['finish_reason']
        if finish=='length':return {},finish
        result=json.loads(choice['message']['content'],object_pairs_hook=unique_object)
        if not isinstance(result,dict) or set(result)!={'move'}:raise ValueError('Expected exactly move')
        return result,finish
