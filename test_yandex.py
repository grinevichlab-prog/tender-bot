import asyncio
import aiohttp
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config.settings import YANDEX_FOLDER_ID, YANDEX_API_KEY, YANDEX_URL


async def test_yandex_gpt():
    print(f"YANDEX_FOLDER_ID: {YANDEX_FOLDER_ID}")
    print(f"YANDEX_API_KEY: {YANDEX_API_KEY[:10]}..." if YANDEX_API_KEY else "YANDEX_API_KEY: НЕ ЗАДАН")
    print(f"YANDEX_URL: {YANDEX_URL}")
    print("-" * 50)

    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json",
    }

    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {
            "stream": False,
            "temperature": 0.3,
            "maxTokens": 100,
        },
        "messages": [
            {"role": "user", "text": "Скажи одним словом: работает"}
        ],
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(YANDEX_URL, headers=headers, json=body) as resp:
            status = resp.status
            text = await resp.text()
            print(f"HTTP статус ответа: {status}")
            print(f"Ответ сервера:\n{text}")

            if status == 200:
                print("-" * 50)
                print("УСПЕХ: ключи рабочие, YandexGPT отвечает.")
            else:
                print("-" * 50)
                print("ОШИБКА: ключи не работают или закончились права доступа. Смотри текст ответа выше.")


if __name__ == "__main__":
    asyncio.run(test_yandex_gpt())