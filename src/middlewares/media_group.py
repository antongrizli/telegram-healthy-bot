import asyncio
from typing import Any, Awaitable, Callable, Dict, List
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

class MediaGroupMiddleware(BaseMiddleware):
    def __init__(self, latency: float = 0.2):
        self.latency = latency
        self.cache: Dict[str, List[Message]] = {}
        self.lock = asyncio.Lock()
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if not isinstance(event, Message) or not event.media_group_id:
            return await handler(event, data)

        async with self.lock:
            # If this is the first message of a media group, initialize list
            is_first = event.media_group_id not in self.cache
            if is_first:
                self.cache[event.media_group_id] = []
            self.cache[event.media_group_id].append(event)

        if is_first:
            # Wait for all other messages of the media group to arrive
            await asyncio.sleep(self.latency)
            
            async with self.lock:
                messages = self.cache.pop(event.media_group_id, [])
                
            # Sort messages by message_id to ensure order
            messages.sort(key=lambda m: m.message_id)
            
            # Put the list of messages in data
            data["album"] = messages
            
            # Call the handler once with the first message (carrying the album in data)
            return await handler(event, data)
        
        # Subsequent messages of the same media group are ignored
        return None
