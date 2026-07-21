"""
Модуль анализа тендерных документов через YandexGPT API.
Сохраняет обратную совместимость по структуре ответа.
"""

import json
import aiohttp
from config.settings import YANDEX_FOLDER_ID, YANDEX_API_KEY, YANDEX_URL

SYSTEM_PROMPT = (
    "Ты — эксперт по тендерам. Проанализируй текст документа и верни JSON.\n"
    "Поля:\n"
    "- has_useful_data: true/false (есть ли в тексте тендерные данные)\n"
    "- tender_name: строка\n"
    "- subject: строка\n"
    "- items: список позиций (наименование, количество, единица измерения)\n"
    "- nmck: число (цена контракта)\n"
    "- delivery_deadline: строка (крайний срок поставки)\n"
    "- region: строка (регион доставки)\n"
    "- purchase_type: строка (закупка по 44/223-ФЗ, запрос котировок, аукцион и т.п.)\n"
    "- classification: строка (ТОВАР, УСЛУГИ, СМЕШАННЫЙ)\n"
    "- summary: строка (краткая суть тендера, 2-3 предложения)\n"
    "Если данных для поля нет, ставь null.\n"
    "Отвечай строго JSON-объектом, без markdown и лишнего текста."
)

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
    """Обрезает текст до заданной длины, сохраняя целостность слов."""
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "…"


async def analyze_tender_document(text: str) -> dict:
    """
    Отправляет текст документа в YandexGPT и возвращает распарсенный JSON.
    В случае любой ошибки возвращает FALLBACK_RESULT.
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
                    print(f"YandexGPT API error {resp.status}: {error_text}")
                    return FALLBACK_RESULT.copy()

                data = await resp.json()
                content = data["result"]["alternatives"][0]["message"]["text"].strip()

                # Пытаемся распарсить JSON
                try:
                    result = json.loads(content)
                except json.JSONDecodeError:
                    if content.startswith("```"):
                        content = content.removeprefix("```json").removesuffix("```").strip()
                        result = json.loads(content)
                    else:
                        raise

                # Валидация структуры
                for key in FALLBACK_RESULT:
                    result.setdefault(key, None if key != "items" else [])
                return result

    except Exception as e:
        print(f"Unexpected error in analyze_tender_document: {e}")
        return FALLBACK_RESULT.copy()


def merge_analyses(analyses: list[dict]) -> dict:
    """
    Объединяет несколько результатов анализа (от разных документов одного тендера)
    в один сводный JSON.
    """
    merged = FALLBACK_RESULT.copy()
    merged["has_useful_data"] = any(a.get("has_useful_data") for a in analyses)

    # Собираем все позиции, суммируя количества по названию
    items_dict = {}
    for a in analyses:
        for item in a.get("items", []):
            name = item.get("name", "").strip()
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
                items_dict[name] = {
                    "name": name,
                    "quantity": qty,
                    "unit": unit,
                }
    merged["items"] = list(items_dict.values()) if items_dict else []

    # Выбираем лучшее значение для строковых полей (самое длинное непустое)
    for field in ["tender_name", "subject", "region", "purchase_type", "classification", "summary"]:
        candidates = [a.get(field) for a in analyses if a.get(field)]
        if candidates:
            merged[field] = max(candidates, key=lambda x: len(str(x)))

    # delivery_deadline — самая дальняя дата
    deadlines = [a.get("delivery_deadline") for a in analyses if a.get("delivery_deadline")]
    if deadlines:
        merged["delivery_deadline"] = max(deadlines)

    # НМЦ — максимальное значение
    nmck_values = [a.get("nmck") for a in analyses if a.get("nmck") is not None]
    if nmck_values:
        merged["nmck"] = max(nmck_values)

    return merged