import base64
import io
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from google import genai
from google.genai import errors
from PIL import Image, PngImagePlugin

from src.services import gemini
from src.services.ai_payload import food_contents, image_part, log_api_error


def png_with_metadata():
    output = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('Source', 'macOS clipboard browser image')
    Image.new('RGBA', (128, 128), (255, 0, 0, 128)).save(output, 'PNG', pnginfo=metadata)
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("count_enabled", [False, True])
async def test_real_sdk_wire_payload_and_token_preflight(monkeypatch, count_enabled):
    image = png_with_metadata()
    requests = []
    food = '{"food_items":[{"name":"Egg","portion":"1","calories":70,"protein":6,"fat":5,"carb":0.5}]}'

    async def respond(request):
        payload = json.loads(request.content)
        requests.append((request.url.path.rsplit(':', 1)[-1], payload))
        if request.url.path.endswith(':countTokens'):
            return httpx.Response(200, json={'totalTokens': 258})
        return httpx.Response(200, json={'candidates': [{'content': {
            'role': 'model', 'parts': [{'text': food}]}}]})

    sdk = genai.Client(api_key='test-key', http_options={
        'async_client_args': {'transport': httpx.MockTransport(respond)}})
    monkeypatch.setattr(gemini, 'client', sdk)
    monkeypatch.setattr(gemini.settings, 'AI_COUNT_TOKENS', count_enabled)
    monkeypatch.setattr(gemini.settings, 'GEMINI_MODEL', 'gemma-4-31b-it')
    reserve = AsyncMock()
    monkeypatch.setattr('src.services.ai_quota.reserve_attempt', reserve)
    try:
        result = await gemini.analyze_food_input(image_bytes=image, text_description='caption')
    finally:
        await sdk.aio.aclose()
    assert result.total_calories == 70
    expected = ['countTokens', 'generateContent'] if count_enabled else ['generateContent']
    assert [operation for operation, _ in requests] == expected
    assert reserve.await_count == len(expected)
    if count_enabled:
        assert requests[0][1]['contents'] == requests[1][1]['contents']
    for _, payload in requests:
        assert len(payload['contents']) == 1
        message = payload['contents'][0]
        assert message['role'] == 'user'
        assert len(message['parts']) == 3
        blob = message['parts'][0]['inlineData']
        mime = blob.get('mimeType') or blob.get('mime_type')
        assert mime == 'image/png'
        assert base64.b64decode(blob['data'], altchars=b'-_', validate=True) == image
        assert message['parts'][1]['text'] == 'caption'
        assert 'nutrition' in message['parts'][2]['text']


@pytest.mark.parametrize('data', [b'', b'not an image'])
def test_reject_missing_or_unknown_image(data):
    with pytest.raises(ValueError):
        food_contents([data], ['prompt'])


@pytest.mark.parametrize('data,mime', [(b'\xff\xd8\xffjpeg', 'image/jpeg'),
                                     (b'RIFF1234WEBPdata', 'image/webp')])
def test_mime_from_binary_signature(data, mime):
    assert image_part(data).inline_data.mime_type == mime


def test_full_response_logged_and_secret_redacted(caplog, monkeypatch):
    monkeypatch.setattr(gemini.settings, 'GEMINI_API_KEY', 'private-test-credential')
    body = json.dumps({'error': {'message': 'x' * 3000, 'details': [
        {'fieldViolations': [{'field': 'contents[0].parts[0]', 'description': 'invalid image'}]}],
        'api_key': 'private-test-credential'}})
    exc = errors.ClientError(400, {'error': {'message': 'generic'}}, httpx.Response(400, text=body))
    log_api_error(exc, model='gemma-4-31b-it', operation='generateContent')
    assert 'x' * 3000 in caplog.text
    assert 'contents[0].parts[0]' in caplog.text
    assert 'private-test-credential' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('total', [2, 6, 258])
async def test_low_token_count_is_visible_without_dropping_attachment(mock_gemini_client, monkeypatch, caplog, total):
    from google.genai import types
    monkeypatch.setattr(gemini.settings, "AI_COUNT_TOKENS", True)
    mock_gemini_client.aio.models.count_tokens.return_value = types.CountTokensResponse(total_tokens=total)
    mock_gemini_client.models.generate_content.return_value.text = '{"food_items":[{"name":"Egg","portion":"1","calories":70,"protein":6,"fat":5,"carb":0.5}]}'
    await gemini.analyze_food_input(image_bytes=png_with_metadata())
    assert ('Suspicious image token count' in caplog.text) == (total <= 6)
    mock_gemini_client.models.generate_content.assert_called_once()
