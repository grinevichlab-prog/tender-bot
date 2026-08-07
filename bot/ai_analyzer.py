"""
Модуль анализа тендерных документов через YandexGPT API.

Этап 1 V2: извлечение структурированных сроков, НМЦК, штрафов,
позиций и требований с сохранением исходных формулировок.
"""

import json
import re
import aiohttp
from config.settings import YANDEX_FOLDER_ID, YANDEX_API_KEY, YANDEX_URL

SYSTEM_PROMPT = r"""Ты — ассистент по анализу тендерной документации.
Тебе передают текст ОДНОГО документа закупки. Верни СТРОГО валидный JSON без markdown и пояснений.

Главный принцип: не угадывай и не исправляй документацию. Если значение не найдено явно — null/[] .
Если в документе есть несколько разных значений одного параметра, НЕ выбирай одно из них: верни все найденные значения в поле conflicts.
Документация закупки важнее любой внешней информации — внешние знания не используй.

ОБЯЗАТЕЛЬНО РАЗЛИЧАЙ:
1) contract_validity — срок действия самого контракта/договора.
2) delivery_period — срок поставки товара/выполнения услуги.
3) delivery_deadline — только конкретная календарная дата поставки, если она прямо указана.
Не превращай «30 рабочих дней с даты заключения» в календарную дату.

Для delivery_period используй:
{
  "raw_text": "исходная формулировка",
  "value": число или null,
  "unit": "WORKING_DAYS" | "CALENDAR_DAYS" | "WEEKS" | "MONTHS" | "YEARS" | null,
  "from_event": "CONTRACT_SIGNING" | "CUSTOMER_REQUEST" | "OTHER" | null,
  "type": "PERIOD_FROM_EVENT" | "FIXED_DATE" | "OTHER" | null
}

Для contract_validity аналогично сохраняй исходную формулировку и структурированный тип, если он очевиден.

ШТРАФЫ:
Извлекай только то, что прямо указано в документации. Может быть несколько видов ответственности.
Для каждого:
{
  "type": "DELIVERY_DELAY" | "CONTRACT_BREACH" | "OTHER",
  "raw_text": "исходная формулировка",
  "rate": число или null,
  "rate_unit": "PERCENT_PER_DAY" | "PERCENT" | null,
  "fixed_amount": число или null,
  "currency": "RUB" | null,
  "base": "CONTRACT_PRICE" | "OVERDUE_OBLIGATION" | "OTHER" | null
}
Не рассчитывай денежную сумму штрафа самостоятельно.

НМЦК:
Извлекай значение именно НМЦК/начальной максимальной цены контракта, если оно есть в документе. Не подменяй её ценой отдельной позиции, суммой коммерческих предложений или другой стоимостью.

ПОЗИЦИИ:
Извлекай точные позиции спецификации и количество. Не объединяй разные позиции только потому, что названия похожи. «или эквивалент», «аналог» и вводные слова не включай в название.
Для каждой позиции извлекай технические требования.

ТРЕБОВАНИЯ:
Для каждого параметра:
{
  "parameter": "название параметра",
  "operator": "=" | ">" | ">=" | "<" | "<=" | "RANGE" | "IN" | "TEXT",
  "value": число/строка/null,
  "min": число/null,
  "max": число/null,
  "unit": "единица"/null,
  "mandatory": true/false,
  "raw_text": "фрагмент требования"
}
Если параметр указан как диапазон, используй RANGE и min/max.
Если требование текстовое и его нельзя корректно представить числом, используй TEXT и сохрани точную формулировку.

ИСТОЧНИКИ:
Для каждого важного извлечённого значения, если возможно, укажи source_evidence:
{
  "field": "nmck|contract_validity|delivery_period|delivery_deadline|penalty|item|requirement",
  "item_index": число или null,
  "raw_text": "точная или максимально близкая цитата из документа"
}
Не придумывай номера страниц — в переданном тексте их может не быть.

Формат:
{
  "has_useful_data": true,
  "tender_name": null,
  "subject": null,
  "nmck": null,
  "delivery_deadline": null,
  "delivery_period": null,
  "contract_validity": null,
  "contract_validity_details": null,
  "region": null,
  "purchase_type": null,
  "classification": null,
  "summary": null,
  "items": [
    {
      "name": "...",
      "quantity": 1,
      "unit": "шт",
      "requirements": []
    }
  ],
  "penalties": [],
  "conflicts": [],
  "source_evidence": []
}
Все ключи обязательны. Если данных нет — null или пустой список.
"""

FALLBACK_RESULT = {
    "has_useful_data": False,
    "tender_name": None,
    "subject": None,
    "nmck": None,
    "delivery_deadline": None,
    "delivery_period": None,
    "contract_validity": None,
    "contract_validity_details": None,
    "region": None,
    "purchase_type": None,
    "classification": None,
    "summary": None,
    "items": [],
    "penalties": [],
    "conflicts": [],
    "source_evidence": [],
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
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start:end + 1]
    return raw


def _normalize_result(result: dict) -> dict:
    merged = FALLBACK_RESULT.copy()
    if isinstance(result, dict):
        merged.update(result)

    if not isinstance(merged.get("items"), list):
        merged["items"] = []
    if not isinstance(merged.get("penalties"), list):
        merged["penalties"] = []
    if not isinstance(merged.get("conflicts"), list):
        merged["conflicts"] = []
    if not isinstance(merged.get("source_evidence"), list):
        merged["source_evidence"] = []

    cleaned_items = []
    for item in merged["items"]:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            continue
        item = dict(item)
        item["name"] = str(item["name"]).strip()
        if not isinstance(item.get("requirements"), list):
            item["requirements"] = []
        cleaned_items.append(item)
    merged["items"] = cleaned_items
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
        "completionOptions": {
            "stream": False,
            "temperature": 0.1,
            "maxTokens": 3000,
        },
        "messages": [
            {"role": "system", "text": SYSTEM_PROMPT},
            {"role": "user", "text": truncate_text(text)},
        ],
    }

    try:
        timeout = aiohttp.ClientTimeout(total=90)
        async with aiohttp.ClientSession(timeout=timeout) as session:
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
                return _normalize_result(result)
    except Exception as e:
        print(f"Unexpected error in analyze_tender_document: {e}", flush=True)
        return FALLBACK_RESULT.copy()


def _normalize_name(name: str) -> str:
    name = name.lower().replace("ё", "е")
    name = re.sub(r"[^\w\s.-]", " ", name)
    tokens = [t for t in name.split() if t not in FILLER_WORDS]
    return " ".join(sorted(tokens))


def _merge_items(source: list) -> list:
    """Консервативно объединяет только явно одинаковые позиции.

    Лучше временно оставить две похожие позиции, чем ошибочно объединить
    разные товары и потерять количество.
    """
    merged_items = []
    index = {}

    for analysis in source:
        for item in analysis.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            key = _normalize_name(name)
            if not key:
                continue

            existing_idx = index.get(key)
            if existing_idx is None:
                copied = dict(item)
                copied["requirements"] = list(item.get("requirements") or [])
                merged_items.append(copied)
                index[key] = len(merged_items) - 1
                continue

            existing = merged_items[existing_idx]
            qty = item.get("quantity")
            unit = item.get("unit")
            if existing.get("quantity") is None and qty is not None:
                existing["quantity"] = qty
            elif qty is not None and existing.get("quantity") != qty:
                existing.setdefault("quantity_conflicts", []).append(qty)
            if not existing.get("unit") and unit:
                existing["unit"] = unit
            existing.setdefault("requirements", []).extend(item.get("requirements") or [])

    return merged_items


def _distinct(values):
    result = []
    for value in values:
        if value is None or value == "":
            continue
        if value not in result:
            result.append(value)
    return result


def _merge_scalar(analyses: list, field: str, conflicts: list, numeric=False):
    values = _distinct([a.get(field) for a in analyses])
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    conflicts.append({
        "field": field,
        "type": "MULTIPLE_VALUES",
        "values": values,
        "status": "OPEN",
    })
    return values[0]


def merge_analyses(analyses: list) -> dict:
    merged = FALLBACK_RESULT.copy()
    useful = [a for a in analyses if a.get("has_useful_data")]
    source = useful if useful else analyses
    merged["has_useful_data"] = bool(useful)

    conflicts = []
    merged["items"] = _merge_items(source)

    # Эти поля могут естественно отличаться между документами (например,
    # краткое описание или формулировка предмета), поэтому конфликтом
    # считаем только действительно критичные для принятия решения значения.
    for field in ["tender_name", "subject", "region", "purchase_type", "classification", "summary"]:
        values = _distinct([a.get(field) for a in source])
        merged[field] = max(values, key=lambda x: len(str(x))) if values else None

    merged["nmck"] = _merge_scalar(source, "nmck", conflicts, numeric=True)
    merged["delivery_deadline"] = _merge_scalar(source, "delivery_deadline", conflicts)
    merged["contract_validity"] = _merge_scalar(source, "contract_validity", conflicts)

    period_values = [a.get("delivery_period") for a in source if a.get("delivery_period")]
    if period_values:
        # Сравниваем JSON-представления, чтобы не считать одинаковые объекты конфликтом.
        normalized_periods = _distinct([json.dumps(v, ensure_ascii=False, sort_keys=True) for v in period_values])
        merged["delivery_period"] = period_values[0]
        if len(normalized_periods) > 1:
            conflicts.append({
                "field": "delivery_period",
                "type": "MULTIPLE_VALUES",
                "values": period_values,
                "status": "OPEN",
            })

    validity_details = [a.get("contract_validity_details") for a in source if a.get("contract_validity_details")]
    if validity_details:
        merged["contract_validity_details"] = validity_details[0]

    penalties = []
    for a in source:
        penalties.extend(a.get("penalties") or [])
    merged["penalties"] = penalties

    for a in source:
        for c in a.get("conflicts") or []:
            if isinstance(c, dict):
                c = dict(c)
                c.setdefault("status", "OPEN")
                conflicts.append(c)

    merged["conflicts"] = conflicts

    evidence = []
    for a in analyses:
        for ev in a.get("source_evidence") or []:
            if isinstance(ev, dict):
                evidence.append(dict(ev))
    merged["source_evidence"] = evidence

    return merged
