import json
import re
import asyncio
import logging
import aiohttp
from config.settings import YANDEX_API_KEY, YANDEX_FOLDER_ID, YANDEX_URL

logger = logging.getLogger(__name__)

MAX_CHUNK = 20000
YANDEX_TIMEOUT = 90
MAX_TOKENS = 8000

SYSTEM_PROMPT = """Ты парсер тендерной документации. Твоя задача - извлечь номенклатуру товаров/работ.
Верни ТОЛЬКО валидный JSON без текста до и после.
Формат: {"items": [{"position_number": 1, "name": "Название товара", "quantity": 1, "unit": "шт", "requirements": []}]}
Правила:
- name - краткое название позиции
- quantity - число, если не указано ставь 1
- unit - единица измерения, если не указано ставь "шт"
- requirements - список характеристик
- Не отказывайся отвечать. Если не нашел позиций - верни {"items": []}
- Отвечай только JSON."""

REPAIR_PROMPT = """Исправь предыдущий ответ. Верни ТОЛЬКО валидный JSON формата {"items": []} без текста и markdown."""

def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    raw = m.group(0).strip()
    for _ in range(2):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raw = re.sub(r",\s*}", "}", raw)
            raw = re.sub(r",\s*]", "]", raw)
            continue
    return None

def _chunk_text(text: str, size: int = MAX_CHUNK) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        if end >= len(text):
            chunks.append(text[start:])
            break
        cut = text.rfind("\n", start, end)
        if cut == -1 or cut <= start + size // 2:
            cut = end
        chunks.append(text[start:cut])
        start = cut
    return chunks

async def _call_yandex(chunk: str, retries: int = 2) -> dict:
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {"stream": False, "temperature": 0.1, "maxTokens": MAX_TOKENS},
        "messages": [
            {"role": "system", "text": SYSTEM_PROMPT},
            {"role": "user", "text": chunk[:MAX_CHUNK]}
        ]
    }
    for attempt in range(retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(YANDEX_URL, json=body, headers=headers, timeout=YANDEX_TIMEOUT) as resp:
                    data = await resp.json()
                    try:
                        text = data["result"]["alternatives"][0]["message"]["text"]
                    except (KeyError, IndexError, TypeError):
                        logger.warning(f"YandexGPT unexpected response: {json.dumps(data, ensure_ascii=False)[:1000]}")
                        text = ""
                    if not text:
                        if attempt < retries:
                            await asyncio.sleep(1)
                            continue
                        return {"items": []}
                    low = text.lower()
                    if "не могу обсуждать" in low or "давайте поговорим" in low or "не могу помочь" in low:
                        logger.warning(f"YandexGPT filter triggered, attempt {attempt+1}: {text[:200]}")
                        if attempt < retries:
                            body["messages"] = [
                                {"role": "system", "text": SYSTEM_PROMPT},
                                {"role": "user", "text": REPAIR_PROMPT + "\n" + chunk[:5000]}
                            ]
                            await asyncio.sleep(1)
                            continue
                        return {"items": []}
                    parsed = _extract_json(text)
                    if parsed is not None and "items" in parsed and isinstance(parsed["items"], list):
                        for it in parsed["items"]:
                            if "name" not in it or not it["name"]:
                                it["name"] = "Не указано"
                            if "quantity" not in it:
                                it["quantity"] = 1
                            if "unit" not in it:
                                it["unit"] = "шт"
                            if "requirements" not in it:
                                it["requirements"] = []
                        return parsed
                    logger.warning(f"YandexGPT returned invalid JSON attempt {attempt+1}: {text[:500]}")
                    if attempt < retries:
                        body["messages"] = [
                            {"role": "system", "text": SYSTEM_PROMPT},
                            {"role": "user", "text": REPAIR_PROMPT + "\nИсходный ответ:\n" + text[:3000]}
                        ]
                        await asyncio.sleep(1)
                        continue
                    return {"items": []}
        except asyncio.TimeoutError:
            logger.warning(f"YandexGPT timeout attempt {attempt+1}")
            if attempt < retries:
                await asyncio.sleep(2)
                continue
            return {"items": []}
        except Exception as e:
            logger.warning(f"YandexGPT call error attempt {attempt+1}: {e}")
            if attempt < retries:
                await asyncio.sleep(1)
                continue
            return {"items": []}
    return {"items": []}

async def analyze_text(full_text: str) -> dict:
    if not full_text or len(full_text.strip()) < 50:
        return {"items": []}
    chunks = _chunk_text(full_text)
    logger.info(f"[ai_analyzer] текст {len(full_text)} символов, чанков {len(chunks)}")
    all_items = []
    pos = 1
    for idx, ch in enumerate(chunks):
        logger.info(f"[ai_analyzer] чанк {idx+1}/{len(chunks)} символов {len(ch)}")
        res = await _call_yandex(ch)
        items = res.get("items", [])
        logger.info(f"[ai_analyzer] чанк {idx+1} -> {len(items)} позиций")
        for it in items:
            it["position_number"] = pos
            pos += 1
            all_items.append(it)
    logger.info(f"[ai_analyzer] итого {len(all_items)} позиций")
    return {"items": all_items}

async def analyze_documents(text: str) -> dict:
    return await analyze_text(text)

async def merge_and_analyze(texts: list[str]) -> dict:
    full = "\n\n".join(t for t in texts if t)
    return await analyze_text(full)

async def analyze_tender_document(text: str) -> dict:
    return await analyze_text(text)

async def merge_analyses(analyses: list[dict]) -> dict:
    all_items = []
    pos = 1
    for a in analyses:
        for it in a.get("items", []) if isinstance(a, dict) else []:
            it["position_number"] = pos
            pos += 1
            all_items.append(it)
    return {"items": all_items}
