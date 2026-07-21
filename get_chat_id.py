import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from aiogram import Bot
from config.settings import TELEGRAM_TOKEN


async def get_updates():
    bot = Bot(token=TELEGRAM_TOKEN)
    updates = await bot.get_updates()
    if not updates:
        print("Обновлений нет. Напиши любое сообщение в группе 'тендер' и запусти скрипт снова.")
        return

    for update in updates:
        if update.message:
            chat = update.message.chat
            print(f"Чат: {chat.title or chat.first_name}")
            print(f"Тип чата: {chat.type}")
            print(f"ID чата: {chat.id}")
            if update.message.message_thread_id:
                print(f"ID темы (топика): {update.message.message_thread_id}")
            print(f"Текст сообщения: {update.message.text}")
            print("-" * 40)

    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(get_updates())