"""
Модуль анализа тендерных документов через YandexGPT API.

Этап 1 V2:
- извлечение НМЦК;
- срок действия контракта;
- срок поставки;
- штрафы;
- позиции;
- технические требования;
- исходные формулировки;
- конфликты между документами.
"""

import asyncio
import json
import re

import aiohttp

from config.settings import (
    YANDEX_FOLDER_ID,
    YANDEX_API_KEY,
    YANDEX_URL,
)


SYSTEM_PROMPT = r"""
Ты — ассистент по анализу тендерной документации.

Тебе передают текст ОДНОГО документа закупки.

Верни СТРОГО валидный JSON без markdown и пояснений.

ГЛАВНЫЙ ПРИНЦИП:

Не угадывай.
Не исправляй документацию.
Не используй внешние знания.

Если значение явно не найдено в документе — возвращай null или [].

Если в документе есть несколько разных значений одного параметра,
НЕ выбирай одно из них. Верни найденные значения в conflicts.

Документация закупки важнее любой информации из интернета.

--------------------------------------------------
СРОК ДЕЙСТВИЯ КОНТРАКТА
--------------------------------------------------

contract_validity — срок действия самого контракта/договора.

НЕ путай его со сроком поставки.

Пример:

"Контракт действует до 31.12.2026"

Это срок действия контракта.

Пример:

"Контракт действует 12 месяцев с даты заключения"

Это также срок действия контракта.

Сохраняй исходную формулировку.

Если возможно, структурируй:

{
  "raw_text": "исходная формулировка",
  "type": "UNTIL_DATE" | "PERIOD_FROM_EVENT" | "OTHER",
  "start_date": null,
  "end_date": null,
  "value": null,
  "unit": null,
  "from_event": null
}

--------------------------------------------------
СРОК ПОСТАВКИ
--------------------------------------------------

delivery_period — срок поставки товара или выполнения услуги.

НЕ превращай относительный срок в конкретную календарную дату.

Например:

"30 рабочих дней с даты заключения контракта"

НЕ превращай в дату.

Верни:

{
  "raw_text": "30 рабочих дней с даты заключения контракта",
  "value": 30,
  "unit": "WORKING_DAYS",
  "from_event": "CONTRACT_SIGNING",
  "type": "PERIOD_FROM_EVENT"
}

Допустимые unit:

"WORKING_DAYS"
"CALENDAR_DAYS"
"WEEKS"
"MONTHS"
"YEARS"

Допустимые from_event:

"CONTRACT_SIGNING"
"CUSTOMER_REQUEST"
"OTHER"

Допустимые type:

"PERIOD_FROM_EVENT"
"FIXED_DATE"
"OTHER"

Если в документе прямо указана дата:

"Поставка до 31.12.2026"

то:

{
  "raw_text": "Поставка до 31.12.2026",
  "value": null,
  "unit": null,
  "from_event": null,
  "type": "FIXED_DATE"
}

А конкретную дату укажи в delivery_deadline.

--------------------------------------------------
ШТРАФЫ И НЕУСТОЙКИ
--------------------------------------------------

Извлекай только то, что прямо указано в документации.

Может быть несколько видов ответственности.

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

--------------------------------------------------
НМЦК
--------------------------------------------------

Извлекай именно НМЦК / начальную максимальную цену контракта.

Не подменяй НМЦК:

- ценой отдельной позиции;
- суммой коммерческого предложения;
- стоимостью доставки;
- стоимостью аналогичного товара;
- другой стоимостью.

Если НМЦК в документе не найдена — null.

--------------------------------------------------
ПОЗИЦИИ
--------------------------------------------------

Извлекай точные позиции спецификации.

Для каждой позиции:

{
  "name": "...",
  "quantity": 1,
  "unit": "шт",
  "requirements": []
}

Не объединяй разные позиции только потому, что названия похожи.

Например:

"Ноутбук Lenovo ThinkPad E14"

и

"Ноутбук Lenovo ThinkPad E16"

— это разные позиции.

Не объединяй их.

Фразы:

"или эквивалент"
"аналог"
"или аналог"

не включай в название товара.

--------------------------------------------------
ТЕХНИЧЕСКИЕ ТРЕБОВАНИЯ
--------------------------------------------------

Для каждого параметра:

{
  "parameter": "название параметра",
  "operator": "=" | ">" | ">=" | "<" | "<=" | "RANGE" | "IN" | "TEXT",
  "value": число/строка/null,
  "min": число/null,
  "max": число/null,
  "unit": "единица"/null,
  "mandatory": true/false,
  "raw_text": "исходный фрагмент требования"
}

Пример:

"Подача не менее 10 м³/ч"

Результат:

{
  "parameter": "Подача",
  "operator": ">=",
  "value": 10,
  "min": null,
  "max": null,
  "unit": "м³/ч",
  "mandatory": true,
  "raw_text": "Подача не менее 10 м³/ч"
}

Для диапазона:

"Температура от -20 до +40 °C"

используй:

{
  "parameter": "Температура",
  "operator": "RANGE",
  "value": null,
  "min": -20,
  "max": 40,
  "unit": "°C",
  "mandatory": true,
  "raw_text": "Температура от -20 до +40 °C"
}

Если требование невозможно представить числом:

{
  "operator": "TEXT"
}

и сохраняй точную формулировку.

--------------------------------------------------
ИСТОЧНИКИ
--------------------------------------------------

Для каждого важного значения, если возможно, укажи:

{
  "field": "nmck|contract_validity|delivery_period|delivery_deadline|penalty|item|requirement",
  "item_index": число или null,
  "raw_text": "точный или максимально близкий фрагмент документа"
}

Не придумывай номера страниц.

В переданном тексте страниц может не быть.

--------------------------------------------------
КОНФЛИКТЫ
--------------------------------------------------

Если в одном документе обнаружены разные значения одного параметра,
не выбирай одно.

Например:

"Срок поставки 30 рабочих дней"

и ниже:

"Срок поставки 20 рабочих дней"

Нужно вернуть конфликт.

--------------------------------------------------
ФОРМАТ ОТВЕТА
--------------------------------------------------

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
  "items": [],
  "penalties": [],
  "conflicts": [],
  "source_evidence": []
}

Все ключи обязательны.

Если данных нет:

null

или:

[]

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


FILLER_WORDS = {
    "или",
    "эквивалент",
    "аналог",
    "шт",
    "шт.",
    "и",
    "либо",
}


def truncate_text(text: str, max_len: int = 20000) -> str:
    """
    Ограничивает объём текста, передаваемого модели.

    Пока сохраняем текущий лимит проекта.
    Позже сделаем разбиение длинных документов на смысловые блоки.
    """

    if not text:
        return ""

    if len(text) <= max_len:
        return text

    truncated = text[:max_len]

    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]

    return truncated + "…"


def _clean_json(raw: str) -> str:
    """
    Убирает markdown-обёртку и пытается выделить JSON.
    """

    if not raw:
        return ""

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
    """
    Приводит ответ YandexGPT к ожидаемой структуре.
    """

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

        if not isinstance(item, dict):
            continue

        name = str(item.get("name") or "").strip()

        if not name:
            continue

        item = dict(item)

        item["name"] = name

        if not isinstance(item.get("requirements"), list):
            item["requirements"] = []

        cleaned_requirements = []

        for requirement in item["requirements"]:

            if not isinstance(requirement, dict):
                continue

            parameter = str(
                requirement.get("parameter") or ""
            ).strip()

            if not parameter:
                continue

            requirement = dict(requirement)

            requirement["parameter"] = parameter

            if requirement.get("operator") not in {
                "=",
                ">",
                ">=",
                "<",
                "<=",
                "RANGE",
                "IN",
                "TEXT",
            }:
                requirement["operator"] = "TEXT"

            cleaned_requirements.append(requirement)

        item["requirements"] = cleaned_requirements

        cleaned_items.append(item)

    merged["items"] = cleaned_items

    return merged


async def analyze_tender_document(text: str) -> dict:
    """
    Анализ одного документа через YandexGPT.
    """

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
            {
                "role": "system",
                "text": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "text": truncate_text(text),
            },
        ],
    }

    try:

        timeout = aiohttp.ClientTimeout(total=90)

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(
                YANDEX_URL,
                headers=headers,
                json=payload,
            ) as response:

                if response.status != 200:

                    error_text = await response.text()

                    print(
                        f"YandexGPT API error "
                        f"{response.status}: "
                        f"{error_text}",
                        flush=True,
                    )

                    return FALLBACK_RESULT.copy()

                data = await response.json()

                alternatives = (
                    data
                    .get("result", {})
                    .get("alternatives", [])
                )

                if not alternatives:
                    print(
                        "YandexGPT response does not contain alternatives",
                        flush=True,
                    )

                    return FALLBACK_RESULT.copy()

                message = alternatives[0].get("message", {})

                content = str(
                    message.get("text") or ""
                ).strip()

                if not content:
                    print(
                        "YandexGPT returned empty response",
                        flush=True,
                    )

                    return FALLBACK_RESULT.copy()

                cleaned = _clean_json(content)

                try:

                    result = json.loads(cleaned)

                except json.JSONDecodeError:

                    print(
                        "YandexGPT returned invalid JSON:",
                        content[:2000],
                        flush=True,
                    )

                    return FALLBACK_RESULT.copy()

                return _normalize_result(result)

    except asyncio.CancelledError:
        raise

    except Exception as exc:

        print(
            f"Unexpected error in "
            f"analyze_tender_document: {exc}",
            flush=True,
        )

        return FALLBACK_RESULT.copy()


def _normalize_name(name: str) -> str:
    """
    Нормализация названия позиции.

    Объединяем только действительно одинаковые названия.
    """

    name = str(name or "").lower()

    name = name.replace("ё", "е")

    name = re.sub(
        r"[^\w\s.-]",
        " ",
        name,
    )

    tokens = [
        token
        for token in name.split()
        if token not in FILLER_WORDS
    ]

    return " ".join(sorted(tokens))


def _merge_items(source: list) -> list:
    """
    Консервативное объединение позиций.

    Важно:

    Лучше оставить две похожие позиции,
    чем ошибочно объединить разные товары
    и потерять количество.
    """

    merged_items = []
    index = {}

    for analysis in source:

        for item in analysis.get("items", []) or []:

            if not isinstance(item, dict):
                continue

            name = str(
                item.get("name") or ""
            ).strip()

            if not name:
                continue

            key = _normalize_name(name)

            if not key:
                continue

            existing_idx = index.get(key)

            if existing_idx is None:

                copied = dict(item)

                copied["requirements"] = list(
                    item.get("requirements") or []
                )

                merged_items.append(copied)

                index[key] = len(merged_items) - 1

                continue

            existing = merged_items[existing_idx]

            quantity = item.get("quantity")
            unit = item.get("unit")

            if (
                existing.get("quantity") is None
                and quantity is not None
            ):
                existing["quantity"] = quantity

            elif (
                quantity is not None
                and existing.get("quantity") != quantity
            ):

                existing.setdefault(
                    "quantity_conflicts",
                    [],
                ).append(quantity)

            if not existing.get("unit") and unit:
                existing["unit"] = unit

            existing.setdefault(
                "requirements",
                [],
            ).extend(
                item.get("requirements") or []
            )

    return merged_items


def _distinct(values):
    """
    Возвращает уникальные значения,
    сохраняя порядок.
    """

    result = []

    for value in values:

        if value is None:
            continue

        if value == "":
            continue

        if value not in result:
            result.append(value)

    return result


def _merge_scalar(
    analyses: list,
    field: str,
    conflicts: list,
):
    """
    Объединяет простое поле.

    Если найдено несколько различных значений,
    первое значение сохраняется для совместимости,
    но обязательно создаётся OPEN conflict.

    В дальнейшем пользователь сможет выбрать
    подтверждённое значение.
    """

    values = _distinct(
        [
            analysis.get(field)
            for analysis in analyses
        ]
    )

    if not values:
        return None

    if len(values) == 1:
        return values[0]

    conflicts.append(
        {
            "field": field,
            "type": "MULTIPLE_VALUES",
            "values": values,
            "status": "OPEN",
        }
    )

    return values[0]


def merge_analyses(analyses: list) -> dict:
    """
    Объединяет результаты анализа нескольких документов.

    Критические значения:

    - НМЦК
    - срок поставки
    - срок действия контракта

    не выбираются через max/min.

    При конфликте сохраняется первое значение,
    а конфликт записывается в conflicts.
    """

    merged = FALLBACK_RESULT.copy()

    if not analyses:
        return merged

    useful = [
        analysis
        for analysis in analyses
        if isinstance(analysis, dict)
        and analysis.get("has_useful_data")
    ]

    source = useful if useful else analyses

    merged["has_useful_data"] = bool(useful)

    conflicts = []

    # -------------------------------------------------
    # ПОЗИЦИИ
    # -------------------------------------------------

    merged["items"] = _merge_items(source)

    # -------------------------------------------------
    # ОБЩИЕ ПОЛЯ
    # -------------------------------------------------

    for field in [
        "tender_name",
        "subject",
        "region",
        "purchase_type",
        "classification",
        "summary",
    ]:

        values = _distinct(
            [
                analysis.get(field)
                for analysis in source
            ]
        )

        if values:

            # Для описательных полей берём
            # наиболее информативное значение.
            merged[field] = max(
                values,
                key=lambda value: len(
                    str(value)
                ),
            )

        else:
            merged[field] = None

    # -------------------------------------------------
    # НМЦК
    # -------------------------------------------------

    merged["nmck"] = _merge_scalar(
        source,
        "nmck",
        conflicts,
    )

    # -------------------------------------------------
    # КОНКРЕТНАЯ ДАТА ПОСТАВКИ
    # -------------------------------------------------

    merged["delivery_deadline"] = _merge_scalar(
        source,
        "delivery_deadline",
        conflicts,
    )

    # -------------------------------------------------
    # СРОК ДЕЙСТВИЯ КОНТРАКТА
    # -------------------------------------------------

    merged["contract_validity"] = _merge_scalar(
        source,
        "contract_validity",
        conflicts,
    )

    # -------------------------------------------------
    # СРОК ПОСТАВКИ
    # -------------------------------------------------

    period_values = [
        analysis.get("delivery_period")
        for analysis in source
        if analysis.get("delivery_period")
    ]

    if period_values:

        normalized_periods = _distinct(
            [
                json.dumps(
                    period,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                for period in period_values
            ]
        )

        merged["delivery_period"] = period_values[0]

        if len(normalized_periods) > 1:

            conflicts.append(
                {
                    "field": "delivery_period",
                    "type": "MULTIPLE_VALUES",
                    "values": period_values,
                    "status": "OPEN",
                }
            )

    # -------------------------------------------------
    # ДЕТАЛИ СРОКА КОНТРАКТА
    # -------------------------------------------------

    validity_details = [
        analysis.get(
            "contract_validity_details"
        )
        for analysis in source
        if analysis.get(
            "contract_validity_details"
        )
    ]

    if validity_details:

        merged[
            "contract_validity_details"
        ] = validity_details[0]

    # -------------------------------------------------
    # ШТРАФЫ
    # -------------------------------------------------

    penalties = []

    for analysis in source:

        penalties.extend(
            analysis.get("penalties") or []
        )

    merged["penalties"] = penalties

    # -------------------------------------------------
    # КОНФЛИКТЫ ИЗ ОТДЕЛЬНЫХ ДОКУМЕНТОВ
    # -------------------------------------------------

    for analysis in source:

        for conflict in (
            analysis.get("conflicts") or []
        ):

            if isinstance(conflict, dict):

                conflict = dict(conflict)

                conflict.setdefault(
                    "status",
                    "OPEN",
                )

                conflicts.append(conflict)

    merged["conflicts"] = conflicts

    # -------------------------------------------------
    # ИСТОЧНИКИ
    # -------------------------------------------------

    evidence = []

    for analysis in analyses:

        for item in (
            analysis.get(
                "source_evidence"
            ) or []
        ):

            if isinstance(item, dict):
                evidence.append(dict(item))

    merged["source_evidence"] = evidence

    return merged
