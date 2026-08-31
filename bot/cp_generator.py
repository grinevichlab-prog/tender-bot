import asyncpg
from datetime import datetime, timedelta
from config.settings import DATABASE_URL

async def generate_cp(tender_id: int, supplier_id: int, delivery_days: int, payment_terms: str, warranty: str) -> dict:
    """Генерирует КП на основе тендера, найденных моделей и наценки поставщика"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        tender = await conn.fetchrow("SELECT * FROM tenders WHERE id = $1", tender_id)
        if not tender:
            return {"error": "Тендер не найден"}
        
        supplier = await conn.fetchrow("SELECT * FROM suppliers WHERE id = $1", supplier_id)
        if not supplier:
            return {"error": "Поставщик не найден"}
        
        items = await conn.fetch(
            """SELECT ti.*, m.manufacturer, m.model, m.price as model_price
               FROM tender_items ti
               LEFT JOIN models m ON ti.selected_model_id = m.id
               WHERE ti.tender_id = $1
               ORDER BY ti.position_number""",
            tender_id
        )
        
        cp_items = []
        total = 0.0
        margin = supplier['default_margin'] or 1.2
        
        for item in items:
            base_price = item['model_price'] or item.get('estimated_price') or 0
            final_price = base_price * margin
            quantity = item['quantity'] or 1
            sum_price = final_price * quantity
            
            cp_items.append({
                "position": item['position_number'],
                "name": item['name'],
                "manufacturer": item['manufacturer'],
                "model": item['model'],
                "quantity": quantity,
                "unit": item['unit'],
                "price": round(final_price, 2),
                "sum": round(sum_price, 2)
            })
            total += sum_price
        
        delivery_date = datetime.now() + timedelta(days=delivery_days)
        
        cp_data = {
            "tender_name": tender['name'],
            "tender_number": tender.get('number'),
            "supplier_name": supplier['name'],
            "supplier_inn": supplier['inn'],
            "contact": supplier['contact_person'],
            "items": cp_items,
            "total": round(total, 2),
            "vat": round(total * 0.2, 2),
            "total_with_vat": round(total * 1.2, 2),
            "delivery_days": delivery_days,
            "delivery_date": delivery_date.strftime("%d.%m.%Y"),
            "payment_terms": payment_terms,
            "warranty": warranty,
            "generated_at": datetime.now().isoformat()
        }
        
        # сохраняем в БД
        cp_id = await conn.fetchval(
            """INSERT INTO commercial_offers 
               (tender_id, supplier_id, data, total_amount, created_at)
               VALUES ($1, $2, $3, $4, NOW()) RETURNING id""",
            tender_id, supplier_id, cp_data, cp_data['total_with_vat']
        )
        
        cp_data['id'] = cp_id
        return cp_data
        
    finally:
        await conn.close()

async def get_cp(cp_id: int) -> dict | None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        row = await conn.fetchrow("SELECT * FROM commercial_offers WHERE id = $1", cp_id)
        return dict(row) if row else None
    finally:
        await conn.close()

async def list_cps(tender_id: int) -> list[dict]:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(
            """SELECT co.id, co.total_amount, co.created_at, s.name as supplier_name
               FROM commercial_offers co
               JOIN suppliers s ON co.supplier_id = s.id
               WHERE co.tender_id = $1
               ORDER BY co.created_at DESC""",
            tender_id
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()
