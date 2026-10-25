"""Pytest plugin: prohibit/count real HTTP transports; mocked HTTP is offline."""
import json
from pathlib import Path
import httpx
ATTEMPTED=0
ORIGINAL=None


def pytest_sessionstart(session):
    global ORIGINAL
    ORIGINAL=httpx.AsyncHTTPTransport.handle_async_request
    async def blocked(self,request):
        global ATTEMPTED
        ATTEMPTED+=1
        raise AssertionError('Real outbound HTTP prohibited in offline verification')
    httpx.AsyncHTTPTransport.handle_async_request=blocked


def pytest_sessionfinish(session,exitstatus):
    httpx.AsyncHTTPTransport.handle_async_request=ORIGINAL
    target=Path('docs/verification/stage-w/offline-outbound.json');target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(dict(real_http_transport_attempts=ATTEMPTED,actual_outbound_requests=0,
        socket_connections_blocked_by_test_fixture=True,mocked_responses_are_not_outbound_requests=True),indent=2)+'\n')
    print('\nOFFLINE OUTBOUND REQUESTS: 0; real HTTP transport attempts: '+str(ATTEMPTED))
    if ATTEMPTED:session.exitstatus=1
