"""Offline engine checks, including the required 200 independent solves."""
from collections import Counter
import random

import kociemba
import pytest
from kociemba.pykociemba.cubiecube import CubieCube, moveCube

from backend.app.cube.engine import (
    FACELET_ORDER, MOVES, MOVE_PERMUTATIONS, SOLVED, STICKERS,
    apply, apply_moves, inverse_move, is_solved, to_net, validate_facelets,
)


def test_convention_matches_frozen_prompts():
    from backend.app import prompts
    assert (FACELET_ORDER, SOLVED, MOVES) == (
        prompts.FACELET_ORDER, prompts.SOLVED, prompts.MOVES)
    assert len(set(STICKERS)) == 54
    assert len(MOVES) == 18
    assert is_solved(SOLVED)
    assert not is_solved(apply(SOLVED, 'R'))
    assert apply_moves(SOLVED, []) == SOLVED


@pytest.mark.parametrize('move', MOVES)
def test_four_repetitions_are_identity(move):
    # Half turns have exact order 2, so also fourth power identity.
    # Check full index permutations, not just uniform face colours.
    permutation = MOVE_PERMUTATIONS[move]
    positions = tuple(range(54))
    for _ in range(4):
        positions = tuple(positions[i] for i in permutation)
    assert positions == tuple(range(54))
    assert apply_moves(SOLVED, [move] * 4) == SOLVED


@pytest.mark.parametrize('face', FACELET_ORDER)
def test_half_turn_equals_two_quarter_turns(face):
    state = apply_moves(SOLVED, ['R', 'U', "F'", 'L2', 'B', 'D'])
    assert apply(state, face + '2') == apply_moves(state, [face, face])
    assert apply_moves(state, [face + '2'] * 2) == state
    assert apply_moves(state, [face] * 2) != state


@pytest.mark.parametrize('move', MOVES)
def test_move_then_inverse_is_identity(move):
    state = apply_moves(SOLVED, ['R', 'U', 'F2', "D'", 'L', 'B2'])
    assert apply_moves(state, [move, inverse_move(move)]) == state
    assert inverse_move(inverse_move(move)) == move


def test_sexy_move_six_times_is_identity():
    assert apply_moves(SOLVED, ['R', 'U', "R'", "U'"] * 6) == SOLVED


def test_opposite_faces_commute_but_adjacent_faces_do_not():
    state = apply_moves(SOLVED, ['F', 'L2', 'B', "R'"])
    assert apply_moves(state, ['U', 'D']) == apply_moves(state, ['D', 'U'])
    assert apply_moves(state, ['U', 'R']) != apply_moves(state, ['R', 'U'])


@pytest.mark.parametrize('move', MOVES)
def test_moves_are_bijections_preserving_counts_and_centres(move):
    assert sorted(MOVE_PERMUTATIONS[move]) == list(range(54))
    state = apply_moves(SOLVED, ['R', 'U', "F'", 'B', 'L2', 'D'])
    after = apply(state, move)
    assert Counter(after) == Counter(SOLVED)
    for i, face in enumerate(FACELET_ORDER):
        assert after[9 * i + 4] == face
        assert MOVE_PERMUTATIONS[move][9 * i + 4] == 9 * i + 4


@pytest.mark.parametrize('move', MOVES)
def test_each_move_matches_independent_kociemba_cubie_rotations(move):
    # Installed 1.2.1 source inspected: moveCube order URFDLB,
    # multiply composes in place; toFaceCube().to_String() serializes.
    cubie = CubieCube()
    turns = 1 if len(move) == 1 else 3 if move[1] == "'" else 2
    for _ in range(turns):
        cubie.multiply(moveCube[FACELET_ORDER.index(move[0])])
    assert apply(SOLVED, move) == cubie.toFaceCube().to_String()


@pytest.mark.parametrize('case', range(200))
def test_random_scramble_solved_by_independent_oracle(case):
    rng = random.Random(20261024 + case)
    scramble = [rng.choice(MOVES) for _ in range(1 + case % 30)]
    state = apply_moves(SOLVED, scramble)
    solution = kociemba.solve(state).split()
    assert all(move in MOVES for move in solution)
    assert is_solved(apply_moves(state, solution)), (case, scramble, solution)


@pytest.mark.parametrize('bad', [' R', 'R ', 'r', 'R\n', 'R\u2032', 'R3', 'x', '', None, 1, []])
def test_invalid_moves_are_rejected_without_normalization(bad):
    with pytest.raises(ValueError):
        apply(SOLVED, bad)
    with pytest.raises(ValueError):
        inverse_move(bad)


@pytest.mark.parametrize('bad', [None, 54, '', SOLVED[:-1], SOLVED + 'U',
                                      'X' + SOLVED[1:], 'R' + SOLVED[1:]])
def test_bad_facelet_format_or_counts_are_rejected(bad):
    with pytest.raises(ValueError):
        validate_facelets(bad)
    with pytest.raises(ValueError):
        apply(bad, 'R')


def test_swapped_centres_are_rejected_even_with_correct_counts():
    facelets = list(SOLVED)
    facelets[4], facelets[13] = facelets[13], facelets[4]
    with pytest.raises(ValueError, match='centres'):
        apply(''.join(facelets), 'U')


def test_bare_algorithm_string_is_not_silently_tokenized():
    with pytest.raises(ValueError):
        apply_moves(SOLVED, "R U R' U'")


def test_text_net_round_trips_every_facelet_without_loss_or_addition():
    # The inverse lives only in this test: decode the documented layout,
    # independently from to_net's implementation, back into URFDLB order.
    rng = random.Random(20261024)
    states = [SOLVED, apply_moves(SOLVED, ['R', 'U', "F'", 'L2'])]
    state = SOLVED
    for _ in range(100):
        state = apply(state, rng.choice(MOVES))
        states.append(state)
    for facelets in states:
        lines = to_net(facelets).splitlines()
        assert len(lines) == 12
        assert (lines[0], lines[4], lines[8]) == ('    U', 'L   F   R   B', '    D')
        assert all(len(lines[i]) == 7 and lines[i].startswith('    ')
                   for i in (1, 2, 3, 9, 10, 11))
        assert all(len(lines[i]) == 15 and len(lines[i].split()) == 4
                   for i in (5, 6, 7))
        upper = ''.join(line[4:] for line in lines[1:4])
        lower = ''.join(line[4:] for line in lines[9:12])
        side_rows = [line.split() for line in lines[5:8]]
        left, front, right, back = (
            ''.join(row[col] for row in side_rows) for col in range(4)
        )
        recovered = upper + right + front + lower + left + back
        assert recovered == facelets
        assert len(recovered) == 54
        assert Counter(recovered) == Counter(facelets)
