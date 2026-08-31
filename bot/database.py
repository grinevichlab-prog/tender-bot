import asyncpg
from config.settings import DATABASE_URL
from datetime import datetime
import uuid

pool = None

async def create_pool():
    return await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)

async def set_pool(p):
    global pool
    pool = p

async def init_db():
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                name TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tenders (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                number TEXT,
                region TEXT,
                chat_id TEXT,
                thread_id TEXT,
                raw_analysis JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tender_items (
                id SERIAL PRIMARY KEY,
                tender_id INTEGER REFERENCES tenders(id) ON DELETE CASCADE,
                position_number INTEGER,
                name TEXT NOT NULL,
                quantity NUMERIC,
                unit TEXT,
                estimated_price NUMERIC,
                requirements JSONB,
                selected_model_id INTEGER,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS models (
                id SERIAL PRIMARY KEY,
                tender_item_id INTEGER REFERENCES tender_items(id) ON DELETE CASCADE,
                manufacturer TEXT,
                model TEXT,
                product_name TEXT,
                specifications JSONB,
                price NUMERIC,
                currency TEXT,
                price_includes_vat BOOLEAN,
                availability TEXT,
                source_url TEXT,
                source_title TEXT,
                source_quote TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS suppliers (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                inn TEXT,
                contact_person TEXT,
                city TEXT,
                default_margin NUMERIC DEFAULT 1.2,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS commercial_offers (
                id SERIAL PRIMARY KEY,
                tender_id INTEGER REFERENCES tenders(id) ON DELETE CASCADE,
                supplier_id INTEGER REFERENCES suppliers(id) ON DELETE CASCADE,
                data JSONB NOT NULL,
                total_amount NUMERIC,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        print("Таблицы БД проверены/созданы.", flush=True)

async def get_or_create_user(telegram_id: int, name: str) -> int:
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT id FROM users WHERE telegram_id = $1", telegram_id)
        if user:
            return user['id']
        user_id = await conn.fetchval(
            "INSERT INTO users (telegram_id, name) VALUES ($1, $2) RETURNING id",
            telegram_id, name
        )
        return user_id

async def create_tender(user_id: int, name: str, number: str | None, region: str | None) -> int:
    """Создает новый тендер с уникальным thread_id"""
    thread_id = f"tender_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    chat_id = f"user_{user_id}"
    
    async with pool.acquire() as conn:
        tender_id = await conn.fetchval(
            """INSERT INTO tenders (user_id, name, number, region, chat_id, thread_id, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, NOW()) 
               RETURNING id""",
            user_id, name, number, region, chat_id, thread_id
        )
        return tender_id

async def get_tender(tender_id: int) -> dict | None:
    """Получает тендер по ID"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM tenders WHERE id = $1", tender_id)
        return dict(row) if row else None

async def get_user_tenders(user_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, number, created_at FROM tenders WHERE user_id = $1 ORDER BY created_at DESC",
            user_id
        )
        return [dict(r) for r in rows]

async def delete_tender(tender_id: int):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tenders WHERE id = $1", tender_id)

async def update_tender_analysis(tender_id: int, analysis: dict):
    import json
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tenders SET raw_analysis = $1 WHERE id = $2",
            json.dumps(analysis, ensure_ascii=False), tender_id
        )

async def sync_tender_items(tender_id: int, items: list[dict]):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tender_items WHERE tender_id = $1", tender_id)
        for item in items:
            await conn.execute(
                """INSERT INTO tender_items 
                   (tender_id, position_number, name, quantity, unit, estimated_price, requirements)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                tender_id,
                item.get('position_number'),
                item.get('name'),
                item.get('quantity'),
                item.get('unit'),
                item.get('estimated_price'),
                item.get('requirements')
            )

async def get_tender_items(tender_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT ti.*, m.manufacturer, m.model 
               FROM tender_items ti
               LEFT JOIN models m ON ti.selected_model_id = m.id
               WHERE ti.tender_id = $1
               ORDER BY ti.position_number""",
            tender_id
        )
        return [dict(r) for r in rows]

async def save_models(tender_item_id: int, models: list[dict]):
    import json
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM models WHERE tender_item_id = $1", tender_item_id)
        for m in models:
            await conn.execute(
                """INSERT INTO models 
                   (tender_item_id, manufacturer, model, product_name, specifications, 
                    price, currency, price_includes_vat, availability, source_url, source_title, source_quote)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
                tender_item_id,
                m.get('manufacturer'),
                m.get('model'),
                m.get('product_name'),
                json.dumps(m.get('specifications', {}), ensure_ascii=False),
                m.get('price'),
                m.get('currency'),
                m.get('price_includes_vat'),
                m.get('availability'),
                m.get('source_url'),
                m.get('source_title'),
                m.get('source_quote')
            )

async def get_models(tender_item_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM models WHERE tender_item_id = $1 ORDER BY created_at",
            tender_item_id
        )
        return [dict(r) for r in rows]

async def delete_models(tender_item_id: int):
    """Удаляет все найденные модели для позиции"""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM models WHERE tender_item_id = $1", tender_item_id)

async def select_model(tender_item_id: int, model_id: int):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tender_items SET selected_model_id = $1 WHERE id = $2",
            model_id, tender_item_id
        )
