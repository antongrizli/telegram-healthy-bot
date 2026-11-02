import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services import gemini


@pytest.fixture
def router(monkeypatch):
    client = MagicMock()
    client.post = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(gemini.httpx, 'AsyncClient', lambda **kw: context)
    monkeypatch.setattr('src.services.ai_quota.reserve_attempt', AsyncMock())
    monkeypatch.setattr(gemini.settings, 'OPENROUTER_FALLBACK_MODEL', 'fallback-model')
    return client


def response(content='', finish='length'):
    result = MagicMock(status_code=200)
    result.json.return_value = {
        'choices': [{'message': {'content': content}, 'finish_reason': finish}],
        'usage': {'completion_tokens': 8192},
    }
    return result


@pytest.mark.asyncio
async def test_reasoning_exhaustion_uses_fallback(router):
    payloads = []

    async def post(*args, **kwargs):
        payloads.append(dict(kwargs['json']))
        return response() if len(payloads) == 1 else response('valid output', 'stop')

    router.post.side_effect = post
    result = await gemini.call_openrouter_with_retry(['test'])
    assert result.text == 'valid output'
    assert payloads[0]['max_tokens'] == gemini.settings.OPENROUTER_MAX_TOKENS
    assert payloads[1]['reasoning'] == {'enabled': False}
    assert payloads[1]['model'] == 'fallback-model'


@pytest.mark.asyncio
@pytest.mark.parametrize('content,finish', [('', 'stop'), ('partial JSON', 'length')])
async def test_unusable_output_is_rejected_after_one_recovery(router, content, finish):
    router.post.return_value = response(content, finish)
    with pytest.raises(ValueError, match='unusable output'):
        await gemini.call_openrouter_with_retry(['test'])
    assert router.post.await_count == 2


@pytest.mark.asyncio
async def test_total_deadline_cancels_request(router, monkeypatch):
    monkeypatch.setattr(gemini.settings, 'OPENROUTER_REQUEST_TIMEOUT', 0.01)

    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    router.post.side_effect = slow
    with pytest.raises(TimeoutError):
        await gemini.call_openrouter_with_retry(['test'])
    assert router.post.await_count == 1
