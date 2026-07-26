r"""
Главный модуль Telegram-бота для обработки тендеров.
Запуск: py bot/main.py
Требуется заполненный .env (TELEGRAM_TOKEN, YANDEX_API_KEY, DATABASE_URL, ...)
"""

import json
import asyncio
from pathlib import Path
from collections import defaultdict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from config.settings import (
    TELEGRAM_TOKEN, TENDER_GROUP_ID, check_settings,
)
import bot.database as db
from bot.parser import extract_text
from bot.ai_analyzer import analyze_tender_document, merge_analyses

# ---------------------- НАСТРОЙКИ ----------------------
ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".txt", ".xlsx", ".xlsm",
    ".zip", ".png", ".jpg", ".jpeg",
}

MEDIA_GROUP_DELAY = 2.0

# ---------------------- КАРТОЧКА ТЕНДЕРА ----------------------
def build_tender_card(analysis: dict) -> str:
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
    return "\n".join(lines) if lines else "Нет данных."


# ---------------------- ОБРАБОТЧИКИ ----------------------
async def process_documents(bot: Bot, message: Message, files: list[dict]):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    telegram_user_id = message.from_user.id

    internal_user_id = await db.get_or_create_user(telegram_user_id, message.from_user.full_name)

    # chat_id и thread_id передаём как строки (в БД теперь TEXT)
    tender_id = await db.create_tender_for_thread(str(chat_id), str(thread_id), internal_user_id)

    for file_info in files:
        file_path = file_info["path"]
        file_name = file_info["name"]
        file_ext = Path(file_name).suffix.lower()

       print(f"[process_documents] обрабатываю файл {file_name}", flush=True)
        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(extract_text, file_path, file_ext),
                timeout=90,
            )
        except asyncio.TimeoutError:
            print(f"[process_documents] ТАЙМАУТ при извлечении текста из {file_name} (90 сек)", flush=True)
            text = ""
        except Exception as e:
            print(f"[process_documents] Не удалось извлечь текст из {file_name}: {e}", flush=True)
            text = ""

        print(f"[process_documents] текст из {file_name} готов, символов: {len(text)}", flush=True)

        try:
            analysis = await asyncio.wait_for(
                analyze_tender_document(text), timeout=60
            ) if text else {}
        except asyncio.TimeoutError:
            print(f"[process_documents] ТАЙМАУТ при анализе {file_name} через YandexGPT", flush=True)
            analysis = {}

        print(f"[process_documents] анализ {file_name} готов: has_useful_data={analysis.get('has_useful_data')}", flush=True)

        await db.add_tender_document(
            tender_id=tender_id,
            file_name=file_name,
            file_path=file_path,
            extracted_text=text,
            analysis_json=analysis,
            is_useful=analysis.get("has_useful_data", False),
        )

    # Берём ВСЕ документы этого тендера (а не только присланные сейчас),
    # чтобы карточка учитывала данные из всех файлов темы, а не перезатирала их
    all_docs = await db.get_tender_documents(tender_id)
    all_analyses = []
    for doc in all_docs:
        raw = doc.get("analysis_json")
        if not raw:
            continue
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        all_analyses.append(parsed)

    merged = merge_analyses(all_analyses)
    await db.update_tender_analysis(tender_id, merged)

    card_text = build_tender_card(merged)

    tender = await db.get_tender_by_thread(str(chat_id), str(thread_id))
    if tender and tender.get("summary_message_id"):
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=tender["summary_message_id"],
                text=card_text,
                parse_mode="HTML",
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                print(f"Не удалось отредактировать карточку: {e}")
                sent_msg = await bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=thread_id,
                    text=card_text,
                    parse_mode="HTML",
                )
                await db.set_summary_message_id(tender_id, sent_msg.message_id)
    else:
        sent_msg = await bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=card_text,
            parse_mode="HTML",
        )
        await db.set_summary_message_id(tender_id, sent_msg.message_id)


async def handle_attachment(message: Message, bot: Bot):
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

    file_info = await bot.get_file(file.file_id)
    dest_path = TEMP_DIR / file.file_name if message.document else TEMP_DIR / f"{file.file_id}.jpg"
    await bot.download_file(file_info.file_path, destination=dest_path)

    await process_documents(
        bot=bot,
        message=message,
        files=[{"name": dest_path.name, "path": str(dest_path)}],
    )


media_buffers = defaultdict(list)
media_timers = {}

async def flush_media_group(bot: Bot, chat_id: int, thread_id: int, user_id: int):
    buffer_key = (chat_id, thread_id)
    files = media_buffers.pop(buffer_key, [])
    if not files:
        return

    class FakeMessage:
        pass
    fake_msg = FakeMessage()
    fake_msg.chat = type("obj", (object,), {"id": chat_id})
    fake_msg.message_thread_id = thread_id
    fake_msg.from_user = type("obj", (object,), {"id": user_id, "full_name": ""})

    await process_documents(bot=bot, message=fake_msg, files=files)

    timer = media_timers.pop(buffer_key, None)
    if timer:
        timer.cancel()


async def on_media_group_message(message: Message, bot: Bot):
    if TENDER_GROUP_ID and message.chat.id != TENDER_GROUP_ID:
        return

    chat_id = message.chat.id
    thread_id = message.message_thread_id
    user_id = message.from_user.id
    buffer_key = (chat_id, thread_id)

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

    media_buffers[buffer_key].append({
        "name": dest_path.name,
        "path": str(dest_path),
    })

    if buffer_key in media_timers:
        media_timers[buffer_key].cancel()
    loop = asyncio.get_event_loop()
    media_timers[buffer_key] = loop.call_later(
        MEDIA_GROUP_DELAY,
        lambda: asyncio.create_task(
            flush_media_group(bot, chat_id, thread_id, user_id)
        ),
    )


async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я бот для обработки тендеров.\n"
        "Отправьте мне документы (PDF, DOCX, XLSX, ZIP, фото) в эту тему, "
        "и я извлеку из них ключевые данные и создам сводную карточку."
    )


# ---------------------- ЗАПУСК ----------------------
async def main():
    check_settings()

    db_pool = await db.create_pool()
    await db.set_pool(db_pool)
    await db.init_db()
    print("База данных готова")

    bot = Bot(token=TELEGRAM_TOKEN)
    dp = Dispatcher()

    dp.message.register(cmd_start, Command("start"))

    @dp.message(F.document | F.photo, ~F.media_group_id)
    async def single_attachment_handler(message: Message, bot: Bot = bot):
        await handle_attachment(message, bot)

    @dp.message(F.document | F.photo, F.media_group_id)
    async def media_group_handler(message: Message, bot: Bot = bot):
        await on_media_group_message(message, bot)

    print("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
