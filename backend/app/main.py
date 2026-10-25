"""FastAPI local UI; only confirmed POST /api/race may dispatch inference."""
from contextlib import asynccontextmanager
import json
import secrets
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import asyncio
from .config import ROOT,load_settings
from .models.registry import MODELS
from .models.accounting import Ledger
from .storage import RunStore
from .benchmark import Workflow


def create_app(workflow=None):
    token=secrets.token_urlsafe(32)
    @asynccontextmanager
    async def lifespan(app):
        yield
        for task in list(app.state.workflow.tasks):task.cancel()
        if app.state.workflow.tasks:
            await asyncio.gather(*list(app.state.workflow.tasks),return_exceptions=True)
    app=FastAPI(title='Rubik’s Cube Race',lifespan=lifespan,docs_url=None,redoc_url=None)
    if workflow is None:
        from scripts.cost_report import collect_calls
        ledger=Ledger(ROOT/'data/results/phase3/ledger.sqlite3');ledger.import_history(collect_calls())
        workflow=Workflow(load_settings(require_identity=False,require_key=False),ledger,RunStore(ROOT/'data/results'))
    app.mount('/static',StaticFiles(directory=ROOT/'frontend/static'),name='static')
    app.state.workflow=workflow
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])

    @app.middleware('http')
    async def headers(request,call_next):
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.get('/api/config')
    async def config():
        s=workflow.settings
        puzzles=[dict(id=p['id'],suite=p['suite'],scramble=p['scramble'],facelets=p['facelets'])
                 for suite in ('warmup','primary','extension') for p in workflow.puzzles(suite)]
        return dict(student_name=s.student_name,student_roll=s.student_roll,
                    identity_configured=bool(s.student_name and s.student_roll),
                    models=[dict(id=m,name=n,category=c) for m,n,c in MODELS],
                    puzzles=puzzles,settings=s.snapshot(),consent_token=token,
                    plans={p['id']:workflow.plan(p['suite'],[p['id']]) for p in puzzles},
                    stage_plans={p.stem:workflow.stage_plan(json.loads(p.read_text())) for p in sorted((ROOT/'stages').glob('*.json'))})

    @app.post('/api/race')
    async def start(request:Request):
        origin=request.headers.get('origin')
        expected=str(request.base_url).rstrip('/')
        if origin and origin!=expected:raise HTTPException(403,'Cross-origin execution denied')
        if request.headers.get('X-Run-Consent')!=token:raise HTTPException(403,'Local session consent token required')
        try:
            body=await request.json()
            if not isinstance(body,dict):raise ValueError('Expected JSON object')
            if body.get('stage_name') is not None:
                name=body['stage_name']
                if name not in {p.stem for p in (ROOT/'stages').glob('*.json')}:raise ValueError('Unknown stage')
                definition=json.loads((ROOT/'stages'/(name+'.json')).read_text())
                if body.get('resume_run_id'):raise PermissionError('Stage interruptions require owner review; no UI resume')
                run,should_start=workflow.submit_stage(definition,confirmed=body.get('confirm') is True)
            else:
                run,should_start=workflow.submit(body.get('request_id'),body.get('suite','warmup'),
                    body.get('puzzle_ids'),body.get('mode','SIMULATED'),confirm=body.get('confirm') is True,
                    concurrent=body.get('concurrent',True),resume_run_id=body.get('resume_run_id'))
            if should_start:workflow.start(run['id'])
            return dict(run_id=run['id'],status=run['status'],plan=run['plan'],reused=not should_start)
        except PermissionError as exc:raise HTTPException(403,str(exc))
        except (ValueError,KeyError,TypeError):raise HTTPException(400,'Invalid selection, active race or resume configuration')

    @app.get('/api/events/{run_id}')
    async def events(run_id:str,request:Request,after:int=0):
        try:workflow.store.get(run_id)
        except (ValueError,KeyError):raise HTTPException(404,'Unknown run')
        try:cursor=max(after,int(request.headers.get('Last-Event-ID','0')))
        except ValueError:raise HTTPException(400,'Invalid event cursor')
        async def stream():
            nonlocal cursor
            while True:
                for event in workflow.store.events(run_id,cursor):
                    cursor=event['event_id']
                    yield f'id: {cursor}\ndata: {json.dumps(event)}\n\n'
                state=workflow.store.get(run_id)['status']
                if state not in ('QUEUED','RUNNING'):
                    yield 'event: end\ndata: {}\n\n';break
                if await request.is_disconnected():break
                yield ': keep-alive\n\n'
                await asyncio.sleep(.15)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})

    @app.get('/api/results')
    async def results():return dict(runs=workflow.results(),accounting=workflow.ledger.dashboard_data())

    @app.get('/api/dashboard')
    async def dashboard():return workflow.dashboard()

    @app.get('/api/replay/{run_id}')
    async def replay(run_id:str):
        try:return workflow.replay(run_id)
        except (KeyError,ValueError):raise HTTPException(404,'Unknown run')

    @app.get('/api/health')
    async def health():return dict(ok=True)

    @app.get('/')
    async def index():return FileResponse(ROOT/'frontend/index.html')

    @app.get('/dashboard.html')
    async def dashboard_page():return FileResponse(ROOT/'frontend/dashboard.html')

    @app.get('/race.js')
    async def javascript():return FileResponse(ROOT/'frontend/race.js',media_type='application/javascript')
    return app


app=create_app()
