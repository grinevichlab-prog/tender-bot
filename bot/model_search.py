"""Поиск конкретных моделей товара и извлечение характеристик из открытых источников."""

from __future__ import annotations

import html
import json
import re
from urllib.parse import urlparse

import aiohttp

from config.settings import YANDEX_FOLDER_ID, YANDEX_API_KEY, YANDEX_URL

DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"

EXCLUDED_DOMAINS = {
    "zakupki.gov.ru", "etp-ets.ru", "sberbank-ast.ru", "roseltorg.ru",
    "rts-tender.ru", "yandex.ru", "google.com", "wikipedia.org",
    "youtube.com", "vk.com", "ok.ru", "2gis.ru", "rusprofile.ru",
    "list-org.com", "avito.ru", "drom.ru",
}

EXCLUDED_WORDS = (
    "тендер", "закупк", "новост", "форум", "отзыв", "ваканс",
    "аукцион", "реестр", "справочник", "агрегатор",
)

RESULT_LINK_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"')
TITLE_RE = re.compile(r'class="result__a"[^>]*>(.*?)</a>', re.DOTALL)

MODEL_SYSTEM_PROMPT = """Ты анализируешь страницу российского поставщика или производителя.
Извлеки только данные о конкретной модели товара, если они прямо подтверждены текстом страницы.
Не придумывай характеристики. Если конкретная модель не указана, верни null.
Верни строго JSON:
{
  "manufacturer": string|null,
  "model": string|null,
  "product_name": string|null,
  "specifications": {"характеристика": {"value": number|string, "unit": string|null, "raw": string|null}},
  "price": number|null,
  "currency": "RUB"|null,
  "price_includes_vat": true|false|null,
  "availability": string|null,
  "source_quote": string|null
}
"""


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _is_candidate(title: str, url: str) -> bool:
    domain = _domain(url)
    if not domain or domain in EXCLUDED_DOMAINS:
        return False
    haystack = f"{title} {url}".lower()
    return not any(word in haystack for word in EXCLUDED_WORDS)


async def _search(query: str, max_results: int = 10) -> list[dict]:
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                DUCKDUCKGO_URL,
                data={"q": query, "kl": "ru-ru"},
                headers={"User-Agent": "Mozilla/5.0"},
            ) as response:
                if response.status != 200:
                    return []
                page = await response.text(errors="ignore")
    except Exception as exc:
        print(f"[model_search] search error: {exc}", flush=True)
        return []

    links = RESULT_LINK_RE.findall(page)
    titles = [re.sub(r"<.*?>", "", t).strip() for t in TITLE_RE.findall(page)]
    result = []
    seen = set()
    for link, title in zip(links, titles):
        if not _is_candidate(title, link):
            continue
        domain = _domain(link)
        if domain in seen:
            continue
        seen.add(domain)
        result.append({"title": html.unescape(title), "url": link, "domain": domain})
        if len(result) >= max_results:
            break
    return result


def build_model_queries(item: dict | str, region: str | None = None) -> list[str]:
    # защита если item пришел строкой из-за битого ai_analyzer
    if isinstance(item, str):
        item = {"name": item, "requirements": []}
    if not isinstance(item, dict):
        return []
    name = (item.get("name") or "").strip()
    if not name:
        return []
    requirements = item.get("requirements") or []
    important = []
    for req in requirements[:5]:
        if isinstance(req, str):
            continue
        if not isinstance(req, dict):
            continue
        parameter = req.get("parameter") or req.get("name")
        value = req.get("value")
        unit = req.get("unit")
        if parameter and value is not None:
            important.append(f'"{parameter}" {value} {unit or ""}'.strip())
    region_part = f' {region}' if region else ""
    queries = [f'"{name}" купить поставщик{region_part}', f'"{name}" модель характеристики купить{region_part}']
    if important:
        queries.append(f'"{name}" ' + " ".join(important[:3]) + region_part)
    return queries


async def _fetch_page(session: aiohttp.ClientSession, url: str) -> str:
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=10),
            ssl=True,
            headers={"User-Agent": "Mozilla/5.0"},
            allow_redirects=True,
        ) as response:
            if response.status != 200:
                return ""
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                return ""
            text = await response.text(errors="ignore")
            text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
            text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
            text = re.sub(r"<[^>]+>", " ", text)
            return re.sub(r"\s+", " ", html.unescape(text)).strip()[:30000]
    except Exception:
        return ""


async def _extract_model(page_text: str) -> dict | None:
    if not page_text or not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        return None
    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt",
        "completionOptions": {"stream": False, "temperature": 0.0, "maxTokens": 1200},
        "messages": [
            {"role": "system", "text": MODEL_SYSTEM_PROMPT},
            {"role": "user", "text": page_text},
        ],
    }
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(YANDEX_URL, headers=headers, json=payload) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                raw = data["result"]["alternatives"][0]["message"]["text"].strip()
                raw = re.sub(r"^```(?:json)?", "", raw).strip()
                raw = re.sub(r"```$", "", raw).strip()
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) and parsed.get("model") else None
    except Exception as exc:
        print(f"[model_search] model extraction error: {exc}", flush=True)
        return None
async def search_models(item: dict | str, region: str | None = None, max_models: int = 12) -> list[dict]:
    if isinstance(item, str):
        item = {"name": item, "requirements": []}
    if not isinstance(item, dict):
        return []
    candidates = []
    seen_urls = set()
    for query in build_model_queries(item, region):
        for result in await _search(query, max_results=15):  # увеличил с 8 до 15
            if result["url"] in seen_urls:
                continue
            seen_urls.add(result["url"])
            candidates.append(result)
            if len(candidates) >= max_models * 3:  # увеличил с *2 до *3
                break
        if len(candidates) >= max_models * 3:
            break

    models = []
    seen_models = set()
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for candidate in candidates:
            page = await _fetch_page(session, candidate["url"])
            if not page:
                continue
            extracted = await _extract_model(page)
            if not extracted:
                continue
            model_key = f'{extracted.get("manufacturer", "")}|{extracted.get("model", "")}'.lower()
            if model_key in seen_models:
                continue
            seen_models.add(model_key)
            extracted.update({"source_url": candidate["url"], "source_title": candidate["title"]})
            models.append(extracted)
            if len(models) >= max_models:
                break

    print(f"[search_models] обработано {len(candidates)} кандидатов, найдено {len(models)} моделей", flush=True)
    return models

