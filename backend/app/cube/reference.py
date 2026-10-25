"""Local references and a cache-free, independently transitioned distance check."""
import kociemba
from kociemba.pykociemba.cubiecube import CubieCube, moveCube
from kociemba.pykociemba.facecube import FaceCube

from .engine import FACELET_ORDER, SOLVED, apply_moves, validate_facelets


def reference_upper_bound(facelets: str) -> dict:
    """Kociemba is an upper bound, never labelled optimal; verify with our engine."""
    validate_facelets(facelets)
    solution = [] if facelets == SOLVED else kociemba.solve(facelets).split()
    if apply_moves(facelets, solution) != SOLVED:
        raise ValueError('Kociemba reference does not solve under our engine')
    return dict(label='reference_upper_bound_not_optimal', length=len(solution),
                solution=solution)


def _oracle_neighbors(facelets: str):
    """Use Kociemba's cubie rotations, not our engine's generated permutations."""
    original = FaceCube(facelets).toCubieCube()
    for index, face in enumerate(FACELET_ORDER):
        cube = CubieCube(cp=list(original.cp), co=list(original.co),
                         ep=list(original.ep), eo=list(original.eo))
        for suffix in ('', '2', "'"):
            cube.multiply(moveCube[index])
            yield cube.toFaceCube().to_String()


def _oracle_ball(start: str, radius: int) -> dict[str, int]:
    """Fresh local search, deliberately neither memoized nor backed by a file."""
    distances = {start: 0}
    frontier = [start]
    for depth in range(1, radius + 1):
        following = []
        for state in frontier:
            for child in _oracle_neighbors(state):
                if child not in distances:
                    distances[child] = depth
                    following.append(child)
        frontier = following
    return distances


def independent_distance(facelets: str, max_depth: int = 5) -> int | None:
    """Exact distance through max_depth via fresh two-ended complete search.

    Every path of <=5 can be split into <=2 and <=3 steps; enumerate those
    two balls using Kociemba cubie transitions and minimize intersection sums.
    No optimal.py import, table lookup, persisted search or kociemba.solve is
    used here. None means no solution within the requested bound.
    """
    validate_facelets(facelets)
    if type(max_depth) is not int or not 0 <= max_depth <= 5:
        raise ValueError('Independent verification bound must be 0..5')
    if facelets == SOLVED:
        return 0
    left = _oracle_ball(facelets, max_depth // 2)
    right = _oracle_ball(SOLVED, max_depth - max_depth // 2)
    candidates = [d + right[state] for state, d in left.items() if state in right]
    return min(candidates) if candidates else None
