"""Create fixed offline suites, verifying exact small distances before writing."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.cube.puzzles import MASTER_SEED, generate_suites, verify_small_suites, write_suites


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--master-seed', type=int, default=MASTER_SEED)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/puzzles')
    parser.add_argument('--report', type=Path, default=ROOT / 'docs/verification/phase2-puzzles.json')
    args = parser.parse_args()
    suites = generate_suites(args.master_seed)
    checks = verify_small_suites(suites)
    write_suites(suites, args.output)
    result = dict(master_seed=args.master_seed, suite_sizes={s: len(v) for s, v in suites.items()},
                  independent_method='Fresh two-ended radius-2/radius-3 searches using '
                                     'Kociemba cubie rotations; no cache or solve() lookup',
                  independent_checks=checks,
                  files={s: dict(bytes=(args.output / f'{s}.json').stat().st_size,
                                  sha256=hashlib.sha256((args.output / f'{s}.json').read_bytes()).hexdigest())
                         for s in suites})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
