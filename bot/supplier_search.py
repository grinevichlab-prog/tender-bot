"""
Поиск поставщиков через API Поиска по организациям Яндекс.Карт
(https://search-maps.yandex.ru/v1/), + попытка найти email на сайте компании.
"""

import re
import aiohttp
from config.settings import YANDEX_MAPS_API_KEY

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


async def search_organizations(query: str, results: int = 8) -> list[dict]:
    """
    Ищет организации по текстовому запросу (например "насосы Красноярск").
    Возвращает список {name, address, phone, url, categories}.
    """
    if not YANDEX_MAPS_API_KEY:
        print("[supplier_search] YANDEX_MAPS_API_KEY не задан", flush=True)
        return []

    params = {
        "text": query,
        "type": "biz",
        "lang": "ru_RU",
        "results": str(results),
        "apikey": YANDEX_MAPS_API_KEY,
    }
    url = "https://search-maps.yandex.ru/v1/"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"[supplier_search] Yandex API error {resp.status}: {error_text}", flush=True)
                    return []
                data = await resp.json()
    except Exception as e:
        print(f"[supplier_search] ошибка запроса к Яндекс API: {e}", flush=True)
        return []

    organizations = []
    for feature in data.get("features", []):
        meta = feature.get("properties", {}).get("CompanyMetaData") or {}
        if not meta:
            continue
        phones = meta.get("Phones") or []
        phone = phones[0].get("formatted") if phones else None
        categories = [c.get("name") for c in (meta.get("Categories") or []) if c.get("name")]
        organizations.append({
            "name": meta.get("name"),
            "address": meta.get("address"),
            "phone": phone,
            "url": meta.get("url"),
            "categories": categories,
        })
    return organizations


async def try_extract_email(url: str) -> str | None:
    """
    Пытается найти email на сайте компании: главная страница, затем /contacts, /kontakty.
    """
    if not url:
        return None

    paths = ["", "/contacts", "/kontakty"]
    try:
        async with aiohttp.ClientSession() as session:
            for path in paths:
                try:
                    target = url.rstrip("/") + path
                    async with session.get(
                        target, timeout=aiohttp.ClientTimeout(total=6), ssl=False
                    ) as resp:
                        if resp.status == 200:
                            text = await resp.text(errors="ignore")
                            match = EMAIL_REGEX.search(text)
                            if match:
                                return match.group(0)
                except Exception:
                    continue
    except Exception as e:
        print(f"[supplier_search] ошибка при извлечении email с {url}: {e}", flush=True)
    return None
