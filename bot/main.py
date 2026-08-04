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
from bot.supplier_search import search_organizations, try_extract_email

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
    if analysis.get("contract_validity"):
        lines.append(f"📄 Срок действия договора: {analysis['contract_validity']}")
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
            specs = item.get("specs")

            qty_str = ""
            if qty is not None:
                qty_str = f" — {qty}"
                if unit:
                    qty_str += f" {unit}"

            lines.append(f"  <b>{idx}. {name}</b>{qty_str}")
            if specs:
                lines.append(f"      <i>{specs}</i>")
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
                    except
