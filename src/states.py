"""Centralized FSM StatesGroup definitions."""
from aiogram.fsm.state import State, StatesGroup


class FoodLoggingState(StatesGroup):
    waiting_for_meal_type = State()
    waiting_for_input = State()
    waiting_for_confirm = State()
    waiting_for_correction = State()


class MealEditingState(StatesGroup):
    waiting_for_edit_text = State()
    waiting_for_edit_confirm = State()


class MealViewingState(StatesGroup):
    viewing = State()


class LocalCorrectionState(StatesGroup):
    value = State()
