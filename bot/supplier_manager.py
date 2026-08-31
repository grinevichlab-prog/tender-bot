import asyncpg
from config.settings import DATABASE_URL

async def add_supplier(name: str, inn: str, contact: str, region: str, margin: float, user_id: int) -> int:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        supplier_id = await conn.fetchval(
            """INSERT INTO suppliers (name, inn, contact_person, region, default_margin, user_id)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
            name, inn, contact, region, margin, user_id
        )
        return supplier_id
    finally:
        await conn.close()

async def get_suppliers(user_id: int) -> list[dict]:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT id, name, inn, region, default_margin FROM suppliers WHERE user_id = $1 ORDER BY name",
            user_id
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()

async def get_supplier(supplier_id: int) -> dict | None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        row = await conn.fetchrow("SELECT * FROM suppliers WHERE id = $1", supplier_id)
        return dict(row) if row else None
    finally:
        await conn.close()

async def update_supplier(supplier_id: int, **fields):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        set_parts = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(fields.keys()))
        query = f"UPDATE suppliers SET {set_parts} WHERE id = $1"
        await conn.execute(query, supplier_id, *fields.values())
    finally:
        await conn.close()

async def delete_supplier(supplier_id: int):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("DELETE FROM suppliers WHERE id = $1", supplier_id)
    finally:
        await conn.close()
