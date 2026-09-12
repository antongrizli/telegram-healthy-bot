from aiogram import Router, F
from aiogram.types import CallbackQuery
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.utils.i18n_locales import get_text

router = Router()


@router.callback_query(F.data.startswith('medtake:'))
async def mark_intake(callback: CallbackQuery):
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, callback.from_user.id)
        if not user or user.is_blocked:
            await callback.answer('Access denied', show_alert=True)
            return
        try:
            _, row_id, status = callback.data.split(':')
            saved = await crud.mark_medication_intake(db, user.telegram_id, int(row_id), status)
        except (ValueError, TypeError):
            saved = False
        if not saved:
            await callback.answer(get_text('med_missing', user.language), show_alert=True)
            return
        status_text = get_text(f'med_{status}', user.language)
        await callback.answer(f'{status_text} ✓')
        await callback.message.edit_text(
            f'{callback.message.text}\n\n✅ {status_text}', reply_markup=None, parse_mode=None)


from datetime import datetime, UTC
import base64
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from src.services import medications as meds, rate_limiter
from src.utils.i18n_locales import LOCALES
from src.config import settings


class MedicationSetup(StatesGroup):
    name = State()
    time = State()
    end = State()


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows])


def tr(key, lang):
    return get_text('med_' + key, lang)


async def show_library(message, uid, lang, page=0):
    async with AsyncSessionLocal() as db:
        items = await crud.list_medications(db, uid)
        reminders = await crud.list_medication_reminders(db, uid)
    rows = [[('➕ ' + tr('add', lang), 'med:new')]]
    entries = [(m.name, f'med:item:{m.id}') for m in items]
    entries += [(f'🕒 {r.medication.name} · {r.reminder_time:%H:%M}', f'med:reminder:{r.id}') for r in reminders]
    rows += [[entry] for entry in entries[page*15:(page+1)*15]]
    nav=[]
    if page: nav.append((tr('back',lang),f'med:page:{page-1}'))
    if (page+1)*15 < len(entries): nav.append((tr('next',lang),f'med:page:{page+1}'))
    if nav: rows.append(nav)
    markup=keyboard(rows)
    markup.inline_keyboard.append([InlineKeyboardButton(text=tr('statistics',lang),
        web_app=WebAppInfo(url=f'{settings.WEBAPP_URL}?tab=medications'))])
    await message.answer(tr('library',lang)+' / '+tr('schedules',lang),reply_markup=markup)


@router.message(F.text.in_({d['btn_medications'] for d in LOCALES.values()}))
async def open_medications(message: Message, state: FSMContext, user_language: str = 'en'):
    await state.clear()
    await show_library(message,message.from_user.id,user_language)


async def days_prompt(message, state, lang):
    data=await state.get_data()
    days=data.get('med_days',list(range(7)))
    rows=[[(tr('daily',lang),'med:daily'),(tr('weekly',lang),'med:weekly')]]
    for day in range(7):
        rows.append([( ('✓ ' if day in days else '')+get_text(f'weekday_{day}',lang),f'med:day:{day}')])
    rows.append([(tr('next',lang),'med:time')])
    rows.append([(tr('cancel',lang),'med:home')])
    await message.answer('3 · '+tr('days',lang),reply_markup=keyboard(rows))


@router.callback_query(F.data.startswith('med:'))
@router.message(F.text.contains('\u2063med:'))
async def medication_navigation(event: Message | CallbackQuery, state: FSMContext):
    callback = event if getattr(event, 'data', None) else None
    message = callback.message if callback else event
    uid=event.from_user.id
    if callback:
        await callback.answer()
    async with AsyncSessionLocal() as db:
        user=await crud.get_user(db,uid)
        if not user or user.is_blocked:
            await message.answer('Access denied');return
        lang=user.language
        # Keep the message branch for users with a previously sent reply
        # keyboard, while new keyboards use hidden inline callback data.
        payload = callback.data if callback else getattr(message, 'text', '')
        if '\u2063' in payload:
            payload = payload.split('\u2063', 1)[1]
        parts = payload.split(':')
        if len(parts) < 2 or parts[0] != 'med':
            return
        action=parts[1]
        data=await state.get_data()
        if action in ('home','page'):
            await state.clear()
            await show_library(message,uid,lang,int(parts[2]) if action=='page' else 0)
        elif action=='new':
            await state.clear()
            await message.answer('1 · '+tr('title',lang),reply_markup=keyboard([
                [(tr(cat,lang),f'med:category:{cat}')] for cat in ('medicine','vitamin','other')]))
        elif action=='category' and parts[2] in ('medicine','vitamin','other'):
            await state.update_data(med_category=parts[2])
            await state.set_state(MedicationSetup.name)
            await message.answer('2 · '+tr('name',lang)+' / '+tr('photo',lang),
                reply_markup=keyboard([[(tr('cancel',lang),'med:home')]]))
        elif action=='item':
            item=next((m for m in await crud.list_medications(db,uid) if m.id==int(parts[2])),None)
            if not item: await message.answer(tr('missing',lang));return
            await message.answer(f'{item.name}\n{item.details}',parse_mode=None,reply_markup=keyboard([
                [(tr('add_schedule',lang),f'med:schedule:{item.id}')],
                [(tr('delete',lang),f'med:deleteitem:{item.id}')],[(tr('back',lang),'med:home')]]))
        elif action=='reminder':
            r=next((r for r in await crud.list_medication_reminders(db,uid) if r.id==int(parts[2])),None)
            if not r: await message.answer(tr('missing',lang));return
            days=', '.join(get_text(f'weekday_{d}',lang) for d in r.weekdays)
            await message.answer(f'{r.medication.name}\n{days} · {r.reminder_time:%H:%M} ({user.timezone})\n{r.dose}\n'+
                (f'{tr("until",lang)} {r.end_date}' if r.end_date else tr('unlimited',lang)),parse_mode=None,reply_markup=keyboard([
                [(tr('edit',lang),f'med:edit:{r.id}')],[(tr('delete',lang),f'med:deletereminder:{r.id}')],[(tr('back',lang),'med:home')]]))
        elif action in ('deleteitem','deletereminder'):
            await message.answer(tr('confirm_delete',lang),reply_markup=keyboard([
                [(tr('delete',lang),f'med:confirmdelete:{action}:{int(parts[2])}')],[(tr('cancel',lang),'med:home')]]))
        elif action=='confirmdelete':
            deleted=await crud.delete_medication_item(db,uid,int(parts[3]),reminder=parts[2]=='deletereminder')
            if not deleted: await message.answer(tr('missing',lang));return
            await show_library(message,uid,lang)
        elif action in ('schedule','edit'):
            await state.clear()
            if action=='edit':
                r=next((r for r in await crud.list_medication_reminders(db,uid) if r.id==int(parts[2])),None)
                if not r: await message.answer(tr('missing',lang));return
                await state.update_data(med_id=r.medication_id,med_reminder=r.id,med_days=r.weekdays,med_dose=r.dose)
            else:
                if not any(m.id==int(parts[2]) for m in await crud.list_medications(db,uid)):
                    await message.answer(tr('missing',lang));return
                await state.update_data(med_id=int(parts[2]),med_days=list(range(7)))
            await days_prompt(message,state,lang)
        elif action in ('day','daily','weekly','time') and data.get('med_id'):
            days=set(data.get('med_days',[]))
            if action=='day':
                day=int(parts[2])
                if day not in range(7): return
                days.symmetric_difference_update({day})
            elif action=='daily':days=set(range(7))
            elif action=='weekly':days=set()
            await state.update_data(med_days=sorted(days))
            if action=='time':
                if not days: await message.answer(tr('days',lang));return
                await state.set_state(MedicationSetup.time)
                await message.answer('4 · '+tr('time',lang)+f' (HH:MM, {user.timezone})')
            else:
                await days_prompt(message,state,lang)
        elif action=='unlimited' and data.get('med_time'):
            await finish_bot_schedule(message,state,user,None)
        else:
            await state.clear()
            await show_library(message,uid,lang)


@router.message(MedicationSetup.name)
async def medication_name(message: Message,state: FSMContext,user_language: str='en'):
    data=await state.get_data()
    if not data.get('med_category'):
        await state.clear();await show_library(message,message.from_user.id,user_language);return
    if message.photo:
        image=await message.bot.download(message.photo[-1])
        raw=image.read()
        if len(raw)>4*1024*1024:
            await message.answer(tr('error',user_language));return
        async with AsyncSessionLocal() as db:
            await rate_limiter.add_to_queue(db,message.from_user.id,message.chat.id,'medication_photo',
                {'image':base64.b64encode(raw).decode(),'mime_type':'image/jpeg','bot_category':data['med_category']})
        await state.clear()
        await message.answer(tr('queued',user_language));return
    try:
        values=meds.medication_values({'name':message.text,'category':data['med_category'],'details':''})
    except ValueError:
        await message.answer(tr('name',user_language)+' (1–200)');return
    async with AsyncSessionLocal() as db:
        item=await crud.save_medication(db,message.from_user.id,values)
    await state.clear();await state.update_data(med_id=item.id,med_days=list(range(7)))
    await days_prompt(message,state,user_language)


@router.message(MedicationSetup.time)
async def medication_time(message: Message,state: FSMContext,user_language: str='en'):
    import re
    from datetime import time
    try:
        if not message.text or not re.fullmatch(r'\d{2}:\d{2}',message.text):raise ValueError()
        time.fromisoformat(message.text)
    except ValueError:
        await message.answer(tr('time',user_language)+' (HH:MM)');return
    await state.update_data(med_time=message.text)
    await state.set_state(MedicationSetup.end)
    await message.answer('5 · '+tr('end',user_language)+' (YYYY-MM-DD)',reply_markup=keyboard([
        [(tr('unlimited',user_language),'med:unlimited')],[(tr('cancel',user_language),'med:home')]]))


async def finish_bot_schedule(message,state,user,end):
    data=await state.get_data()
    try:
        values=meds.reminder_values(dict(medication_id=data.get('med_id'),weekdays=data.get('med_days'),
            time=data.get('med_time'),end_date=end,dose=data.get('med_dose','')),datetime.now(meds.user_zone(user)).date())
    except (ValueError,TypeError):
        await message.answer(tr('end',user.language)+' (YYYY-MM-DD)');return
    async with AsyncSessionLocal() as db:
        await meds.materialize_intakes(db,user,datetime.now(UTC))
        item=await crud.save_medication_reminder(db,user.telegram_id,values,data.get('med_reminder'))
    await state.clear()
    await message.answer(tr('saved' if item else 'missing',user.language))
    await show_library(message,user.telegram_id,user.language)


@router.message(MedicationSetup.end)
async def medication_end(message: Message,state: FSMContext):
    async with AsyncSessionLocal() as db:
        user=await crud.get_user(db,message.from_user.id)
    if not message.text:
        await message.answer(tr('end',user.language)+' (YYYY-MM-DD)');return
    await finish_bot_schedule(message,state,user,message.text)


@router.callback_query(F.data.startswith('medphoto:'))
@router.message(F.text.contains('\u2063medphoto:'))
async def confirm_photo(event: Message | CallbackQuery,state: FSMContext):
    callback = event if getattr(event, 'data', None) else None
    message = callback.message if callback else event
    if callback:
        await callback.answer()
    async with AsyncSessionLocal() as db:
        user=await crud.get_user(db,event.from_user.id)
        if not user or user.is_blocked:
            await message.answer('Access denied');return
        payload = callback.data if callback else message.text.split('\u2063', 1)[1]
        item=await crud.confirm_medication_photo(db,user.telegram_id,int(payload.split(':')[1]))
    if not item:
        await message.answer(tr('missing',user.language));return
    await state.clear();await state.update_data(med_id=item.id,med_days=list(range(7)))
    await message.answer(tr('saved',user.language))
    await days_prompt(message,state,user.language)


async def send_photo_result(bot, item, lang):
    result=item.payload['result']
    rows=[]
    if result.get('name'):
        rows.append([(tr('save',lang),f'medphoto:{item.id}')])
    rows.append([(tr('edit',lang),f'med:category:{item.payload["bot_category"]}')])
    rows.append([(tr('cancel',lang),'med:home')])
    await bot.send_message(item.chat_id,
        f'{result.get("name", "")}\n{result.get("details", "")}\n\n'+tr('verify',lang),
        parse_mode=None,reply_markup=keyboard(rows))
