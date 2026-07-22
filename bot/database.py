"""
Модуль для работы с PostgreSQL: пул соединений, создание таблиц, CRUD.
"""

import asyncpg
from config.settings import DATABASE_URL

# Глобальная переменная для пула
pool = None


async def create_pool():
    """Создаёт и возвращает пул соединений с БД."""
    return await asyncpg.create_pool(DATABASE_URL)


async def set_pool(p):
    """Сохраняет пул соединений в глобальную переменную модуля."""
    global pool
    pool = p


# ---------------------- СОЗДАНИЕ ТАБЛИЦ ----------------------
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    name TEXT,
    email_login TEXT,
    email_password TEXT,
    role TEXT DEFAULT 'user',
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS suppliers (
    id SERIAL PRIMARY KEY,
    name TEXT,
    inn TEXT,
    email TEXT,
    phone TEXT,
    contact_person TEXT,
    city TEXT,
    categories TEXT[],
    brands TEXT[],
    keywords TEXT[],
    rating NUMERIC(3,2) DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    last_deal_date DATE
);

CREATE TABLE IF NOT EXISTS platforms (
    id SERIAL PRIMARY KEY,
    name TEXT,
    url TEXT,
    fee_type TEXT,
    fee_percent NUMERIC,
    fee_fixed NUMERIC,
    fee_max NUMERIC,
    fee_min NUMERIC
);

CREATE TABLE IF NOT EXISTS tenders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    chat_id BIGINT NOT NULL,
    thread_id BIGINT,
    name TEXT,
    status TEXT DEFAULT 'new',
    nmck NUMERIC,
    delivery_deadline DATE,
    region TEXT,
    supplier_id INTEGER REFERENCES suppliers(id),
    final_price NUMERIC,
    final_margin NUMERIC,
    archived_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    purchase_type TEXT,
    classification TEXT,
    summary TEXT,
    summary_message_id BIGINT,
    tender_name TEXT,
    subject TEXT,
    items JSONB,
    UNIQUE(chat_id, thread_id)
);

CREATE TABLE IF NOT EXISTS supplier_deals (
    id SERIAL PRIMARY KEY,
    supplier_id INTEGER REFERENCES suppliers(id),
    tender_id INTEGER REFERENCES tenders(id),
    product_name TEXT,
    price_total NUMERIC,
    delivery_days_actual INTEGER,
    quality_rating INTEGER CHECK(quality_rating BETWEEN 1 AND 5),
    issues TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS delivery_quotes (
    id SERIAL PRIMARY KEY,
    tender_id INTEGER REFERENCES tenders(id),
    supplier_id INTEGER REFERENCES suppliers(id),
    origin_city TEXT,
    destination_city TEXT,
    weight_kg NUMERIC,
    volume_m3 NUMERIC,
    cost NUMERIC,
    delivery_days INTEGER
);

CREATE TABLE IF NOT EXISTS tender_documents (
    id SERIAL PRIMARY KEY,
    tender_id INTEGER REFERENCES tenders(id),
    file_name TEXT NOT NULL,
    file_path TEXT,
    extracted_text TEXT,
    analysis_json JSONB,
    is_useful BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
"""


async def init_db():
    """Выполняет создание таблиц и миграции при старте."""
    async with pool.acquire() as conn:
        # Создаём таблицы, если их нет
        await conn.execute(CREATE_TABLES_SQL)

        # Миграция: добавляем chat_id и thread_id, если они отсутствуют
        # Проверяем наличие столбца chat_id
        has_chat_id = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name='tenders' AND column_name='chat_id')"
        )
        if not has_chat_id:
            print("Добавляем столбец chat_id в tenders...")
            await conn.execute("ALTER TABLE tenders ADD COLUMN chat_id BIGINT")
            # Если столбца не было, thread_id тоже может отсутствовать
            has_thread_id = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_name='tenders' AND column_name='thread_id')"
            )
            if not has_thread_id:
                await conn.execute("ALTER TABLE tenders ADD COLUMN thread_id BIGINT")
            # Заполняем chat_id значением по умолчанию для существующих записей (возможно, 0)
            # Но лучше установить NOT NULL после заполнения, если требуется.
            # Пока оставим без NOT NULL, чтобы не сломать старые записи.
            # При желании можно потом обновить и установить NOT NULL.
            print("Столбцы chat_id и thread_id добавлены.")

        # Убедимся, что существует уникальный индекс для (chat_id, thread_id),
        # если его ещё нет. Создадим, если отсутствует.
        index_exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='tenders_chat_thread_unique')"
        )
        if not index_exists:
            print("Создаём уникальный индекс на (chat_id, thread_id)...")
            await conn.execute(
                "CREATE UNIQUE INDEX tenders_chat_thread_unique ON tenders (chat_id, thread_id) "
                "WHERE chat_id IS NOT NULL AND thread_id IS NOT NULL"
            )

    print("Таблицы БД проверены/созданы.")


# ---------------------- ПОЛЬЗОВАТЕЛИ ----------------------
async def get_or_create_user(telegram_id: int, name: str) -> int:
    """Возвращает ID пользователя, создаёт если не существует."""
    async with pool.acquire() as conn:
        user_id = await conn.fetchval(
            "SELECT id FROM users WHERE telegram_id = $1", telegram_id
        )
        if user_id:
            return user_id
        return await conn.fetchval(
            "INSERT INTO users (telegram_id, name) VALUES ($1, $2) RETURNING id",
            telegram_id, name,
        )


# ---------------------- ТЕНДЕРЫ ----------------------
async def create_tender_for_thread(chat_id: int, thread_id: int, user_id: int):
    """Создаёт тендер для указанной темы, если его ещё нет. Возвращает tender_id."""
    async with pool.acquire() as conn:
        tender_id = await conn.fetchval(
            """
            INSERT INTO tenders (user_id, chat_id, thread_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (chat_id, thread_id) DO NOTHING
            RETURNING id
            """,
            user_id, chat_id, thread_id,
        )
        if tender_id is None:
            tender_id = await conn.fetchval(
                "SELECT id FROM tenders WHERE chat_id = $1 AND thread_id = $2",
                chat_id, thread_id,
            )
        return tender_id


async def get_tender_by_thread(chat_id: int, thread_id: int) -> dict | None:
    """Возвращает запись тендера по чату и теме."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM tenders WHERE chat_id = $1 AND thread_id = $2",
            chat_id, thread_id,
        )
        return dict(row) if row else None


async def update_tender_analysis(tender_id: int, analysis: dict):
    """Обновляет поля тендера данными из объединённого анализа."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE tenders SET
                tender_name = $2,
                subject = $3,
                items = $4,
                nmck = $5,
                delivery_deadline = CASE WHEN $6::TEXT IS NOT NULL THEN $6::DATE ELSE delivery_deadline END,
                region = $7,
                purchase_type = $8,
                classification = $9,
                summary = $10
            WHERE id = $1
            """,
            tender_id,
            analysis.get("tender_name"),
            analysis.get("subject"),
            analysis.get("items") if analysis.get("items") else None,
            analysis.get("nmck"),
            analysis.get("delivery_deadline"),
            analysis.get("region"),
            analysis.get("purchase_type"),
            analysis.get("classification"),
            analysis.get("summary"),
        )


async def set_summary_message_id(tender_id: int, message_id: int):
    """Сохраняет ID сообщения-карточки для последующего обновления."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tenders SET summary_message_id = $1 WHERE id = $2",
            message_id, tender_id,
        )


# ---------------------- ДОКУМЕНТЫ ----------------------
async def add_tender_document(
    tender_id: int,
    file_name: str,
    file_path: str,
    extracted_text: str,
    analysis_json: dict,
    is_useful: bool,
):
    """Сохраняет запись о загруженном документе и результате анализа."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tender_documents
                (tender_id, file_name, file_path, extracted_text, analysis_json, is_useful)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            tender_id, file_name, file_path, extracted_text,
            analysis_json, is_useful,
        )


async def get_tender_documents(tender_id: int) -> list[dict]:
    """Возвращает список всех документов тендера."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM tender_documents WHERE tender_id = $1 ORDER BY id",
            tender_id,
        )
        return [dict(r) for r in rows]