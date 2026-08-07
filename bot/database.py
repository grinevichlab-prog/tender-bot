"""
Модуль для работы с PostgreSQL: пул соединений, создание таблиц, CRUD.
"""

import asyncpg
import re
from config.settings import DATABASE_URL
import json

pool = None

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _sanitize_date(value):
    """Возвращает value только если это валидная дата ГГГГ-ММ-ДД, иначе None."""
    if not value or not isinstance(value, str) or not _DATE_RE.match(value):
        return None
    return value

async def create_pool():
    return await asyncpg.create_pool(DATABASE_URL, statement_cache_size=0)

async def set_pool(p):
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
    user_id BIGINT REFERENCES users(id),
    chat_id TEXT NOT NULL,
    thread_id TEXT,
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
    contract_validity TEXT,
    delivery_period JSONB,
    contract_validity_details JSONB,
    penalties JSONB,
    analysis_conflicts JSONB,
    source_evidence JSONB,
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

CREATE TABLE IF NOT EXISTS tender_items (
    id SERIAL PRIMARY KEY,
    tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
    position_number INTEGER,
    name TEXT NOT NULL,
    quantity NUMERIC,
    unit TEXT,
    requirements JSONB NOT NULL DEFAULT '[]'::jsonb,
    quantity_conflicts JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tender_requirements (
    id SERIAL PRIMARY KEY,
    tender_item_id INTEGER NOT NULL REFERENCES tender_items(id) ON DELETE CASCADE,
    parameter TEXT NOT NULL,
    operator TEXT NOT NULL,
    value JSONB,
    min_value NUMERIC,
    max_value NUMERIC,
    unit TEXT,
    mandatory BOOLEAN DEFAULT TRUE,
    raw_text TEXT,
    source_document TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS requirement_conflicts (
    id SERIAL PRIMARY KEY,
    tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    conflict_type TEXT NOT NULL,
    values_json JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS source_evidence (
    id SERIAL PRIMARY KEY,
    tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    item_index INTEGER,
    source_document TEXT,
    source_text TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
"""


async def init_db():
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLES_SQL)

        await conn.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='tenders' AND column_name='user_id' AND data_type != 'bigint'
                ) THEN
                    ALTER TABLE tenders ALTER COLUMN user_id TYPE BIGINT;
                END IF;
            END;
            $$;
        """)

        await conn.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='tenders' AND column_name='chat_id' AND data_type != 'text'
                ) THEN
                    ALTER TABLE tenders ALTER COLUMN chat_id TYPE TEXT USING chat_id::TEXT;
                END IF;
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='tenders' AND column_name='thread_id' AND data_type != 'text'
                ) THEN
                    ALTER TABLE tenders ALTER COLUMN thread_id TYPE TEXT USING thread_id::TEXT;
                END IF;
            END;
            $$;
        """)

        await conn.execute("""
            ALTER TABLE tenders ADD COLUMN IF NOT EXISTS contract_validity TEXT;
            ALTER TABLE tenders ADD COLUMN IF NOT EXISTS delivery_period JSONB;
            ALTER TABLE tenders ADD COLUMN IF NOT EXISTS contract_validity_details JSONB;
            ALTER TABLE tenders ADD COLUMN IF NOT EXISTS penalties JSONB;
            ALTER TABLE tenders ADD COLUMN IF NOT EXISTS analysis_conflicts JSONB;
            ALTER TABLE tenders ADD COLUMN IF NOT EXISTS source_evidence JSONB;
        """)

        await conn.execute("""
            ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS url TEXT;
        """)

        print("Таблицы БД проверены/созданы.")


# ---------------------- ПОЛЬЗОВАТЕЛИ ----------------------
async def get_or_create_user(telegram_id: int, name: str) -> int:
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
async def create_tender_for_thread(chat_id: str, thread_id: str, user_id: int):
    async with pool.acquire() as conn:
        tender_id = await conn.fetchval(
            """
            INSERT INTO tenders (user_id, chat_id, thread_id)
            VALUES ($3, $1, $2)
            ON CONFLICT (chat_id, thread_id) DO NOTHING
            RETURNING id
            """,
            chat_id, thread_id, user_id,
        )
        if tender_id is None:
            tender_id = await conn.fetchval(
                "SELECT id FROM tenders WHERE chat_id = $1 AND thread_id = $2",
                chat_id, thread_id,
            )
        return tender_id


async def get_tender_by_thread(chat_id: str, thread_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM tenders WHERE chat_id = $1 AND thread_id = $2",
            chat_id, thread_id,
        )
        return dict(row) if row else None


async def update_tender_analysis(tender_id: int, analysis: dict):
    safe_deadline = _sanitize_date(analysis.get("delivery_deadline"))

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE tenders SET
                    tender_name = $2,
                    subject = $3,
                    items = $4::jsonb,
                    nmck = $5,
                    delivery_deadline = $6::DATE,
                    region = $7,
                    purchase_type = $8,
                    classification = $9,
                    summary = $10,
                    contract_validity = $11,
                    delivery_period = $12::jsonb,
                    contract_validity_details = $13::jsonb,
                    penalties = $14::jsonb,
                    analysis_conflicts = $15::jsonb,
                    source_evidence = $16::jsonb,
                    updated_at = NOW()
                WHERE id = $1
                """,
                tender_id,
                analysis.get("tender_name"),
                analysis.get("subject"),
                json.dumps(analysis.get("items") or [], ensure_ascii=False),
                analysis.get("nmck"),
                safe_deadline,
                analysis.get("region"),
                analysis.get("purchase_type"),
                analysis.get("classification"),
                analysis.get("summary"),
                analysis.get("contract_validity"),
                json.dumps(analysis.get("delivery_period"), ensure_ascii=False) if analysis.get("delivery_period") else None,
                json.dumps(analysis.get("contract_validity_details"), ensure_ascii=False) if analysis.get("contract_validity_details") else None,
                json.dumps(analysis.get("penalties") or [], ensure_ascii=False),
                json.dumps(analysis.get("conflicts") or [], ensure_ascii=False),
                json.dumps(analysis.get("source_evidence") or [], ensure_ascii=False),
            )

            await conn.execute("DELETE FROM tender_items WHERE tender_id = $1", tender_id)
            await conn.execute("DELETE FROM requirement_conflicts WHERE tender_id = $1", tender_id)
            await conn.execute("DELETE FROM source_evidence WHERE tender_id = $1", tender_id)

            for position_number, item in enumerate(analysis.get("items") or [], 1):
                item_id = await conn.fetchval(
                    """
                    INSERT INTO tender_items
                        (tender_id, position_number, name, quantity, unit, requirements, quantity_conflicts)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
                    RETURNING id
                    """,
                    tender_id, position_number, item.get("name"), item.get("quantity"),
                    item.get("unit"),
                    json.dumps(item.get("requirements") or [], ensure_ascii=False),
                    json.dumps(item.get("quantity_conflicts") or [], ensure_ascii=False),
                )

                for req in item.get("requirements") or []:
                    if not isinstance(req, dict) or not req.get("parameter"):
                        continue
                    value = req.get("value")
                    min_value = req.get("min")
                    max_value = req.get("max")
                    await conn.execute(
                        """
                        INSERT INTO tender_requirements
                            (tender_item_id, parameter, operator, value, min_value, max_value, unit, mandatory, raw_text, source_document)
                        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10)
                        """,
                        item_id, req.get("parameter"), req.get("operator") or "TEXT",
                        json.dumps(value, ensure_ascii=False) if value is not None else None,
                        min_value, max_value, req.get("unit"),
                        bool(req.get("mandatory", True)), req.get("raw_text"),
                        req.get("source_document"),
                    )

            for conflict in analysis.get("conflicts") or []:
                if not isinstance(conflict, dict):
                    continue
                await conn.execute(
                    """
                    INSERT INTO requirement_conflicts
                        (tender_id, field, conflict_type, values_json, status)
                    VALUES ($1, $2, $3, $4::jsonb, $5)
                    """,
                    tender_id, conflict.get("field") or "unknown",
                    conflict.get("type") or "UNKNOWN",
                    json.dumps(conflict.get("values") or [], ensure_ascii=False),
                    conflict.get("status") or "OPEN",
                )

            for evidence in analysis.get("source_evidence") or []:
                if not isinstance(evidence, dict):
                    continue
                await conn.execute(
                    """
                    INSERT INTO source_evidence
                        (tender_id, field, item_index, source_document, source_text)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    tender_id, evidence.get("field") or "unknown",
                    evidence.get("item_index"), evidence.get("source_document"),
                    evidence.get("raw_text"),
                )


async def set_summary_message_id(tender_id: int, message_id: int):
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
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tender_documents
                (tender_id, file_name, file_path, extracted_text, analysis_json, is_useful)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            """,
            tender_id, file_name, file_path, extracted_text,
            json.dumps(analysis_json) if analysis_json else None, is_useful,
        )


async def get_tender_documents(tender_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM tender_documents WHERE tender_id = $1 ORDER BY id",
            tender_id,
        )
        return [dict(r) for r in rows]


# ---------------------- ПОСТАВЩИКИ ----------------------
async def add_supplier(
    name: str,
    phone: str | None,
    email: str | None,
    city: str | None,
    categories: list[str],
    url: str | None,
) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO suppliers (name, phone, email, city, categories, url)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            name, phone, email, city, categories or [], url,
        )
