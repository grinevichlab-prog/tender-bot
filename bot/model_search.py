"""
Поиск моделей товаров через DuckDuckGo + YandexGPT для извлечения характеристик
"""

import re
import html
import json
import asyncio
import aiohttp
from urllib.parse import urlparse, quote_plus
from config.settings import YANDEX_FOLDER_ID, YANDEX_API_KEY, YANDEX_URL

DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"

RESULT_LINK_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"')
TITLE_RE = re.compile(r'class="result__a"[^>]*>(.*?)</a>', re.DOTALL)

EXCLUDED_DOMAINS = {
    "wikipedia.org", "youtube.com", "vk.com", "ok.ru", "yandex.ru", "yandex.by",
    "google.com", "avito.ru", "2gis.ru", "rusprofile.ru", "list-org.com",
    "zakupki.gov.ru", "gosuslugi.ru", "roseltorg.ru", "sberbank-ast.ru",
    "rts-tender.ru", "fabrikant.ru", "etp-ets.ru", "etpgpb.ru", "etpgroup.ru",
    "tektorg.ru", "b2b-center.ru", "otc.ru", "zakazrf.ru", "tenderplan.ru",
    "tenderguru.ru", "tender.mos.ru", "tenderland.ru",
}

EXCLUDED_WORDS = (
    "тендер", "закупк", "конкурс", "аукцион", "торги", "новости", "форум",
    "вакансии", "резюме", "объявления"
)

EXTRACTION_SYSTEM_PROMPT = """Ты анализируешь страницу интернет-магазина или каталога товара.
Извлеки характеристики товара в формате JSON.

Верни строго JSON:
{
  "manufacturer": string|null,
  "model": string|null,
  "product_name": string|null,
  "price": number|null,
  "currency": "RUB"|"USD"|"EUR"|null,
  "price_includes_vat": bool|null,
  "availability": string|null,
  "specifications": {
    "param_name": "value",
    ...
  }
}

Если страница не о товаре или данные отсутствуют, верни null."""


def _domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1).lower() if m else ""


def _is_candidate(title: str, url: str) -> bool:
    """Фильтр: исключаем нерелевантные сайты"""
    domain = _domain(url)
    if domain in EXCLUDED_DOMAINS:
        return False
    lower = (title + " " + url).lower()
    if any(word in lower for word in EXCLUDED_WORDS):
        return False
    return True


async def _search(query: str, max_results: int = 10) -> list[dict]:
    """Поиск через DuckDuckGo HTML"""
    print(f"[model_search] Запрос к DuckDuckGo: '{query}'", flush=True)
    
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                DUCKDUCKGO_URL,
                data={"q": query, "kl": "ru-ru"},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            ) as response:
                if response.status != 200:
                    print(f"[model_search] DuckDuckGo error: status {response.status}", flush=True)
                    return []
                page = await response.text(errors="ignore")
                print(f"[model_search] DuckDuckGo ответил, HTML длина: {len(page)}", flush=True)
    except Exception as exc:
        print(f"[model_search] Ошибка запроса к DuckDuckGo: {exc}", flush=True)
        return []

    links = RESULT_LINK_RE.findall(page)
    titles = [re.sub(r"<.*?>", "", t).strip() for t in TITLE_RE.findall(page)]
    
    print(f"[model_search] Найдено ссылок: {len(links)}, заголовков: {len(titles)}", flush=True)
    
    result = []
    seen = set()
    
    for link, title in zip(links, titles):
        if not _is_candidate(title, link):
            print(f"[model_search] Отфильтровано: {title[:50]}", flush=True)
            continue
        
        domain = _domain(link)
        if domain in seen:
            continue
        seen.add(domain)
        
        result.append({"title": html.unescape(title), "url": link, "domain": domain})
        print(f"[model_search] Добавлен кандидат: {title[:50]} | {domain}", flush=True)
        
        if len(result) >= max_results:
            break
    
    print(f"[model_search] Итого кандидатов после фильтрации: {len(result)}", flush=True)
    return result


async def _fetch_page(session: aiohttp.ClientSession, url: str) -> str | None:
    """Скачивает содержимое страницы"""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10), ssl=False) as response:
            if response.status != 200:
                return None
            text = await response.text(errors="ignore")
            return text[:50000]
    except Exception as e:
        print(f"[model_search] Ошибка загрузки {url}: {e}", flush=True)
        return None


async def _extract_model_info(page_text: str, source_url: str) -> dict | None:
    """Извлекает характеристики товара через YandexGPT"""
    if not page_text or not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        return None
    
    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {"stream": False, "temperature": 0.0, "maxTokens": 1000},
        "messages": [
            {"role": "system", "text": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "text": page_text[:15000]},
        ],
    }
    
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json",
    }
    
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(YANDEX_URL, headers=headers, json=payload) as response:
                if response.status != 200:
                    print(f"[model_search] YandexGPT ошибка {response.status} для {source_url}", flush=True)
                    return None
                
                data = await response.json()
                raw = data["result"]["alternatives"][0]["message"]["text"].strip()
                
                # Убираем markdown
                raw = re.sub(r"^```(?:json)?", "", raw).strip()
                raw = re.sub(r"```$", "", raw).strip()
                
                parsed = json.loads(raw)
                
                if isinstance(parsed, dict) and (parsed.get("manufacturer") or parsed.get("model")):
                    parsed["source_url"] = source_url
                    print(f"[model_search] Извлечена модель: {parsed.get('manufacturer')} {parsed.get('model')}", flush=True)
                    return parsed
                
                return None
                
    except json.JSONDecodeError as e:
        print(f"[model_search] JSON parse error для {source_url}: {e}", flush=True)
        return None
    except Exception as e:
        print(f"[model_search] YandexGPT error для {source_url}: {e}", flush=True)
        return None


async def search_models(tender_item: dict, region: str | None = None, max_models: int = 10) -> list[dict]:
    """
    Ищет модели товаров для позиции тендера
    """
    item_name = tender_item.get('name', '')
    manufacturer = tender_item.get('manufacturer', '')
    
    # Формируем поисковые запросы
    queries = []
    if manufacturer:
        queries.append(f"{manufacturer} {item_name} купить цена характеристики")
    queries.append(f"{item_name} купить интернет-магазин")
    
    candidates = []
    seen_domains = set()
    
    print(f"[search_models] Ищу модели для '{item_name}'", flush=True)
    
    for query in queries:
        for result in await _search(query, max_results=15):
            domain = result["domain"]
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            candidates.append(result)
            if len(candidates) >= max_models * 3:
                break
        if len(candidates) >= max_models * 3:
            break
    
    print(f"[search_models] Найдено {len(candidates)} кандидатов для обработки", flush=True)
    
    if not candidates:
        print(f"[search_models] Нет кандидатов, поиск завершен", flush=True)
        return []
    
    models = []
    timeout = aiohttp.ClientTimeout(total=15)
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for idx, candidate in enumerate(candidates, 1):
            print(f"[search_models] Обрабатываю {idx}/{len(candidates)}: {candidate['url']}", flush=True)
            
            page = await _fetch_page(session, candidate["url"])
            if not page:
                continue
            
            model_info = await _extract_model_info(page, candidate["url"])
            if not model_info:
                continue
            
            model_info["source_title"] = candidate["title"]
            models.append(model_info)
            
            if len(models) >= max_models:
                break
            
            await asyncio.sleep(1)
    
    print(f"[search_models] обработано {len(candidates)} кандидатов, найдено {len(models)} моделей", flush=True)
    return models
