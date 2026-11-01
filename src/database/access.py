"""Durable, race-safe access requests, independent of the registration wizard."""
from datetime import datetime, UTC

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from src.config import settings
from src.database.models import User, UserAccess


async def has_access(db, user_id, user=None):
    user = user if user is not None else await db.get(User, user_id)
    if user is not None and user.is_blocked:
        return False
    if user_id in settings.ADMIN_USER_IDS or (user is not None and user.is_admin):
        return True
    admission = await db.get(UserAccess, user_id)
    if admission is not None:
        return admission.status == 'approved'
    # Pre-existing profiles are grandfathered; new profiles are gated before setup.
    return user is not None


async def request_access(db, telegram_user, language):
    record = await db.get(UserAccess, telegram_user.id)
    if record:
        return record, False
    record = UserAccess(telegram_id=telegram_user.id, username=telegram_user.username,
        display_name=telegram_user.full_name[:128], language=language, status='pending')
    db.add(record)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        record = await db.get(UserAccess, telegram_user.id)
        if record is None:
            raise
        return record, False
    return record, True


async def decide_access(db, user_id, admin_id, approve):
    result = await db.execute(update(UserAccess).where(
        UserAccess.telegram_id == user_id, UserAccess.status == 'pending').values(
        status='approved' if approve else 'rejected', decided_by=admin_id,
        decided_at=datetime.now(UTC).replace(tzinfo=None)))
    await db.commit()
    return result.rowcount == 1


async def pending_access(db, page=1):
    return list((await db.scalars(select(UserAccess).where(UserAccess.status == 'pending')
        .order_by(UserAccess.created_at, UserAccess.telegram_id).offset((page - 1) * 10).limit(10))).all())


async def admin_recipients(db):
    admins = (await db.scalars(select(User).where(User.is_admin.is_(True), User.is_blocked.is_(False)))).all()
    result = {user.telegram_id: user.language for user in admins}
    for user_id in settings.ADMIN_USER_IDS:
        user = await db.get(User, user_id)
        if user is None or not user.is_blocked:
            result[user_id] = user.language if user else 'en'
    return result


async def pending_access_page(db, page=1, page_size=5):
    total = await pending_access_count(db)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, pages))
    requests = list((await db.scalars(select(UserAccess).where(UserAccess.status == 'pending')
        .order_by(UserAccess.created_at, UserAccess.telegram_id)
        .offset((page - 1) * page_size).limit(page_size))).all())
    return dict(requests=requests, total=total, pages=pages, page=page)


async def pending_access_count(db):
    from sqlalchemy import func
    return await db.scalar(select(func.count()).select_from(UserAccess).where(UserAccess.status == 'pending'))
