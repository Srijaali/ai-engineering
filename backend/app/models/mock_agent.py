"""SIMULATED seeded random moves only. Never access a solver or HTTP."""
import asyncio
import random
import time
from .base import MoveDecision
from ..prompts import MOVES


class MockAgent:
    def __init__(self,seed=0,delay=.1,run_budget_s=300):
        self.rng=random.Random(seed);self.delay=delay;self.run_budget_s=run_budget_s

    async def decide(self,observation,context):
        start=time.perf_counter()
        if context.first_request_perf is None:
            context.first_request_perf=start;context.first_request_wall=time.time()
            if context.deadline is None:context.deadline=start+self.run_budget_s
            if context.on_first_send:context.on_first_send(context.first_request_wall)
        await asyncio.sleep(self.delay)
        return MoveDecision(move=self.rng.choice(MOVES),decision_latency_ms=(time.perf_counter()-start)*1000)
