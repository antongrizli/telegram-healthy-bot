"""Build admin reply keyboards with a fresh pending-admission count."""
from src.database import access
from src.keyboards import reply


async def admin_menu(language):
    from src.database.connection import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        count = await access.pending_access_count(db)
    return reply.get_admin_menu(language, count)


async def access_menu(language):
    from src.database.connection import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        count = await access.pending_access_count(db)
    return reply.get_access_menu(language, count)
