"""Offline presentation geometry and colour tests; no execution policy changes."""
import json
from pathlib import Path
import shutil
import subprocess
from xml.etree import ElementTree as ET
import pytest
from backend.app.cube.engine import SOLVED,apply_moves
from scripts.check_contrast import audit
ROOT=Path(__file__).resolve().parents[2]


def test_theme_all_text_fill_pairs_meet_wcag():
    assert all(r['passes'] for r in audit())


@pytest.mark.parametrize('scramble',[[],['R','U',"F'",'L2'],['F','R',"U'",'B2']])
def test_isometric_renderer_preserves_facelet_indices_and_labels(scramble):
    if not shutil.which('node'):pytest.skip('Node required for renderer verification')
    state=apply_moves(SOLVED,scramble)
    source="const fs=require('fs'),vm=require('vm');let sandbox={document:{addEventListener(){}},colours:{},html:s=>s};vm.createContext(sandbox);vm.runInContext(fs.readFileSync('frontend/static/cube-view.js','utf8'),sandbox);process.stdout.write(sandbox.cubeSvg("+json.dumps(state)+",'fixture'));"
    svg=ET.fromstring(subprocess.check_output(['node','-e',source],cwd=ROOT,text=True))
    groups=svg.findall('g')
    assert len(groups)==27
    assert [int(g.attrib['data-index']) for g in groups]==list(range(9))+list(range(18,27))+list(range(9,18))
    for group in groups:
        index=int(group.attrib['data-index'])
        assert group.find('text').text==state[index]
        assert len(group.find('polygon').attrib['points'].split())==4
    # U front-right corner meets F top-right and R top-left at (130,118).
    assert '130,118' in groups[8].find('polygon').attrib['points'].split()
    assert '130,118' in groups[11].find('polygon').attrib['points'].split()
    assert groups[18].find('polygon').attrib['points'].split()[0]=='130,118'


def test_display_assets_are_local_read_only_and_make_no_inference(tmp_path):
    import asyncio
    import httpx
    from backend.app.config import Settings
    from backend.app.models.accounting import Ledger
    from backend.app.storage import RunStore
    from backend.app.benchmark import Workflow
    from backend.app.main import create_app
    ledger=Ledger(tmp_path/'ledger.sqlite3')
    def forbidden():raise AssertionError('Static asset route opened an inference client')
    workflow=Workflow(Settings(),ledger,RunStore(tmp_path/'results'),client_factory=forbidden)
    async def read():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(workflow)),base_url='http://testserver') as client:
            for asset in ('theme.css','cube-view.js','Fredoka.ttf','OFL-Fredoka.txt'):
                response=await client.get('/static/'+asset)
                assert response.status_code==200 and len(response.content)>100
            assert (await client.get('/static/.env')).status_code==404
    asyncio.run(read())
    assert ledger.dashboard_data()['total']['requests']==0
