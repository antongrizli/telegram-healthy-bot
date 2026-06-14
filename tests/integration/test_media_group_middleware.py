import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message
from src.middlewares.media_group import MediaGroupMiddleware

pytestmark = pytest.mark.asyncio

async def test_middleware_single_message():
    # Setup middleware and mock handler
    middleware = MediaGroupMiddleware(latency=0.01)
    handler = AsyncMock(return_value="handled")
    
    # Message without media_group_id
    message = MagicMock(spec=Message)
    message.media_group_id = None
    
    data = {}
    result = await middleware(handler, message, data)
    
    assert result == "handled"
    handler.assert_called_once_with(message, data)
    assert "album" not in data

async def test_middleware_media_group():
    # Setup middleware with short latency for testing
    middleware = MediaGroupMiddleware(latency=0.05)
    handler = AsyncMock(return_value="handled_group")
    
    # Create 3 messages with the same media_group_id
    msg1 = MagicMock(spec=Message)
    msg1.media_group_id = "group_123"
    msg1.message_id = 101
    
    msg2 = MagicMock(spec=Message)
    msg2.media_group_id = "group_123"
    msg2.message_id = 102
    
    msg3 = MagicMock(spec=Message)
    msg3.media_group_id = "group_123"
    msg3.message_id = 103
    
    data1 = {}
    data2 = {}
    data3 = {}
    
    # Call the middleware concurrently to simulate fast incoming Telegram updates
    results = await asyncio.gather(
        middleware(handler, msg1, data1),
        middleware(handler, msg2, data2),
        middleware(handler, msg3, data3)
    )
    
    # Handler should have been called exactly once
    assert handler.call_count == 1
    
    # The first processed call returns the handler result, others return None
    assert "handled_group" in results
    assert None in results
    assert results.count(None) == 2
    
    # Find which data dictionary was passed to the handler
    called_data = handler.call_args[0][1]
    assert "album" in called_data
    album = called_data["album"]
    assert len(album) == 3
    # Check sorting order by message_id
    assert album[0].message_id == 101
    assert album[1].message_id == 102
    assert album[2].message_id == 103
