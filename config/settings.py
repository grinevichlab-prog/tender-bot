import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# --- YandexGPT ---
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_URL = os.getenv(
    "YANDEX_URL",
    "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
)

# --- Почта (Mail.ru) ---
EMAIL_LOGIN = os.getenv("EMAIL_LOGIN")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_SMTP_HOST = "smtp.mail.ru"
EMAIL_SMTP_PORT = 465
EMAIL_IMAP_HOST = "imap.mail.ru"
EMAIL_IMAP_PORT = 993

# --- База данных (Railway PostgreSQL) ---
DATABASE_URL = os.getenv("DATABASE_URL")
TENDER_GROUP_ID = int(os.getenv("TENDER_GROUP_ID", "0"))


def check_settings():
    """Проверяет, что все обязательные переменные окружения заданы."""
    missing = []
    required = {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "YANDEX_FOLDER_ID": YANDEX_FOLDER_ID,
        "YANDEX_API_KEY": YANDEX_API_KEY,
        "EMAIL_LOGIN": EMAIL_LOGIN,
        "EMAIL_PASSWORD": EMAIL_PASSWORD,
        "DATABASE_URL": DATABASE_URL,
    }
    for name, value in required.items():
        if not value:
            missing.append(name)
    if missing:
        print("ВНИМАНИЕ: не заданы переменные окружения:", ", ".join(missing))
    else:
        print("Все переменные окружения на месте.")