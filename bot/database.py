"""
Модуль для работы с PostgreSQL: пул соединений, создание таблиц, CRUD.
"""

import asyncpg
import re
import json

from config.settings import DATABASE_URL


pool = None

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _sanitize_date(value):
    """Возвращает value только если это валидная дата ГГГГ-ММ-ДД, иначе None."""
    if not value or not isinstance(value, str) or not _DATE_RE.match(value):
        return None
    return value


async def create_pool():
    return await asyncpg.create_pool(
        DATABASE_URL,
        statement_cache_size=0,
    )


async def set_pool(p):
    global pool
    pool = p
async def save_models(tender_item_id: int, models: list[dict]):
    """Сохраняет найденные модели для позиции тендера"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Удаляем старые модели для этой позиции
        await conn.execute("DELETE FROM models WHERE tender_item_id = $1", tender_item_id)
        
        # Вставляем новые
        for model in models:
            await conn.execute("""
                INSERT INTO models 
                (tender_item_id, manufacturer, model, product_name, specifications, 
                 price, currency, availability, source_url, source_title)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
                tender_item_id,
                model.get('manufacturer'),
                model.get('model'),
                model.get('product_name'),
                json.dumps(model.get('specifications', {})),
                model.get('price'),
                model.get('currency', 'RUB'),
                model.get('availability'),
                model.get('source_url'),
                model.get('source_title')
            )
    finally:
        await conn.close()

async def get_models(tender_item_id: int) -> list[dict]:
    """Получает найденные модели для позиции"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT * FROM models WHERE tender_item_id = $1 ORDER BY id",
            tender_item_id
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()

async def select_model(tender_item_id: int, model_id: int):
    """Привязывает выбранную модель к позиции тендера"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("""
            UPDATE tender_items 
            SET selected_model_id = $1 
            WHERE id = $2
        """, model_id, tender_item_id)
    finally:
        await conn.close()

# ============================================================
# СОЗДАНИЕ ТАБЛИЦ
# ============================================================

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
    created_at TIMESTAMP DEFAULT NOW()
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


# ============================================================
# ИНИЦИАЛИЗАЦИЯ И МИГРАЦИИ БД
# ============================================================

async def init_db():
            # Таблица поставщиков
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS suppliers (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                inn TEXT,
                contact_person TEXT,
                phone TEXT,
                email TEXT,
                region TEXT,
                default_margin FLOAT DEFAULT 1.2,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # Таблица найденных моделей
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS models (
                id SERIAL PRIMARY KEY,
                tender_item_id INTEGER REFERENCES tender_items(id) ON DELETE CASCADE,
                manufacturer TEXT,
                model TEXT,
                product_name TEXT,
                specifications JSONB,
                price FLOAT,
                currency TEXT DEFAULT 'RUB',
                availability TEXT,
                source_url TEXT,
                source_title TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # Таблица коммерческих предложений
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS commercial_offers (
                id SERIAL PRIMARY KEY,
                tender_id INTEGER REFERENCES tenders(id) ON DELETE CASCADE,
                supplier_id INTEGER REFERENCES suppliers(id) ON DELETE CASCADE,
                data JSONB NOT NULL,
                total_amount FLOAT,
                status TEXT DEFAULT 'draft',
                created_at TIMESTAMP DEFAULT NOW(),
                sent_at TIMESTAMP
            )
        """)
        
        # Индексы
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_suppliers_user ON suppliers(user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_models_item ON models(tender_item_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_co_tender ON commercial_offers(tender_id)")
    async with pool.acquire() as conn:

        await conn.execute(CREATE_TABLES_SQL)

        # ----------------------------------------------------
        # Совместимость со старой схемой tenders
        # ----------------------------------------------------

        await conn.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'tenders'
                      AND column_name = 'user_id'
                      AND data_type != 'bigint'
                ) THEN
                    ALTER TABLE tenders
                    ALTER COLUMN user_id TYPE BIGINT;
                END IF;
            END;
            $$;
        """)

        await conn.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'tenders'
                      AND column_name = 'chat_id'
                      AND data_type != 'text'
                ) THEN
                    ALTER TABLE tenders
                    ALTER COLUMN chat_id TYPE TEXT
                    USING chat_id::TEXT;
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'tenders'
                      AND column_name = 'thread_id'
                      AND data_type != 'text'
                ) THEN
                    ALTER TABLE tenders
                    ALTER COLUMN thread_id TYPE TEXT
                    USING thread_id::TEXT;
                END IF;
            END;
            $$;
        """)

        # ----------------------------------------------------
        # Новые поля тендера
        # ----------------------------------------------------

        await conn.execute("""
            ALTER TABLE tenders
            ADD COLUMN IF NOT EXISTS contract_validity TEXT;

            ALTER TABLE tenders
            ADD COLUMN IF NOT EXISTS delivery_period JSONB;

            ALTER TABLE tenders
            ADD COLUMN IF NOT EXISTS penalties JSONB;
        """)

        # ----------------------------------------------------
        # URL поставщика
        # ----------------------------------------------------

        await conn.execute("""
            ALTER TABLE suppliers
            ADD COLUMN IF NOT EXISTS url TEXT;
        """)

        # ----------------------------------------------------
        # Миграция tender_items
        #
        # В старой БД таблица могла существовать без UNIQUE.
        # CREATE TABLE IF NOT EXISTS не меняет существующую таблицу,
        # поэтому ON CONFLICT (tender_id, position_number) падает:
        # there is no unique or exclusion constraint matching
        # the ON CONFLICT specification
        #
        # Сначала удаляем дубли.
        # Затем создаём UNIQUE INDEX.
        # ----------------------------------------------------

        await conn.execute("""
            DELETE FROM tender_items a
            USING tender_items b
            WHERE a.id > b.id
              AND a.tender_id = b.tender_id
              AND a.position_number = b.position_number;
        """)

        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            tender_items_tender_position_idx
            ON tender_items (
                tender_id,
                position_number
            );
        """)

        # ----------------------------------------------------
        # Миграция product_models
        #
        # В старой БД таблица могла существовать без UNIQUE.
        #
        # Поэтому CREATE TABLE IF NOT EXISTS недостаточно:
        # PostgreSQL НЕ меняет уже существующую таблицу.
        #
        # Сначала удаляем дубли.
        # Затем создаём UNIQUE INDEX.
        # ----------------------------------------------------

        await conn.execute("""
            DELETE FROM product_models a
            USING product_models b
            WHERE a.id > b.id
              AND a.tender_item_id = b.tender_item_id
              AND a.model = b.model
              AND a.source_url IS NOT DISTINCT FROM b.source_url;
        """)

        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            product_models_tender_item_model_url_idx
            ON product_models (
                tender_item_id,
                model,
                source_url
            );
        """)

        print("Таблицы БД проверены/созданы.")


# ============================================================
# ПОЛЬЗОВАТЕЛИ
# ============================================================

async def get_or_create_user(
    telegram_id: int,
    name: str,
) -> int:

    async with pool.acquire() as conn:

        user_id = await conn.fetchval(
            """
            SELECT id
            FROM users
            WHERE telegram_id = $1
            """,
            telegram_id,
        )

        if user_id:
            return user_id

        return await conn.fetchval(
            """
            INSERT INTO users (
                telegram_id,
                name
            )
            VALUES ($1, $2)
            RETURNING id
            """,
            telegram_id,
            name,
        )


# ============================================================
# ТЕНДЕРЫ
# ============================================================

async def create_tender_for_thread(
    chat_id: str,
    thread_id: str,
    user_id: int,
):

    async with pool.acquire() as conn:

        tender_id = await conn.fetchval(
            """
            INSERT INTO tenders (
                user_id,
                chat_id,
                thread_id
            )
            VALUES ($3, $1, $2)

            ON CONFLICT (
                chat_id,
                thread_id
            )
            DO NOTHING

            RETURNING id
            """,
            chat_id,
            thread_id,
            user_id,
        )

        if tender_id is None:

            tender_id = await conn.fetchval(
                """
                SELECT id
                FROM tenders
                WHERE chat_id = $1
                  AND thread_id = $2
                """,
                chat_id,
                thread_id,
            )

        return tender_id


async def get_tender_by_thread(
    chat_id: str,
    thread_id: str,
) -> dict | None:

    async with pool.acquire() as conn:

        row = await conn.fetchrow(
            """
            SELECT *
            FROM tenders
            WHERE chat_id = $1
              AND thread_id = $2
            """,
            chat_id,
            thread_id,
        )

        return dict(row) if row else None


async def update_tender_analysis(
    tender_id: int,
    analysis: dict,
):

    safe_deadline = _sanitize_date(
        analysis.get("delivery_deadline")
    )

    raw_deadline = analysis.get(
        "delivery_deadline"
    )

    summary = analysis.get("summary")

    # Если срок поставки указан текстом,
    # а не конкретной датой, не теряем информацию.
    if raw_deadline and not safe_deadline:

        note = (
            "Срок поставки "
            "(не является календарной датой): "
            f"{raw_deadline}."
        )

        summary = (
            f"{summary} {note}".strip()
            if summary
            else note
        )

    async with pool.acquire() as conn:

        await conn.execute(
            """
            UPDATE tenders
            SET
                tender_name = $2,
                subject = $3,
                items = $4::jsonb,
                nmck = $5,

                delivery_deadline =
                    CASE
                        WHEN $6::TEXT IS NOT NULL
                        THEN $6::DATE
                        ELSE delivery_deadline
                    END,

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

            json.dumps(
                analysis.get("items")
            )
            if analysis.get("items")
            else None,

            analysis.get("nmck"),

            safe_deadline,

            analysis.get("region"),

            analysis.get("purchase_type"),

            analysis.get("classification"),

            summary,

            analysis.get("contract_validity"),

            json.dumps(
                analysis.get("delivery_period"),
                ensure_ascii=False,
            )
            if analysis.get("delivery_period")
            else None,

            json.dumps(
                analysis.get("penalties") or [],
                ensure_ascii=False,
            ),
        )


async def set_summary_message_id(
    tender_id: int,
    message_id: int,
):

    async with pool.acquire() as conn:

        await conn.execute(
            """
            UPDATE tenders
            SET summary_message_id = $1
            WHERE id = $2
            """,
            message_id,
            tender_id,
        )


# ============================================================
# ДОКУМЕНТЫ
# ============================================================

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
            INSERT INTO tender_documents (
                tender_id,
                file_name,
                file_path,
                extracted_text,
                analysis_json,
                is_useful
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5::jsonb,
                $6
            )
            """,

            tender_id,
            file_name,
            file_path,
            extracted_text,

            json.dumps(
                analysis_json
            )
            if analysis_json
            else None,

            is_useful,
        )


async def get_tender_documents(
    tender_id: int,
) -> list[dict]:

    async with pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT *
            FROM tender_documents
            WHERE tender_id = $1
            ORDER BY id
            """,
            tender_id,
        )

        return [
            dict(row)
            for row in rows
        ]


# ============================================================
# ПОЗИЦИИ ТЕНДЕРА
# ============================================================

async def sync_tender_items(
    tender_id: int,
    items: list[dict],
) -> list[int]:

    ids = []

    async with pool.acquire() as conn:

        for position, item in enumerate(
            items or [],
            1,
        ):

            name = str(
                item.get("name") or ""
            ).strip()

            if not name:
                continue

            row = await conn.fetchrow(
                """
                INSERT INTO tender_items (
                    tender_id,
                    position_number,
                    name,
                    quantity,
                    unit,
                    requirements
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    $5,
                    $6::jsonb
                )

                ON CONFLICT (
                    tender_id,
                    position_number
                )

                DO UPDATE SET
                    name = EXCLUDED.name,
                    quantity = EXCLUDED.quantity,
                    unit = EXCLUDED.unit,
                    requirements = EXCLUDED.requirements

                RETURNING id
                """,

                tender_id,
                position,
                name,
                item.get("quantity"),
                item.get("unit"),

                json.dumps(
                    item.get("requirements") or [],
                    ensure_ascii=False,
                ),
            )

            ids.append(row["id"])

    return ids


async def get_tender_items(
    tender_id: int,
) -> list[dict]:

    async with pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT *
            FROM tender_items
            WHERE tender_id = $1
            ORDER BY position_number, id
            """,
            tender_id,
        )

        result = []

        for row in rows:

            item = dict(row)

            if isinstance(
                item.get("requirements"),
                str,
            ):

                try:
                    item["requirements"] = json.loads(
                        item["requirements"]
                    )

                except Exception:
                    item["requirements"] = []

            result.append(item)

        return result


# ============================================================
# МОДЕЛИ ТОВАРОВ
# ============================================================

async def save_product_model(
    tender_item_id: int,
    model: dict,
) -> int:
    """
    Сохраняет найденную модель.

    ВАЖНО:

    Здесь специально НЕ используется ON CONFLICT.

    Причина:
    существующая PostgreSQL БД могла быть создана
    до появления уникального ограничения.

    Мы сначала ищем существующую запись,
    затем UPDATE либо INSERT.

    Это позволяет корректно работать даже со старой БД.
    """

    async with pool.acquire() as conn:

        specifications_json = json.dumps(
            model.get("specifications") or {},
            ensure_ascii=False,
        )

        match_result_json = json.dumps(
            model.get("match_result") or {},
            ensure_ascii=False,
        )

        manufacturer = model.get(
            "manufacturer"
        )

        model_name = str(
            model.get("model") or ""
        ).strip()

        product_name = model.get(
            "product_name"
        )

        source_url = model.get(
            "source_url"
        )

        source_title = model.get(
            "source_title"
        )

        price = model.get(
            "price"
        )

        currency = model.get(
            "currency"
        )

        price_includes_vat = model.get(
            "price_includes_vat"
        )

        availability = model.get(
            "availability"
        )

        match_status = model.get(
            "match_status"
        )

        if not model_name:
            raise ValueError(
                "Нельзя сохранить модель без названия модели."
            )

        # ----------------------------------------------------
        # Ищем существующую запись.
        #
        # IS NOT DISTINCT FROM нужен для корректной
        # обработки NULL в source_url.
        # ----------------------------------------------------

        existing_id = await conn.fetchval(
            """
            SELECT id
            FROM product_models

            WHERE tender_item_id = $1
              AND model = $2
              AND source_url IS NOT DISTINCT FROM $3

            ORDER BY id

            LIMIT 1
            """,

            tender_item_id,
            model_name,
            source_url,
        )

        # ----------------------------------------------------
        # Если запись уже существует — обновляем её.
        # ----------------------------------------------------

        if existing_id is not None:

            await conn.execute(
                """
                UPDATE product_models
                SET
                    manufacturer = $1,
                    product_name = $2,
                    source_title = $3,
                    specifications = $4::jsonb,
                    price = $5,
                    currency = $6,
                    price_includes_vat = $7,
                    availability = $8,
                    match_status = $9,
                    match_result = $10::jsonb

                WHERE id = $11
                """,

                manufacturer,
                product_name,
                source_title,
                specifications_json,
                price,
                currency,
                price_includes_vat,
                availability,
                match_status,
                match_result_json,
                existing_id,
            )

            return existing_id

        # ----------------------------------------------------
        # Если модели ещё нет — создаём.
        # ----------------------------------------------------

        new_id = await conn.fetchval(
            """
            INSERT INTO product_models (
                tender_item_id,
                manufacturer,
                model,
                product_name,
                source_url,
                source_title,
                specifications,
                price,
                currency,
                price_includes_vat,
                availability,
                match_status,
                match_result
            )

            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7::jsonb,
                $8,
                $9,
                $10,
                $11,
                $12,
                $13::jsonb
            )

            RETURNING id
            """,

            tender_item_id,
            manufacturer,
            model_name,
            product_name,
            source_url,
            source_title,
            specifications_json,
            price,
            currency,
            price_includes_vat,
            availability,
            match_status,
            match_result_json,
        )

        return new_id


async def get_product_models(
    tender_item_id: int,
) -> list[dict]:

    async with pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT *
            FROM product_models
            WHERE tender_item_id = $1
            ORDER BY id
            """,
            tender_item_id,
        )

        result = []

        for row in rows:

            model = dict(row)

            # asyncpg обычно возвращает JSONB
            # уже как Python-объект.
            # Но оставляем совместимость
            # со старыми драйверами/данными.

            if isinstance(
                model.get("specifications"),
                str,
            ):

                try:
                    model["specifications"] = json.loads(
                        model["specifications"]
                    )

                except Exception:
                    model["specifications"] = {}

            if isinstance(
                model.get("match_result"),
                str,
            ):

                try:
                    model["match_result"] = json.loads(
                        model["match_result"]
                    )

                except Exception:
                    model["match_result"] = {}

            result.append(model)

        return result


async def select_product_model(
    model_id: int,
    tender_item_id: int,
) -> None:

    async with pool.acquire() as conn:

        async with conn.transaction():

            # Сначала снимаем выбор со всех моделей
            # этой позиции.

            await conn.execute(
                """
                UPDATE product_models
                SET selected = FALSE

                WHERE tender_item_id = $1
                """,
                tender_item_id,
            )

            # Затем выбираем одну конкретную модель.

            await conn.execute(
                """
                UPDATE product_models
                SET selected = TRUE

                WHERE id = $1
                  AND tender_item_id = $2
                """,
                model_id,
                tender_item_id,
            )


# ============================================================
# ПОСТАВЩИКИ
# ============================================================

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
            INSERT INTO suppliers (
                name,
                phone,
                email,
                city,
                categories,
                url
            )

            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6
            )

            RETURNING id
            """,

            name,
            phone,
            email,
            city,
            categories or [],
            url,
        )
