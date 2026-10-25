"""The accepted common payload must remain exactly the approved N2 version."""
import json
from pathlib import Path

from backend.app import prompts
from backend.app.cube.engine import SOLVED, apply_moves, to_net

ROOT = Path(__file__).resolve().parents[2]


def test_accepted_representation_matches_exact_n2_payload():
    expected = json.loads((ROOT / 'docs/verification/net-n2.request.json').read_text())
    state = apply_moves(SOLVED, ['R', 'U', "F'", 'L2'])
    actual = {'model': expected['model'], 'state': prompts.observation(state),
              'questions': {'next_move': {'type': 'choice',
                            'instructions': prompts.instructions(), 'criteria': prompts.OPTIONS}}}
    assert actual == expected
    assert json.dumps(actual, ensure_ascii=False, separators=(',', ':')).encode() == (
        ROOT / 'docs/verification/net-n2.request.json').read_bytes()


def test_common_observation_metadata_and_engine_net():
    moves = ['R', 'U', 'F', 'L', 'B', 'D', 'R2']
    state = apply_moves(SOLVED, moves)
    obs = prompts.observation(state, moves, moves_used=7)
    assert obs == dict(facelets=state, facelet_order='URFDLB', recent_moves=moves[-6:],
                       moves_used=7, moves_remaining=23, net=to_net(state))
