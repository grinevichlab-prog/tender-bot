import asyncio
import logging
import sys
import tempfile
import zipfile
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import BOT_TOKEN
from bot import database as db
from bot.ai_analyzer import analyze_tender_document, merge_analyses
from bot.parser import extract_text
from bot.model_search import search_models
from bot.supplier_manager import add_supplier, get_suppliers, get_supplier, update_supplier, delete_supplier
from bot.supplier_search import search_suppliers_web
from bot.cp_generator import generate_cp, get_cp, list_cps
from bot.excel_export import export_tender_to_excel, export_cp_to_excel
from bot.word_export import export_cp_to_word
from bot.pdf_export import export_cp_to_pdf
from bot.keyboards import (
    main_menu, tender_actions, item_actions, model_select,
    supplier_actions, confirm_cp, export_format
)
from bot.states import SupplierStates, TenderStates, CPStates

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

router = Router()
from bot.supplier_search import search_suppliers_web

@router.callback_query(F.data.startswith("generate_cp_"))
async def generate_cp_start(callback: CallbackQuery, state: FSMContext):
    logger.info(f"[generate_cp_start] triggered, callback_data={callback.data}")
    tender_id = int(callback.data.split("_")[2])
    
    # Получаем user_id
    user_id = await db.get_or_create_user(
        telegram_id=callback.from_user.id,
        name=callback.from_user.full_name or "User"
    )
    
    logger.info(f"[generate_cp_start] user_id={user_id}, tender_id={tender_id}")
    
    suppliers = await get_suppliers(user_id)
    
    logger.info(f"[generate_cp_start] found {len(suppliers)} suppliers")
    
    # Если поставщиков нет - запускаем автопоиск
    if not suppliers:
        await callback.message.edit_text("🔍 Поставщиков нет в базе. Запускаю автопоиск...\nЭто займет 1-2 минуты.")
        
        # Получаем название тендера
        tender = await db.get_tender(tender_id)
        if not tender:
            await callback.answer("Тендер не найден", show_alert=True)
            return
        
        # Получаем первую позицию для формирования запроса
        items = await db.get_tender_items(tender_id)
        if not items:
            await callback.answer("Позиции тендера не найдены", show_alert=True)
            return
        
        # Формируем поисковый запрос из первой позиции
        search_query = items[0].get('name', tender.get('name', 'товары'))
        
        # Ищем поставщиков
        found_suppliers = await search_suppliers_web(search_query, max_results=5)
        
        if not found_suppliers:
            await callback.message.edit_text(
                "❌ Поставщики не найдены автоматически.\n\n"
                "Добавьте поставщика вручную через /add_supplier"
            )
            await callback.answer()
            return
        
        # Добавляем найденных поставщиков в БД
        added_count = 0
        for s_info in found_suppliers:
            try:
                contact_parts = []
                if s_info.get('phone'):
                    contact_parts.append(s_info['phone'])
                if s_info.get('email'):
                    contact_parts.append(s_info['email'])
                contact = " | ".join(contact_parts) if contact_parts else "Не указано"
                
                await add_supplier(
                    name=s_info.get('name', 'Неизвестно')[:100],
                    inn='0000000000',
                    contact=contact[:200],
                    region='Москва',
                    margin=1.25,
                    user_id=user_id
                )
                added_count += 1
                logger.info(f"[generate_cp_start] Добавлен поставщик: {s_info.get('name')}")
            except Exception as e:
                logger.warning(f"[generate_cp_start] Не удалось добавить поставщика {s_info.get('name')}: {e}")
        
        if added_count == 0:
            await callback.message.edit_text(
                "❌ Не удалось добавить поставщиков.\n\n"
                "Добавьте поставщика вручную через /add_supplier"
            )
            await callback.answer()
            return
        
        suppliers = await get_suppliers(user_id)
        
        await callback.message.edit_text(
            f"✅ Найдено и добавлено {added_count} поставщиков!\n\n"
            "Выберите поставщика для КП:"
        )
    
    text = "👥 Выберите поставщика для КП:\n\n"
    for s in suppliers:
        text += f"/supplier_{s['id']} - {s['name']} (наценка {s.get('default_margin', 1.2):.0%})\n"
    
    await state.update_data(tender_id=tender_id)
    await callback.message.edit_text(text)
    await callback.answer()

# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============

def _plural(num: int, forms: tuple) -> str:
    """Склонение: (1 позиция, 2 позиции, 5 позиций)"""
    n = abs(num) % 100
    if 10 < n < 20:
        return forms[2]
    n = n % 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]

# ============ СТАРТ И ПОМОЩЬ ============

@router.message(Command("start"))
async def cmd_start(message: Message):
    # Создаем пользователя если его нет
    user_id = await db.get_or_create_user(
        telegram_id=message.from_user.id,
        name=message.from_user.full_name or "User"
    )
    
    await message.answer(
        "👋 Добро пожаловать в TenderBot!\n\n"
        "Я помогу вам:\n"
        "📤 Обработать документацию тендера\n"
        "🔍 Найти подходящие модели товаров\n"
        "💼 Сформировать коммерческое предложение\n"
        "📊 Экспортировать данные\n\n"
        "Загрузите ZIP-архив с документами тендера или выберите действие:",
        reply_markup=main_menu()
    )

@router.message(F.text == "❓ Помощь")
@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Инструкция по использованию:</b>\n\n"
        "<b>1. Загрузка тендера:</b>\n"
        "Отправьте ZIP-архив с документами (.docx, .doc, .xlsx, .xls, .pdf)\n\n"
        "<b>2. Просмотр позиций:</b>\n"
        "Нажмите 'Мои тендеры' → выберите тендер → 'Просмотр позиций'\n\n"
        "<b>3. Поиск моделей:</b>\n"
        "Выберите позицию → 'Найти модели' → система найдет подходящие товары\n\n"
        "<b>4. Поставщики:</b>\n"
        "Добавьте поставщиков с их наценкой через /add_supplier\n\n"
        "<b>5. Генерация КП:</b>\n"
        "Тендер → 'Генерация КП' → выберите поставщика → укажите условия\n\n"
        "<b>6. Экспорт:</b>\n"
        "Экспортируйте КП в Excel, Word или PDF\n\n"
        "❓ Вопросы? Напишите @support",
        parse_mode="HTML"
    )

# ============ ЗАГРУЗКА ТЕНДЕРА ============

@router.message(F.text == "📤 Загрузить тендер")
async def request_tender(message: Message):
    await message.answer(
        "📤 Отправьте ZIP-архив с документами тендера.\n\n"
        "Поддерживаемые форматы внутри архива:\n"
        "• .docx, .doc\n"
        "• .xlsx, .xls\n"
        "• .pdf\n\n"
        "Бот извлечет номенклатуру и характеристики товаров."
    )

@router.message(F.document)
async def handle_attachment(message: Message, bot: Bot):
    doc = message.document
    if not doc.file_name.lower().endswith('.zip'):
        await message.answer("⚠️ Пожалуйста, отправьте ZIP-архив с документами.")
        return
    
    status_msg = await message.answer("⏳ Скачиваю файл...")
    
    # Создаем/получаем пользователя
    user_id = await db.get_or_create_user(
        telegram_id=message.from_user.id,
        name=message.from_user.full_name or "User"
    )
    
    file = await bot.get_file(doc.file_id)
    dest = Path(tempfile.gettempdir()) / doc.file_name
    await bot.download_file(file.file_path, dest)
    
    await status_msg.edit_text("📂 Распаковываю архив...")
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(dest, 'r') as z:
                z.extractall(tmpdir)
            
            files = list(Path(tmpdir).rglob('*'))
            files = [f for f in files if f.is_file() and not f.name.startswith('.')]
            
            if not files:
                await status_msg.edit_text("⚠️ Архив пустой или не содержит поддерживаемых файлов.")
                return
            
            await status_msg.edit_text(f"📄 Найдено файлов: {len(files)}\n⏳ Извлекаю текст...")
            
            texts = []
            for f in files:
                try:
                    text = extract_text(str(f), f.suffix)
                    if text and len(text) > 50:
                        texts.append(text)
                        logger.info(f"[extract_text] готово {f.name}, символов: {len(text)}")
                except Exception as e:
                    logger.warning(f"[extract_text] ошибка {f.name}: {e}")
            
            if not texts:
                await status_msg.edit_text("⚠️ Не удалось извлечь текст из файлов.")
                return
            
            await status_msg.edit_text(f"🤖 Анализирую {sum(len(t) for t in texts)} символов через YandexGPT...")
            
            # Объединяем тексты и анализируем
            full_text = "\n\n".join(texts)
            analysis = await analyze_tender_document(full_text)
            
            items = analysis.get('items', [])
            if not items:
                await status_msg.edit_text("⚠️ Не удалось извлечь номенклатуру из документов.")
                return
            
          await status_msg.edit_text(f"💾 Сохраняю {len(items)} позиций в БД...")

# Создаем тендер с правильным user_id
tender_id = await db.create_tender(
    user_id=user_id,
    name=doc.file_name.replace('.zip', ''),
    number=None,
    region=None
)

# Сохраняем анализ - УБРАЛИ
# await db.update_tender_analysis(tender_id, analysis)

# Сохраняем позиции
await db.sync_tender_items(tender_id, items)
            
            await status_msg.delete()
            await message.answer(
                f"✅ Тендер обработан!\n\n"
                f"📋 Извлечено позиций: {len(items)}\n"
                f"🆔 ID тендера: {tender_id}\n\n"
                f"Выберите действие:",
                reply_markup=tender_actions(tender_id)
            )
    
    except Exception as e:
        logger.error(f"[handle_attachment] error: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Ошибка обработки: {e}")
    finally:
        dest.unlink(missing_ok=True)

# ============ МОИ ТЕНДЕРЫ ============

@router.message(F.text == "📋 Мои тендеры")
async def list_tenders(message: Message):
    # Получаем user_id из таблицы users
    user_id = await db.get_or_create_user(
        telegram_id=message.from_user.id,
        name=message.from_user.full_name or "User"
    )
    
    tenders = await db.get_user_tenders(user_id)
    if not tenders:
        await message.answer("У вас пока нет тендеров. Загрузите ZIP-архив с документами.")
        return
    
    text = "📋 <b>Ваши тендеры:</b>\n\n"
    for t in tenders[:20]:
        text += f"🆔 <b>{t['id']}</b> - {t['name']}\n"
        text += f"📅 {t['created_at'].strftime('%d.%m.%Y %H:%M')}\n\n"
    
    await message.answer(text, parse_mode="HTML")
    await message.answer("Введите ID тендера для просмотра деталей или выберите из списка выше.")

@router.callback_query(F.data.startswith("view_"))
async def view_tender(callback: CallbackQuery):
    tender_id = int(callback.data.split("_")[1])
    items = await db.get_tender_items(tender_id)
    
    if not items:
        await callback.answer("Позиции не найдены", show_alert=True)
        return
    
    text = f"📋 <b>Позиции тендера #{tender_id}:</b>\n\n"
    for item in items[:15]:
        text += f"<b>{item['position_number']}.</b> {item['name']}\n"
        text += f"   Кол-во: {item['quantity']} {item['unit']}\n"
        if item.get('manufacturer') and item.get('model'):
            text += f"   ✅ {item['manufacturer']} {item['model']}\n"
        text += "\n"
    
    if len(items) > 15:
        text += f"... и ещё {len(items) - 15} позиций\n"
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=tender_actions(tender_id))
    await callback.answer()

@router.callback_query(F.data.startswith("show_export_"))
async def show_export_menu(callback: CallbackQuery):
    tender_id = int(callback.data.split("_")[2])
    await callback.message.edit_text(
        "📥 Выберите формат экспорта:",
        reply_markup=export_format(tender_id)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("delete_tender_"))
async def delete_tender(callback: CallbackQuery):
    tender_id = int(callback.data.split("_")[2])
    await db.delete_tender(tender_id)
    await callback.message.edit_text("🗑 Тендер удален.")
    await callback.answer()

# ============ ПОИСК МОДЕЛЕЙ ============
@router.callback_query(F.data.startswith("refresh_search_"))
async def refresh_search_models(callback: CallbackQuery):
    """Очищает старые результаты и ищет заново"""
    tender_id = int(callback.data.split("_")[2])
    items = await db.get_tender_items(tender_id)
    
    # Удаляем все старые модели
    for item in items:
        await db.delete_models(item['id'])
    
    await callback.answer("Старые результаты очищены, запускаю поиск заново", show_alert=True)
    
    # Перенаправляем на обычный поиск
    callback.data = f"search_{tender_id}"
    await search_tender_models(callback)
    
    # Проверяем сколько позиций уже имеют модели
    items_with_models = 0
    for item in items:
        existing_models = await db.get_models(item['id'])
        if existing_models:
            items_with_models += 1
    
    # Если уже есть модели — предлагаем обновить
    if items_with_models > 0:
        await callback.answer(
            f"⚠️ Найдены модели для {items_with_models} {_plural(items_with_models, ('позиции', 'позиций', 'позиций'))}.\n"
            "Повторный поиск может дать другие результаты.\n"
            "Продолжить?",
            show_alert=True
        )
    
    await callback.message.edit_text(
        f"🔍 Ищу модели для {items_count} {items_word}...\n"
        f"Это может занять от 2 до 5 минут."
    )
    
    found_count = 0
    processed_items = []
    
    for idx, item in enumerate(items, 1):
        # Пропускаем если уже есть модели
        existing = await db.get_models(item['id'])
        if existing:
            found_count += len(existing)
            continue
        
        logger.info(f"[search_models] Ищу модели для позиции {idx}: {item['name']}")
        
        models = await search_models(item, region=None, max_models=10)
        
        if models:
            await db.save_models(item['id'], models)
            found_count += len(models)
            
            # Анализируем соответствие (берем первую модель)
            best_model = models[0]
            match_status = "✅ Полное соответствие"
            
            # Простая проверка соответствия по наличию характеристик
            requirements = item.get('requirements') or []
            if requirements and isinstance(requirements, list):
                specs = best_model.get('specifications', {})
                matched = 0
                for req in requirements[:5]:
                    if isinstance(req, dict):
                        param = req.get('parameter') or req.get('name')
                        if param and any(param.lower() in str(k).lower() for k in specs.keys()):
                            matched += 1
                
                total_reqs = min(len(requirements), 5)
                if matched == 0:
                    match_status = "⚠️ Не соответствует ТЗ"
                elif matched < total_reqs // 2:
                    match_status = "🔶 Частичное соответствие"
                elif matched < total_reqs:
                    match_status = "🟡 Хорошее соответствие"
            
            processed_items.append({
                'name': item['name'][:40],
                'models_count': len(models),
                'match': match_status
            })
            
            logger.info(f"[search_models] Найдено {len(models)} моделей для '{item['name']}', статус: {match_status}")
        else:
            logger.warning(f"[search_models] Модели не найдены для '{item['name']}'")
            processed_items.append({
                'name': item['name'][:40],
                'models_count': 0,
                'match': "❌ Не найдено"
            })
        
        # Обновляем прогресс каждые 2 позиции
        if idx % 2 == 0 or idx == items_count:
            try:
                progress_text = f"🔍 Обработано {idx}/{items_count} {_plural(idx, ('позиция', 'позиции', 'позиций'))}\n"
                progress_text += f"Найдено моделей: {found_count}\n\n"
                
                # Показываем последние 3 обработанные позиции
                for pi in processed_items[-3:]:
                    progress_text += f"• {pi['name']}: {pi['models_count']} шт. {pi['match']}\n"
                
                await callback.message.edit_text(progress_text)
            except Exception as e:
                logger.warning(f"[search_models] Не удалось обновить прогресс: {e}")
        
        await asyncio.sleep(2)
    
    # Финальный отчет
    summary_text = f"✅ Поиск завершен!\n\n"
    summary_text += f"📊 Обработано: {items_count} {_plural(items_count, ('позиция', 'позиции', 'позиций'))}\n"
    summary_text += f"🔍 Найдено моделей: {found_count}\n\n"
    summary_text += "<b>Результаты по позициям:</b>\n\n"
    
    for pi in processed_items:
        summary_text += f"• {pi['name']}\n  {pi['models_count']} моделей {pi['match']}\n\n"
    
    summary_text += "Выберите позицию для просмотра найденных моделей."
    
    await callback.message.edit_text(
        summary_text,
        parse_mode="HTML",
        reply_markup=tender_actions(tender_id)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("models_"))
async def show_item_models(callback: CallbackQuery):
    parts = callback.data.split("_")
    tender_id = int(parts[1])
    item_id = int(parts[2])
    
    models = await db.get_models(item_id)
    if not models:
        await callback.answer("Модели не найдены. Запустите поиск моделей.", show_alert=True)
        return
    
    text = "🔍 <b>Найденные модели:</b>\n\n"
    for i, m in enumerate(models[:10], 1):
        text += f"<b>{i}.</b> {m.get('manufacturer', '?')} {m.get('model', '?')}\n"
        if m.get('price'):
            text += f"   💰 {m['price']} {m.get('currency', 'RUB')}\n"
        if m.get('source_url'):
            text += f"   🔗 {m['source_url'][:50]}...\n"
        text += "\n"
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=model_select(tender_id, item_id, models))
    await callback.answer()

@router.callback_query(F.data.startswith("select_"))
async def select_item_model(callback: CallbackQuery):
    parts = callback.data.split("_")
    tender_id = int(parts[1])
    item_id = int(parts[2])
    model_idx = int(parts[3])
    
    models = await db.get_models(item_id)
    if model_idx >= len(models):
        await callback.answer("Модель не найдена", show_alert=True)
        return
    
    selected_model = models[model_idx]
    await db.select_model(item_id, selected_model['id'])
    
    await callback.answer("✅ Модель выбрана", show_alert=True)
    await callback.message.edit_text(
        f"✅ Модель выбрана:\n\n"
        f"{selected_model.get('manufacturer')} {selected_model.get('model')}\n"
        f"💰 {selected_model.get('price')} {selected_model.get('currency', 'RUB')}",
        reply_markup=tender_actions(tender_id)
    )

# ============ ПОСТАВЩИКИ ============

@router.message(F.text == "👥 Поставщики")
async def suppliers_menu(message: Message):
    # Получаем user_id
    user_id = await db.get_or_create_user(
        telegram_id=message.from_user.id,
        name=message.from_user.full_name or "User"
    )
    
    suppliers = await get_suppliers(user_id)
    
    if not suppliers:
        await message.answer(
            "У вас пока нет поставщиков.\n\n"
            "Добавьте поставщика командой /add_supplier"
        )
        return
    
    text = "👥 <b>Ваши поставщики:</b>\n\n"
    for s in suppliers:
        text += f"🆔 <b>{s['id']}</b> - {s['name']}\n"
        text += f"ИНН: {s.get('inn', 'не указан')}\n"
        text += f"Город: {s.get('city', 'не указан')}\n"  # изменил region на city
        text += f"Наценка: {s.get('default_margin', 1.2):.0%}\n\n"
    
    await message.answer(text, parse_mode="HTML")

@router.message(Command("add_supplier"))
async def add_supplier_start(message: Message, state: FSMContext):
    # Сохраняем user_id в state
    user_id = await db.get_or_create_user(
        telegram_id=message.from_user.id,
        name=message.from_user.full_name or "User"
    )
    await state.update_data(user_id=user_id)
    
    await message.answer("👤 Введите название поставщика:")
    await state.set_state(SupplierStates.waiting_name)

@router.message(SupplierStates.waiting_name)
async def supplier_name_received(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("🔢 Введите ИНН поставщика:")
    await state.set_state(SupplierStates.waiting_inn)

@router.message(SupplierStates.waiting_inn)
async def supplier_inn_received(message: Message, state: FSMContext):
    await state.update_data(inn=message.text)
    await message.answer("📞 Введите контактное лицо и телефон:")
    await state.set_state(SupplierStates.waiting_contact)

@router.message(SupplierStates.waiting_contact)
async def supplier_contact_received(message: Message, state: FSMContext):
    await state.update_data(contact=message.text)
    await message.answer("🌍 Введите город поставщика:")  # изменил "регион" на "город"
    await state.set_state(SupplierStates.waiting_region)

@router.message(SupplierStates.waiting_region)
async def supplier_region_received(message: Message, state: FSMContext):
    await state.update_data(region=message.text)
    await message.answer("💰 Введите наценку поставщика (например, 1.2 для 20% или 1.5 для 50%):")
    await state.set_state(SupplierStates.waiting_margin)

@router.message(SupplierStates.waiting_margin)
async def supplier_margin_received(message: Message, state: FSMContext):
    try:
        margin = float(message.text.replace(',', '.'))
        if margin < 1.0 or margin > 10.0:
            await message.answer("⚠️ Наценка должна быть от 1.0 до 10.0")
            return
    except ValueError:
        await message.answer("⚠️ Введите число, например: 1.2")
        return
    
    data = await state.get_data()
    supplier_id = await add_supplier(
        name=data['name'],
        inn=data['inn'],
        contact=data['contact'],
        region=data['region'],
        margin=margin,
        user_id=data['user_id']
    )
    
    await state.clear()
    await message.answer(
        f"✅ Поставщик добавлен!\n\n"
        f"🆔 ID: {supplier_id}\n"
        f"Название: {data['name']}\n"
        f"Наценка: {margin:.0%}"
    )

# ============ ГЕНЕРАЦИЯ КП ============

@router.callback_query(F.data.startswith("cp_"))
async def generate_cp_start(callback: CallbackQuery, state: FSMContext):
    logger.info(f"[generate_cp_start] triggered, callback_data={callback.data}")
    tender_id = int(callback.data.split("_")[1])
    
    # Получаем user_id
    user_id = await db.get_or_create_user(
        telegram_id=callback.from_user.id,
        name=callback.from_user.full_name or "User"
    )
    
    logger.info(f"[generate_cp_start] user_id={user_id}, tender_id={tender_id}")
    
    suppliers = await get_suppliers(user_id)
    
    logger.info(f"[generate_cp_start] found {len(suppliers)} suppliers")
    
    if not suppliers:
        await callback.answer("Сначала добавьте поставщика через /add_supplier", show_alert=True)
        return
    
    text = "👥 Выберите поставщика для КП:\n\n"
    for s in suppliers:
        text += f"/supplier_{s['id']} - {s['name']} (наценка {s.get('default_margin', 1.2):.0%})\n"
    
    await state.update_data(tender_id=tender_id)
    await callback.message.edit_text(text)
    await callback.answer()

@router.message(F.text.regexp(r"^/supplier_(\d+)$"))
async def supplier_selected(message: Message, state: FSMContext):
    supplier_id = int(message.text.split("_")[1])
    await state.update_data(supplier_id=supplier_id)
    
    await message.answer("📦 Введите срок поставки в днях (например, 30):")
    await state.set_state(CPStates.waiting_delivery_days)

@router.message(CPStates.waiting_delivery_days)
async def delivery_days_received(message: Message, state: FSMContext):
    try:
        days = int(message.text)
        if days < 1 or days > 365:
            await message.answer("⚠️ Срок должен быть от 1 до 365 дней")
            return
    except ValueError:
        await message.answer("⚠️ Введите число")
        return
    
    await state.update_data(delivery_days=days)
    await message.answer("💳 Введите условия оплаты (например: '100% предоплата' или '30% аванс, 70% по факту'):")
    await state.set_state(CPStates.waiting_payment_terms)

@router.message(CPStates.waiting_payment_terms)
async def payment_terms_received(message: Message, state: FSMContext):
    await state.update_data(payment_terms=message.text)
    await message.answer("🛡 Введите гарантийный срок (например: '12 месяцев'):")
    await state.set_state(CPStates.waiting_warranty)

@router.message(CPStates.waiting_warranty)
async def warranty_received(message: Message, state: FSMContext):
    await state.update_data(warranty=message.text)
    
    data = await state.get_data()
    
    status_msg = await message.answer("⏳ Генерирую КП...")
    
    cp_data = await generate_cp(
        tender_id=data['tender_id'],
        supplier_id=data['supplier_id'],
        delivery_days=data['delivery_days'],
        payment_terms=data['payment_terms'],
        warranty=data['warranty']
    )
    
    if 'error' in cp_data:
        await status_msg.edit_text(f"❌ Ошибка: {cp_data['error']}")
        await state.clear()
        return
    
    await status_msg.delete()
    
    text = f"✅ <b>Коммерческое предложение #{cp_data['id']}</b>\n\n"
    text += f"Поставщик: {cp_data['supplier_name']}\n"
    text += f"Тендер: {cp_data['tender_name']}\n\n"
    text += f"Позиций: {len(cp_data['items'])}\n"
    text += f"Итого: {cp_data['total']:,.2f} ₽\n"
    text += f"С НДС: {cp_data['total_with_vat']:,.2f} ₽\n\n"
    text += f"Срок поставки: {cp_data['delivery_days']} дней\n"
    text += f"Оплата: {cp_data['payment_terms']}\n"
    text += f"Гарантия: {cp_data['warranty']}"
    
    await message.answer(text, parse_mode="HTML", reply_markup=export_format(data['tender_id']))
    await state.clear()

# ============ ЭКСПОРТ ============

@router.callback_query(F.data.startswith("export_"))
async def handle_export(callback: CallbackQuery):
    parts = callback.data.split("_")
    format_type = parts[1]
    tender_id = int(parts[2])
    
    # Получаем последнее КП для тендера
    cps = await list_cps(tender_id)
    if not cps:
        await callback.answer("Сначала сгенерируйте КП", show_alert=True)
        return
    
    cp = await get_cp(cps[0]['id'])
    cp_data = cp['data']
    
    await callback.message.edit_text("⏳ Формирую файл...")
    
    try:
        if format_type == "excel":
            buffer = export_cp_to_excel(cp_data)
            filename = f"CP_{tender_id}_{cps[0]['id']}.xlsx"
            
        elif format_type == "word":
            buffer = export_cp_to_word(cp_data)
            filename = f"CP_{tender_id}_{cps[0]['id']}.docx"
            
        elif format_type == "pdf":
            buffer = export_cp_to_pdf(cp_data)
            filename = f"CP_{tender_id}_{cps[0]['id']}.pdf"
        else:
            await callback.answer("Неизвестный формат", show_alert=True)
            return
        
        # Сохраняем во временный файл
        temp_path = Path(tempfile.gettempdir()) / filename
        temp_path.write_bytes(buffer.read())
        
        # Отправляем файл
        await callback.message.answer_document(
            FSInputFile(temp_path, filename=filename),
            caption=f"✅ Коммерческое предложение #{cps[0]['id']}"
        )
        
        temp_path.unlink()
        await callback.message.delete()
        
    except Exception as e:
        logger.error(f"Export error: {e}", exc_info=True)
        await callback.message.edit_text(f"❌ Ошибка экспорта: {e}")
    
    await callback.answer()

# ============ СТАТИСТИКА ============

@router.message(F.text == "📊 Статистика")
async def show_stats(message: Message):
    # Получаем user_id
    user_id = await db.get_or_create_user(
        telegram_id=message.from_user.id,
        name=message.from_user.full_name or "User"
    )
    
    tenders = await db.get_user_tenders(user_id)
    suppliers = await get_suppliers(user_id)
    
    total_items = 0
    for t in tenders:
        items = await db.get_tender_items(t['id'])
        total_items += len(items)
    
    text = "📊 <b>Ваша статистика:</b>\n\n"
    text += f"📋 Тендеров обработано: {len(tenders)}\n"
    text += f"📦 Всего позиций: {total_items}\n"
    text += f"👥 Поставщиков: {len(suppliers)}\n"
    
    await message.answer(text, parse_mode="HTML")

# ============ MAIN ============

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    # Создаем pool перед init_db
    pool = await db.create_pool()
    await db.set_pool(pool)
    
    await db.init_db()
    logger.info("База данных инициализирована")
    
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
