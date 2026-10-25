"""Rebuild the complete local HTM cache; no model/network/config imports."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.cube.optimal import DEFAULT_CACHE, build_cache


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--report', type=Path, default=ROOT / 'docs/verification/phase2-bfs.json')
    args = parser.parse_args()
    result = build_cache(args.cache)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
