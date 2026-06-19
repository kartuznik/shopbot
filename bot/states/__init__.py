from aiogram.fsm.state import State, StatesGroup


class ProductStates(StatesGroup):
    name = State()
    description = State()
    price = State()
    photo_id = State()


class OrderStates(StatesGroup):
    quantity = State()
    confirm = State()
