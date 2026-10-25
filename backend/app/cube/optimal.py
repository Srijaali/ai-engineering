"""Complete offline HTM breadth-first distances through depth five.

Cache files are local trusted gzip pickles. Rebuild rather than loading a
downloaded/untrusted pickle. Missing/corrupt/incompatible caches never trigger
silent rebuilding or an 'unknown' answer.
"""
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
import pickle
import resource
import sys
import time
from types import MappingProxyType
from typing import Mapping

from .engine import FACELET_ORDER, MOVES, MOVE_PERMUTATIONS, SOLVED, validate_facelets

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE = ROOT / 'data/cache/htm-depth5-v1.pkl.gz'
EXPECTED_COUNTS = (1, 18, 243, 3240, 43239, 574908)
UNKNOWN_DISTANCE = 'unknown_6_or_more'
FORMAT_VERSION = 1


class CountMismatch(RuntimeError):
    """Stop immediately: expected published counts must never be adjusted."""


@dataclass(frozen=True)
class DistanceTable:
    distances: Mapping[str, int]
    counts: tuple[int, ...]
    max_depth: int


def engine_fingerprint() -> str:
    encoded = json.dumps([MOVE_PERMUTATIONS[m] for m in MOVES], separators=(',', ':'))
    return hashlib.sha256(encoded.encode()).hexdigest()


def build_table(max_depth: int = 5) -> DistanceTable:
    if type(max_depth) is not int or not 0 <= max_depth <= 5:
        raise ValueError('max_depth must be an integer from 0 through 5')
    distances = {SOLVED: 0}
    frontier = [SOLVED]
    counts = [1]
    permutations = tuple(MOVE_PERMUTATIONS[m] for m in MOVES)
    for depth in range(1, max_depth + 1):
        following = []
        for state in frontier:
            for permutation in permutations:
                child = ''.join(map(state.__getitem__, permutation))
                if child not in distances:
                    distances[child] = depth
                    following.append(child)
        counts.append(len(following))
        if counts[-1] != EXPECTED_COUNTS[depth]:
            raise CountMismatch(f'Depth {depth}: observed {counts[-1]}, expected '
                                f'{EXPECTED_COUNTS[depth]}; stop and diagnose')
        frontier = following
    return DistanceTable(MappingProxyType(distances), tuple(counts), max_depth)


def write_cache(table: DistanceTable, path: Path = DEFAULT_CACHE) -> None:
    """Atomic deterministic compressed pickle: no timestamps or output filename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    payload = dict(format_version=FORMAT_VERSION, metric='HTM', facelet_order=FACELET_ORDER,
                   moves=MOVES, engine_fingerprint=engine_fingerprint(),
                   max_depth=table.max_depth, counts=table.counts,
                   distances=dict(table.distances))
    try:
        with temporary.open('wb') as raw:
            with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0,
                               compresslevel=6) as compressed:
                pickle.dump(payload, compressed, protocol=5)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@lru_cache(maxsize=2)
def load_table(path: Path = DEFAULT_CACHE) -> DistanceTable:
    """Load once per path, verifying metadata and actual distance histogram."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f'BFS cache missing: {path}; run scripts/build_bfs.py')
    with gzip.open(path, 'rb') as stream:
        payload = pickle.load(stream)
    required = dict(format_version=FORMAT_VERSION, metric='HTM', facelet_order=FACELET_ORDER,
                    moves=MOVES, engine_fingerprint=engine_fingerprint())
    if not isinstance(payload, dict) or any(payload.get(k) != v for k, v in required.items()):
        raise ValueError('Incompatible BFS cache metadata; rebuild with scripts/build_bfs.py')
    depth = payload.get('max_depth')
    if type(depth) is not int or not 0 <= depth <= 5:
        raise ValueError('Invalid cache depth')
    distances = payload.get('distances')
    counts = tuple(payload.get('counts', ()))
    if not isinstance(distances, dict) or counts != EXPECTED_COUNTS[:depth + 1]:
        raise ValueError('Invalid BFS cache counts')
    actual = Counter(distances.values())
    if (distances.get(SOLVED) != 0 or len(distances) != sum(counts)
            or actual != dict(enumerate(counts))
            or any(not isinstance(s, str) or len(s) != 54 for s in distances)):
        raise ValueError('BFS cache contents fail integrity checks')
    return DistanceTable(MappingProxyType(distances), counts, depth)


def exact_distance(facelets: str, *, table: DistanceTable | None = None) -> int | str:
    """For legal cube states: exact 0..5, otherwise explicit unknown >=6.

    Structural validation is inherited from the engine, not a full physical
    legality test. Generated suite states are legal by construction.
    """
    validate_facelets(facelets)
    table = load_table() if table is None else table
    if table.max_depth != 5:
        raise ValueError('unknown_6_or_more requires a complete depth-five table')
    return table.distances.get(facelets, UNKNOWN_DISTANCE)


def build_cache(path: Path = DEFAULT_CACHE) -> dict:
    started = time.perf_counter()
    table = build_table()
    search_time = time.perf_counter() - started
    write_cache(table, path)
    total_time = time.perf_counter() - started
    # ru_maxrss is bytes on macOS, KiB on Linux; report whole-process high water.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_bytes = int(peak if sys.platform == 'darwin' else peak * 1024)
    estimated = (sys.getsizeof(dict(table.distances))
                 + sum(sys.getsizeof(s) for s in table.distances)
                 + sum(sys.getsizeof(i) for i in range(6)))
    load_table.cache_clear()
    return dict(counts=list(table.counts), total_states=len(table.distances),
                search_time_s=search_time, cache_write_time_s=total_time-search_time,
                total_build_time_s=total_time, peak_process_rss_bytes=peak_bytes,
                estimated_retained_table_bytes=estimated, compressed_bytes=Path(path).stat().st_size,
                cache_file=str(Path(path)), engine_fingerprint=engine_fingerprint(),
                memory_definition='RSS is whole-process high-water during search/write; '
                                  'retained estimate counts dict, string keys and shared depth integers')
