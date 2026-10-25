"""Fail if any Python module under backend/app is absent from flow.md."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_flow(root: Path = ROOT) -> list[str]:
    text = (root / 'flow.md').read_text()
    return [p.relative_to(root).as_posix() for p in sorted((root / 'backend/app').rglob('*.py'))
            if p.relative_to(root).as_posix() not in text]


if __name__ == '__main__':
    missing = check_flow()
    if missing:
        raise SystemExit('Missing from flow.md: ' + ', '.join(missing))
    print('PASS: every backend/app Python module is mentioned in flow.md')
