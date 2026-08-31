import asyncpg
from config.settings import DATABASE_URL
from datetime import datetime
import uuid
import json

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
                region TEXT,
                chat_id TEXT,
                thread_id TEXT,
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

# ============ USERS ============

async def get_or_create_user(telegram_id: int, name: str) -> int:
    """Получает или создает пользователя"""
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT id FROM users WHERE telegram_id = $1", telegram_id)
        if user:
            return user['id']
        user_id = await conn.fetchval(
            "INSERT INTO users (telegram_id, name) VALUES ($1, $2) RETURNING id",
            telegram_id, name
        )
        return user_id

# ============ TENDERS ============

async def create_tender(user_id: int, name: str, number: str | None, region: str | None) -> int:
    """Создает новый тендер с уникальным thread_id"""
    thread_id = f"tender_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    chat_id = f"user_{user_id}"
    
    async with pool.acquire() as conn:
        tender_id = await conn.fetchval(
            """INSERT INTO tenders (user_id, name, region, chat_id, thread_id, created_at)
               VALUES ($1, $2, $3, $4, $5, NOW()) 
               RETURNING id""",
            user_id, name, region, chat_id, thread_id
        )
        return tender_id

async def get_tender(tender_id: int) -> dict | None:
    """Получает тендер по ID"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM tenders WHERE id = $1", tender_id)
        return dict(row) if row else None

async def get_user_tenders(user_id: int) -> list[dict]:
    """Получает все тендеры пользователя"""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, created_at FROM tenders WHERE user_id = $1 ORDER BY created_at DESC",
            user_id
        )
        return [dict(r) for r in rows]

async def delete_tender(tender_id: int):
    """Удаляет тендер"""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tenders WHERE id = $1", tender_id)

# ============ TENDER ITEMS ============

async def sync_tender_items(tender_id: int, items: list[dict]):
    """Синхронизирует позиции тендера"""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tender_items WHERE tender_id = $1", tender_id)
        for item in items:
            requirements = item.get('requirements') or {}
            
            await conn.execute(
                """INSERT INTO tender_items 
                   (tender_id, position_number, name, quantity, unit, requirements)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                tender_id,
                item.get('position_number'),
                item.get('name'),
                item.get('quantity'),
                item.get('unit'),
                json.dumps(requirements, ensure_ascii=False)
            )

async def get_tender_items(tender_id: int) -> list[dict]:
    """Получает все позиции тендера с выбранными моделями"""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT ti.*, m.manufacturer, m.model 
               FROM tender_items ti
               LEFT JOIN models m ON ti.selected_model_id = m.id
               WHERE ti.tender_id = $1
               ORDER BY ti.position_number""",
            tender_id
        )
        result = []
        for r in rows:
            item = dict(r)
            if item.get('requirements') and isinstance(item['requirements'], str):
                try:
                    item['requirements'] = json.loads(item['requirements'])
                except:
                    pass
            result.append(item)
        return result

# ============ MODELS ============

async def save_models(tender_item_id: int, models: list[dict]):
    """Сохраняет найденные модели для позиции"""
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
    """Получает все найденные модели для позиции"""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM models WHERE tender_item_id = $1 ORDER BY created_at",
            tender_item_id
        )
        result = []
        for r in rows:
            model = dict(r)
            if model.get('specifications') and isinstance(model['specifications'], str):
                try:
                    model['specifications'] = json.loads(model['specifications'])
                except:
                    pass
            result.append(model)
        return result

async def delete_models(tender_item_id: int):
    """Удаляет все найденные модели для позиции"""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM models WHERE tender_item_id = $1", tender_item_id)

async def select_model(tender_item_id: int, model_id: int):
    """Выбирает модель для позиции"""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tender_items SET selected_model_id = $1 WHERE id = $2",
            model_id, tender_item_id
        )

# ============ SUPPLIERS ============

async def get_suppliers(user_id: int) -> list[dict]:
    """Получает всех поставщиков пользователя"""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, inn, contact_person, city, default_margin 
               FROM suppliers 
               WHERE user_id = $1 
               ORDER BY name""",
            user_id
        )
        return [dict(r) for r in rows]

async def get_supplier(supplier_id: int) -> dict | None:
    """Получает поставщика по ID"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM suppliers WHERE id = $1", 
            supplier_id
        )
        return dict(row) if row else None

# ============ COMMERCIAL OFFERS ============

async def save_commercial_offer(tender_id: int, supplier_id: int, data: dict, total_amount: float) -> int:
    """Сохраняет коммерческое предложение"""
    async with pool.acquire() as conn:
        cp_id = await conn.fetchval(
            """INSERT INTO commercial_offers (tender_id, supplier_id, data, total_amount, created_at)
               VALUES ($1, $2, $3, $4, NOW())
               RETURNING id""",
            tender_id, supplier_id, json.dumps(data, ensure_ascii=False), total_amount
        )
        return cp_id

async def get_commercial_offer(cp_id: int) -> dict | None:
    """Получает КП по ID"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM commercial_offers WHERE id = $1",
            cp_id
        )
        if not row:
            return None
        result = dict(row)
        if result.get('data') and isinstance(result['data'], str):
            try:
                result['data'] = json.loads(result['data'])
            except:
                pass
        return result

async def list_commercial_offers(tender_id: int) -> list[dict]:
    """Получает все КП для тендера"""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, supplier_id, total_amount, created_at 
               FROM commercial_offers 
               WHERE tender_id = $1 
               ORDER BY created_at DESC""",
            tender_id
        )
        return [dict(r) for r in rows]
