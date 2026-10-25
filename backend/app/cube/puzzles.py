"""Deterministic offline suite construction and independent verification."""
from pathlib import Path
import json
import random
import time

from .engine import MOVES, SOLVED, apply_moves
from .optimal import DistanceTable, exact_distance, load_table
from .reference import independent_distance, reference_upper_bound

MASTER_SEED = 20261024
SUITE_LENGTHS = {'warmup': (3, 4, 3), 'primary': (4,) * 5 + (5,) * 5,
                 'extension': (15, 16, 17, 18, 20)}
FORBIDDEN_OPPOSITE_ORDER = {('D', 'U'), ('R', 'L'), ('B', 'F')}


def generate_scramble(seed: int, length: int) -> list[str]:
    if type(seed) is not int or type(length) is not int or length < 1:
        raise ValueError('seed must be an integer and length a positive integer')
    rng = random.Random(seed)
    result = []
    for _ in range(length):
        previous = result[-1][0] if result else None
        options = [m for m in MOVES if m[0] != previous
                   and (previous, m[0]) not in FORBIDDEN_OPPOSITE_ORDER]
        result.append(rng.choice(options))
    return result


def generate_suites(master_seed: int = MASTER_SEED, *,
                    table: DistanceTable | None = None) -> dict[str, list[dict]]:
    if type(master_seed) is not int:
        raise ValueError('master seed must be an integer')
    table = load_table() if table is None else table
    rng = random.Random(master_seed)
    seen = set()
    suites = {}
    for suite, lengths in SUITE_LENGTHS.items():
        records = []
        for index, length in enumerate(lengths, 1):
            for _ in range(10000):
                seed = rng.getrandbits(64)
                scramble = generate_scramble(seed, length)
                facelets = apply_moves(SOLVED, scramble)
                if facelets == SOLVED or facelets in seen:
                    continue
                optimum = None if suite == 'extension' else exact_distance(facelets, table=table)
                if suite != 'extension' and optimum != length:
                    continue
                reference = reference_upper_bound(facelets) if suite == 'extension' else None
                records.append(dict(id=f'{suite}-{index:03d}', suite=suite, seed=seed,
                                    scramble=scramble, facelets=facelets, scramble_length=length,
                                    exact_optimal=optimum, reference_upper_bound=reference))
                seen.add(facelets)
                break
            else:
                raise RuntimeError(f'Candidate limit exhausted for {suite}-{index:03d}')
        suites[suite] = records
    return suites


def suite_bytes(records: list[dict]) -> bytes:
    return (json.dumps(records, indent=2, ensure_ascii=True) + '\n').encode('utf-8')


def write_suites(suites: dict[str, list[dict]], directory: Path) -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for suite, records in suites.items():
        path = directory / f'{suite}.json'
        temporary = path.with_suffix('.json.tmp')
        temporary.write_bytes(suite_bytes(records))
        temporary.replace(path)


def verify_small_suites(suites: dict[str, list[dict]]) -> list[dict]:
    """Cache-free independent check; any disagreement aborts publication."""
    checks = []
    for suite in ('warmup', 'primary'):
        for puzzle in suites[suite]:
            started = time.perf_counter()
            distance = independent_distance(puzzle['facelets'])
            elapsed = time.perf_counter() - started
            if distance != puzzle['exact_optimal']:
                raise ValueError(f'Independent distance mismatch for {puzzle["id"]}: '
                                 f'{distance} != {puzzle["exact_optimal"]}')
            checks.append(dict(id=puzzle['id'], cached_exact=puzzle['exact_optimal'],
                               independent_exact=distance, verification_time_s=elapsed))
    return checks
