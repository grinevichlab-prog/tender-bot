r"""Главный модуль Telegram-бота для обработки тендеров."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config.settings import TELEGRAM_TOKEN, TENDER_GROUP_ID, check_settings
import bot.database as db
from bot.parser import extract_text, extract_texts_from_zip
from bot.ai_analyzer import analyze_tender_document, merge_analyses
from bot.model_search import search_models
from bot.model_matcher import match_model
from bot.web_supplier_search import search_suppliers_web

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".xlsx", ".xlsm", ".xls", ".zip", ".png", ".jpg", ".jpeg"}
MEDIA_GROUP_DELAY = 2.0

media_buffers = defaultdict(list)
media_timers = {}
pending_supplier_results: dict[tuple, list[dict]] = {}


def _as_list(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return []
    return value if isinstance(value, list) else []


def _format_delivery_period(period: dict | None) -> str | None:
    if not period:
        return None
    raw = period.get("raw_text")
    if raw:
        return str(raw)
    value = period.get("value")
    unit = period.get("unit")
    date = period.get("date")
    if date:
        return str(date)
    if value is not None and unit:
        return f"{value} {unit}"
    return None


def build_tender_card(analysis: dict) -> str:
    lines = []
    if analysis.get("tender_name"):
        lines.append(f"📌 <b>{analysis['tender_name']}</b>")
    if analysis.get("subject"):
        lines.append(f"📋 Предмет: {analysis['subject']}")
    if analysis.get("nmck") is not None:
        lines.append(f"💰 НМЦК: {analysis['nmck']:,.2f} руб.")
    elif analysis.get("nmck_conflicts"):
        values = ", ".join(f"{v:,.2f}" for v in analysis["nmck_conflicts"])
        lines.append(f"⚠️ НМЦК: конфликт значений ({values} руб.)")
    period = analysis.get("delivery_period")
    if period:
        lines.append(f"🚚 Срок поставки: {_format_delivery_period(period)}")
    elif analysis.get("delivery_period_conflicts"):
        variants = "; ".join(str(x.get("raw_text")) for x in analysis["delivery_period_conflicts"] if x.get("raw_text"))
        lines.append(f"⚠️ Срок поставки: конфликт ({variants})")
    if analysis.get("contract_validity"):
        lines.append(f"📄 Срок действия договора: {analysis['contract_validity']}")
    penalties = analysis.get("penalties") or []
    if penalties:
        lines.append("⚠️ Ответственность:")
        for p in penalties[:5]:
            lines.append(f"  • {p.get('raw_text') or p.get('type') or 'условие'}")
    if analysis.get("region"):
        lines.append(f"📍 Регион: {analysis['region']}")
    if analysis.get("purchase_type"):
        lines.append(f"⚙️ Тип закупки: {analysis['purchase_type']}")
    if analysis.get("classification"):
        lines.append(f"📦 Категория: {analysis['classification']}")

    items = _as_list(analysis.get("items"))
    if items:
        lines.append("🛒 Позиции:")
        for idx, item in enumerate(items[:25], 1):
            name = item.get("name", "—")
            qty = item.get("quantity")
            unit = item.get("unit")
            qty_str = f" — {qty} {unit or ''}" if qty is not None else ""
            lines.append(f"  <b>{idx}. {name}</b>{qty_str}")
            reqs = item.get("requirements") or []
            if reqs:
                lines.append(f"      требований: {len(reqs)}")

    if analysis.get("summary"):
        summary = str(analysis["summary"])
        lines.append(f"\n📝 {summary[:800]}{'…' if len(summary) > 800 else ''}")

    text = "\n".join(lines) if lines else "Нет данных."
    return text[:3800] + ("\n\n…(текст обрезан)" if len(text) > 3800 else "")


def models_keyboard(items: list[dict]) -> InlineKeyboardMarkup | None:
    if not items:
        return None
    rows = []
    for item in items[:20]:
        rows.append([InlineKeyboardButton(
            text=f"🔎 {item['position_number']}. {item['name'][:45]}",
            callback_data=f"model_item:{item['id']}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def model_results_keyboard(models: list[dict], item_id: int) -> InlineKeyboardMarkup | None:
    if not models:
        return None
    rows = []
    for model in models[:12]:
        status = model.get("match_status") or "UNKNOWN"
        icon = {"MATCH": "✅", "NEEDS_CLARIFICATION": "⚠️", "UNKNOWN": "❓", "REJECTED": "❌"}.get(status, "❓")
        label = f"{icon} {model.get('model', 'без модели')[:45]}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"model_select:{item_id}:{model['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def process_documents(bot: Bot, message: Message, files: list[dict]):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    internal_user_id = await db.get_or_create_user(message.from_user.id, message.from_user.full_name)
    tender_id = await db.create_tender_for_thread(str(chat_id), str(thread_id), internal_user_id)

    for file_info in files:
        file_path, file_name = file_info["path"], file_info["name"]
        file_ext = Path(file_name).suffix.lower()
        print(f"[process_documents] обрабатываю {file_name}", flush=True)

        if file_ext == ".zip":
            try:
                zip_items = await asyncio.wait_for(asyncio.to_thread(extract_texts_from_zip, file_path), timeout=120)
            except Exception as exc:
                print(f"[process_documents] ZIP error: {exc}", flush=True)
                zip_items = []
            for item in zip_items:
                sub_name, sub_text = f"{file_name}/{item['name']}", item.get("text", "")
                analysis = await analyze_tender_document(sub_text) if sub_text else {}
                await db.add_tender_document(tender_id, sub_name, file_path, sub_text, analysis, analysis.get("has_useful_data", False))
            continue

        try:
            text = await asyncio.wait_for(asyncio.to_thread(extract_text, file_path, file_ext), timeout=90)
        except Exception as exc:
            print(f"[process_documents] extraction error: {exc}", flush=True)
            text = ""
        analysis = await analyze_tender_document(text) if text else {}
        await db.add_tender_document(tender_id, file_name, file_path, text, analysis, analysis.get("has_useful_data", False))

    all_docs = await db.get_tender_documents(tender_id)
    analyses = []
    for doc in all_docs:
        raw = doc.get("analysis_json")
        if raw:
            analyses.append(json.loads(raw) if isinstance(raw, str) else raw)

    merged = merge_analyses(analyses)
    await db.update_tender_analysis(tender_id, merged)
    await db.sync_tender_items(tender_id, merged.get("items") or [])

    card_text = build_tender_card(merged)
    tender = await db.get_tender_by_thread(str(chat_id), str(thread_id))
    old_message_id = tender.get("summary_message_id") if tender else None
    if old_message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_message_id)
        except Exception:
            pass

    sent = await bot.send_message(chat_id=chat_id, message_thread_id=thread_id, text=card_text, parse_mode="HTML")
    await db.set_summary_message_id(tender_id, sent.message_id)

    items = await db.get_tender_items(tender_id)
    keyboard = models_keyboard(items)
    if keyboard:
        await bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text="После анализа выберите позицию, для которой нужно найти конкретные модели: ",
            reply_markup=keyboard,
        )


async def handle_attachment(message: Message, bot: Bot):
    if TENDER_GROUP_ID and message.chat.id != TENDER_GROUP_ID:
        return
    file = message.document if message.document else (message.photo[-1] if message.photo else None)
    if not file:
        return
    temp_dir = Path("data/uploads")
    temp_dir.mkdir(parents=True, exist_ok=True)
    tg_file = await bot.get_file(file.file_id)
    dest = temp_dir / (file.file_name if message.document else f"{file.file_id}.jpg")
    await bot.download_file(tg_file.file_path, destination=dest)
    await process_documents(bot, message, [{"name": dest.name, "path": str(dest)}])


async def flush_media_group(bot: Bot, chat_id: int, thread_id: int, user_id: int):
    key = (chat_id, thread_id)
    files = media_buffers.pop(key, [])
    media_timers.pop(key, None)
    if not files:
        return
    class FakeMessage: pass
    fake = FakeMessage()
    fake.chat = type("obj", (), {"id": chat_id})
    fake.message_thread_id = thread_id
    fake.from_user = type("obj", (), {"id": user_id, "full_name": ""})
    await process_documents(bot, fake, files)


async def on_media_group_message(message: Message, bot: Bot):
    if TENDER_GROUP_ID and message.chat.id != TENDER_GROUP_ID:
        return
    file = message.document if message.document else (message.photo[-1] if message.photo else None)
    if not file:
        return
    temp_dir = Path("data/uploads")
    temp_dir.mkdir(parents=True, exist_ok=True)
    tg_file = await bot.get_file(file.file_id)
    dest = temp_dir / (file.file_name if message.document else f"{file.file_id}.jpg")
    await bot.download_file(tg_file.file_path, destination=dest)
    key = (message.chat.id, message.message_thread_id)
    media_buffers[key].append({"name": dest.name, "path": str(dest)})
    if key in media_timers:
        media_timers[key].cancel()
    loop = asyncio.get_running_loop()
    media_timers[key] = loop.call_later( MEDIA_GROUP_DELAY, lambda: asyncio.create_task(flush_media_group(bot, key[0], key[1], message.from_user.id)))


async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Пришлите документы тендера в тему. Бот извлечёт ТЗ, сроки, НМЦК и позиции.\n\n"
        "После анализа можно выбрать позицию и найти конкретные модели.\n"
        "Для ручного поиска поставщиков: /поставщики_запрос текст"
    )


async def cmd_models(message: Message):
    if TENDER_GROUP_ID and message.chat.id != TENDER_GROUP_ID:
        return
    tender = await db.get_tender_by_thread(str(message.chat.id), str(message.message_thread_id))
    if not tender:
        await message.answer("Сначала пришлите документы тендера в эту тему.")
        return
    items = await db.get_tender_items(tender["id"])
    keyboard = models_keyboard(items)
    if not keyboard:
        await message.answer("В тендере пока нет распознанных позиций.")
        return
    await message.answer("Выберите позицию для поиска моделей:", reply_markup=keyboard)


async def callback_model_item(callback: CallbackQuery):
    try:
        item_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Некорректная позиция")
        return
    await callback.answer("Ищу модели...")
    async with db.pool.acquire() as conn:
        item_row = await conn.fetchrow("SELECT * FROM tender_items WHERE id=$1", item_id)
        if not item_row:
            await callback.message.answer("Позиция не найдена.")
            return
        tender = await conn.fetchrow("SELECT * FROM tenders WHERE id=$1", item_row["tender_id"])
    item = dict(item_row)
    requirements = item.get("requirements") or []
    if isinstance(requirements, str):
        requirements = json.loads(requirements)
    item["requirements"] = requirements
    region = tender["region"] if tender else None
    await callback.message.answer(f"🔎 Ищу модели для: {item['name']}...")
    found = await search_models(item, region=region, max_models=10)
    if not found:
        await callback.message.answer("Конкретных моделей с подтверждёнными данными не найдено. Попробуйте уточнить требования или искать вручную.")
        return

    saved = []
    for model in found:
        comparison = match_model(requirements, model.get("specifications") or {})
        model["match_status"] = comparison["status"]
        model["match_result"] = comparison
        model_id = await db.save_product_model(item_id, model)
        model["id"] = model_id
        saved.append(model)

    lines = [f"Найдено моделей: {len(saved)}", ""]
    for idx, model in enumerate(saved, 1):
        status = model["match_status"]
        icon = {"MATCH": "✅", "NEEDS_CLARIFICATION": "⚠️", "UNKNOWN": "❓", "REJECTED": "❌"}.get(status, "❓")
        price = f", {model['price']:,.2f} ₽" if model.get("price") is not None else ""
        lines.append(f"{icon} {idx}. <b>{model.get('manufacturer') or ''} {model.get('model')}</b>{price}")
        lines.append(f"   {status}")
        lines.append(f"   {model.get('source_url')}")
    await callback.message.answer("\n".join(lines)[:3800], parse_mode="HTML", reply_markup=model_results_keyboard(saved, item_id))


async def callback_model_select(callback: CallbackQuery):
    try:
        _, item_id_raw, model_id_raw = callback.data.split(":")
        item_id, model_id = int(item_id_raw), int(model_id_raw)
    except Exception:
        await callback.answer("Некорректный выбор")
        return
    await db.select_product_model(model_id, item_id)
    await callback.answer("Модель выбрана")
    models = await db.get_product_models(item_id)
    selected = next((m for m in models if m.get("id") == model_id), None)
    if not selected:
        await callback.message.answer("Модель не найдена.")
        return
    await callback.message.answer(
        f"✅ Выбрана модель: <b>{selected.get('manufacturer') or ''} {selected.get('model')}</b>\n"
        f"Статус соответствия ТЗ: <b>{selected.get('match_status')}</b>\n\n"
        "Поиск поставщиков для этой модели будет следующим этапом.",
        parse_mode="HTML",
    )


# ---------------------- ПОСТАВЩИКИ: текущий ручной режим ----------------------
def _build_supplier_query(tender: dict) -> str:
    items = _as_list(tender.get("items"))
    parts = []
    if items:
        parts.append(", ".join(i.get("name", "").strip() for i in items[:3] if i.get("name")))
    elif tender.get("subject"):
        parts.append(tender["subject"][:60])
    if tender.get("region"):
        parts.append(str(tender["region"]).split(",")[0].strip())
    return ", ".join(p for p in parts if p)


def _format_supplier_candidates(results: list[dict]) -> str:
    lines = ["Нашёл кандидатов — ответьте номерами через запятую, кого сохранить:", ""]
    for idx, org in enumerate(results, 1):
        line = f"{idx}. <b>{org['name']}</b>"
        if org.get("address"): line += f"\n    {org['address']}"
        if org.get("phone"): line += f"\n    ☎️ {org['phone']}"
        if org.get("email"): line += f"\n    ✉️ {org['email']}"
        if org.get("url"): line += f"\n    🌐 {org['url']}"
        lines.append(line)
    return "\n".join(lines)


async def cmd_find_suppliers(message: Message):
    if TENDER_GROUP_ID and message.chat.id != TENDER_GROUP_ID:
        return
    tender = await db.get_tender_by_thread(str(message.chat.id), str(message.message_thread_id))
    if not tender:
        await message.answer("Сначала пришлите документы тендера.")
        return
    query = _build_supplier_query(tender)
    if not query:
        await message.answer("Не смог собрать запрос.")
        return
    await message.answer(f"Ищу поставщиков по запросу: «{query}»...")
    results = await search_suppliers_web(query)
    if not results:
        await message.answer("Ничего не найдено.")
        return
    pending_supplier_results[(message.chat.id, message.message_thread_id)] = results
    await message.answer(_format_supplier_candidates(results), parse_mode="HTML")


async def cmd_find_suppliers_custom(message: Message):
    if TENDER_GROUP_ID and message.chat.id != TENDER_GROUP_ID:
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Пример: /поставщики_запрос ЭЦВ 6-10-110 Москва")
        return
    results = await search_suppliers_web(parts[1].strip())
    if not results:
        await message.answer("Ничего не найдено.")
        return
    pending_supplier_results[(message.chat.id, message.message_thread_id)] = results
    await message.answer(_format_supplier_candidates(results), parse_mode="HTML")


async def handle_supplier_selection(message: Message):
    key = (message.chat.id, message.message_thread_id)
    if key not in pending_supplier_results:
        return
    text = (message.text or "").strip()
    if not text or not all(c.isdigit() or c in ", " for c in text):
        return
    try:
        indices = [int(x.strip()) for x in text.split(",") if x.strip()]
    except ValueError:
        return
    results = pending_supplier_results[key]
    tender = await db.get_tender_by_thread(str(message.chat.id), str(message.message_thread_id))
    categories = [tender.get("classification")] if tender and tender.get("classification") else []
    saved = []
    for idx in indices:
        if 1 <= idx <= len(results):
            org = results[idx - 1]
            supplier_id = await db.add_supplier(org.get("name"), org.get("phone"), org.get("email"), tender.get("region") if tender else None, categories, org.get("url"))
            saved.append((org.get("name"), supplier_id))
    pending_supplier_results.pop(key, None)
    await message.answer("Сохранено:\n" + "\n".join(f"— {name}" for name, _ in saved) if saved else "Не удалось сохранить выбранные позиции.")


async def main():
    check_settings()
    db_pool = await db.create_pool()
    await db.set_pool(db_pool)
    await db.init_db()
    bot = Bot(token=TELEGRAM_TOKEN)
    dp = Dispatcher()

    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_models, Command("модели"))
    dp.message.register(cmd_find_suppliers, Command("поставщики"))
    dp.message.register(cmd_find_suppliers_custom, Command("поставщики_запрос"))
    dp.callback_query.register(callback_model_item, F.data.startswith("model_item:"))
    dp.callback_query.register(callback_model_select, F.data.startswith("model_select:"))
    dp.message.register(handle_supplier_selection, F.text)

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
