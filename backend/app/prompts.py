"""Shared Phase 0 wording; changes must be recorded in decisions.md."""
import json
from .cube.engine import to_net

FACELET_ORDER = 'URFDLB'
SOLVED = ''.join(face * 9 for face in FACELET_ORDER)
MOVES = tuple(face + suffix for face in 'UDLRFB' for suffix in ('', "'", '2'))
QUESTION = 'Choose the single next move most likely to progress toward the solved cube.'
FACE_NAMES = dict(U='upper', D='down', L='left', R='right', F='front', B='back')
OPTIONS = {
    move: f'Turn the {FACE_NAMES[move[0]]} face '
    + ('clockwise by 90 degrees' if len(move) == 1 else
       'counterclockwise by 90 degrees' if move[1] == "'" else 'by 180 degrees')
    + ', viewed directly from outside that face.'
    for move in MOVES
}
RULES = (
    'The cube has 54 stickers in face order U R F D L B. Each face has '
    '9 stickers, numbered 1 through 9 row-major as viewed directly from '
    'outside that face, using the Kociemba convention. The unfolded net has '
    'U above F, L F R B across, and D below F. Each sticker letter is the '
    'face/colour to which it belongs in the solved cube. Centres stay fixed. '
    f'The unique solved facelets string is {SOLVED}. '
    'Only the 18 listed face turns are allowed; each costs one move, '
    'including a half-turn. Whole-cube rotations are not allowed. '
    'Observation fields: facelets, facelet_order, recent_moves (last 6), '
    'moves_used, moves_remaining. Select exactly one listed move. '
    'The net field shows the same cube state in unfolded form.'
)


def instructions() -> str:
    return RULES + '\n' + QUESTION


def system_prompt() -> str:
    return instructions() + '\nMove definitions: ' + json.dumps(OPTIONS) + '\nReturn JSON with only the key move.'


def observation(facelets: str, recent_moves=(), moves_used: int = 0,
                max_moves: int = 30) -> dict:
    """Accepted common representation; the engine is the only net renderer."""
    return dict(facelets=facelets, facelet_order=FACELET_ORDER,
                recent_moves=list(recent_moves)[-6:], moves_used=moves_used,
                moves_remaining=max_moves - moves_used, net=to_net(facelets))


def smoke_observation() -> dict:
    """Solved route probe, excluded from benchmark results."""
    return observation(SOLVED)
