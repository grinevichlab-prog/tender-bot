"""Анализ тендерной документации через YandexGPT."""

from __future__ import annotations

import json
import re

import aiohttp

from config.settings import YANDEX_FOLDER_ID, YANDEX_API_KEY, YANDEX_URL

SYSTEM_PROMPT = r'''Ты анализируешь ОДИН документ тендерной документации.
Твоя задача — извлечь только фактически подтверждённые данные. Ничего не додумывай.
Если значение отсутствует или его нельзя уверенно определить — верни null.
Верни строго JSON без markdown.

Особенно важно:
1. delivery_period — срок поставки/исполнения. Сохраняй исходную формулировку.
2. contract_validity — срок действия договора, это НЕ срок поставки.
3. penalties — условия ответственности за просрочку/неисполнение. Сохраняй исходный текст и, если возможно, числовую ставку.
4. nmck — НМЦК только если она явно указана в документе. Не вычисляй её самостоятельно.
5. items — конкретные позиции и фактическое количество из спецификации/ТЗ.
6. requirements — технические требования каждой позиции. Каждое требование должно содержать parameter, operator, value/min/max, unit, mandatory и raw_text.
7. Не превращай отсутствие характеристики в соответствие.
8. Не объединяй разные позиции только потому, что их названия похожи.

Допустимые операторы: =, >, >=, <, <=, RANGE.

Формат:
{
  "has_useful_data": true,
  "tender_name": string|null,
  "subject": string|null,
  "items": [
    {
      "name": string,
      "quantity": number|null,
      "unit": string|null,
      "requirements": [
        {
          "parameter": string,
          "operator": "="|">"|">="|"<"|"<="|"RANGE",
          "value": number|string|null,
          "min": number|string|null,
          "max": number|string|null,
          "unit": string|null,
          "mandatory": true|false,
          "raw_text": string
        }
      ]
    }
  ],
  "nmck": number|null,
  "delivery_period": {
    "raw_text": string,
    "value": number|null,
    "unit": "WORKING_DAYS"|"CALENDAR_DAYS"|"MONTHS"|"FIXED_DATE"|null,
    "from_event": string|null,
    "date": "YYYY-MM-DD"|null
  }|null,
  "contract_validity": string|null,
  "penalties": [
    {
      "type": string,
      "raw_text": string,
      "rate": number|null,
      "rate_unit": string|null,
      "fixed_amount": number|null,
      "currency": string|null
    }
  ],
  "region": string|null,
  "purchase_type": string|null,
  "classification": "ТОВАР"|"СМЕШАННЫЙ"|"УСЛУГИ"|null,
  "summary": string|null
}'''

FALLBACK_RESULT = {
    "has_useful_data": False,
    "tender_name": None,
    "subject": None,
    "items": [],
    "nmck": None,
    "delivery_period": None,
    "contract_validity": None,
    "penalties": [],
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
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    start, end = raw.find("{"), raw.rfind("}")
    return raw[start:end + 1] if start >= 0 and end > start else raw


def _normalize_result(result: dict) -> dict:
    merged = FALLBACK_RESULT.copy()
    if not isinstance(result, dict):
        return merged
    merged.update(result)
    merged["items"] = result.get("items") or []
    merged["penalties"] = result.get("penalties") or []
    return merged


async def analyze_tender_document(text: str) -> dict:
    if not text or not text.strip():
        return FALLBACK_RESULT.copy()

    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt",
        "completionOptions": {"stream": False, "temperature": 0.1, "maxTokens": 2500},
        "messages": [
            {"role": "system", "text": SYSTEM_PROMPT},
            {"role": "user", "text": truncate_text(text)},
        ],
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.post(YANDEX_URL, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    print(f"YandexGPT API error {resp.status}: {await resp.text()}", flush=True)
                    return FALLBACK_RESULT.copy()
                data = await resp.json()
                content = data["result"]["alternatives"][0]["message"]["text"].strip()
                try:
                    result = json.loads(_clean_json(content))
                except json.JSONDecodeError:
                    print(f"YandexGPT returned invalid JSON: {content[:1000]}", flush=True)
                    return FALLBACK_RESULT.copy()
                return _normalize_result(result)
    except Exception as exc:
        print(f"Unexpected error in analyze_tender_document: {exc}", flush=True)
        return FALLBACK_RESULT.copy()


def _same_item(a: dict, b: dict) -> bool:
    def tokens(name: str) -> set[str]:
        value = re.sub(r"[^\w\s.-]", " ", (name or "").lower())
        result = set(value.split())
        return result - FILLER_WORDS

    ta, tb = tokens(a.get("name")), tokens(b.get("name"))
    if not ta or not tb:
        return (a.get("name") or "").strip().lower() == (b.get("name") or "").strip().lower()
    overlap = len(ta & tb) / min(len(ta), len(tb))
    return overlap >= 0.85


def _merge_items(analyses: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for analysis in analyses:
        for item in analysis.get("items") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            current = {
                "name": str(item["name"]).strip(),
                "quantity": item.get("quantity"),
                "unit": item.get("unit"),
                "requirements": item.get("requirements") or [],
            }
            existing = next((x for x in merged if _same_item(x, current)), None)
            if existing is None:
                merged.append(current)
                continue
            if existing.get("quantity") is None and current.get("quantity") is not None:
                existing["quantity"] = current["quantity"]
            if not existing.get("unit") and current.get("unit"):
                existing["unit"] = current["unit"]
            # Требования объединяем без удаления конфликтующих значений.
            existing["requirements"].extend(current["requirements"])
    return merged


def merge_analyses(analyses: list[dict]) -> dict:
    merged = FALLBACK_RESULT.copy()
    useful = [a for a in analyses if a.get("has_useful_data")]
    source = useful or analyses
    merged["has_useful_data"] = bool(useful)
    merged["items"] = _merge_items(source)

    for field in ["tender_name", "subject", "region", "purchase_type", "classification", "summary", "contract_validity"]:
        candidates = [a.get(field) for a in source if a.get(field)]
        if candidates:
            merged[field] = max(candidates, key=lambda x: len(str(x)))

    nmck_values = [a.get("nmck") for a in source if a.get("nmck") is not None]
    if nmck_values:
        # Если значения отличаются, не выбираем большее/меньшее молча.
        unique = {str(v) for v in nmck_values}
        merged["nmck"] = nmck_values[0] if len(unique) == 1 else None
        if len(unique) > 1:
            merged["nmck_conflicts"] = nmck_values

    periods = [a.get("delivery_period") for a in source if a.get("delivery_period")]
    if periods:
        serialized = {json.dumps(p, sort_keys=True, ensure_ascii=False) for p in periods}
        merged["delivery_period"] = periods[0] if len(serialized) == 1 else None
        if len(serialized) > 1:
            merged["delivery_period_conflicts"] = periods

    penalties = []
    seen = set()
    for a in source:
        for p in a.get("penalties") or []:
            raw = str(p.get("raw_text") or "").strip()
            if raw and raw not in seen:
                seen.add(raw)
                penalties.append(p)
    merged["penalties"] = penalties
    return merged
