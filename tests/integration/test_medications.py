import base64
import json
from datetime import datetime, date, time, timedelta, UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select, func
from src.database import crud
from src.database.models import Medication, MedicationReminder, MedicationIntake, AiRequestQueue
from src.services import medications as meds, gemini, rate_limiter, scheduler, briefing, gamification
from src.webapp import medications as api, server


async def user(db, uid=123, **kwargs):
    return await crud.create_or_update_user(db, uid, name='Test', sex='male', age=30,
        height_cm=180, weight_kg=80, activity_level='light', goal='maintain', language='ru',
        target_calories=2000, target_protein=100, target_fat=70, target_carb=250, **kwargs)


async def setup(db):
    u = await user(db)
    m = await crud.save_medication(db, 123, dict(name='Test product', category='vitamin', details='label'))
    r = await crud.save_medication_reminder(db, 123, dict(medication_id=m.id, weekdays=list(range(7)),
        reminder_time=time(9), start_date=date(2026,1,1), end_date=None, dose='user dose'))
    r.created_at = datetime(2026,1,1)
    await db.commit()
    return u, m, r


def patch_session(monkeypatch, module, db):
    class Session:
        async def __aenter__(self): return db
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(module, 'AsyncSessionLocal', Session)


@pytest.mark.asyncio
async def test_owner_crud_and_profile_cascade(db_session):
    u, m, r = await setup(db_session)
    await user(db_session, 456)
    assert await crud.list_medications(db_session, 456) == []
    assert await crud.list_medication_reminders(db_session, 456) == []
    assert await crud.save_medication(db_session, 456, dict(name='stolen', category='other', details=''), m.id) is None
    values=dict(medication_id=m.id, weekdays=[0], reminder_time=time(10), start_date=date.today(), end_date=None, dose='')
    assert await crud.save_medication_reminder(db_session, 456, values) is None
    assert await crud.save_medication_reminder(db_session, 456, values, r.id) is None
    assert not await crud.delete_medication_item(db_session,456,m.id)
    assert not await crud.delete_medication_item(db_session,456,r.id,True)
    intake=await crud.create_medication_occurrence(db_session,r,date(2026,1,1),datetime(2026,1,1,9))
    assert not await crud.mark_medication_intake(db_session,456,intake.id,'taken')
    assert await crud.mark_medication_intake(db_session,123,intake.id,'taken')
    assert not await crud.mark_medication_intake(db_session,123,intake.id,'taken')
    assert not await crud.mark_medication_intake(db_session,123,intake.id,'skipped')
    assert await crud.delete_user(db_session,123)
    for model in (Medication, MedicationReminder, MedicationIntake):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_medication_reminder_choice_is_shown_and_final(db_session, monkeypatch):
    from src.handlers import medications as handler

    _, _, reminder = await setup(db_session)
    intake = await crud.create_medication_occurrence(
        db_session, reminder, date(2026, 1, 1), datetime(2026, 1, 1, 9))
    patch_session(monkeypatch, handler, db_session)
    message = SimpleNamespace(text='💊 Test product\n🕒 09:00', edit_text=AsyncMock())
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=123), data=f'medtake:{intake.id}:taken',
        message=message, answer=AsyncMock())

    await handler.mark_intake(callback)

    callback.answer.assert_awaited_once_with('Принято ✓')
    message.edit_text.assert_awaited_once_with(
        '💊 Test product\n🕒 09:00\n\n✅ Принято', reply_markup=None, parse_mode=None)
    callback.data = f'medtake:{intake.id}:skipped'
    await handler.mark_intake(callback)
    assert message.edit_text.await_count == 1
    assert callback.answer.await_args.kwargs['show_alert'] is True


@pytest.mark.asyncio
async def test_occurrences_unique_and_delete_cascade(db_session):
    _, m, r = await setup(db_session)
    for _ in range(2):
        await crud.create_medication_occurrence(db_session,r,date(2026,1,1),datetime(2026,1,1,9))
    assert await db_session.scalar(select(func.count()).select_from(MedicationIntake)) == 1
    assert await crud.delete_medication_item(db_session,123,m.id)
    assert await db_session.scalar(select(func.count()).select_from(MedicationIntake)) == 0
    assert await crud.list_medication_reminders(db_session,123) == []


@pytest.mark.parametrize('patch', [dict(weekdays=[]),dict(weekdays=[True]),dict(weekdays=[7]),
    dict(weekdays='1'),dict(time='25:00'),dict(time='9:00'),dict(time='09:00:00'),
    dict(end_date='2025-01-01'),dict(end_date='nonsense'),dict(medication_id=True),dict(dose='x'*201)])
def test_invalid_reminder_inputs(patch):
    data=dict(medication_id=1,weekdays=[0],time='09:00',end_date=None)
    with pytest.raises((ValueError,TypeError)):
        meds.reminder_values({**data,**patch},date(2026,1,1))


def test_weekdays_end_date_timezone_dst():
    r=SimpleNamespace(start_date=date(2026,1,1),end_date=date(2026,3,29),weekdays=[6],reminder_time=time(2,30),created_at=None)
    zone=ZoneInfo('Europe/Berlin')
    assert meds.scheduled_instant(r,date(2026,3,28),zone) is None  # Saturday
    assert meds.scheduled_instant(r,date(2026,3,29),zone) is None  # Missing spring hour
    r.reminder_time=time(9)
    assert meds.scheduled_instant(r,date(2026,3,29),zone)==datetime(2026,3,29,7)  # inclusive end
    assert meds.scheduled_instant(r,date(2026,4,5),zone) is None
    r.end_date=None;r.reminder_time=time(2,30)
    assert meds.scheduled_instant(r,date(2026,10,25),zone)==datetime(2026,10,25,0,30)


@pytest.mark.asyncio
async def test_delivery_once_and_disabled_user(db_session,monkeypatch):
    u,_,r=await setup(db_session)
    now=datetime.now(UTC)
    r.reminder_time=now.replace(second=0,microsecond=0,tzinfo=None).time()
    await db_session.commit()
    patch_session(monkeypatch,meds,db_session)
    bot=SimpleNamespace(send_message=AsyncMock())
    u.notifications_enabled=False;await db_session.commit()
    await meds.send_medication_reminders(bot)
    bot.send_message.assert_not_awaited()
    u.notifications_enabled=True;await db_session.commit()
    await meds.send_medication_reminders(bot)
    await meds.send_medication_reminders(bot)
    assert bot.send_message.await_count==1
    assert bot.send_message.call_args.kwargs['parse_mode'] is None


@pytest.mark.asyncio
async def test_photo_queue_persists_result_without_saving_product(db_session,monkeypatch):
    await user(db_session)
    photo=AsyncMock(return_value={'name':'Read name','details':'Read label'})
    monkeypatch.setattr(gemini,'recognize_medication',photo)
    qid=await rate_limiter.add_to_queue(db_session,123,123,'medication_photo',
        {'image':base64.b64encode(b'image').decode(),'mime_type':'image/jpeg'})
    item=await crud.get_medication_photo_request(db_session,123,qid)
    assert await crud.get_medication_photo_request(db_session,456,qid) is None
    for _ in range(2):
        assert await rate_limiter.execute_queued_item(SimpleNamespace(id=1),MemoryStorage(),db_session,item)
    assert photo.await_count==1
    assert item.payload=={'result':{'name':'Read name','details':'Read label'}}
    assert await crud.list_medications(db_session,123)==[]


@pytest.mark.asyncio
async def test_api_full_flow_and_ownership(db_session,monkeypatch):
    await user(db_session)
    patch_session(monkeypatch,api,db_session)
    monkeypatch.setattr(api,'validate_init_data',lambda request:123)
    app=server.create_app(None)
    async with TestClient(TestServer(app)) as client:
        res=await client.post('/api/medications/items',json={'name':'Product','category':'medicine','details':''})
        assert res.status==201
        mid=(await res.json())['id']
        res=await client.post('/api/medications/reminders',json={'medication_id':mid,'weekdays':[0,2,4],'time':'09:00'})
        assert res.status==201
        rid=(await res.json())['id']
        res=await client.patch(f'/api/medications/reminders/{rid}',json={'medication_id':mid,'weekdays':[1],'time':'11:00'})
        assert res.status==200
        data=await (await client.get('/api/medications')).json()
        assert data['reminders'][0]['time']=='11:00'
        assert data['statistics']['total']==0  # no invented doses before creation
        await user(db_session,456)
        monkeypatch.setattr(api,'validate_init_data',lambda request:456)
        assert (await (await client.get('/api/medications')).json())['medications']==[]
        assert (await client.delete(f'/api/medications/items/{mid}')).status==404
        assert (await client.patch(f'/api/medications/reminders/{rid}',json={'medication_id':mid,'weekdays':[1],'time':'12:00'})).status==404
        u=await crud.get_user(db_session,456);u.is_blocked=True;await db_session.commit()
        assert (await client.get('/api/medications')).status==403
        assert (await client.post('/api/medications/items',json={})).status==403


@pytest.mark.asyncio
async def test_api_rejects_unsigned_auth():
    async with TestClient(TestServer(server.create_app(None))) as client:
        assert (await client.get('/api/medications')).status==401
        assert (await client.post('/api/medications/photos',json={})).status==401


@pytest.mark.asyncio
async def test_report_context_only_with_reminders(db_session,monkeypatch):
    _,m,r=await setup(db_session)
    assert (await meds.report_context(db_session,123))[0]['name']==m.name
    assert await meds.report_context(db_session,456)==[]
    await crud.delete_medication_item(db_session,123,r.id,True)
    assert await meds.report_context(db_session,123)==[]  # library alone is insufficient


@pytest.mark.asyncio
@pytest.mark.parametrize('report_type',['daily','weekly','monthly'])
async def test_report_prompts_include_conditional_section(monkeypatch,report_type):
    call=AsyncMock(return_value=SimpleNamespace(text='Report'))
    monkeypatch.setattr(gemini,'call_gemini_with_retry',call)
    await gemini.generate_report({'medications':[{'name':'Example'}]},[],[],report_type,'ru')
    prompt=call.call_args.kwargs['contents'][0]
    assert 'Example' in prompt and 'potential side effects' in prompt and 'NOT proof of ingestion' in prompt
    await gemini.generate_report({},[],[],report_type,'ru')
    assert 'Do not include a medication' in call.call_args.kwargs['contents'][0]


@pytest.mark.asyncio
async def test_photo_confirmation_is_owned_idempotent_and_survives_delete(db_session):
    await user(db_session)
    qid=await rate_limiter.add_to_queue(db_session,123,123,'medication_photo',
        {'bot_category':'medicine','result':{'name':'Package','details':'strength'}})
    assert await crud.confirm_medication_photo(db_session,456,qid) is None
    first=await crud.confirm_medication_photo(db_session,123,qid)
    again=await crud.confirm_medication_photo(db_session,123,qid)
    assert first.id==again.id
    assert len(await crud.list_medications(db_session,123))==1
    await crud.delete_medication_item(db_session,123,first.id)
    assert await crud.confirm_medication_photo(db_session,123,qid) is None


@pytest.mark.asyncio
async def test_bot_wizard_manual_weekdays_and_end(db_session,monkeypatch):
    from src.handlers import medications as handler
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    await user(db_session)
    patch_session(monkeypatch,handler,db_session)
    state=FSMContext(MemoryStorage(),StorageKey(bot_id=1,chat_id=123,user_id=123))
    msg=SimpleNamespace(answer=AsyncMock(),from_user=SimpleNamespace(id=123),text='Test name',photo=None)
    callback=SimpleNamespace(from_user=msg.from_user,message=msg,answer=AsyncMock(),data='med:category:vitamin')
    await handler.medication_navigation(callback,state)
    assert await state.get_state()==handler.MedicationSetup.name.state
    await handler.medication_name(msg,state,user_language='ru')
    assert (await crud.list_medications(db_session,123))[0].name=='Test name'
    for data in ['med:weekly','med:day:0','med:day:2','med:time']:
        callback.data=data
        await handler.medication_navigation(callback,state)
    msg.text='09:30'
    await handler.medication_time(msg,state,user_language='ru')
    assert await state.get_state()==handler.MedicationSetup.end.state
    callback.data='med:unlimited'
    await handler.medication_navigation(callback,state)
    r=(await crud.list_medication_reminders(db_session,123))[0]
    assert r.weekdays==[0,2] and r.reminder_time==time(9,30) and r.end_date is None
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_delivery_failure_does_not_duplicate_uncertain_send(db_session,monkeypatch):
    _,_,r=await setup(db_session)
    now=datetime.now(UTC)
    r.reminder_time=now.replace(second=0,microsecond=0,tzinfo=None).time()
    await db_session.commit()
    patch_session(monkeypatch,meds,db_session)
    bot=SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError()))
    await meds.send_medication_reminders(bot)
    await meds.send_medication_reminders(bot)
    assert bot.send_message.await_count==1
    rows=await crud.get_medication_intakes(db_session,123,now.replace(hour=0,minute=0,second=0,microsecond=0,tzinfo=None),now.replace(tzinfo=None))
    assert rows[0].delivery_status=='uncertain' and rows[0].status=='unmarked'


@pytest.mark.asyncio
async def test_morning_and_card_notes_receive_medication_instructions(db_session,monkeypatch):
    await setup(db_session)
    call=AsyncMock(return_value=SimpleNamespace(text='Note'))
    monkeypatch.setattr(gemini,'call_gemini_with_retry',call)
    await briefing.generate_morning_briefing(db_session,123)
    assert 'Test product' in call.call_args.kwargs['contents'][0]
    card={'overall_score':50,'categories':{k:{'score':50} for k in ('nutrition','consistency','weight_progress')}}
    await gamification.gemini_generate_card_note({'medications':[{'name':'Card product'}]},card,'ru')
    assert 'Card product' in call.call_args.kwargs['contents'][0]
    await gamification.gemini_generate_card_note({},card,'ru')
    assert 'Do not include a medication' in call.call_args.kwargs['contents'][0]
