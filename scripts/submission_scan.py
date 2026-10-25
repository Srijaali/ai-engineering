"""Offline secret scan: report paths/categories only, never matching contents."""
import json
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.config import ROOT
from dotenv import dotenv_values


def scan(root=ROOT):
    env=root/'.env';env_bytes=env.read_bytes() if env.exists() else b''
    key=(dotenv_values(env).get('OPENROUTER_API_KEY') or '').encode() if env.exists() else b''
    if key==b'replace_with_your_key':key=b''
    key_pattern=re.compile(rb'sk-(?:or-v1-|proj-)?[A-Za-z0-9_-]{24,}')
    auth_pattern=re.compile(rb'Authorization[\"\x27\s:]+Bearer[\s\"\x27:]+([^\s\"\x27<>]+)',re.I)
    findings=[];reviewed_headers=[];files=0
    def inspect(name,data,scope):
        actual=bool(key and key in data) or bool(env_bytes and env_bytes in data) or bool(key_pattern.search(data))
        if actual:findings.append(dict(scope=scope,path=name,category='Potential credential or exact private env content',expected_private_env=(scope=='working_tree' and name=='.env')))
        if auth_pattern.search(data):reviewed_headers.append(dict(scope=scope,path=name,category='Authorization syntax; review placeholders/redaction versus credential',contains_potential_secret=actual))
    for p in root.rglob('*'):
        rel=p.relative_to(root)
        if any(part in ('.git','.venv','venv','__pycache__','.pytest_cache') for part in rel.parts) or not p.is_file():continue
        files+=1;inspect(str(rel),p.read_bytes(),'working_tree')
    blobs={}
    try:
        revisions=subprocess.check_output(['git','rev-list','--all'],cwd=root,text=True).splitlines()
        for rev in revisions:
            for entry in subprocess.check_output(['git','ls-tree','-r',rev],cwd=root,text=True).splitlines():
                meta,path=entry.split('\t',1);mode,kind,oid=meta.split()
                if kind=='blob':blobs.setdefault(oid,path)
        for oid,path in blobs.items():inspect(path,subprocess.check_output(['git','cat-file','blob',oid],cwd=root),'git_history')
    except subprocess.CalledProcessError:revisions=[]
    unexpected=[r for r in findings if not r['expected_private_env']]
    ignored=subprocess.run(['git','check-ignore','-q','.env'],cwd=root).returncode==0
    report=dict(status='PASS' if not unexpected and ignored else 'REVIEW_REQUIRED',working_files_scanned=files,history_commits=len(revisions),history_unique_blobs=len(blobs),env_ignored=ignored,findings=findings,authorization_syntax_hits=reviewed_headers,unexpected_secret_hits=len(unexpected),matching_values_printed=False)
    target=root/'docs/submission/security-scan.json';target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    r=scan();print(json.dumps({k:v for k,v in r.items() if k not in ('authorization_syntax_hits','findings')},indent=2))
    if r['status']!='PASS':raise SystemExit(1)
