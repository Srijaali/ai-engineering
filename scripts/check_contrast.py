"""Offline WCAG contrast audit for every theme text/fill pair, including stickers."""
import json
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def luminance(colour):
    channels=[int(colour[i:i+2],16)/255 for i in (1,3,5)]
    linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in channels]
    return sum(v*w for v,w in zip(linear,(.2126,.7152,.0722)))

def ratio(a,b):
    a,b=sorted((luminance(a),luminance(b)))
    return (b+.05)/(a+.05)

def audit():
    css=(ROOT/'frontend/static/theme.css').read_text()
    v=dict(re.findall(r'--([\w-]+):\s*(#[0-9A-Fa-f]{6})',css))
    pairs=[('body/background',v['ink'],v['bg']),('body/surface',v['ink'],v['surface']),
           ('muted/background',v['muted'],v['bg']),('muted/surface',v['muted'],v['surface']),
           ('identity and selected toggle',v['surface'],v['ink']),('missing identity','#FFFFFF','#A4262C'),
           ('baseline muted',v['muted'],'#F1E8E0'),('disabled control',v['muted'],'#E9DCD2')]
    for name in ('mustard','coral','teal'):pairs.append((name+' fill',v['ink'],v[name]))
    for name,fill in [('gold','#EBC56C'),('silver','#D8DCDF'),('bronze','#DCA786'),('grey','#D6D0CA'),('success metric','#F5E4CE'),('gap metric','#E7EFE5')]:pairs.append((name,v['ink'],fill))
    js=(ROOT/'frontend/race.js').read_text()
    colours=dict(re.findall(r"([URFDLB]):'(#[A-Fa-f0-9]{6})'",js))
    pairs.extend(('sticker '+name,v['ink'],fill) for name,fill in colours.items())
    result=[dict(element=name,foreground=fg,background=bg,ratio=round(ratio(fg,bg),3),passes=ratio(fg,bg)>=4.5) for name,fg,bg in pairs]
    if not all(r['passes'] for r in result):raise AssertionError(json.dumps([r for r in result if not r['passes']]))
    return result

if __name__=='__main__':
    result=audit();output=ROOT/'docs/verification/theme/contrast.json';output.parent.mkdir(exist_ok=True,parents=True)
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(f'PASS: {len(result)} text/fill pairs; minimum {min(r["ratio"] for r in result):.3f}:1')
