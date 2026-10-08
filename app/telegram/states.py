from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    balance = State()
    markets = State()


class ResetPaper(StatesGroup):
    confirm = State()
    balance = State()
