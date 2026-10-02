"""Reserve provider quota before each attempt, across concurrent callers."""
import asyncio
from weakref import WeakKeyDictionary
from src.database.connection import AsyncSessionLocal
from src.database import crud

_locks = WeakKeyDictionary()


class AIQuotaExceeded(Exception):
    def __init__(self, scope, retry_after):
        super().__init__("AI quota temporarily exhausted")
        self.scope = scope
        self.retry_after = retry_after


async def reserve_attempt(user_id=None, request_type="ai"):
    loop = asyncio.get_running_loop()
    lock = _locks.setdefault(loop, asyncio.Lock())
    async with lock:
        async with AsyncSessionLocal() as db:
            exhausted = await crud.reserve_ai_attempt(db, user_id, request_type)
    if exhausted:
        raise AIQuotaExceeded(*exhausted)
