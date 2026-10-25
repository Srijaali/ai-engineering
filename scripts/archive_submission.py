"""List first, then MOVE reproducible clutter into a dated sibling archive."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.config import ROOT


def plan():
    choices=[]
    def add(p,reason):
        if p.exists():choices.append((p,reason))
    for rel,reason in [('data/cache','Reproducible BFS cache; rebuild script retained'),('data/results/SIMULATED','Mock rehearsal logs excluded from LIVE results'),('data/results/BASELINE','Reproducible free Random baseline; generator retained')]:add(ROOT/rel,reason)
    for p in ROOT.rglob('*'):
        rel=p.relative_to(ROOT)
        if any(part in ('.venv','venv','.git') for part in rel.parts):continue
        if p.is_dir() and p.name in ('__pycache__','.pytest_cache','.mypy_cache','.ruff_cache'):add(p,'Generated Python/test cache')
        elif p.is_file() and p.name=='.DS_Store':add(p,'OS metadata')
    for p in (ROOT/'docs').glob('PHASE*_REVIEW.md'):add(p,'Superseded phase review; decisions and final README retained')
    for p in (ROOT/'docs/verification').rglob('*'):
        if p.is_file() and (p.suffix.lower() in ('.png','.jpg','.jpeg','.webp','.xml') or p.name=='REVIEW.md'):
            add(p,'Superseded verification screenshot/test report; final docs/img and reproducible tests retained')
    for rel in ('results/COSTS.md','results/COST_LEDGER.json','results/COST_LEDGER.csv','results/COSTS-FINAL-2026-10-25.md','results/COSTS-FINAL-2026-10-25.csv'):
        add(ROOT/rel,'Superseded cost snapshot; complete latest CSV, stage report, ledger evidence and reconciliation retained')
    selected=[]
    for p,why in sorted(choices,key=lambda x:(len(x[0].parts),str(x[0]))):
        if not any(parent==p or parent in p.parents for parent,_ in selected):selected.append((p,why))
    rows=[]
    for p,why in selected:
        files=[p] if p.is_file() else sorted(x for x in p.rglob('*') if x.is_file())
        rows.extend(dict(path=str(x.relative_to(ROOT)),reason=why) for x in files)
    return selected,rows


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--move',action='store_true');args=parser.parse_args()
    selected,rows=plan();target=ROOT/'docs/submission';target.mkdir(parents=True,exist_ok=True)
    manifest=target/'archive-plan.json'
    if args.move:
        old=json.loads(manifest.read_text())
        if old['files']!=rows:raise SystemExit('Archive plan changed; review a fresh list first')
        archive=ROOT.parent/'ai-engineering-archive'/('submission-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        archive.mkdir(parents=True,exist_ok=False)
        for p,_ in selected:
            dest=archive/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(p),str(dest))
        record=dict(archive=str(archive),files=rows,deleted_files=0)
        (target/'archive-manifest.json').write_text(json.dumps(record,indent=2)+'\n')
        lines=['# Submission archive manifest','',f'Archive: `{archive}`. All entries were moved, not deleted. `.env`, `.venv`, source, real LIVE logs and public billing evidence were untouched.','', '| Original repository path | Reason |','|---|---|']
        lines += [f"| `{r['path']}` | {r['reason']} |" for r in rows]
        lines+=['','Ambiguous items retained: diagnostic JSON/request evidence used by cost-report tests and provenance; prompt-review candidates showing unadopted alternatives; phase-2 build/verification JSON documenting exact-count checks; C1/C2/C3/W stage metadata and execution logs; assignment PDF; original tracked deletion assignment 2/empty was not altered. The mutable private SQLite ledger and local virtualenv remain in place and ignored. The full original recording was downloaded outside the repository and preserved.']
        (target/'archive-manifest.md').write_text('\n'.join(lines)+'\n')
        print(json.dumps(dict(archive=str(archive),moved_files=len(rows))))
    else:
        manifest.write_text(json.dumps(dict(files=rows),indent=2)+'\n')
        print(json.dumps(dict(planned_files=len(rows),groups=[dict(path=str(p.relative_to(ROOT)),reason=why) for p,why in selected]),indent=2))

if __name__=='__main__':main()
