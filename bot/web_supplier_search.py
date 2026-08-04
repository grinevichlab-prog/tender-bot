"""
Бесплатный поиск поставщиков через веб-поиск (DuckDuckGo, без API-ключа)
с фильтром на российский регион + извлечение email/телефона с найденных сайтов.
"""

import re
import aiohttp

DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_REGEX = re.compile(r"(?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")
RESULT_LINK_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"')
TITLE_RE = re.compile(r'class="result__a"[^>]*>(.*?)</a>', re.DOTALL)

EXCLUDED_DOMAINS = {
    "wikipedia.org", "youtube.com", "vk.com", "ok.ru", "zakupki.gov.ru",
    "gosuslugi.ru", "yandex.ru", "google.com", "avito.ru", "2gis.ru",
    "rusprofile.ru", "list-org.com", "yandex.by",
}


def _domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1).lower() if m else ""


async def _duckduckgo_search(query: str, max_results: int = 8) -> list[dict]:
    params = {"q": query, "kl": "ru-ru"}
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
        seen_domains.add(domain)
        results.append({"name": title or domain, "url": link})
        if len(results) >= max_results:
            break

    return results


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
    Ищет поставщиков через DuckDuckGo (с упором на российский регион),
    затем пытается достать email/телефон с сайта каждого кандидата.
    Возвращает список {name, url, email, phone, address: None, categories: []}.
    """
    candidates = await _duckduckgo_search(query, max_results=max_results)
    if not candidates:
        return []

    results = []
    async with aiohttp.ClientSession() as session:
        for c in candidates:
            contacts = await _extract_contacts(session, c["url"])
            results.append({
                "name": c["name"],
                "url": c["url"],
                "email": contacts["email"],
                "phone": contacts["phone"],
                "address": None,
                "categories": [],
            })
    return results
