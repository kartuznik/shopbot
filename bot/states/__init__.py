from aiogram.fsm.state import State, StatesGroup


class ProductStates(StatesGroup):
    name = State()
    description = State()
    category_id = State()
    price = State()
    photo = State()


class OrderStates(StatesGroup):
    quantity = State()
    confirm = State()


class ReviewStates(StatesGroup):
    comment = State()


class DeliveryAddressStates(StatesGroup):
    address = State()
    zone = State()


class DeliveryZoneStates(StatesGroup):
    zone_name = State()
    zone_cost = State()
