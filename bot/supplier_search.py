"""
Бесплатный поиск поставщиков через веб-поиск (DuckDuckGo, без API-ключа)
с фильтром на российский регион, отсечением тендерных/новостных сайтов
и дополнительной фильтрацией результатов через YandexGPT.
"""

import re
import json
import asyncio
import aiohttp
from config.settings import YANDEX_FOLDER_ID, YANDEX_API_KEY, YANDEX_URL

DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_REGEX = re.compile(r"(?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")
RESULT_LINK_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"')
TITLE_RE = re.compile(r'class="result__a"[^>]*>(.*?)</a>', re.DOTALL)

EXCLUDED_DOMAINS = {
    "wikipedia.org", "youtube.com", "vk.com", "ok.ru", "yandex.ru", "yandex.by",
    "google.com", "avito.ru", "2gis.ru", "rusprofile.ru", "list-org.com",
    "zakupki.gov.ru", "gosuslugi.ru", "roseltorg.ru", "sberbank-ast.ru",
    "rts-tender.ru", "fabrikant.ru", "etp-ets.ru", "etpgpb.ru", "etpgroup.ru",
    "tektorg.ru", "b2b-center.ru", "otc.ru", "zakazrf.ru", "tenderplan.ru",
    "tenderguru.ru", "tender.mos.ru", "tenderland.ru", "seldon.tenderplan.ru",
}

EXCLUDED_URL_PATTERNS = re.compile(
    r"(news|novosti|/tender|/zakupk|/auktsion|/torgi|forum\.)", re.IGNORECASE
)


def _domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1).lower() if m else ""


async def _duckduckgo_search(query: str, max_results: int = 10) -> list[dict]:
    commercial_query = f"{query} купить поставщик цена -тендер -закупка"
    params = {"q": commercial_query, "kl": "ru-ru"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                DUCKDUCKGO_URL, data=params,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                if resp.status != 200:
                    print(f"[web_supplier_search] DuckDuckGo error {resp.status}", flush=True)
                    return []
                html = await resp.text()
    except Exception as e:
        print(f"[web_supplier_search] ошибка запроса к DuckDuckGo: {e}", flush=True)
        return []

    links = RESULT_LINK_RE.findall(html)
    titles = [re.sub("<.*?>", "", t).strip() for t in TITLE_RE.findall(html)]

    results = []
    seen_domains = set()
    for link, title in zip(links, titles):
        domain = _domain(link)
        if not domain or domain in EXCLUDED_DOMAINS or domain in seen_domains:
            continue
        if EXCLUDED_URL_PATTERNS.search(link):
            continue
        seen_domains.add(domain)
        results.append({"name": title or domain, "url": link})
        if len(results) >= max_results:
            break

    return results


async def _ai_filter_candidates(query: str, candidates: list[dict]) -> list[dict]:
    """
    Просит YandexGPT оставить только реальных поставщиков/продавцов товара,
    отсеяв новости, тендерные площадки, форумы и нерелевантные страницы.
    При ошибке возвращает исходный список без изменений (не блокируем поиск).
    """
    if not candidates:
        return candidates

    numbered = "\n".join(f"{i+1}. {c['name']} — {c['url']}" for i, c in enumerate(candidates))
    prompt = (
        f"Ищем поставщиков товара/услуги по запросу: «{query}».\n"
        f"Вот список найденных сайтов:\n{numbered}\n\n"
        "Оставь только те номера, которые ведут на сайты РЕАЛЬНЫХ КОМПАНИЙ-ПОСТАВЩИКОВ "
        "или интернет-магазинов, продающих именно такой товар/услугу. "
        "ИСКЛЮЧИ номера, ведущие на новости, тендерные площадки, госзакупки, форумы, "
        "справочники организаций, статьи, соцсети. "
        "Верни СТРОГО JSON-массив номеров, например [1,3,4]. Ничего больше."
    )

    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {"stream": False, "temperature": 0.0, "maxTokens": 200},
        "messages": [{"role": "user", "text": prompt}],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                YANDEX_URL, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    print(f"[web_supplier_search] AI-фильтр: ошибка {resp.status}", flush=True)
                    return candidates
                data = await resp.json()
                raw = data["result"]["alternatives"][0]["message"]["text"].strip()
                raw = raw.strip("`").replace("json", "", 1).strip()
                indices = json.loads(raw)
                kept = [candidates[i - 1] for i in indices if 1 <= i <= len(candidates)]
                return kept if kept else candidates
    except Exception as e:
        print(f"[web_supplier_search] AI-фильтр не сработал, оставляю все: {e}", flush=True)
        return candidates


async def _extract_contacts(session: aiohttp.ClientSession, url: str) -> dict:
    email, phone = None, None
    paths = ["", "/contacts", "/kontakty"]
    for path in paths:
        try:
            target = url.rstrip("/") + path
            async with session.get(
                target, timeout=aiohttp.ClientTimeout(total=6), ssl=False
            ) as resp:
                if resp.status == 200:
                    text = await resp.text(errors="ignore")
                    if not email:
                        m = EMAIL_REGEX.search(text)
                        if m:
                            email = m.group(0)
                    if not phone:
                        m = PHONE_REGEX.search(text)
                        if m:
                            phone = m.group(0)
                    if email and phone:
                        break
        except Exception:
            continue
    return {"email": email, "phone": phone}


async def search_suppliers_web(query: str, max_results: int = 6) -> list[dict]:
    """
    Ищет поставщиков через DuckDuckGo (упор на РФ), отсеивает тендерные/новостные
    сайты, дополнительно фильтрует через YandexGPT, затем достаёт email/телефон.
    """
    candidates = await _duckduckgo_search(query, max_results=max_results * 2)
    if not candidates:
        return []

    candidates = await _ai_filter_candidates(query, candidates)
    candidates = candidates[:max_results]

    async with aiohttp.ClientSession() as session:
        contacts_list = await asyncio.gather(
            *[_extract_contacts(session, c["url"]) for c in candidates],
            return_exceptions=True,
        )

    results = []
    for c, contacts in zip(candidates, contacts_list):
        if isinstance(contacts, Exception):
            contacts = {"email": None, "phone": None}
        results.append({
            "name": c["name"],
            "url": c["url"],
            "email": contacts["email"],
            "phone": contacts["phone"],
            "address": None,
            "categories": [],
        })
    return results
