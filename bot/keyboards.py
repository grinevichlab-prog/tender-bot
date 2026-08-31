from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📤 Загрузить тендер")],
            [KeyboardButton(text="📋 Мои тендеры"), KeyboardButton(text="👥 Поставщики")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="❓ Помощь")]
        ],
        resize_keyboard=True
    )

def tender_actions(tender_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Просмотр позиций", callback_data=f"view_{tender_id}")],
        [InlineKeyboardButton(text="🔍 Найти модели", callback_data=f"search_{tender_id}")],
        [InlineKeyboardButton(text="💼 Генерация КП", callback_data=f"cp_{tender_id}")],
        [InlineKeyboardButton(text="📥 Экспорт", callback_data=f"export_{tender_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{tender_id}")]
    ])

def item_actions(tender_id: int, item_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Модели", callback_data=f"models_{tender_id}_{item_id}")],
        [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_{tender_id}_{item_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"view_{tender_id}")]
    ])

def model_select(tender_id: int, item_id: int, models: list[dict]):
    buttons = []
    for i, m in enumerate(models[:10]):
        name = f"{m.get('manufacturer', '?')} {m.get('model', '?')}"[:40]
        buttons.append([InlineKeyboardButton(
            text=f"{i+1}. {name}",
            callback_data=f"select_{tender_id}_{item_id}_{i}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"view_{tender_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def supplier_actions(supplier_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_supplier_{supplier_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_supplier_{supplier_id}")],
        [InlineKeyboardButton(text="🔙 К списку", callback_data="suppliers_list")]
    ])

def confirm_cp(tender_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_cp_{tender_id}")],
        [InlineKeyboardButton(text="✏️ Изменить условия", callback_data=f"edit_cp_{tender_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_cp_{tender_id}")]
    ])

def export_format(tender_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Excel", callback_data=f"export_excel_{tender_id}")],
        [InlineKeyboardButton(text="📄 Word", callback_data=f"export_word_{tender_id}")],
        [InlineKeyboardButton(text="📋 PDF", callback_data=f"export_pdf_{tender_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"view_{tender_id}")]
    ])
