"""
Модуль анализа тендерных документов через YandexGPT API.
"""

import json
import re
import aiohttp
from config.settings import YANDEX_FOLDER_ID, YANDEX_API_KEY, YANDEX_URL

SYSTEM_PROMPT = """Ты — ассистент по анализу тендерной документации (техническое задание, контракт, извещение о закупке, спецификация, обоснование НМЦК).
Это стандартная бизнес-задача извлечения деловых данных для системы автоматизации закупок юрлица, ничего конфиденциального.

Тебе присылают текст ОДНОГО документа тендера. Извлеки ключевые данные и верни СТРОГО JSON без пояснений, без markdown-обёрток.

ВАЖНО, различай два РАЗНЫХ срока — их часто путают:
- "delivery_deadline" — срок ПОСТАВКИ ТОВАРА / ВЫПОЛНЕНИЯ УСЛУГИ (когда именно товар должен быть поставлен или услуга оказана). Обычно указан в техническом задании или спецификации. Формат ГГГГ-ММ-ДД.
- "contract_validity" — срок ДЕЙСТВИЯ САМОГО ДОГОВОРА (до какой даты договор в целом действует, включая гарантийные обязательства). Обычно указан в разделе "Срок действия договора" проекта договора. Это НЕ то же самое, что срок поставки. Пиши как есть в тексте, например "до 31.12.2027" или "12 месяцев с момента подписания".
Если документ не содержит явного упоминания именно поставки товара с конкретной датой — НЕ заполняй delivery_deadline датой окончания договора. Оставь null.

Про ПОЗИЦИИ (items) — если в документе есть техническое задание или спецификация с точным перечнем и количеством — используй именно эти точные данные (наименование, количество, единица измерения). Указывай наименование без вводных слов вроде "или эквивалент", "аналог" — только суть товара. Не создавай несколько записей для одного и того же товара, даже если он упоминается в разных формулировках в этом же документе — объединяй в одну позицию.

Формат ответа (все поля обязательны, если данных нет — пиши null или пустой список, но ключ должен присутствовать всегда):
{
  "has_useful_data": true или false — есть ли в тексте реально полезные данные о предмете закупки, товарах, НМЦК, сроках. Если это пустой бланк, форма, доверенность без конкретики — false,
  "tender_name": "короткое название тендера, 3-6 слов" или null,
  "subject": "предмет закупки одним предложением" или null,
  "items": [{"name": "название позиции без 'или эквивалент'", "quantity": число или null, "unit": "единица измерения" или null}],
  "nmck": число без пробелов и валюты (например 1250000.50) или null,
  "delivery_deadline": "дата в формате ГГГГ-ММ-ДД срока ПОСТАВКИ ТОВАРА" или null,
  "contract_validity": "срок действия ДОГОВОРА как указано в тексте (строка)" или null,
  "region": "регион или город поставки" или null,
  "purchase_type": "тип закупки" или null,
  "classification": одно из: "ТОВАР", "СМЕШАННЫЙ", "УСЛУГИ", или null,
  "summary": "краткое описание сути закупки, 2-4 предложения"
}

Отвечай ТОЛЬКО валидным JSON, ничего больше."""

FALLBACK_RESULT = {
    "has_useful_data": False,
    "tender_name": None,
    "subject": None,
    "items": [],
    "nmck": None,
    "delivery_deadline": None,
    "contract_validity": None,
    "region": None,
    "purchase_type": None,
    "classification": None,
    "summary": None,
}

FILLER_WORDS = {"или", "эквивалент", "аналог", "шт", "шт.", "и", "либо"}


def truncate_text(text: str, max_len: int = 20000) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "…"


def _clean_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start:end + 1]
    return raw


def _tokenize_item_name(name: str) -> set:
    name = name.lower()
    name = re.sub(r"[^\w\s]", " ", name)
    tokens = set(name.split())
    tokens -= FILLER_WORDS
    return tokens


async def analyze_tender_document(text: str) -> dict:
    if not text or not text.strip():
        return FALLBACK_RESULT.copy()

    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt",
        "completionOptions": {
            "stream": False,
            "temperature": 0.1,
            "maxTokens": 1500,
        },
        "messages": [
            {"role": "system", "text": SYSTEM_PROMPT},
            {"role": "user", "text": truncate_text(text)},
        ],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(YANDEX_URL, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"YandexGPT API error {resp.status}: {error_text}", flush=True)
                    return FALLBACK_RESULT.copy()

                data = await resp.json()
                content = data["result"]["alternatives"][0]["message"]["text"].strip()

                try:
                    result = json.loads(_clean_json(content))
                except json.JSONDecodeError:
                    print(f"YandexGPT returned non-JSON after cleaning: {content}", flush=True)
                    return FALLBACK_RESULT.copy()

                merged = FALLBACK_RESULT.copy()
                merged.update(result)
                if merged.get("has_useful_data") is None:
                    merged["has_useful_data"] = False
                if merged.get("items") is None:
                    merged["items"] = []
                return merged

    except Exception as e:
        print(f"Unexpected error in analyze_tender_document: {e}", flush=True)
        return FALLBACK_RESULT.copy()


def _merge_items(source: list) -> list:
    """
    Объединяет позиции по смысловому совпадению (набор слов в названии),
    а не по точному совпадению строки — чтобы "Насос гидравлический X"
    и "Гидравлический насос X" считались одной и той же позицией.
    """
    merged_items = []  # список dict: name, tokens, quantity, unit

    for a in source:
        for item in a.get("items", []) or []:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            tokens = _tokenize_item_name(name)
            qty = item.get("quantity")
            unit = item.get("unit")

            match = None
            for existing in merged_items:
                if not tokens or not existing["tokens"]:
                    continue
                overlap = len(tokens & existing["tokens"])
                smaller = min(len(tokens), len(existing["tokens"]))
                if smaller > 0 and overlap / smaller >= 0.6:
                    match = existing
                    break

            if match:
                if match["quantity"] is None and qty is not None:
                    match["quantity"] = qty
                if match["unit"] is None and unit:
                    match["unit"] = unit
                if len(name) > len(match["name"]):
                    match["name"] = name
                    match["tokens"] = tokens
            else:
                merged_items.append({
                    "name": name, "tokens": tokens, "quantity": qty, "unit": unit,
                })

    return [
        {"name": i["name"], "quantity": i["quantity"], "unit": i["unit"]}
        for i in merged_items
    ]


def merge_analyses(analyses: list) -> dict:
    merged = FALLBACK_RESULT.copy()
    useful = [a for a in analyses if a.get("has_useful_data")]
    merged["has_useful_data"] = bool(useful)

    source = useful if useful else analyses

    merged["items"] = _merge_items(source)

    for field in ["tender_name", "subject", "region", "purchase_type", "classification", "summary", "contract_validity"]:
        candidates = [a.get(field) for a in source if a.get(field)]
        if candidates:
            merged[field] = max(candidates, key=lambda x: len(str(x)))

    deadlines = [a.get("delivery_deadline") for a in source if a.get("delivery_deadline")]
    if deadlines:
        merged["delivery_deadline"] = max(deadlines)

    nmck_values = [a.get("nmck") for a in source if a.get("nmck") is not None]
    if nmck_values:
        merged["nmck"] = max(nmck_values)

    return merged
