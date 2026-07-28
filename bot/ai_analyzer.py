"""
Модуль анализа тендерных документов через YandexGPT API.
"""

import json
import aiohttp
from config.settings import YANDEX_FOLDER_ID, YANDEX_API_KEY, YANDEX_URL

SYSTEM_PROMPT = """Ты — ассистент по анализу тендерной документации (техническое задание, контракт, извещение о закупке, спецификация, обоснование НМЦК).
Это стандартная бизнес-задача извлечения деловых данных для системы автоматизации закупок юрлица, ничего конфиденциального.

Тебе присылают текст документа. Извлеки ключевые данные и верни СТРОГО JSON без пояснений, без markdown-обёрток, без ```json оберток.

Формат ответа (все поля обязательны, если данных нет — пиши null или пустой список, но ключ должен присутствовать всегда):
{
  "has_useful_data": true или false — есть ли в тексте реально полезные данные о предмете закупки, товарах, НМЦК, сроках. Если это пустой бланк, форма, доверенность, договор без конкретных цифр — false,
  "tender_name": "короткое название тендера, 3-6 слов" или null,
  "subject": "предмет закупки одним предложением" или null,
  "items": [{"name": "название позиции", "quantity": число или null, "unit": "единица измерения" или null}],
  "nmck": число без пробелов и валюты (например 1250000.50) или null,
  "delivery_deadline": "дата в формате ГГГГ-ММ-ДД" или null,
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
    "region": None,
    "purchase_type": None,
    "classification": None,
    "summary": None,
}


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


def merge_analyses(analyses: list) -> dict:
    merged = FALLBACK_RESULT.copy()
    useful = [a for a in analyses if a.get("has_useful_data")]
    merged["has_useful_data"] = bool(useful)

    source = useful if useful else analyses

    items_dict = {}
    for a in source:
        for item in a.get("items", []) or []:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            qty = item.get("quantity")
            unit = item.get("unit")
            if name in items_dict:
                existing = items_dict[name]
                if qty is not None and existing["quantity"] is not None:
                    existing["quantity"] += qty
                else:
                    existing["quantity"] = existing["quantity"] or qty
                existing["unit"] = existing["unit"] or unit
            else:
                items_dict[name] = {"name": name, "quantity": qty, "unit": unit}
    merged["items"] = list(items_dict.values())

    for field in ["tender_name", "subject", "region", "purchase_type", "classification", "summary"]:
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
