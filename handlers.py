import os
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import asyncio
import openpyxl

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, filters

from config import MINI_APP_URL, TEMP_DIR, DB_PATH, USER_NAMES, logger, ALLOWED_USERS, PROXY_URL
from models import (
    get_all_reports, get_all_report_ids, get_report_values, get_report_metrics,
    get_previous_report_id, get_previous_reports, get_article_stats_for_report,
    get_report_date_range, get_current_cost, get_cost_history,
    delete_cost_history, delete_all_costs_for_article,
    set_product_cost, delete_report, delete_reports, save_report_to_db,
    get_report_id_by_period, get_active_cost,
    calculate_file_hash,
    get_all_articles_with_costs,
    get_nm_id_by_article, get_article_by_nm_id,
    get_last_spp, get_spp_history, is_muted, mute_article,
    get_user_subscriptions, subscribe_user, unsubscribe_user,
    get_user_brand_subscriptions, subscribe_brand, unsubscribe_brand,
    init_spp_tables, init_spp_global_settings,
    get_spp_global_settings, set_spp_global_settings,
    get_all_tracked_articles, get_all_brand_subscribers,
    get_articles_by_brand,
    add_or_update_article
)
from services import (
    detect_report_type, parse_date_from_period,
    ReportProcessor, scheduler
)
from spp_parser import get_spp_for_article_async
from spp_monitor import generate_spp_graph, monitor_spp
from wb_api import get_all_nm_ids_from_api

REPORTS_DIR = Path("/data/reports")
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

async def check_access(update: Update) -> bool:
    if not ALLOWED_USERS:
        return True
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        if update.message:
            await update.message.reply_text("⛔ Доступ запрещён.")
        elif update.callback_query:
            await update.callback_query.answer("⛔ Доступ запрещён.", show_alert=True)
        return False
    return True

def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("📱 Открыть приложение", web_app={"url": MINI_APP_URL})],
        [InlineKeyboardButton("📊 Аналитика", callback_data="menu_analytics_main")],
        [InlineKeyboardButton("📂 Архив отчетов", callback_data="menu_history")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="menu_settings")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    await update.message.reply_text(
        "👋 Привет! Я бот для аналитики WB.\n"
        "Используй меню ниже.",
        reply_markup=get_main_menu()
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    await update.message.reply_text(
        "📋 Команды:\n"
        "/start — начать\n"
        "/help — помощь\n"
        "/osn — отметить файл как основной\n"
        "/vyk — отметить файл как выкупы\n"
        "/articles — детали по артикулам\n"
        "/spp_subscribe <nm_id> [порог] — подписаться\n"
        "/spp_unsubscribe <nm_id> — отписаться\n"
        "/spp_list — список подписок\n"
        "/spp_check — запустить проверку\n"
        "/spp_status — статус мониторинга\n"
        "/spp_stats — статистика СПП\n"
        "/test_parser <nm_id> — проверить парсер\n"
        "/test_proxy — проверить прокси\n"
        "/sync_articles — синхронизировать артикулы из статистики\n"
        "/set_article <nm_id> <артикул> — установить артикул продавца\n"
        "/articles — детали по артикулам\n\n"
        "Для получения помощи используйте /help.",
        parse_mode='Markdown',
        reply_markup=get_main_menu()
    )

async def menu_analytics_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("📈 Аналитика по артикулам", callback_data="menu_analytics")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]
    ]
    await query.edit_message_text("📊 Выберите подраздел:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def menu_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    context.user_data['history_page'] = 0
    context.user_data['history_delete_mode'] = False
    context.user_data['history_selected_for_delete'] = []
    await show_history_page(query, context, page=0)

async def menu_analytics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    context.user_data['analytics_selected'] = []
    context.user_data['analytics_page'] = 0
    await show_analytics_selection(query, context, page=0)

async def menu_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("💰 Себестоимость", callback_data="menu_costs")],
        [InlineKeyboardButton("📊 Мониторинг СПП", callback_data="menu_spp")],
        [InlineKeyboardButton("🛠 Команды разработчика", callback_data="dev_commands")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    await query.edit_message_text("⚙️ Настройки", reply_markup=InlineKeyboardMarkup(keyboard))

async def back_to_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👋 Главное меню:", reply_markup=get_main_menu())

async def dev_commands_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    text = (
        "🛠 *Команды разработчика*\n\n"
        "Эти команды доступны только по прямому вводу:\n\n"
        "`/spp_check` — запустить проверку СПП сейчас\n"
        "`/spp_status` — показать статус мониторинга\n"
        "`/spp_list` — список ваших подписок\n"
        "`/spp_subscribe <nm_id> [порог]` — подписаться вручную\n"
        "`/spp_unsubscribe <nm_id>` — отписаться\n"
        "`/test_parser <nm_id>` — проверить парсер\n"
        "`/test_proxy` — проверить прокси\n"
        "`/sync_articles` — синхронизация артикулов из статистики\n"
        "`/set_article <nm_id> <артикул>` — установить артикул продавца\n"
        "`/osn` / `/vyk` — ручное указание типа файла (устарело)\n"
        "`/articles` — детали по артикулам\n\n"
        "Для получения помощи используйте `/help`."
    )
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад", callback_data="menu_settings")]
    ]))

# === НАСТРОЙКИ СЕБЕСТОИМОСТИ ===
async def menu_costs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not await check_access(update):
            return
        query = update.callback_query
        await query.answer()
        articles = get_all_articles_with_costs()
        keyboard = []
        for item in articles:
            article = item['article']
            cost = item['cost']
            date_from = item['date_from']
            label = f"📦 {article}"
            if cost is not None:
                label += f": {cost:.2f} ₽"
                if date_from:
                    label += f" (с {date_from})"
            else:
                label += ": не задана"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"cost_edit_{article}")])
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="menu_settings")])
        await query.edit_message_text("💰 Управление себестоимостью", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

async def cost_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not await check_access(update):
            return
        query = update.callback_query
        await query.answer()
        article = query.data[9:]
        current = get_current_cost(article)
        keyboard = [
            [InlineKeyboardButton("➕ Установить новую", callback_data=f"cost_set_{article}")],
            [InlineKeyboardButton("📜 История", callback_data=f"cost_history_{article}")],
            [InlineKeyboardButton("◀️ Назад к списку", callback_data="menu_costs")]
        ]
        text = f"💰 Артикул: `{article}`\n\n"
        if current:
            text += f"Текущая себестоимость: {current['cost']:.2f} ₽\n"
            text += f"Установлена: {current['date_from']}\n"
        else:
            text += "Себестоимость не задана.\n"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

async def cost_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    article = query.data[8:]
    context.user_data['waiting_for_cost'] = article
    await query.edit_message_text(
        "💵 Введите новую себестоимость (только число):\nНапример: 450.50",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад к списку", callback_data="menu_costs")]
        ])
    )

async def cost_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    article = query.data[12:]
    history = get_cost_history(article)
    if not history:
        await query.edit_message_text(f"📭 Нет истории для `{article}`.", parse_mode='Markdown')
        return
    text = f"📜 История себестоимости: `{article}`\n\n"
    keyboard = []
    for record in history:
        rec_id, cost, date_from, date_to, set_by, created_at = record
        date_to_str = date_to if date_to else "действует"
        user_name = USER_NAMES.get(set_by, str(set_by)) if set_by else "неизвестно"
        text += f"• {date_from} → {date_to_str}: {cost:.2f} ₽ (установил {user_name})\n"
        if date_to is not None:
            keyboard.append([InlineKeyboardButton(f"🗑️ Удалить запись от {date_from}", callback_data=f"cost_delete_{rec_id}")])
    keyboard.append([InlineKeyboardButton("🗑️ Удалить все записи", callback_data=f"cost_delete_all_{article}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад к деталям", callback_data=f"cost_edit_{article}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад к списку", callback_data="menu_costs")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def cost_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    record_id = int(query.data[11:])
    success = delete_cost_history(record_id)
    if success:
        await query.edit_message_text("✅ Запись удалена.")
        await menu_costs_callback(update, context)
    else:
        await query.edit_message_text("❌ Не удалось удалить (возможно, активна).")

async def cost_delete_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    article = query.data[15:]
    keyboard = [
        [InlineKeyboardButton("✅ Да, удалить всё", callback_data=f"cost_confirm_delete_all_{article}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"cost_history_{article}")]
    ]
    await query.edit_message_text(
        f"⚠️ Удалить все записи для `{article}`? Действие необратимо.",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cost_confirm_delete_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    article = query.data[23:]
    deleted = delete_all_costs_for_article(article)
    if deleted > 0:
        await query.edit_message_text(f"✅ Удалено {deleted} записей для `{article}`.", parse_mode='Markdown')
    else:
        await query.edit_message_text(f"❌ Не найдено записей для `{article}`.", parse_mode='Markdown')
    await asyncio.sleep(1)
    await menu_costs_callback(update, context)

async def handle_cost_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    article = context.user_data.get('waiting_for_cost')
    if not article:
        return
    try:
        cost = float(update.message.text.replace(',', '.'))
        if cost < 0:
            await update.message.reply_text("❌ Себестоимость не может быть отрицательной.")
            return
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT brand FROM article_stats WHERE article = ? LIMIT 1', (article,))
        row = cursor.fetchone()
        brand = row[0] if row else 'Unknown'
        conn.close()
        set_product_cost(article, brand, cost, update.effective_user.id)
        context.user_data['waiting_for_cost'] = None
        keyboard = [[InlineKeyboardButton("◀️ К артикулам", callback_data="menu_costs")]]
        await update.message.reply_text(
            f"✅ Себестоимость для `{article}` установлена: {cost:.2f} ₽",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except ValueError:
        await update.message.reply_text("❌ Введите число.")

# === АНАЛИТИКА ПО АРТИКУЛАМ ===
async def show_analytics_selection(query, context, page):
    reports, total = get_all_reports(page=page, per_page=10)
    if not reports:
        await query.edit_message_text("📭 Нет отчётов.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))
        return
    selected = context.user_data.get('analytics_selected', [])
    total_pages = (total + 9) // 10 if total > 0 else 1
    current_page = page
    msg = f"📊 Выберите отчёты\nВыбрано: {len(selected)} из {total}\nСтраница {current_page+1} из {total_pages}\n\n"
    keyboard = []
    for r in reports:
        report_id, file_name, date_period, start_date, end_date, processed_at = r
        checked = "✅" if report_id in selected else "⬜"
        button_text = f"{checked} {file_name} ({date_period})"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"analytics_toggle_{report_id}")])
    quick_buttons = [
        InlineKeyboardButton("✅ Выбрать все", callback_data="analytics_select_all"),
        InlineKeyboardButton("📅 Неделя (1)", callback_data="analytics_quick_1"),
        InlineKeyboardButton("📅 2 недели", callback_data="analytics_quick_2"),
        InlineKeyboardButton("📅 4 недели", callback_data="analytics_quick_4"),
        InlineKeyboardButton("📅 12 недель", callback_data="analytics_quick_12"),
    ]
    quick_rows = [quick_buttons[i:i+2] for i in range(0, len(quick_buttons), 2)]
    keyboard.extend(quick_rows)
    if selected:
        keyboard.append([InlineKeyboardButton("❌ Отменить все", callback_data="analytics_deselect_all")])
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"analytics_page_{current_page-1}"))
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"analytics_page_{current_page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("📊 Показать аналитику", callback_data="analytics_show")])
    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")])
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def analytics_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("analytics_toggle_"):
        report_id = int(data.split("_")[2])
        selected = context.user_data.get('analytics_selected', [])
        if report_id in selected:
            selected.remove(report_id)
        else:
            selected.append(report_id)
        context.user_data['analytics_selected'] = selected
        page = context.user_data.get('analytics_page', 0)
        await show_analytics_selection(query, context, page)

async def analytics_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("analytics_page_"):
        page = int(data.split("_")[2])
        context.user_data['analytics_page'] = page
        await show_analytics_selection(query, context, page)

async def analytics_select_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    all_ids = get_all_report_ids()
    context.user_data['analytics_selected'] = all_ids
    page = context.user_data.get('analytics_page', 0)
    await show_analytics_selection(query, context, page)

async def analytics_deselect_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    context.user_data['analytics_selected'] = []
    page = context.user_data.get('analytics_page', 0)
    await show_analytics_selection(query, context, page)

async def analytics_quick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "analytics_quick_1":
        limit = 1
    elif data == "analytics_quick_2":
        limit = 2
    elif data == "analytics_quick_4":
        limit = 4
    elif data == "analytics_quick_12":
        limit = 12
    else:
        return
    reports, total = get_all_reports(page=0, per_page=limit)
    selected = [r[0] for r in reports]
    context.user_data['analytics_selected'] = selected
    page = context.user_data.get('analytics_page', 0)
    await show_analytics_selection(query, context, page)

async def analytics_show_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    selected = context.user_data.get('analytics_selected', [])
    if not selected:
        await query.edit_message_text("⚠️ Вы не выбрали ни одного отчёта.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад к выбору", callback_data="menu_analytics")],
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))
        return
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    placeholders = ','.join('?' * len(selected))
    cursor.execute(f'''
        SELECT id, start_date, end_date, date_period
        FROM reports
        WHERE id IN ({placeholders})
        ORDER BY start_date ASC
    ''', selected)
    reports_data = cursor.fetchall()
    conn.close()
    if len(reports_data) < 1:
        await query.edit_message_text("❌ Не удалось загрузить отчёты.")
        return
    articles_agg = {}
    total_orders = 0
    total_revenue = 0
    for rid, start, end, period in reports_data:
        articles = get_article_stats_for_report(rid)
        for art, data in articles.items():
            if art not in articles_agg:
                articles_agg[art] = {'quantity': 0, 'revenue': 0}
            articles_agg[art]['quantity'] += data['quantity']
            articles_agg[art]['revenue'] += data['revenue']
            total_orders += data['quantity']
            total_revenue += data['revenue']
    if not articles_agg:
        await query.edit_message_text("❌ Нет данных по артикулам.")
        return
    first_report_start = reports_data[0][1]
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id
        FROM reports
        WHERE start_date < ?
        ORDER BY start_date DESC
        LIMIT ?
    ''', (first_report_start, len(reports_data)))
    prev_ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    prev_articles_agg = {}
    if prev_ids:
        for pid in prev_ids:
            articles = get_article_stats_for_report(pid)
            for art, data in articles.items():
                if art not in prev_articles_agg:
                    prev_articles_agg[art] = {'quantity': 0, 'revenue': 0}
                prev_articles_agg[art]['quantity'] += data['quantity']
                prev_articles_agg[art]['revenue'] += data['revenue']
    period_str = f"{reports_data[0][3]} — {reports_data[-1][3]}" if len(reports_data) > 1 else reports_data[0][3]
    msg = f"📊 Аналитика\n📅 Период: {period_str}\n📦 Заказов: {total_orders}\n💰 Выручка: {total_revenue:,.2f} ₽\n\n"
    sorted_articles = sorted(articles_agg.items(), key=lambda x: x[1]['revenue'], reverse=True)
    top_articles = sorted_articles[:20]
    msg += "Топ-20 по выручке:\n"
    for art, data in top_articles:
        qty = data['quantity']
        rev = data['revenue']
        if art in prev_articles_agg:
            prev_q = prev_articles_agg[art]['quantity']
            prev_rev = prev_articles_agg[art]['revenue']
            if prev_q > 0 and prev_rev > 0:
                change_q = ((qty - prev_q) / prev_q) * 100
                change_rev = ((rev - prev_rev) / prev_rev) * 100
                change_str = f" (Δ {change_q:+.1f}% / {change_rev:+.1f}%)"
            else:
                change_str = ""
        else:
            change_str = ""
        msg += f"• {art}: {qty} шт. | {rev:,.2f} ₽{change_str}\n"
    if len(sorted_articles) > 20:
        msg += f"\n… и еще {len(sorted_articles)-20} артикулов."
    keyboard = [
        [InlineKeyboardButton("◀️ Назад к выбору", callback_data="menu_analytics")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# === ИСТОРИЯ (АРХИВ) ===
async def show_history_page(query, context, page):
    reports, total = get_all_reports(page=page, per_page=10)
    if not reports:
        await query.edit_message_text("📭 Архив пуст.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))
        return
    delete_mode = context.user_data.get('history_delete_mode', False)
    selected_for_delete = context.user_data.get('history_selected_for_delete', [])
    total_pages = (total + 9) // 10 if total > 0 else 1
    current_page = page
    min_date, max_date = get_report_date_range()
    msg = f"📊 Всего отчетов: {total}\n"
    if min_date and max_date:
        msg += f"📅 Данные с {min_date} по {max_date}\n"
    msg += f"\nСтраница {current_page+1} из {total_pages}\n"
    keyboard = []
    for r in reports:
        report_id, file_name, date_period, start_date, end_date, processed_at = r
        if delete_mode:
            checked = "✅" if report_id in selected_for_delete else "⬜"
            button_text = f"{checked} {file_name} ({date_period})"
            callback_data = f"history_toggle_delete_{report_id}"
        else:
            short_name = file_name if len(file_name) <= 25 else file_name[:22] + "..."
            button_text = f"📄 {short_name} ({date_period})"
            callback_data = f"history_report_{report_id}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    nav = []
    if current_page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"history_page_{current_page-1}"))
    if current_page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"history_page_{current_page+1}"))
    if nav:
        keyboard.append(nav)
    if delete_mode:
        keyboard.append([InlineKeyboardButton("✅ Удалить выбранные", callback_data="history_confirm_delete")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="history_cancel_delete")])
    else:
        keyboard.append([InlineKeyboardButton("🗑 Включить режим удаления", callback_data="history_enable_delete")])
    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")])
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def history_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("_")[-1])
    await show_history_page(query, context, page)

async def history_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    report_id = int(query.data.split("_")[-1])
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT date_period FROM reports WHERE id = ?", (report_id,))
    row = cursor.fetchone()
    conn.close()
    period = row[0] if row else None
    
    report_file = None
    if period:
        report_file = REPORTS_DIR / f"отчёт_{period}.xlsx"
    
    if report_file and report_file.exists():
        with open(report_file, 'rb') as f:
            await query.message.reply_document(
                document=f,
                filename=f"отчёт_{period}.xlsx",
                caption="📄 Заполненный шаблон отчёта"
            )
    
    metrics = get_report_metrics(report_id)
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT date_period, file_name FROM reports WHERE id = ?", (report_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        await query.edit_message_text("❌ Отчёт не найден.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в архив", callback_data="menu_history")]
        ]))
        return
    period = row[0] or "неизвестный период"
    file_name = row[1] or "отчёт"

    if metrics:
        wb_total = metrics.get('wb_total', 0)
        wb_carp = metrics.get('wb_carp', 0)
        wb_hara = metrics.get('wb_hara', 0)
        avg_acquiring = metrics.get('avg_acquiring', 0)
        k_vyvodu_carp = metrics.get('k_vyvodu_carp', 0)
        k_vyvodu_hara = metrics.get('k_vyvodu_hara', 0)
        k_vyvodu_hara_nalog = metrics.get('k_vyvodu_hara_nalog', 0)
        summary = (
            f"📊 *Сводка за {period}*\n"
            f"📄 Файл: {file_name}\n\n"
            f"💰 Общий оборот: {format_number(wb_total)} ₽\n"
            f"🟢 ЦАП: {format_number(wb_carp)} ₽\n"
            f"🔴 Harakiri: {format_number(wb_hara)} ₽\n"
            f"💳 Средний эквайринг: {avg_acquiring:.2f}%\n"
            f"💵 К выводу ЦАП: {format_number(k_vyvodu_carp)} ₽\n"
            f"💵 К выводу Harakiri: {format_number(k_vyvodu_hara)} ₽\n"
            f"💵 К выводу Harakiri с вычетом налога: {format_number(k_vyvodu_hara_nalog)} ₽\n"
        )
    else:
        summary = f"📊 *Отчёт за {period}*\n\nНет данных для отображения."

    buttons = [
        [InlineKeyboardButton("◀️ Назад в архив", callback_data="menu_history")],
        [InlineKeyboardButton("🗑 Удалить отчёт", callback_data=f"history_confirm_delete_{report_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(summary, parse_mode='Markdown', reply_markup=reply_markup)

async def history_enable_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    context.user_data['history_delete_mode'] = True
    context.user_data['history_selected_for_delete'] = []
    await show_history_page(query, context, page=context.user_data.get('history_page', 0))

async def history_cancel_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    context.user_data['history_delete_mode'] = False
    context.user_data['history_selected_for_delete'] = []
    await show_history_page(query, context, page=context.user_data.get('history_page', 0))

async def history_toggle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    report_id = int(query.data.split("_")[-1])
    selected = context.user_data.get('history_selected_for_delete', [])
    if report_id in selected:
        selected.remove(report_id)
    else:
        selected.append(report_id)
    context.user_data['history_selected_for_delete'] = selected
    await show_history_page(query, context, page=context.user_data.get('history_page', 0))

async def history_confirm_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "history_confirm_delete":
        selected = context.user_data.get('history_selected_for_delete', [])
        if not selected:
            await query.edit_message_text("Нет выбранных отчётов.")
            return
        deleted = delete_reports(selected)
        context.user_data['history_delete_mode'] = False
        context.user_data['history_selected_for_delete'] = []
        await query.edit_message_text(f"✅ Удалено {deleted} отчётов.")
    elif data.startswith("history_confirm_delete_"):
        report_id = int(data.split("_")[-1])
        if delete_report(report_id):
            await query.edit_message_text("✅ Отчёт удалён.")
        else:
            await query.edit_message_text("❌ Ошибка удаления.")

# === ОБРАБОТЧИКИ ФАЙЛОВ ===
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    document = update.message.document
    if not document:
        await update.message.reply_text("❌ Это не файл.")
        return
    file_name = document.file_name or "unknown.xlsx"
    if not (file_name.endswith('.xlsx') or file_name.endswith('.xls')):
        await update.message.reply_text("❌ Поддерживаются только Excel-файлы.")
        return
    context.user_data['original_file_name'] = file_name

    temp_path = Path(TEMP_DIR) / f"{datetime.now().timestamp()}_{file_name}"
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    file = await document.get_file()
    await file.download_to_drive(temp_path)
    report_type = detect_report_type(file_name)
    if not report_type:
        await update.message.reply_text("❌ Не удалось определить тип файла. В имени должно быть «осн» или «вык».")
        temp_path.unlink(missing_ok=True)
        return
    if 'files' not in context.user_data:
        context.user_data['files'] = {}
    context.user_data['files'][report_type] = str(temp_path)
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)
    else:
        await update.message.reply_text(f"✅ Файл «{file_name}» загружен. Жду второй файл (для {'вык' if report_type == 'osn' else 'осн'}).")

# ===== ПРОЦЕСС ОБРАБОТКИ ОТЧЁТА (С НОВОЙ ФОРМУЛОЙ НАЛОГА) =====
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
            await update.message.reply_text("❌ Файл шаблона 'шаблон.xlsx' не найден.")
            return
        
        values, articles, date_period = processor.process_files(osn_path, vyk_path, template_path)
        start_date, end_date = parse_date_from_period(date_period)
        
        file_hash = calculate_file_hash(osn_path) + calculate_hash(vyk_path)
        metrics = extract_metrics(values, articles, start_date, end_date)
        
        # Новая формула: К выводу Harakiri с налогом = F13 - (оборот Harakiri * 1%)
        f13_val = values.get('F13', 0) or 0
        hara_turnover = metrics.get('wb_hara', 0)
        metrics['k_vyvodu_hara_nalog'] = f13_val - hara_turnover * 0.01
        
        success, report_id = save_report_to_db(
            file_name=context.user_data.get('original_file_name', Path(osn_path).name),
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
        
        temp_template = Path(TEMP_DIR) / f"filled_{datetime.now().timestamp()}.xlsx"
        shutil.copy2(template_path, temp_template)
        report_file = REPORTS_DIR / f"отчёт_{date_period}.xlsx"
        shutil.copy2(temp_template, report_file)
        logger.info(f"📁 Сохранён файл отчёта: {report_file}")
        
        with open(temp_template, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=f"отчёт_{date_period}.xlsx",
                caption=f"✅ Отчёт за {date_period} обработан и сохранён!"
            )
        temp_template.unlink(missing_ok=True)
        
        summary = (
            f"📊 Сводка за {date_period}\n"
            f"💰 Оборот: {format_number(metrics.get('wb_total', 0))} ₽\n"
            f"🟢 ЦАП: {format_number(metrics.get('wb_carp', 0))} ₽\n"
            f"🔴 Harakiri: {format_number(metrics.get('wb_hara', 0))} ₽\n"
            f"💳 Эквайринг: {metrics.get('avg_acquiring', 0):.2f}%\n"
            f"💵 К выводу ЦАП: {format_number(metrics.get('k_vyvodu_carp', 0))} ₽\n"
            f"💵 К выводу Harakiri: {format_number(metrics.get('k_vyvodu_hara', 0))} ₽\n"
            f"💵 К выводу Harakiri с вычетом налога: {format_number(metrics.get('k_vyvodu_hara_nalog', 0))} ₽\n"
        )
        await update.message.reply_text(summary, parse_mode='Markdown')
        
        context.user_data['files'] = {}
        
    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {e}")
    finally:
        for p in [osn_path, vyk_path]:
            if p and Path(p).exists():
                Path(p).unlink(missing_ok=True)

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def parse_date_from_period(date_period):
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

def format_number(num):
    if num is None:
        return "0"
    if isinstance(num, float) and num.is_integer():
        return f"{int(num):,}".replace(",", " ")
    return f"{num:,.2f}".replace(",", " ")

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
        'total_profit': 0,
        'margin': 0
    }
    return metrics

# === ОБРАБОТЧИК ТЕКСТА ===
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    if context.user_data.get('waiting_for_cost'):
        await handle_cost_input(update, context)
        return
    if context.user_data.get('spp_awaiting_subscribe_nm'):
        await spp_handle_subscribe_input(update, context)
        return
    if context.user_data.get('spp_awaiting_brand'):
        await spp_handle_brand_threshold_input(update, context)
        return
    if context.user_data.get('spp_waiting_threshold'):
        await spp_handle_threshold_input(update, context)
        return
    await update.message.reply_text("Используйте кнопки меню или команды из /help")

# === КОМАНДЫ СПП ===
async def spp_subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("❌ Укажите nm_id. Пример: /spp_subscribe 123456789 5")
        return
    try:
        nm_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Некорректный nm_id.")
        return
    threshold = 5.0
    if len(args) > 1:
        try:
            threshold = float(args[1])
        except ValueError:
            pass
    user_id = update.effective_user.id
    subscribe_user(user_id, nm_id, threshold)
    article_name = get_article_by_nm_id(nm_id)
    await update.message.reply_text(f"✅ Подписка на {article_name} (nm_id={nm_id}) оформлена. Порог: {threshold} п.п.")

async def spp_unsubscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("❌ Укажите nm_id.")
        return
    try:
        nm_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Некорректный nm_id.")
        return
    user_id = update.effective_user.id
    unsubscribe_user(user_id, nm_id)
    await update.message.reply_text(f"✅ Отписка от nm_id {nm_id} оформлена.")

async def spp_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    subs = get_user_subscriptions(user_id)
    brand_subs = get_user_brand_subscriptions(user_id)
    if not subs and not brand_subs:
        await update.message.reply_text("📭 У вас нет активных подписок.")
        return
    text = "📋 Ваши подписки:\n\n"
    for sub in subs:
        article_name = get_article_by_nm_id(sub['nm_id'])
        text += f"• Артикул: {article_name} (nm_id={sub['nm_id']}, порог={sub['threshold']} п.п.)\n"
    for bs in brand_subs:
        text += f"• Бренд: {bs['brand']} (порог={bs['threshold']} п.п.)\n"
    await update.message.reply_text(text)

async def spp_check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    await update.message.reply_text("🔄 Запускаю проверку СПП...")
    try:
        await monitor_spp(context.bot_data.get('application', context.application))
        await update.message.reply_text("✅ Проверка завершена.")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def spp_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    settings = get_spp_global_settings()
    status = "✅ Включён" if settings['enabled'] else "❌ Отключён"
    await update.message.reply_text(f"Текущий статус мониторинга СПП: {status}")

async def test_parser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("❌ Укажите nm_id для проверки. Пример: /test_parser 123456789")
        return
    try:
        nm_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Некорректный nm_id.")
        return
    await update.message.reply_text(f"⏳ Парсинг артикула {nm_id}...")
    data = await get_spp_for_article_async(nm_id)
    if data:
        text = (
            f"✅ *Данные для {nm_id}*\n\n"
            f"Цена по API (до скидки): {data['api_price']} ₽\n"
            f"Цена со скидкой (текущая): {data['site_price']} ₽\n"
            f"СПП: {data['spp_percent']:.1f}%\n"
            f"Название: {data['title']}\n"
            f"Ссылка: {data['url']}"
        )
        await update.message.reply_text(text, parse_mode='Markdown', disable_web_page_preview=True)
    else:
        await update.message.reply_text(f"❌ Не удалось получить данные для {nm_id}.")

async def test_proxy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    await update.message.reply_text("🔄 Проверяю прокси...")
    try:
        import requests
        test_url = "https://api.ipify.org?format=json"
        proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
        response = requests.get(test_url, proxies=proxies, timeout=15, verify=False)
        if response.status_code == 200:
            ip = response.json().get('ip')
            await update.message.reply_text(f"✅ Прокси работает! Ваш внешний IP: {ip}")
        else:
            await update.message.reply_text(f"❌ Прокси вернул статус {response.status_code}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка проверки прокси: {e}")

async def sync_articles_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    await update.message.reply_text("🔄 Синхронизация артикулов из статистики продаж и остатков...")
    try:
        from models import add_or_update_article
        nm_ids = get_all_nm_ids_from_api(days_back=90)
        if not nm_ids:
            await update.message.reply_text("⚠️ Не удалось найти артикулы в статистике.")
            return
        count = 0
        for nm_id in nm_ids:
            add_or_update_article(nm_id, str(nm_id), "Unknown")
            count += 1
        await update.message.reply_text(f"✅ Синхронизировано {count} артикулов.")
    except Exception as e:
        logger.error(f"Ошибка синхронизации: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def set_article_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ Укажите nm_id и артикул. Пример: /set_article 100975057 ПБпесоч5")
        return
    try:
        nm_id = int(args[0])
        article = " ".join(args[1:])
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('UPDATE article_stats SET article = ? WHERE nm_id = ? AND report_id = 0', (article, nm_id))
        if cursor.rowcount == 0:
            cursor.execute('INSERT INTO article_stats (report_id, brand, article, quantity, revenue, nm_id) VALUES (0, "Unknown", ?, 0, 0, ?)', (article, nm_id))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Артикул для {nm_id} установлен: {article}")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")

# ... (остальные функции СПП без изменений)
