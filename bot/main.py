r"""
Главный модуль Telegram-бота для обработки тендеров.
Запуск: py bot/main.py
...
"""

import asyncio
from pathlib import Path
from collections import defaultdict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from config.settings import (
    TELEGRAM_TOKEN, TENDER_GROUP_ID, check_settings,
)
from bot.database import (
    set_pool, init_db, get_or_create_user,
    create_tender_for_thread, update_tender_analysis,
    add_tender_document, get_tender_documents, get_tender_by_thread,
    set_summary_message_id,
)
from bot.parser import extract_text
from bot.ai_analyzer import analyze_tender_document, merge_analyses

# ---------------------- НАСТРОЙКИ ----------------------
ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".txt", ".xlsx", ".xlsm",
    ".zip", ".png", ".jpg", ".jpeg",
}

# Задержка перед обработкой медиагруппы (ждём, пока Telegram соберёт все файлы)
MEDIA_GROUP_DELAY = 2.0

# ---------------------- КАРТОЧКА ТЕНДЕРА ----------------------
def build_tender_card(analysis: dict) -> str:
    """Собирает читаемое сообщение-карточку из словаря анализа."""
    lines = []
    if analysis.get("tender_name"):
        lines.append(f"📌 <b>{analysis['tender_name']}</b>")
    if analysis.get("subject"):
        lines.append(f"📋 Предмет: {analysis['subject']}")
    if analysis.get("nmck"):
        lines.append(f"💰 НМЦ: {analysis['nmck']:,.2f} руб.")
    if analysis.get("delivery_deadline"):
        lines.append(f"⏳ Срок поставки: {analysis['delivery_deadline']}")
    if analysis.get("region"):
        lines.append(f"📍 Регион: {analysis['region']}")
    if analysis.get("purchase_type"):
        lines.append(f"⚙️ Тип закупки: {analysis['purchase_type']}")
    if analysis.get("classification"):
        lines.append(f"📦 Категория: {analysis['classification']}")
    if analysis.get("items"):
        lines.append("🛒 Позиции:")
        for idx, item in enumerate(analysis["items"], 1):
            name = item.get("name", "—")
            qty = item.get("quantity")
            unit = item.get("unit")
            parts = [f"  {idx}. {name}"]
            if qty is not None:
                parts.append(f"— {qty}")
                if unit:
                    parts.append(f" {unit}")
            lines.append(" ".join(parts))
    if analysis.get("summary"):
        lines.append(f"\n📝 {analysis['summary']}")
    if not lines:
        lines.append("Нет данных.")
    return "\n".join(lines)


# ---------------------- ОБРАБОТЧИКИ ----------------------
async def process_documents(bot: Bot, message: Message, files: list[dict]):
    """
    Основная логика обработки загруженных документов:
    - сохранение на диск,
    - извлечение текста,
    - AI-анализ каждого файла,
    - сохранение в БД,
    - объединение результатов и обновление карточки тендера.
    """
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    user_id = message.from_user.id

    # Убедимся, что пользователь есть в БД
    await get_or_create_user(user_id, message.from_user.full_name)

    # Создаём или получаем тендер для этой темы
    tender_id = await create_tender_for_thread(chat_id, thread_id, user_id)

    analyses = []

    for file_info in files:
        file_path = file_info["path"]
        file_name = file_info["name"]
        file_ext = Path(file_name).suffix.lower()

        # 1. Извлечение текста
        try:
            text = await asyncio.to_thread(extract_text, file_path, file_ext)
        except Exception as e:
            print(f"Не удалось извлечь текст из {file_name}: {e}")
            text = ""

        # 2. AI-анализ
        analysis = await analyze_tender_document(text) if text else {}
        analyses.append(analysis)

        # 3. Сохранение документа и анализа в БД
        await add_tender_document(
            tender_id=tender_id,
            file_name=file_name,
            file_path=file_path,
            extracted_text=text,
            analysis_json=analysis,
            is_useful=analysis.get("has_useful_data", False),
        )

    # 4. Объединение всех анализов и обновление тендера
    merged = merge_analyses(analyses)
    await update_tender_analysis(tender_id, merged)

    # 5. Формирование/обновление карточки в группе
    card_text = build_tender_card(merged)

    # Если уже было сообщение-карточка — редактируем, иначе шлём новое
    tender = await get_tender_by_thread(chat_id, thread_id)
    if tender and tender.get("summary_message_id"):
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=tender["summary_message_id"],
                text=card_text,
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"Не удалось отредактировать карточку: {e}")
            sent_msg = await bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=card_text,
                parse_mode="HTML",
            )
            await set_summary_message_id(tender_id, sent_msg.message_id)
    else:
        sent_msg = await bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=card_text,
            parse_mode="HTML",
        )
        await set_summary_message_id(tender_id, sent_msg.message_id)


# Обработчик одиночных файлов и фото (с проверкой группы)
async def handle_attachment(message: Message, bot: Bot):
    """Скачивает любой документ/фото и запускает обработку."""
    # Проверка группы
    if TENDER_GROUP_ID and message.chat.id != TENDER_GROUP_ID:
        return

    if message.document:
        file = message.document
    elif message.photo:
        file = message.photo[-1]
    else:
        return

    TEMP_DIR = Path("data/uploads")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # Скачиваем файл
    file_info = await bot.get_file(file.file_id)
    dest_path = TEMP_DIR / file.file_name if message.document else TEMP_DIR / f"{file.file_id}.jpg"
    await bot.download_file(file_info.file_path, destination=dest_path)

    await process_documents(
        bot=bot,
        message=message,
        files=[{"name": dest_path.name, "path": str(dest_path)}],
    )


# Буферизация медиагрупп
media_buffers = defaultdict(list)
media_timers = {}

async def flush_media_group(bot: Bot, chat_id: int, thread_id: int, user_id: int):
    """Отправляет накопленные файлы медиагруппы на обработку."""
    buffer_key = (chat_id, thread_id)
    files = media_buffers.pop(buffer_key, [])
    if not files:
        return

    # Создаём фейковое сообщение для передачи в process_documents
    class FakeMessage:
        pass
    fake_msg = FakeMessage()
    fake_msg.chat = type("obj", (object,), {"id": chat_id})
    fake_msg.message_thread_id = thread_id
    fake_msg.from_user = type("obj", (object,), {"id": user_id, "full_name": ""})

    await process_documents(bot=bot, message=fake_msg, files=files)

    # Очищаем таймер
    timer = media_timers.pop(buffer_key, None)
    if timer:
        timer.cancel()


async def on_media_group_message(message: Message, bot: Bot):
    """
    При получении первого файла медиагруппы ставим таймер.
    Все последующие файлы попадают в буфер.
    По истечении MEDIA_GROUP_DELAY вызывается flush.
    """
    # Проверка группы
    if TENDER_GROUP_ID and message.chat.id != TENDER_GROUP_ID:
        return

    chat_id = message.chat.id
    thread_id = message.message_thread_id
    user_id = message.from_user.id
    buffer_key = (chat_id, thread_id)

    # Скачиваем файл
    if message.document:
        file = message.document
    elif message.photo:
        file = message.photo[-1]
    else:
        return

    TEMP_DIR = Path("data/uploads")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    file_info = await bot.get_file(file.file_id)
    dest_path = TEMP_DIR / (file.file_name if message.document else f"{file.file_id}.jpg")
    await bot.download_file(file_info.file_path, destination=dest_path)

    # Кладём в буфер
    media_buffers[buffer_key].append({
        "name": dest_path.name,
        "path": str(dest_path),
    })

    # Если таймер уже есть — обновляем (сбрасываем)
    if buffer_key in media_timers:
        media_timers[buffer_key].cancel()
    loop = asyncio.get_event_loop()
    media_timers[buffer_key] = loop.call_later(
        MEDIA_GROUP_DELAY,
        lambda: asyncio.create_task(
            flush_media_group(bot, chat_id, thread_id, user_id)
        ),
    )


# ---------------------- КОМАНДЫ ----------------------
async def cmd_start(message: Message):
    """Обработчик /start."""
    await message.answer(
        "👋 Привет! Я бот для обработки тендеров.\n"
        "Отправьте мне документы (PDF, DOCX, XLSX, ZIP, фото) в эту тему, "
        "и я извлеку из них ключевые данные и создам сводную карточку."
    )


# ---------------------- ЗАПУСК ----------------------
async def main():
    check_settings()

    # Инициализация БД
    import bot.database as db
    db_pool = await db.create_pool()
    await db.set_pool(db_pool)
    await db.init_db()
    print("База данных готова")

    # Бот и диспетчер
    bot = Bot(token=TELEGRAM_TOKEN)
    dp = Dispatcher()

    # Регистрируем команды
    dp.message.register(cmd_start, Command("start"))

    # Обработчик одиночных документов и фото (не в составе медиагруппы)
    @dp.message(F.document | F.photo, ~F.media_group_id)
    async def single_attachment_handler(message: Message, bot: Bot = bot):
        await handle_attachment(message, bot)

    # Обработчик медиагрупп (альбомов)
    @dp.message(F.document | F.photo, F.media_group_id)
    async def media_group_handler(message: Message, bot: Bot = bot):
        await on_media_group_message(message, bot)

    print("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())