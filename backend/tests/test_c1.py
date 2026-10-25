"""C1 confirmation and truncation accounting, with mocked HTTP only."""
import asyncio
import json
from fastapi.testclient import TestClient
import httpx
from backend.app.main import create_app
from backend.app.runner import output_counts
from backend.app.models.registry import MODELS
from backend.tests.test_stages import workflow,response,definition


def test_counts_separate_truncation_from_other_malformed_and_refusals():
    rows=[dict(finish_reason='length',error_class='LENGTH_OUTPUT'),
          dict(finish_reason='stop',error_class='BAD_JSON'),
          dict(error_class='MODEL_REFUSAL')]
    from backend.app.models.base import MALFORMED
    other=next(e for e in MALFORMED if e!='LENGTH_OUTPUT')
    rows[1]['error_class']=other
    assert output_counts(rows)==dict(truncated_responses=1,other_malformed_outputs=1)


def test_c1_opus_length_counts_in_saved_run_and_dashboard(tmp_path):
    calls=[]
    def handler(req):
        model=json.loads(req.content)['model'];calls.append(model)
        if model==MODELS[-1][0]:
            return httpx.Response(200,json={'model':model,'choices':[{'finish_reason':'length','message':{'content':''}}],
                'usage':{'cost':.00001,'prompt_tokens':100,'completion_tokens':1024,'completion_tokens_details':{'reasoning_tokens':1024}}})
        return response(req)
    w=workflow(tmp_path,handler)
    stage=definition('C1',240,['primary-001'],[m for m,_,_ in MODELS],'primary')
    run,_=w.submit_stage(stage,True);asyncio.run(w.execute(run['id']))
    opus=next(t for t in w.store.trials(run['id']) if t['model_id']==MODELS[-1][0])
    assert opus['truncated_responses']==opus['forfeits']==30
    assert opus['other_malformed_outputs']==opus['refusals']==0
    assert len(calls)==120
    row=next(r for r in w.dashboard()['statistics']['primary'] if r['model_id']==MODELS[-1][0])
    assert row['truncated_responses']==30 and row['other_malformed_outputs']==0
    assert all(m['attempt_usage'][0]['finish_reason']=='length' for m in opus['moves'])


def test_web_stage_requires_click_and_reuses_existing_without_restart(tmp_path,monkeypatch):
    w=workflow(tmp_path,lambda req: (_ for _ in ()).throw(AssertionError('No inference before confirmation')))
    started=[];monkeypatch.setattr(w,'start',started.append)
    with TestClient(create_app(w)) as client:
        cfg=client.get('/api/config').json()
        assert cfg['stage_plans']['C1']['planned_initial_calls']==120
        assert w.ledger.dashboard_data()['total']['requests']==0 and not started
        headers={'X-Run-Consent':cfg['consent_token']}
        assert client.post('/api/race',headers=headers,json={'stage_name':'C1','confirm':False}).status_code==403
        first=client.post('/api/race',headers=headers,json={'stage_name':'C1','confirm':True})
        assert first.status_code==200 and len(started)==1
        again=client.post('/api/race',headers=headers,json={'stage_name':'C1','confirm':True})
        assert again.json()['reused'] and len(started)==1
        assert client.post('/api/race',headers=headers,json={'stage_name':'C1','confirm':True,'resume_run_id':first.json()['run_id']}).status_code==403
        assert w.ledger.dashboard_data()['total']['requests']==0
