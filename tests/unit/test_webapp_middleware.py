import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from src.webapp.middlewares import block_scanners_middleware


async def dummy_handler(request):
    return web.Response(text="Success")


@pytest.mark.asyncio
async def test_block_scanners_middleware_allowed():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    req = make_mocked_request("GET", "/", headers=headers)
    resp = await block_scanners_middleware(req, dummy_handler)
    assert resp.status == 200
    assert resp.text == "Success"


@pytest.mark.asyncio
async def test_security_headers_middleware():
    from src.webapp.middlewares import security_headers_middleware

    req = make_mocked_request("GET", "/")
    resp = await security_headers_middleware(req, dummy_handler)
    assert resp.status == 200
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "frame-ancestors" in resp.headers.get("Content-Security-Policy", "")


@pytest.mark.asyncio
async def test_block_scanners_middleware_blocked_cortex():
    headers = {"User-Agent": "Hello from Palo Alto Networks, find out more about our scans in https://docs-cortex.paloaltonetworks.com/r/1/Cortex-Xpanse/Scanning-activity"}
    req = make_mocked_request("GET", "/", headers=headers)
    resp = await block_scanners_middleware(req, dummy_handler)
    assert resp.status == 403
    assert resp.text == "Access Denied"


@pytest.mark.asyncio
async def test_block_scanners_middleware_blocked_censys():
    headers = {"User-Agent": "CensysInspect/1.1"}
    req = make_mocked_request("GET", "/", headers=headers)
    resp = await block_scanners_middleware(req, dummy_handler)
    assert resp.status == 403
    assert resp.text == "Access Denied"


@pytest.mark.asyncio
async def test_block_scanners_middleware_no_agent():
    req = make_mocked_request("GET", "/")
    resp = await block_scanners_middleware(req, dummy_handler)
    assert resp.status == 200
    assert resp.text == "Success"
