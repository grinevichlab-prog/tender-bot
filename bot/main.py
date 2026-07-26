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
            await asyncio.wait_for(
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=tender["summary_message_id"],
                    text=card_text,
                    parse_mode="HTML",
                ),
                timeout=30,
            )
            print(f"[process_documents] карточка отредактирована", flush=True)
        except asyncio.TimeoutError:
            print(f"[process_documents] ТАЙМАУТ при редактировании сообщения в Telegram", flush=True)
        except Exception as e:
            if "message is not modified" not in str(e):
