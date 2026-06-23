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


class BroadcastStates(StatesGroup):
    title = State()
    message_type = State()
    content = State()
    schedule_type = State()
    scheduled_at = State()
    confirm = State()


class SheetsStates(StatesGroup):
    spreadsheet_id = State()
    sheet_name = State()
    report_type = State()
    auto_sync = State()
