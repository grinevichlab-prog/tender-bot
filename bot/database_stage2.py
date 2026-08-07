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
    penalties JSONB,
    UNIQUE(chat_id, thread_id)
);

CREATE TABLE IF NOT EXISTS tender_items (
    id SERIAL PRIMARY KEY,
    tender_id INTEGER REFERENCES tenders(id) ON DELETE CASCADE,
    position_number INTEGER,
    name TEXT NOT NULL,
    quantity NUMERIC,
    unit TEXT,
    requirements JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(tender_id, position_number)
);

CREATE TABLE IF NOT EXISTS product_models (
    id SERIAL PRIMARY KEY,
    tender_item_id INTEGER REFERENCES tender_items(id) ON DELETE CASCADE,
    manufacturer TEXT,
    model TEXT NOT NULL,
    product_name TEXT,
    source_url TEXT,
    source_title TEXT,
    specifications JSONB,
    price NUMERIC,
    currency TEXT,
    price_includes_vat BOOLEAN,
    availability TEXT,
    match_status TEXT,
    match_result JSONB,
    selected BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(tender_item_id, model, source_url)
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
            ALTER TABLE tenders ADD COLUMN IF NOT EXISTS penalties JSONB;
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
    raw_deadline = analysis.get("delivery_deadline")
    summary = analysis.get("summary")

    # Если срок поставки указан текстом (не конкретной датой), не теряем эту
    # информацию - добавляем её в описание, раз в поле DATE она не помещается.
    if raw_deadline and not safe_deadline:
        note = f"Срок поставки (не является календарной датой): {raw_deadline}."
        summary = f"{summary} {note}".strip() if summary else note

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE tenders SET
                tender_name = $2,
                subject = $3,
                items = $4::jsonb,
                nmck = $5,
                delivery_deadline = CASE WHEN $6::TEXT IS NOT NULL THEN $6::DATE ELSE delivery_deadline END,
                region = $7,
                purchase_type = $8,
                classification = $9,
                summary = $10,
                contract_validity = $11,
                delivery_period = $12::jsonb,
                penalties = $13::jsonb
            WHERE id = $1
            """,
            tender_id,
            analysis.get("tender_name"),
            analysis.get("subject"),
            json.dumps(analysis.get("items")) if analysis.get("items") else None,
            analysis.get("nmck"),
            safe_deadline,
            analysis.get("region"),
            analysis.get("purchase_type"),
            analysis.get("classification"),
            summary,
            analysis.get("contract_validity"),
            json.dumps(analysis.get("delivery_period"), ensure_ascii=False) if analysis.get("delivery_period") else None,
            json.dumps(analysis.get("penalties") or [], ensure_ascii=False),
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



# ---------------------- ПОЗИЦИИ И МОДЕЛИ ----------------------
async def sync_tender_items(tender_id: int, items: list[dict]) -> list[int]:
    """Синхронизирует позиции тендера с таблицей tender_items и возвращает их id."""
    ids = []
    async with pool.acquire() as conn:
        for position, item in enumerate(items or [], 1):
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            row = await conn.fetchrow(
                """
                INSERT INTO tender_items (tender_id, position_number, name, quantity, unit, requirements)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                ON CONFLICT (tender_id, position_number) DO UPDATE SET
                    name=EXCLUDED.name, quantity=EXCLUDED.quantity, unit=EXCLUDED.unit, requirements=EXCLUDED.requirements
                RETURNING id
                """,
                tender_id, position, name, item.get("quantity"), item.get("unit"),
                json.dumps(item.get("requirements") or []),
            )
            ids.append(row["id"])
    return ids


async def get_tender_items(tender_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM tender_items WHERE tender_id = $1 ORDER BY position_number, id",
            tender_id,
        )
        result = []
        for row in rows:
            item = dict(row)
            if isinstance(item.get("requirements"), str):
                try:
                    item["requirements"] = json.loads(item["requirements"])
                except Exception:
                    item["requirements"] = []
            result.append(item)
        return result


async def save_product_model(tender_item_id: int, model: dict) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO product_models
                (tender_item_id, manufacturer, model, product_name, source_url,
                 source_title, specifications, price, currency, price_includes_vat,
                 availability, match_status, match_result)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12,$13::jsonb)
            ON CONFLICT (tender_item_id, model, source_url)
            DO UPDATE SET
                manufacturer=EXCLUDED.manufacturer,
                product_name=EXCLUDED.product_name,
                source_title=EXCLUDED.source_title,
                specifications=EXCLUDED.specifications,
                price=EXCLUDED.price,
                currency=EXCLUDED.currency,
                price_includes_vat=EXCLUDED.price_includes_vat,
                availability=EXCLUDED.availability,
                match_status=EXCLUDED.match_status,
                match_result=EXCLUDED.match_result
            RETURNING id
            """,
            tender_item_id,
            model.get("manufacturer"),
            model.get("model"),
            model.get("product_name"),
            model.get("source_url"),
            model.get("source_title"),
            json.dumps(model.get("specifications") or {}),
            model.get("price"),
            model.get("currency"),
            model.get("price_includes_vat"),
            model.get("availability"),
            model.get("match_status"),
            json.dumps(model.get("match_result") or {}),
        )


async def get_product_models(tender_item_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM product_models WHERE tender_item_id=$1 ORDER BY id",
            tender_item_id,
        )
        return [dict(r) for r in rows]


async def select_product_model(model_id: int, tender_item_id: int) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE product_models SET selected=FALSE WHERE tender_item_id=$1",
                tender_item_id,
            )
            await conn.execute(
                "UPDATE product_models SET selected=TRUE WHERE id=$1 AND tender_item_id=$2",
                model_id, tender_item_id,
            )

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
