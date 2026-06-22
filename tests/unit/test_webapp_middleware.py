import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from src.webapp.middlewares import block_scanners_middleware

async def dummy_handler(request):
    return web.Response(text="Success")

@pytest.mark.asyncio
async def test_block_scanners_middleware_allowed():
    app = web.Application(middlewares=[block_scanners_middleware])
    app.router.add_get("/", dummy_handler)
    
    async with TestClient(TestServer(app)) as client:
        # Standard User-Agent
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = await client.get("/", headers=headers)
        assert resp.status == 200
        assert await resp.text() == "Success"

@pytest.mark.asyncio
async def test_block_scanners_middleware_blocked_cortex():
    app = web.Application(middlewares=[block_scanners_middleware])
    app.router.add_get("/", dummy_handler)
    
    async with TestClient(TestServer(app)) as client:
        # Cortex Xpanse / Palo Alto scanning user agent
        headers = {"User-Agent": "Hello from Palo Alto Networks, find out more about our scans in https://docs-cortex.paloaltonetworks.com/r/1/Cortex-Xpanse/Scanning-activity"}
        resp = await client.get("/", headers=headers)
        assert resp.status == 403
        assert await resp.text() == "Access Denied"

@pytest.mark.asyncio
async def test_block_scanners_middleware_blocked_censys():
    app = web.Application(middlewares=[block_scanners_middleware])
    app.router.add_get("/", dummy_handler)
    
    async with TestClient(TestServer(app)) as client:
        # Censys scanner User-Agent
        headers = {"User-Agent": "CensysInspect/1.1"}
        resp = await client.get("/", headers=headers)
        assert resp.status == 403
        assert await resp.text() == "Access Denied"

@pytest.mark.asyncio
async def test_block_scanners_middleware_no_agent():
    app = web.Application(middlewares=[block_scanners_middleware])
    app.router.add_get("/", dummy_handler)
    
    async with TestClient(TestServer(app)) as client:
        # Missing User-Agent header should be allowed
        resp = await client.get("/")
        assert resp.status == 200
        assert await resp.text() == "Success"
