"""Offline public billing snapshot; exclude all reusable consent credentials."""
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.config import ROOT

TABLES=('attempts','reconciliations','stages','control','blocks','sequence_guards')

def export(root=ROOT):
    source=root/'data/results/phase3/ledger.sqlite3'
    db=sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    tables={name:[dict(r) for r in db.execute('SELECT * FROM '+name+' ORDER BY 1')] for name in TABLES}
    db.close()
    target=root/'data/evidence/ledger.json';target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(dict(format_version=1,cost_unit='USD / 1000000000',tables=tables),sort_keys=True,indent=2)+'\n')
    return target

if __name__=='__main__':print(export())
