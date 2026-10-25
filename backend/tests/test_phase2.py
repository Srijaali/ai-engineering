"""Offline exact-distance, independent oracle and deterministic suite checks."""
from collections import Counter
import gzip
import json
from pathlib import Path
import pickle

import pytest

from backend.app.cube import optimal, puzzles, reference
from backend.app.cube.engine import MOVES, SOLVED, apply, apply_moves

ROOT = Path(__file__).resolve().parents[2]
SUITES = {s: json.loads((ROOT / f'data/puzzles/{s}.json').read_text())
          for s in puzzles.SUITE_LENGTHS}
ALL = [p for suite in SUITES.values() for p in suite]
SMALL = SUITES['warmup'] + SUITES['primary']


@pytest.fixture(scope='module')
def table():
    return optimal.load_table()


def test_full_bfs_counts_are_from_actual_states_not_only_metadata(table):
    assert table.max_depth == 5
    assert table.counts == (1, 18, 243, 3240, 43239, 574908)
    assert Counter(table.distances.values()) == dict(enumerate(table.counts))
    assert len(table.distances) == 621649


def test_builder_and_deterministic_compressed_cache_roundtrip(tmp_path):
    small = optimal.build_table(2)
    assert small.counts == (1, 18, 243)
    a, b = tmp_path / 'a.gz', tmp_path / 'b.gz'
    optimal.write_cache(small, a)
    optimal.write_cache(small, b)
    assert a.read_bytes() == b.read_bytes()
    loaded = optimal.load_table(a)
    assert loaded.distances == small.distances
    with pytest.raises(ValueError, match='complete depth-five'):
        optimal.exact_distance(SOLVED, table=loaded)


def test_missing_or_incompatible_cache_raises_instead_of_guessing(tmp_path):
    with pytest.raises(FileNotFoundError, match='build_bfs'):
        optimal.load_table(tmp_path / 'missing.gz')
    path = tmp_path / 'cache.gz'
    optimal.write_cache(optimal.build_table(1), path)
    with gzip.open(path, 'rb') as stream:
        payload = pickle.load(stream)
    payload['engine_fingerprint'] = 'incompatible'
    with gzip.open(path, 'wb') as stream:
        pickle.dump(payload, stream)
    with pytest.raises(ValueError, match='metadata'):
        optimal.load_table(path)


def test_lookup_zero_and_all_eighteen_one_move_states(table):
    assert optimal.exact_distance(SOLVED, table=table) == 0
    assert all(optimal.exact_distance(apply(SOLVED, m), table=table) == 1 for m in MOVES)


@pytest.mark.parametrize('puzzle', ALL, ids=lambda p: p['id'])
def test_suite_schema_lookup_and_scramble_reproduction(puzzle, table):
    assert set(puzzle) == {'id', 'suite', 'seed', 'scramble', 'facelets',
                           'scramble_length', 'exact_optimal', 'reference_upper_bound'}
    assert type(puzzle['seed']) is int
    assert puzzle['scramble'] == puzzles.generate_scramble(puzzle['seed'], puzzle['scramble_length'])
    assert len(puzzle['scramble']) == puzzle['scramble_length']
    assert apply_moves(SOLVED, puzzle['scramble']) == puzzle['facelets'] != SOLVED
    actual = optimal.exact_distance(puzzle['facelets'], table=table)
    if puzzle['suite'] == 'extension':
        assert puzzle['exact_optimal'] is None
        assert actual == optimal.UNKNOWN_DISTANCE  # verified for these fixed five states
        assert puzzle['reference_upper_bound']['label'] == 'reference_upper_bound_not_optimal'
    else:
        assert actual == puzzle['exact_optimal'] == puzzle['scramble_length']
        assert puzzle['reference_upper_bound'] is None


@pytest.mark.parametrize('puzzle', SMALL, ids=lambda p: p['id'])
def test_independent_oracle_does_not_access_table(puzzle, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('independent verification accessed cached distance code')
    monkeypatch.setattr(optimal, 'load_table', forbidden)
    monkeypatch.setattr(optimal, 'exact_distance', forbidden)
    monkeypatch.setattr(reference.kociemba, 'solve', forbidden)
    assert reference.independent_distance(puzzle['facelets']) == puzzle['exact_optimal']


@pytest.mark.parametrize('puzzle', SUITES['extension'], ids=lambda p: p['id'])
def test_extension_reference_is_an_engine_verified_upper_bound(puzzle):
    ref = puzzle['reference_upper_bound']
    assert set(ref) == {'label', 'length', 'solution'}
    assert ref['label'] == 'reference_upper_bound_not_optimal'
    assert ref['length'] == len(ref['solution'])
    assert apply_moves(puzzle['facelets'], ref['solution']) == SOLVED
    assert reference.reference_upper_bound(puzzle['facelets']) == ref


@pytest.mark.parametrize('seed', range(40))
def test_scramble_rules_and_seed_reproducibility(seed):
    scramble = puzzles.generate_scramble(seed, 20)
    assert scramble == puzzles.generate_scramble(seed, 20)
    assert all(m in MOVES for m in scramble)
    for previous, following in zip(scramble, scramble[1:]):
        assert previous[0] != following[0]
        assert previous[0] + following[0] not in ('DU', 'RL', 'BF')


def test_suite_sizes_disjointness_no_duplicates_and_exact_primary_balance():
    assert {s: len(v) for s, v in SUITES.items()} == dict(warmup=3, primary=10, extension=5)
    assert len({p['id'] for p in ALL}) == len({p['facelets'] for p in ALL}) == 18
    assert Counter(p['scramble_length'] for p in SUITES['primary']) == {4: 5, 5: 5}
    assert all(3 <= p['scramble_length'] <= 4 for p in SUITES['warmup'])
    assert all(15 <= p['scramble_length'] <= 20 for p in SUITES['extension'])


def test_same_master_seed_produces_identical_file_bytes(tmp_path, table):
    generated = puzzles.generate_suites(puzzles.MASTER_SEED, table=table)
    puzzles.write_suites(generated, tmp_path)
    first = {s: (tmp_path / f'{s}.json').read_bytes() for s in generated}
    again = puzzles.generate_suites(puzzles.MASTER_SEED, table=table)
    puzzles.write_suites(again, tmp_path)
    for suite in generated:
        assert first[suite] == (tmp_path / f'{suite}.json').read_bytes()
        assert first[suite] == (ROOT / f'data/puzzles/{suite}.json').read_bytes()
        assert 'timestamp' not in first[suite].decode()


def test_solved_candidate_is_rejected(table, monkeypatch):
    original = puzzles.generate_scramble
    calls = 0
    def first_solved_then_normal(seed, length):
        nonlocal calls
        calls += 1
        return [] if calls == 1 else original(seed, length)
    monkeypatch.setattr(puzzles, 'generate_scramble', first_solved_then_normal)
    generated = puzzles.generate_suites(table=table)
    assert calls >= 19
    assert all(p['facelets'] != SOLVED for suite in generated.values() for p in suite)


def test_independent_zero_and_bounded_no_solution():
    assert reference.independent_distance(SOLVED) == 0
    assert reference.independent_distance(apply(SOLVED, 'R'), max_depth=0) is None
