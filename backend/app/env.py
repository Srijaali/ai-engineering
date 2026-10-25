"""Shared fixed 30-action cube environment; no solver or model policy."""
from .cube.engine import apply,is_solved,validate_facelets
from .prompts import observation


class CubeEnv:
    def reset(self,puzzle):
        validate_facelets(puzzle['facelets'])
        self.facelets=puzzle['facelets'];self.recent_moves=[];self.moves_used=0
        return self.observation()

    def observation(self):
        return observation(self.facelets,self.recent_moves,self.moves_used,30)

    def step(self,move=None):
        if self.moves_used>=30:raise ValueError('30-move limit reached')
        if move is not None:
            self.facelets=apply(self.facelets,move)
            self.recent_moves.append(move)
            self.recent_moves=self.recent_moves[-6:]
        self.moves_used+=1
        return self.observation(),is_solved(self.facelets)
