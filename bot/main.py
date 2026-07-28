r"""
Главный модуль Telegram-бота для обработки тендеров.
Запуск: py -m bot.main
Требуется заполненный .env (TELEGRAM_TOKEN, YANDEX_API_KEY, DATABASE_URL, ...)
"""

import asyncio
import json
from pathlib import Path
from collections import defaultdict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from config.settings import (
    TELEGRAM_TOKEN, TENDER_GROUP_ID, check_settings,
)
import bot.database as db
from bot.parser import extract_text, extract_texts_from_zip
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

    items = analysis.get("items") or []
    if items:
        max_items = 25
        lines.append("🛒 Позиции:")
        for idx, item in enumerate(items[:max_items], 1):
            name = item.get("name", "—")
            qty = item.get("quantity")
            unit = item.get("unit")
            parts = [f"  {idx}. {name}"]
            if qty is not None:
                parts.append(f"— {qty}")
                if unit:
                    parts.append(f" {unit}")
            lines.append(" ".join(parts))
        if len(items) > max_items:
            lines.append(f"  ...и ещё {len(items) - max_items} позиций (см. документы)")

    if analysis.get("summary"):
        summary = analysis["summary"]
        if len(summary) > 800:
            summary = summary[:800] + "…"
        lines.append(f"\n📝 {summary}")

    text = "\n".join(lines) if lines else "Нет данных."

    max_len = 3800
    if len(text) > max_len:
        text = text[:max_len] + "\n\n…(текст обрезан, полные данные в исходных файлах)"

    return text


# ---------------------- ОБРАБОТЧИКИ ----------------------
async def process_documents(bot: Bot, message: Message, files: list[dict]):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    telegram_user_id = message.from_user.id

    internal_user_id = await db.get_or_create_user(telegram_user_id, message.from_user.full_name)

    tender_id = await db.create_tender_for_thread(str(chat_id), str(thread_id), internal_user_id)

    for file_info in files:
        file_path = file_info["path"]
        file_name = file_info["name"]
        file_ext = Path(file_name).suffix.lower()

        print(f"[process_documents] обрабатываю файл {file_name}", flush=True)

       if file_ext == ".zip":
            try:
                zip_items = await asyncio.wait_for(
                    asyncio.to_thread(extract_texts_from_zip, file_path),
                    timeout=120,
                )
            except asyncio.TimeoutError:
                print(f"[process_documents] ТАЙМАУТ при распаковке архива {file_name}", flush=True)
                zip_items = []

            print(f"[process_documents] в архиве {file_name} найдено файлов: {len(zip_items)}", flush=True)

            for item in zip_items:
                sub_name = f"{file_name}/{item['name']}"
                sub_text = item["text"]
                print(f"[process_documents] анализирую {sub_name}, символов: {len(sub_text)}", flush=True)

                sub_analysis = {}
                if sub_text:
                    try:
                        sub_analysis = await asyncio.wait_for(
                            analyze_tender_document(sub_text), timeout=60
                        )
                    except asyncio.TimeoutError:
                        print(f"[process_documents] ТАЙМАУТ при анализе {sub_name}", flush=True)
                        sub_analysis = {}

                print(f"[process_documents] {sub_name}: has_useful_data={sub_analysis.get('has_useful_data')}", flush=True)

                try:
                    await asyncio.wait_for(
                        db.add_tender_document(
                            tender_id=tender_id,
                            file_name=sub_name,
                            file_path=file_path,
                            extracted_text=sub_text,
                            analysis_json=sub_analysis,
                            is_useful=sub_analysis.get("has_useful_data", False),
                        ),
                        timeout=30,
                    )
                except asyncio.TimeoutError:
                    print(f"[process_documents] ТАЙМАУТ при сохранении {sub_name} в БД", flush=True)

            continue

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

        analysis = {}
        if text:
            try:
                analysis = await asyncio.wait_for(
                    analyze_tender_document(text), timeout=60
                )
            except asyncio.TimeoutError:
                print(f"[process_documents] ТАЙМАУТ при анализе {file_name} через YandexGPT", flush=True)
                analysis = {}

        print(f"[process_documents] анализ {file_name} готов: has_useful_data={analysis.get('has_useful_data')}", flush=True)

        try:
            await asyncio.wait_for(
                db.add_tender_document(
                    tender_id=tender_id,
                    file_name=file_name,
                    file_path=file_path,
                    extracted_text=text,
                    analysis_json=analysis,
                    is_useful=analysis.get("has_useful_data", False),
                ),
                timeout=30,
            )
        except asyncio.TimeoutError:
            print(f"[process_documents] ТАЙМАУТ при сохранении документа {file_name} в БД", flush=True)

        print(f"[process_documents] документ {file_name} сохранён в БД", flush=True)

        analysis = {}
        if text:
            try:
                analysis = await asyncio.wait_for(
                    analyze_tender_document(text), timeout=60
                )
            except asyncio.TimeoutError:
                print(f"[process_documents] ТАЙМАУТ при анализе {file_name} через YandexGPT", flush=True)
                analysis = {}

        print(f"[process_documents] анализ {file_name} готов: has_useful_data={analysis.get('has_useful_data')}", flush=True)

        try:
            await asyncio.wait_for(
                db.add_tender_document(
                    tender_id=tender_id,
                    file_name=file_name,
                    file_path=file_path,
                    extracted_text=text,
                    analysis_json=analysis,
                    is_useful=analysis.get("has_useful_data", False),
                ),
                timeout=30,
            )
        except asyncio.TimeoutError:
            print(f"[process_documents] ТАЙМАУТ при сохранении документа {file_name} в БД", flush=True)

        print(f"[process_documents] документ {file_name} сохранён в БД", flush=True)

    print(f"[process_documents] все файлы обработаны, собираю карточку", flush=True)

    try:
        all_docs = await asyncio.wait_for(db.get_tender_documents(tender_id), timeout=30)
    except asyncio.TimeoutError:
        print(f"[process_documents] ТАЙМАУТ при чтении документов тендера из БД", flush=True)
        all_docs = []

    print(f"[process_documents] получено документов из БД: {len(all_docs)}", flush=True)

    all_analyses = []
    for doc in all_docs:
        raw = doc.get("analysis_json")
        if not raw:
            continue
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        all_analyses.append(parsed)

    merged = merge_analyses(all_analyses)
    print(f"[process_documents] merge_analyses готов", flush=True)

    try:
        await asyncio.wait_for(db.update_tender_analysis(tender_id, merged), timeout=30)
    except asyncio.TimeoutError:
        print(f"[process_documents] ТАЙМАУТ при записи анализа тендера в БД", flush=True)

    print(f"[process_documents] update_tender_analysis готов", flush=True)

    card_text = build_tender_card(merged)
    print(f"[process_documents] размер карточки: {len(card_text)} символов", flush=True)

    try:
        tender = await asyncio.wait_for(
            db.get_tender_by_thread(str(chat_id), str(thread_id)), timeout=30
        )
    except asyncio.TimeoutError:
        print(f"[process_documents] ТАЙМАУТ при чтении тендера по теме из БД", flush=True)
        tender = None

    print(f"[process_documents] карточка готова, summary_message_id={tender.get('summary_message_id') if tender else None}", flush=True)

    if tender and tender.get("summary_message_id"):
        try:
            print(f"[process_documents] отправляю edit_message_text...", flush=True)
            await asyncio.wait_for(
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=tender["summary_message_id"],
                    text=card_text,
                    parse_mode="HTML",
                ),
                timeout=20,
            )
            print(f"[process_documents] карточка отредактирована", flush=True)
        except asyncio.TimeoutError:
            print(f"[process_documents] ТАЙМАУТ при редактировании сообщения в Telegram (20 сек)", flush=True)
        except Exception as e:
            print(f"[process_documents] ошибка при редактировании: {e}", flush=True)
            if "message is not modified" not in str(e):
                try:
                    sent_msg = await asyncio.wait_for(
                        bot.send_message(
                            chat_id=chat_id,
                            message_thread_id=thread_id,
                            text=card_text,
                            parse_mode="HTML",
                        ),
                        timeout=20,
                    )
                    await db.set_summary_message_id(tender_id, sent_msg.message_id)
                    print(f"[process_documents] новое сообщение отправлено взамен", flush=True)
                except Exception as e2:
                    print(f"[process_documents] не удалось отправить новое сообщение: {e2}", flush=True)
    else:
        try:
            print(f"[process_documents] отправляю send_message...", flush=True)
            sent_msg = await asyncio.wait_for(
                bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=thread_id,
                    text=card_text,
                    parse_mode="HTML",
                ),
                timeout=20,
            )
            await db.set_summary_message_id(tender_id, sent_msg.message_id)
            print(f"[process_documents] новая карточка отправлена", flush=True)
        except Exception as e:
            print(f"[process_documents] не удалось отправить карточку: {e}", flush=True)


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
