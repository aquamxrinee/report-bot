import os
import io
import re
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackContext
import pandas as pd
import openpyxl

from config import TEMP_DIR, ALLOWED_USERS, logger
from models import (
    save_report_to_db, get_report_id_by_period, get_all_reports,
    get_report_values, get_report_metrics, get_previous_report_id,
    get_article_stats_for_report, get_previous_reports,
    get_active_cost, get_current_cost, get_all_articles_with_costs,
    get_cost_history, set_product_cost, delete_cost_history,
    delete_all_costs_for_article, get_earliest_report_date,
    get_news_settings, set_news_settings, delete_report, delete_reports,
    get_all_report_ids, get_report_date_range
)
from services import ReportProcessor, fetch_news, format_news_digest
from wb_api import get_aggregated_stats

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def is_allowed(user_id):
    return str(user_id) in ALLOWED_USERS

def format_number(num):
    if num is None:
        return "0"
    if isinstance(num, float) and num.is_integer():
        return f"{int(num):,}".replace(",", " ")
    return f"{num:,.2f}".replace(",", " ")

# ===== КОМАНДЫ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📱 Открыть приложение", url=os.getenv("MINI_APP_URL", "https://worker-production-a75a.up.railway.app/mini"))],
        [InlineKeyboardButton("📊 Аналитика", callback_data="menu_analytics")],
        [InlineKeyboardButton("📂 Архив отчетов", callback_data="menu_history")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="menu_settings")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 Привет! Я бот для аналитики Wildberries.\n"
        "📌 Отправьте мне два Excel-файла: «осн» и «вык» за одну неделю.",
        reply_markup=reply_markup
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    text = (
        "🤖 *Помощь по боту*\n\n"
        "📤 *Отправка отчётов:*\n"
        "Пришлите два файла: с «осн» и «вык» в названии.\n"
        "Бот распознает период, заполнит шаблон и сохранит данные.\n\n"
        "📱 *Mini App:* кнопка «Открыть приложение» — дашборд.\n\n"
        "📊 *Аналитика:* детальный разбор по артикулам.\n\n"
        "📂 *Архив:* история отчётов с пагинацией.\n\n"
        "⚙️ *Настройки:* новости и себестоимость.\n"
        "Команды: /start, /help, /news_now, /set_news, /wb_stats"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    
    document = update.message.document
    if not document:
        await update.message.reply_text("❌ Это не файл.")
        return
    
    file_name = document.file_name or "unknown.xlsx"
    if not (file_name.endswith('.xlsx') or file_name.endswith('.xls')):
        await update.message.reply_text("❌ Поддерживаются только Excel-файлы (.xlsx, .xls).")
        return
    
    # Сохраняем файл во временную папку
    temp_path = Path(TEMP_DIR) / f"{datetime.now().timestamp()}_{file_name}"
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    file = await document.get_file()
    await file.download_to_drive(temp_path)
    
    # Определяем тип файла
    report_type = None
    if 'осн' in file_name.lower() or 'osn' in file_name.lower():
        report_type = 'osn'
    elif 'вык' in file_name.lower() or 'vyk' in file_name.lower():
        report_type = 'vyk'
    
    if not report_type:
        await update.message.reply_text("❌ Не удалось определить тип файла. В имени должно быть «осн» или «вык».")
        temp_path.unlink(missing_ok=True)
        return
    
    # Сохраняем в context.user_data
    if 'files' not in context.user_data:
        context.user_data['files'] = {}
    context.user_data['files'][report_type] = str(temp_path)
    
    # Проверяем, есть ли второй файл
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        # Оба файла загружены — запускаем обработку
        await process_and_send(update, context)
    else:
        await update.message.reply_text(f"✅ Файл «{file_name}» загружен. Жду второй файл (для { 'вык' if report_type == 'osn' else 'осн' }).")

async def process_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    files = context.user_data.get('files', {})
    osn_path = files.get('osn')
    vyk_path = files.get('vyk')
    
    if not osn_path or not vyk_path:
        await update.message.reply_text("❌ Не хватает файлов. Нужны оба: осн и вык.")
        return
    
    await update.message.reply_text("🔄 Обрабатываю отчёты...")
    
    try:
        processor = ReportProcessor()
        template_path = Path("шаблон.xlsx")
        if not template_path.exists():
            await update.message.reply_text("❌ Файл шаблона 'шаблон.xlsx' не найден на сервере.")
            return
        
        # Обработка
        values, articles, date_period = processor.process_files(osn_path, vyk_path, template_path)
        start_date, end_date = parse_date_period(date_period)
        
        # Сохраняем в БД
        file_hash = calculate_hash(osn_path) + calculate_hash(vyk_path)  # упрощённо
        metrics = extract_metrics(values, articles, start_date, end_date)
        
        # ===== РАСЧЁТ ПРИБЫЛИ =====
        total_profit, margin = calculate_profit_and_margin(articles, start_date, end_date)
        metrics['total_profit'] = total_profit
        metrics['margin'] = margin
        
        # Сохраняем в БД
        success, report_id = save_report_to_db(
            file_name=Path(osn_path).name,
            file_hash=file_hash,
            date_period=date_period,
            start_date=start_date,
            end_date=end_date,
            values=values,
            metrics=metrics,
            articles=articles
        )
        
        if not success:
            await update.message.reply_text("❌ Ошибка сохранения отчёта в БД.")
            return
        
        # Отправляем заполненный шаблон
        with open(template_path, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=f"отчёт_{date_period}.xlsx",
                caption=f"✅ Отчёт за период {date_period} обработан и сохранён!"
            )
        
        # Показываем краткую сводку
        summary = (
            f"📊 *Сводка за {date_period}*\n"
            f"💰 Общий оборот: {format_number(metrics.get('wb_total', 0))} ₽\n"
            f"🟢 ЦАП: {format_number(metrics.get('wb_carp', 0))} ₽\n"
            f"🔴 Harakiri: {format_number(metrics.get('wb_hara', 0))} ₽\n"
            f"💳 Средний эквайринг: {metrics.get('avg_acquiring', 0):.2f}%\n"
            f"📈 Чистая прибыль: {format_number(total_profit)} ₽\n"
            f"📊 Маржинальность: {margin:.2f}%\n"
        )
        await update.message.reply_text(summary, parse_mode="Markdown")
        
        # Очищаем временные файлы
        for p in [osn_path, vyk_path]:
            Path(p).unlink(missing_ok=True)
        context.user_data['files'] = {}
        
    except Exception as e:
        logger.error(f"Ошибка обработки: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Обработка текстовых сообщений (например, ввод себестоимости)
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    
    text = update.message.text.strip()
    # Проверяем, ожидаем ли мы ввод себестоимости
    if context.user_data.get('awaiting_cost_input'):
        await process_cost_input(update, context)
    else:
        await update.message.reply_text("Отправьте мне файлы Excel или используйте кнопки меню.")

# ===== ОБРАБОТКА ТЕКСТА ДЛЯ СЕБЕСТОИМОСТИ =====
async def process_cost_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    article = context.user_data.get('cost_article')
    brand = context.user_data.get('cost_brand')
    if not article or not brand:
        await update.message.reply_text("❌ Не найден артикул. Попробуйте заново.")
        context.user_data['awaiting_cost_input'] = False
        return
    
    try:
        cost = float(update.message.text.replace(',', '.'))
        if cost < 0:
            await update.message.reply_text("❌ Себестоимость не может быть отрицательной.")
            return
    except ValueError:
        await update.message.reply_text("❌ Введите число (например, 150.5).")
        return
    
    # Сохраняем себестоимость
    success = set_product_cost(article, brand, cost, user_id)
    if success:
        await update.message.reply_text(f"✅ Себестоимость для {article} установлена: {cost} ₽")
    else:
        await update.message.reply_text("❌ Ошибка сохранения себестоимости.")
    
    context.user_data['awaiting_cost_input'] = False
    context.user_data['cost_article'] = None
    context.user_data['cost_brand'] = None

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ОБРАБОТКИ =====
def parse_date_period(date_period):
    try:
        parts = date_period.split('-')
        start = parts[0].strip()
        end = parts[1].strip()
        year = datetime.now().year
        start_dt = datetime.strptime(start + f".{year}", "%d.%m.%Y")
        end_dt = datetime.strptime(end + f".{year}", "%d.%m.%Y")
        return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")
    except:
        return None, None

def calculate_hash(file_path):
    import hashlib
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            md5.update(chunk)
    return md5.hexdigest()

def extract_metrics(values, articles, start_date, end_date):
    metrics = {
        'avg_acquiring': values.get('B56', 0),
        'median_acquiring': values.get('B59', 0),
        'min_acquiring': values.get('B62', 0),
        'max_acquiring': values.get('B65', 0),
        'wb_carp': values.get('B44', 0),
        'wb_hara': values.get('B32', 0),
        'wb_total': values.get('B44', 0) + values.get('B32', 0),
        'k_vyvodu_carp': values.get('B4', 0),
        'k_vyvodu_hara': values.get('F4', 0),
        'reklama_carp': 0,
        'reklama_hara': 0,
        'shtrafy': values.get('B10', 0),
        'nalog': 0,
        'carp_orders': 0,
        'hara_orders': 0,
        'profit_carp': 0,
        'profit_hara': 0,
        'margin_carp': 0,
        'margin_hara': 0,
    }
    return metrics

def calculate_profit_and_margin(articles, start_date, end_date):
    total_profit = 0
    total_revenue = 0
    for brand, data in articles.items():
        for key in ['sales', 'vyk']:
            for article, stats in data.get(key, {}).items():
                quantity = stats.get('quantity', 0)
                revenue = stats.get('revenue', 0)
                if quantity == 0 or revenue == 0:
                    continue
                cost = get_active_cost(article, end_date)  # используем себестоимость на дату отчёта
                if cost is None:
                    cost = 0
                profit = revenue - (cost * quantity)
                total_profit += profit
                total_revenue += revenue
    
    margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0
    return total_profit, margin

# ===== МЕНЮ И КОЛБЭКИ =====

# Главное меню
async def menu_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_history_page(update, context, page=0)

async def menu_analytics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_analytics_menu(update, context)

async def menu_analytics_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_analytics_menu(update, context)

async def menu_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_settings_menu(update, context)

async def back_to_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start(update, context)

# ===== ИСТОРИЯ ОТЧЁТОВ =====
async def show_history_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    query = update.callback_query
    user_id = update.effective_user.id
    reports, total = get_all_reports(page=page, per_page=10)
    
    if not reports:
        await query.edit_message_text("📭 Нет сохранённых отчётов.", reply_markup=back_menu())
        return
    
    text = f"📂 *Архив отчётов* (стр. {page+1}, всего {total})\n\n"
    buttons = []
    for r in reports:
        rid, fname, period, start, end, processed = r
        text += f"📄 {fname}  |  {period}\n"
        buttons.append([InlineKeyboardButton(f"📊 {period}", callback_data=f"history_report_{rid}")])
    
    # Пагинация
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Назад", callback_data=f"history_page_{page-1}"))
    if (page+1)*10 < total:
        nav.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"history_page_{page+1}"))
    if nav:
        buttons.append(nav)
    
    # Кнопка удаления
    buttons.append([InlineKeyboardButton("🗑 Включить режим удаления", callback_data="history_enable_delete")])
    buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
    
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def history_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("_")[-1])
    await show_history_page(update, context, page=page)

async def history_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    report_id = int(query.data.split("_")[-1])
    # Показываем детали отчёта
    metrics = get_report_metrics(report_id)
    values = get_report_values(report_id)
    text = f"📊 *Отчёт ID {report_id}*\n\n"
    if metrics:
        for k, v in metrics.items():
            text += f"{k}: {format_number(v)}\n"
    else:
        text += "Нет метрик.\n"
    
    # Кнопки: назад, удалить
    buttons = [
        [InlineKeyboardButton("🔙 Назад", callback_data="menu_history")],
        [InlineKeyboardButton("🗑 Удалить отчёт", callback_data=f"history_confirm_delete_{report_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

# ===== УДАЛЕНИЕ ОТЧЁТОВ =====
async def history_enable_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # Показываем список отчётов с чекбоксами
    reports, total = get_all_reports(page=0, per_page=20)
    if not reports:
        await query.edit_message_text("Нет отчётов для удаления.", reply_markup=back_menu())
        return
    buttons = []
    for r in reports:
        rid = r[0]
        period = r[2] or "без периода"
        buttons.append([InlineKeyboardButton(f"☑️ {period}", callback_data=f"history_toggle_delete_{rid}")])
    buttons.append([InlineKeyboardButton("✅ Удалить выбранные", callback_data="history_confirm_delete")])
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="menu_history")])
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text("Выберите отчёты для удаления (нажмите на каждый):", reply_markup=reply_markup)
    # Сохраняем список выбранных
    context.user_data['delete_selected'] = set()

async def history_toggle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    report_id = int(query.data.split("_")[-1])
    if 'delete_selected' not in context.user_data:
        context.user_data['delete_selected'] = set()
    selected = context.user_data['delete_selected']
    if report_id in selected:
        selected.remove(report_id)
        status = "☑️"
    else:
        selected.add(report_id)
        status = "✅"
    # Обновляем текст кнопки — нужно перерисовать всё меню, проще пересоздать
    # Пересоздаём клавиатуру заново
    reports, _ = get_all_reports(page=0, per_page=20)
    buttons = []
    for r in reports:
        rid = r[0]
        period = r[2] or "без периода"
        check = "✅" if rid in selected else "☑️"
        buttons.append([InlineKeyboardButton(f"{check} {period}", callback_data=f"history_toggle_delete_{rid}")])
    buttons.append([InlineKeyboardButton("✅ Удалить выбранные", callback_data="history_confirm_delete")])
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="menu_history")])
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(
        f"Выбрано {len(selected)} отчётов. Нажмите на отчёт для выбора/снятия.",
        reply_markup=reply_markup
    )

async def history_confirm_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected = context.user_data.get('delete_selected', set())
    if not selected:
        await query.edit_message_text("Нет выбранных отчётов.", reply_markup=back_menu())
        return
    # Подтверждение
    buttons = [
        [InlineKeyboardButton("✅ Да, удалить", callback_data="history_confirm_delete_yes")],
        [InlineKeyboardButton("❌ Отмена", callback_data="menu_history")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(f"Удалить {len(selected)} отчётов? Это действие необратимо.", reply_markup=reply_markup)

async def history_confirm_delete_yes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected = context.user_data.get('delete_selected', set())
    if not selected:
        await query.edit_message_text("Нет отчётов для удаления.", reply_markup=back_menu())
        return
    deleted = delete_reports(list(selected))
    context.user_data['delete_selected'] = set()
    await query.edit_message_text(f"✅ Удалено {deleted} отчётов.", reply_markup=back_menu())

async def history_cancel_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['delete_selected'] = set()
    await query.edit_message_text("❌ Удаление отменено.", reply_markup=back_menu())

# ===== АНАЛИТИКА =====
async def show_analytics_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    # Получаем последние отчёты
    reports, _ = get_all_reports(page=0, per_page=5)
    if not reports:
        await query.edit_message_text("📭 Нет отчётов для аналитики.", reply_markup=back_menu())
        return
    
    buttons = []
    for r in reports:
        rid, fname, period, start, end, processed = r
        buttons.append([InlineKeyboardButton(f"📊 {period}", callback_data=f"analytics_quick_{rid}")])
    buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text("Выберите отчёт для аналитики:", reply_markup=reply_markup)

async def analytics_quick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    report_id = int(query.data.split("_")[-1])
    # Показываем аналитику по артикулам
    await show_articles_analytics(update, context, report_id)

async def show_articles_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE, report_id):
    query = update.callback_query
    articles = get_article_stats_for_report(report_id)
    if not articles:
        await query.edit_message_text("Нет данных по артикулам.", reply_markup=back_menu())
        return
    
    # Сортируем по revenue
    sorted_articles = sorted(articles.items(), key=lambda x: x[1]['revenue'], reverse=True)
    top20 = sorted_articles[:20]
    text = f"📊 *Топ-20 артикулов по выручке*\n\n"
    for i, (art, stats) in enumerate(top20, 1):
        text += f"{i}. {art} — {format_number(stats['revenue'])} ₽ ({stats['quantity']} шт.)\n"
    
    buttons = [
        [InlineKeyboardButton("📈 Рост", callback_data=f"growth_{report_id}")],
        [InlineKeyboardButton("📉 Падение", callback_data=f"decline_{report_id}")],
        [InlineKeyboardButton("📊 Сравнение", callback_data=f"compare_articles_{report_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="menu_analytics")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

# ===== КОЛБЭКИ ДЛЯ АРТИКУЛОВ =====
async def growth_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    report_id = int(query.data.split("_")[-1])
    # Анализ роста — сравниваем с предыдущим периодом
    await show_growth_analysis(update, context, report_id, direction='growth')

async def decline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    report_id = int(query.data.split("_")[-1])
    await show_growth_analysis(update, context, report_id, direction='decline')

async def show_growth_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE, report_id, direction):
    query = update.callback_query
    current_articles = get_article_stats_for_report(report_id)
    prev_id = get_previous_report_id(report_id)
    if not prev_id:
        await query.edit_message_text("Нет предыдущего отчёта для сравнения.", reply_markup=back_menu())
        return
    prev_articles = get_article_stats_for_report(prev_id)
    
    # Собираем изменения
    changes = []
    for art, stats in current_articles.items():
        prev_stats = prev_articles.get(art, {'quantity': 0, 'revenue': 0})
        current_rev = stats['revenue']
        prev_rev = prev_stats['revenue']
        if prev_rev > 0:
            change = (current_rev - prev_rev) / prev_rev * 100
        else:
            change = 0 if current_rev == 0 else 100
        changes.append((art, current_rev, prev_rev, change))
    
    # Сортируем
    if direction == 'growth':
        changes.sort(key=lambda x: x[3], reverse=True)
    else:
        changes.sort(key=lambda x: x[3])
    
    top10 = changes[:10]
    text = f"📈 *Топ-10 роста*" if direction == 'growth' else f"📉 *Топ-10 падения*"
    text += "\n\n"
    for art, curr, prev, chg in top10:
        text += f"• {art}: {format_number(curr)} ₽ (было {format_number(prev)}) → {chg:+.1f}%\n"
    
    buttons = [[InlineKeyboardButton("🔙 Назад", callback_data=f"analytics_quick_{report_id}")]]
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

# ===== СЕБЕСТОИМОСТЬ =====
async def menu_costs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    articles = get_all_articles_with_costs()
    if not articles:
        await query.edit_message_text("Нет артикулов с установленной себестоимостью.", reply_markup=back_menu())
        return
    
    buttons = []
    for item in articles:
        article = item['article']
        cost = item['cost']
        label = f"{article} — {cost} ₽" if cost else f"{article} — не установлена"
        buttons.append([InlineKeyboardButton(label, callback_data=f"cost_edit_{article}")])
    buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="menu_settings")])
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text("📦 *Себестоимость артикулов*\nВыберите артикул для управления:", parse_mode="Markdown", reply_markup=reply_markup)

async def cost_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    article = query.data.split("_", 2)[-1]
    # Получаем информацию
    current = get_current_cost(article)
    history = get_cost_history(article)
    
    text = f"📦 *{article}*\n"
    if current:
        text += f"Текущая себестоимость: {current['cost']} ₽ (с {current['date_from']})\n"
    else:
        text += "Себестоимость не установлена.\n"
    text += f"Всего записей в истории: {len(history)}\n\n"
    
    buttons = [
        [InlineKeyboardButton("✏️ Установить новую", callback_data=f"cost_set_{article}")],
        [InlineKeyboardButton("📜 История", callback_data=f"cost_history_{article}")],
        [InlineKeyboardButton("🗑 Удалить все записи", callback_data=f"cost_delete_all_{article}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="menu_costs")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def cost_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    article = query.data.split("_", 2)[-1]
    # Запрашиваем ввод
    context.user_data['awaiting_cost_input'] = True
    context.user_data['cost_article'] = article
    # Находим бренд (можно из истории или из article_stats)
    # Упрощённо: берём из первого найденного
    # В реальности нужно получить бренд из БД, но упростим:
    # попробуем взять из article_stats
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT brand FROM article_stats WHERE article = ? LIMIT 1", (article,))
    row = cursor.fetchone()
    conn.close()
    brand = row[0] if row else "Цап царапкин"  # fallback
    context.user_data['cost_brand'] = brand
    
    await query.edit_message_text(
        f"Введите новую себестоимость для {article} (в рублях, число):\n\n"
        "Например: 150.5"
    )

async def cost_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    article = query.data.split("_", 2)[-1]
    history = get_cost_history(article)
    if not history:
        await query.edit_message_text("Нет истории.", reply_markup=back_menu())
        return
    
    text = f"📜 *История себестоимости {article}*\n\n"
    for record in history:
        id_, cost, date_from, date_to, set_by, created = record
        status = "действует" if date_to is None else f"до {date_to}"
        text += f"• {date_from}: {cost} ₽ ({status})\n"
    
    buttons = [
        [InlineKeyboardButton("🗑 Удалить запись", callback_data=f"cost_delete_{article}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"cost_edit_{article}")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def cost_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # Показываем список записей для удаления
    article = query.data.split("_", 2)[-1]
    history = get_cost_history(article)
    if not history:
        await query.edit_message_text("Нет записей для удаления.", reply_markup=back_menu())
        return
    
    buttons = []
    for record in history:
        id_, cost, date_from, date_to, set_by, created = record
        label = f"{date_from}: {cost} ₽"
        buttons.append([InlineKeyboardButton(label, callback_data=f"cost_delete_record_{id_}")])
    buttons.append([InlineKeyboardButton("🔙 Назад", callback_data=f"cost_edit_{article}")])
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text("Выберите запись для удаления:", reply_markup=reply_markup)

async def cost_delete_record_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    record_id = int(query.data.split("_")[-1])
    success = delete_cost_history(record_id)
    if success:
        await query.edit_message_text("✅ Запись удалена.", reply_markup=back_menu())
    else:
        await query.edit_message_text("❌ Не удалось удалить запись (возможно, она активна).", reply_markup=back_menu())

# ===== УДАЛЕНИЕ ВСЕХ ЗАПИСЕЙ СЕБЕСТОИМОСТИ (ИСПРАВЛЕНО) =====
async def cost_delete_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # Извлекаем артикул из callback data: "cost_delete_all_article"
    parts = query.data.split("_")
    if len(parts) < 3:
        await query.edit_message_text("❌ Ошибка: не указан артикул.", reply_markup=back_menu())
        return
    article = "_".join(parts[2:])  # на случай, если в названии есть подчёркивания
    # Запрашиваем подтверждение
    buttons = [
        [InlineKeyboardButton("✅ Да, удалить всё", callback_data=f"cost_confirm_delete_all_{article}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"cost_edit_{article}")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(
        f"⚠️ Вы уверены, что хотите удалить ВСЕ записи себестоимости для артикула {article}? Это действие необратимо.",
        reply_markup=reply_markup
    )

async def cost_confirm_delete_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    if len(parts) < 5:  # "cost_confirm_delete_all_article"
        await query.edit_message_text("❌ Ошибка: не указан артикул.", reply_markup=back_menu())
        return
    article = "_".join(parts[4:])
    deleted = delete_all_costs_for_article(article)
    if deleted > 0:
        await query.edit_message_text(f"✅ Удалено {deleted} записей для {article}.", reply_markup=back_menu())
    else:
        await query.edit_message_text(f"❌ Не найдено записей для {article}.", reply_markup=back_menu())

# ===== НАСТРОЙКИ =====
async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    buttons = [
        [InlineKeyboardButton("📰 Новости", callback_data="news_settings")],
        [InlineKeyboardButton("💰 Себестоимость", callback_data="menu_costs")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text("⚙️ *Настройки*", parse_mode="Markdown", reply_markup=reply_markup)

# ===== НОВОСТИ =====
async def news_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    settings = get_news_settings(user_id)
    status = "✅ Включены" if settings['enabled'] else "❌ Отключены"
    text = (
        f"📰 *Настройки новостей*\n\n"
        f"Статус: {status}\n"
        f"Запрос: {settings['query']}\n"
        f"Утро: {settings['morning_time']}\n"
        f"Вечер: {settings['evening_time']}\n"
    )
    buttons = [
        [InlineKeyboardButton("🔄 Переключить", callback_data="news_toggle")],
        [InlineKeyboardButton("✏️ Изменить запрос", callback_data="news_query")],
        [InlineKeyboardButton("⏰ Изменить время", callback_data="news_time")],
        [InlineKeyboardButton("📰 Новости сейчас", callback_data="news_now")],
        [InlineKeyboardButton("🔙 Назад", callback_data="menu_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def news_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    settings = get_news_settings(user_id)
    new_enabled = not settings['enabled']
    set_news_settings(user_id, enabled=new_enabled)
    await news_settings_callback(update, context)

async def news_query_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # Просим ввести запрос
    context.user_data['awaiting_news_query'] = True
    await query.edit_message_text(
        "Введите новый поисковый запрос для новостей (например, 'Wildberries OR ВБ'):\n"
        "Отправьте текстовое сообщение."
    )

async def news_time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    buttons = [
        [InlineKeyboardButton("🌅 Утро", callback_data="news_time_morning")],
        [InlineKeyboardButton("🌇 Вечер", callback_data="news_time_evening")],
        [InlineKeyboardButton("🔙 Назад", callback_data="news_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text("Выберите время для изменения:", reply_markup=reply_markup)

async def news_time_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    part = query.data.split("_")[-1]  # morning или evening
    context.user_data['awaiting_news_time'] = part
    await query.edit_message_text(
        f"Введите новое время для {'утра' if part == 'morning' else 'вечера'} в формате ЧЧ:ММ (например, 08:30):"
    )

async def news_now_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    settings = get_news_settings(user_id)
    articles = fetch_news(settings['query'], limit=10)
    text = format_news_digest(articles, "📰 *Новости сейчас*")
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back_menu())

# ===== ОБРАБОТКА ТЕКСТА ДЛЯ НОВОСТЕЙ =====
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    
    text = update.message.text.strip()
    
    # Ожидание ввода запроса новостей
    if context.user_data.get('awaiting_news_query'):
        set_news_settings(user_id, query=text)
        context.user_data['awaiting_news_query'] = False
        await update.message.reply_text(f"✅ Запрос новостей обновлён: {text}")
        return
    
    # Ожидание ввода времени
    if context.user_data.get('awaiting_news_time'):
        time_part = context.user_data['awaiting_news_time']
        # Проверка формата
        import re
        if re.match(r'^\d{2}:\d{2}$', text):
            # Обновляем время
            settings = get_news_settings(user_id)
            if time_part == 'morning':
                set_news_settings(user_id, morning_time=text)
            else:
                set_news_settings(user_id, evening_time=text)
            context.user_data['awaiting_news_time'] = None
            await update.message.reply_text(f"✅ Время обновлено: {text}")
        else:
            await update.message.reply_text("❌ Неверный формат. Используйте ЧЧ:ММ (например, 08:30).")
        return
    
    # Ожидание ввода себестоимости
    if context.user_data.get('awaiting_cost_input'):
        await process_cost_input(update, context)
        return
    
    await update.message.reply_text("Отправьте мне файлы Excel или используйте кнопки меню.")

# ===== КОМАНДА /WB_STATS =====
async def wb_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    await update.message.reply_text("⏳ Загружаю статистику Wildberries...")
    data = get_aggregated_stats(force_refresh=True)
    if data.get("error"):
        await update.message.reply_text(f"❌ Ошибка: {data['error']}")
        return
    text = (
        "📊 *Статистика Wildberries (за 7 дней)*\n\n"
        f"💰 Выручка: {format_number(data.get('total_revenue', 0))} ₽\n"
        f"📦 Заказов: {data.get('total_orders', 0)}\n"
        f"📈 Средний чек: {format_number(data.get('avg_order_value', 0))} ₽\n"
        f"📦 Остатки: {data.get('total_stock', 0)} шт.\n"
        f"🆕 Уникальных артикулов: {data.get('unique_articles', 0)}\n"
        f"👀 Просмотры: {data.get('views', 0)}\n"
        f"🛒 В корзине: {data.get('cart_adds', 0)}\n"
        f"📦 Заказы (воронка): {data.get('orders', 0)}\n"
        f"✅ Покупки: {data.get('purchases', 0)}\n"
        f"🔄 Конверсия просмотр→корзина: {data.get('conversion_view_to_cart', 0):.2f}%\n"
        f"🔄 Конверсия корзина→заказ: {data.get('conversion_cart_to_order', 0):.2f}%\n"
        f"🔄 Конверсия заказ→покупка: {data.get('conversion_order_to_purchase', 0):.2f}%\n"
    )
    if data.get('partial_error'):
        text += "\n⚠️ Частичная ошибка загрузки данных (см. логи)."
    await update.message.reply_text(text, parse_mode="Markdown")

# ===== ВСПОМОГАТЕЛЬНЫЕ КНОПКИ =====
def back_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]])

# ===== РЕГИСТРАЦИЯ В main.py ВЫПОЛНЯЕТСЯ ТАМ =====
# Все обработчики уже зарегистрированы в main.py, поэтому здесь только определения функций.
