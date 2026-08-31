from aiogram.fsm.state import State, StatesGroup

class SupplierStates(StatesGroup):
    waiting_name = State()
    waiting_inn = State()
    waiting_contact = State()
    waiting_region = State()
    waiting_margin = State()

class TenderStates(StatesGroup):
    waiting_region = State()
    editing_item = State()
    waiting_quantity = State()
    waiting_price = State()

class CPStates(StatesGroup):
    waiting_delivery_days = State()
    waiting_payment_terms = State()
    waiting_warranty = State()
    confirming = State()
