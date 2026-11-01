"""Build and inspect multimodal requests without logging image or prompt data."""
import json
import logging

from google.genai import types

from src.utils.log_manager import redact_secrets

logger = logging.getLogger(__name__)


def image_part(data: bytes) -> types.Part:
    if not isinstance(data, bytes) or not data:
        raise ValueError('Image attachment is empty or is not binary data')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        mime = 'image/png'
    elif data.startswith(b'\xff\xd8\xff'):
        mime = 'image/jpeg'
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        mime = 'image/webp'
    else:
        raise ValueError('Unsupported image format; expected JPEG, PNG, or WebP bytes')
    return types.Part.from_bytes(data=data, mime_type=mime)


def food_contents(images: list[bytes], texts: list[str | None]) -> list[types.Content]:
    parts = [image_part(image) for image in images]
    parts.extend(types.Part.from_text(text=text) for text in texts if text)
    return [types.Content(role='user', parts=parts)]


def log_api_error(exc: Exception, *, model: str, operation: str, config=None):
    response = getattr(exc, 'response', None)
    try:
        body = response.text if response is not None else None
    except (AttributeError, RuntimeError):
        body = None
    if not isinstance(body, str) or not body:
        details = getattr(exc, 'details', None)
        body = json.dumps(details, ensure_ascii=False, default=str) if details is not None else str(exc)
    settings = json.dumps(config.model_dump(exclude_none=True), default=str, ensure_ascii=False) if config is not None else '{}'
    # Preserve the entire Response, including fieldViolations/details, but redact credentials.
    logger.error('AI API error operation=%s model=%s generationConfig=%s Response=%s',
                 operation, model, redact_secrets(settings), redact_secrets(body))
