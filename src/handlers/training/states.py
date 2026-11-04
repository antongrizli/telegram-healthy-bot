"""FSM states for Training v2 workflows."""
from aiogram.fsm.state import State, StatesGroup


class TrainingFSM(StatesGroup):
    hub = State()
    goal = State()           # 🎯 выбор цели, принимает текст цели
    pick_machines = State()  # ручной выбор тренажёров
    plan = State()           # карточка плана
    live = State()           # живая запись, принимает сокращённый текст подходов
    live_custom = State()    # «✍️ Другое»
    draft = State()          # сценарий B, принимает многострочный текст
    fix_line = State()       # исправление строки черновика
    repeat = State()         # сценарий C
    onboarding = State()
    equipment_add = State()
    equipment_delete = State()
