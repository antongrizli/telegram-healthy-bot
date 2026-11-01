"""Run with python -m src.services.diagnose_gemini inside the bot container.

Uses five small API requests to the configured model and a synthetic PNG.
No Telegram messages, database writes, or private photographs are involved.
"""
import asyncio
import base64
import importlib.metadata
import hashlib
import json
import struct
import zlib

import httpx
from google import genai
from google.genai import types

from src.config import settings
from src.services.gemini import async_client_args
from src.services.ai_payload import food_contents, log_api_error
from src.utils.log_manager import redact_secrets


def sample_png():
    """Create a valid 128x128 red RGB PNG without extra dependencies."""
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))

    rows = (b'\x00' + b'\xff\x00\x00' * 128) * 128
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('!2I5B', 128, 128, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


async def main():
    print('Model:', settings.GEMINI_MODEL)
    for package in ('google-genai', 'aiogram', 'httpx', 'pydantic'):
        print(package, importlib.metadata.version(package))
    async def audit_request(request):
        if not any(op in request.url.path for op in ('generateContent', 'countTokens')):
            return
        payload = json.loads(request.content)
        messages = payload.get('contents', [])
        summary = []
        for message in messages:
            parts = []
            for part in message.get('parts', []):
                if 'inlineData' in part:
                    blob = part['inlineData']
                    data = base64.b64decode(blob['data'], altchars=b'-_', validate=True)
                    assert data == image, 'Image bytes changed during serialization'
                    parts.append({'mime': blob['mimeType'], 'bytes': len(data),
                                  'sha256': hashlib.sha256(data).hexdigest()})
                elif 'text' in part:
                    parts.append({'text_chars': len(part['text'])})
                else:
                    parts.append({'keys': list(part)})
            summary.append({'role': message.get('role'), 'parts': parts})
        print('WIRE:', json.dumps({'operation': request.url.path.rsplit(':', 1)[-1],
              'contents': summary, 'generationConfig': payload.get('generationConfig', {})}), flush=True)

    args = dict(async_client_args)
    args['event_hooks'] = {'request': [audit_request]}
    client = genai.Client(api_key=settings.GEMINI_API_KEY,
                          http_options=types.HttpOptions(async_client_args=args, retry_options=types.HttpRetryOptions(attempts=1)))
    image = sample_png()
    prompt = 'Name the main color in this image in one word.'

    contents = food_contents([image], [prompt])

    async def count_text():
        return (await client.aio.models.count_tokens(model=settings.GEMINI_MODEL,
                contents=food_contents([], [prompt]))).model_dump(exclude_none=True, exclude={"sdk_http_response"})

    async def count_image():
        return (await client.aio.models.count_tokens(model=settings.GEMINI_MODEL,
                contents=contents)).model_dump(exclude_none=True, exclude={"sdk_http_response"})

    async def sdk_text():
        return await client.aio.models.generate_content(model=settings.GEMINI_MODEL, contents='Reply OK.')

    async def sdk_image():
        return await client.aio.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=contents,
        )

    async def rest_image():
        args = {'timeout': 40.0, 'event_hooks': {'request': [audit_request]}}
        if settings.FORCE_IPV6:
            args['transport'] = httpx.AsyncHTTPTransport(local_address='::')
        async with httpx.AsyncClient(**args) as session:
            response = await session.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent',
                headers={'x-goog-api-key': settings.GEMINI_API_KEY},
                json={'contents': [{'role': 'user', 'parts': [
                    {'inlineData': {'mimeType': 'image/png', 'data': base64.b64encode(image).decode('ascii')}},
                    {'text': prompt},
                ]}]},
            )
            if response.is_error:
                raise RuntimeError(f'HTTP {response.status_code}: {response.text}')
            return response.json()

    for label, probe in [('countTokens text', count_text), ('countTokens image+text', count_image),
                         ('SDK text', sdk_text), ('SDK image, no config', sdk_image), ('REST image, no config', rest_image)]:
        try:
            result = await asyncio.wait_for(probe(), timeout=45)
            text = json.dumps(result) if isinstance(result, dict) else str(result.text)
            print(label, 'OK:', redact_secrets(text)[:500], flush=True)
        except Exception as exc:
            print(label, 'ERROR:', redact_secrets(f'{type(exc).__name__}: {exc}'), flush=True)
            log_api_error(exc, model=settings.GEMINI_MODEL, operation=label)
            if getattr(exc, 'code', None) in (401, 403):
                print('Stopping: authentication/permission failure.', flush=True)
                break
    await client.aio.aclose()


if __name__ == '__main__':
    asyncio.run(main())
