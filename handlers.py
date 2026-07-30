import os
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, filters

from config import (
    MINI_APP_URL, TEMP_DIR, DB_PATH, USER_NAMES, logger, ALLOWED_USERS
)
from models import (
    get_all_reports, get_all_report_ids, get_report_values, get_report_metrics,
    get_previous_report_id, get_previous_reports, get_article_stats_for_report,
    get_report_date_range, get_current_cost, get_cost_history,
    delete_cost_history, delete_all_costs_for_article,
    set_product_cost, delete_report, delete_reports, save_report_to_db,
    get_report_id_by_period, get_active_cost,
    get_news_settings, set_news_settings,
    calculate_file_hash,
    get_all_articles_with_costs,
    # Функции для СПП
    get_last_spp, get_spp_history, is_muted, mute_article,
    get_user_subscriptions, subscribe_user, unsubscribe_user,
    init_spp_tables
)
from services import (
    fetch_news, format_news_digest, detect_report_type, parse_date_from_period,
    ReportProcessor, scheduler, scheduled_morning_digest, scheduled_evening_digest
)
from spp_parser import get_spp_for_article
from spp_monitor import generate_spp_graph

# ===== ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ =====
async def check_access(update: Update) -> bool:
    if not ALLOWED_USERS:
        return True
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        if update.message:
            await update.message.reply_text("⛔ Доступ запрещён. Вы не авторизованы.")
        elif update.callback_query:
            await update.callback_query.answer("⛔ Доступ запрещён.", show_alert=True)
        return False
    return True

# ===== ГЛАВНОЕ МЕНЮ =====
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("📱 Открыть приложение", web_app={"url": MINI_APP_URL})],
        [InlineKeyboardButton("📊 Аналитика", callback_data="menu_analytics_main")],
        [InlineKeyboardButton("📂 Архив отчетов", callback_data="menu_history")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="menu_settings")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ===== КОМАНДЫ БОТА =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    await update.message.reply_text(
        "👋 Привет! Я бот для аналитики кабинета WB по брендам Цап царапкин & Harakiri.\n\n"
        "📊 Используй меню ниже для быстрого доступа к функциям.",
        reply_markup=get_main_menu()
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    await update.message.reply_text(
        "📋 **Доступные команды:**\n"
        "/start — начать\n"
        "/help — помощь\n"
        "/osn — отметить файл как основной (вручную)\n"
        "/vyk — отметить файл как выкупы (вручную)\n"
        "/articles — детали по артикулам (текущий отчет)\n"
        "/news_now — получить новости прямо сейчас\n"
        "/set_news — настроить новостные сводки\n"
        "/set_news_query — изменить поисковый запрос\n\n"
        "**Мониторинг СПП:**\n"
        "/spp_subscribe <nm_id> [порог] — подписаться на изменения СПП\n"
        "/spp_unsubscribe <nm_id> — отписаться\n"
        "/spp_list — список ваших подписок\n\n"
        "Также можно использовать кнопки меню.",
        parse_mode='Markdown',
        reply_markup=get_main_menu()
    )

# ===== ПОДМЕНЮ "АНАЛИТИКА" =====
async def menu_analytics_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("📈 Аналитика по артикулам", callback_data="menu_analytics")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]
    ]
    await query.edit_message_text(
        "📊 **Раздел аналитики**\n\nВыберите нужный подраздел:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

# ===== ОБРАБОТЧИКИ МЕНЮ =====
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
        [InlineKeyboardButton("📰 Новости", callback_data="news_settings")],
        [InlineKeyboardButton("💰 Себестоимость", callback_data="menu_costs")],
        [InlineKeyboardButton("📊 Мониторинг СПП", callback_data="menu_spp")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    await query.edit_message_text("⚙️ **Настройки**\n\nВыберите раздел:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def back_to_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "👋 Главное меню:",
        reply_markup=get_main_menu()
    )

# ===== НОВОСТНОЙ РАЗДЕЛ =====
async def news_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    settings = get_news_settings(user_id)
    status = "✅ Включены" if settings['enabled'] else "❌ Отключены"
    text = f"⚙️ **Настройки новостей**\n\n"
    text += f"Статус: {status}\n"
    text += f"Поисковый запрос: `{settings['query']}`\n"
    text += f"Утреннее время: {settings['morning_time']}\n"
    text += f"Вечернее время: {settings['evening_time']}\n\n"
    text += "Выберите действие:"
    keyboard = [
        [InlineKeyboardButton("📰 Получить новости сейчас", callback_data="news_now")],
        [InlineKeyboardButton("🔄 Вкл/Выкл", callback_data="news_toggle")],
        [InlineKeyboardButton("📝 Изменить запрос", callback_data="news_query")],
        [InlineKeyboardButton("🕐 Изменить время", callback_data="news_time")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu_settings")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def news_now_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    settings = get_news_settings(user_id)
    articles = fetch_news(settings['query'], limit=10)
    text = format_news_digest(articles, "📰 **Свежие новости по теме Wildberries**")
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад к настройкам", callback_data="menu_settings")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]))

async def news_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    settings = get_news_settings(user_id)
    new_enabled = not settings['enabled']
    set_news_settings(user_id, enabled=new_enabled)
    await news_settings_callback(update, context)

async def news_query_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📝 Введите новый поисковый запрос для новостей.\n"
        "Например: `Wildberries OR ВБ`\n"
        "Используйте команду /set_news_query <запрос>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад", callback_data="news_settings")]
        ]),
        parse_mode='Markdown'
    )

async def news_time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🌅 Утро (08:30)", callback_data="news_time_morning_08:30")],
        [InlineKeyboardButton("🌅 Утро (09:00)", callback_data="news_time_morning_09:00")],
        [InlineKeyboardButton("🌅 Утро (07:00)", callback_data="news_time_morning_07:00")],
        [InlineKeyboardButton("🌇 Вечер (20:40)", callback_data="news_time_evening_20:40")],
        [InlineKeyboardButton("🌇 Вечер (21:00)", callback_data="news_time_evening_21:00")],
        [InlineKeyboardButton("🌇 Вечер (19:00)", callback_data="news_time_evening_19:00")],
        [InlineKeyboardButton("◀️ Назад", callback_data="news_settings")]
    ]
    await query.edit_message_text("Выберите время для утренней/вечерней сводки:", reply_markup=InlineKeyboardMarkup(keyboard))

async def news_time_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split("_")
    time_of_day = parts[2]
    time_str = parts[3]
    user_id = update.effective_user.id
    if time_of_day == 'morning':
        set_news_settings(user_id, morning_time=time_str)
    else:
        set_news_settings(user_id, evening_time=time_str)
    await news_settings_callback(update, context)

async def news_now_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    settings = get_news_settings(user_id)
    articles = fetch_news(settings['query'], limit=10)
    text = format_news_digest(articles, "📰 **Свежие новости по теме Wildberries**")
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]))

async def set_news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    settings = get_news_settings(user_id)
    status = "включены" if settings['enabled'] else "отключены"
    text = f"Текущие настройки новостей:\n"
    text += f"Рассылка: {status}\n"
    text += f"Запрос: `{settings['query']}`\n"
    text += f"Утро: {settings['morning_time']}\n"
    text += f"Вечер: {settings['evening_time']}\n\n"
    text += "Используйте меню для настройки (кнопка '⚙️ Настройки' в главном меню)."
    await update.message.reply_text(text, parse_mode='Markdown')

async def set_news_query_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text("❌ Укажите запрос. Пример: /set_news_query Wildberries OR ВБ")
        return
    new_query = ' '.join(args)
    set_news_settings(user_id, query=new_query)
    await update.message.reply_text(f"✅ Поисковый запрос обновлён: `{new_query}`", parse_mode='Markdown')

# ===== НАСТРОЙКИ СЕБЕСТОИМОСТИ =====
async def menu_costs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not await check_access(update):
            return
        query = update.callback_query
        await query.answer()
        logger.info("📂 Открываем меню себестоимости")
        articles = get_all_articles_with_costs()
        logger.info(f"✅ Получено {len(articles)} артикулов")
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
        await query.edit_message_text(
            "💰 **Управление себестоимостью**\n\n"
            "Выберите артикул для редактирования. Текущая себестоимость указана на кнопке.\n"
            "Первая установленная себестоимость будет действовать с даты первого отчёта.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"❌ Ошибка в menu_costs_callback: {e}")
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

async def cost_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not await check_access(update):
            return
        query = update.callback_query
        await query.answer()
        article = query.data.split("_")[2]
        current = get_current_cost(article)
        keyboard = [
            [InlineKeyboardButton("➕ Установить новую себестоимость", callback_data=f"cost_set_{article}")],
            [InlineKeyboardButton("📜 История изменений", callback_data=f"cost_history_{article}")],
            [InlineKeyboardButton("◀️ Назад к списку", callback_data="menu_costs")]
        ]
        text = f"💰 **Артикул:** `{article}`\n\n"
        if current:
            text += f"Текущая себестоимость: **{current['cost']:.2f} ₽**\n"
            text += f"Установлена: {current['date_from']}\n"
        else:
            text += "Себестоимость не задана.\n"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"❌ Ошибка в cost_edit_callback: {e}")
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

async def cost_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    article = query.data.split("_")[2]
    context.user_data['waiting_for_cost'] = article
    await query.edit_message_text(
        f"💵 Введите новую себестоимость для артикула `{article}` в рублях (только число):\n\n"
        "Например: `450.50`\n\n"
        "Если это первая установка, она будет действовать с даты первого отчёта.",
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
    article = query.data.split("_")[2]
    history = get_cost_history(article)
    if not history:
        await query.edit_message_text(f"📭 Для артикула `{article}` нет истории изменений.", parse_mode='Markdown')
        return
    text = f"📜 **История себестоимости:** `{article}`\n\n"
    keyboard = []
    for record in history:
        rec_id, cost, date_from, date_to, set_by, created_at = record
        date_to_str = date_to if date_to else "действует"
        user_name = USER_NAMES.get(set_by, str(set_by)) if set_by else "неизвестно"
        text += f"• {date_from} → {date_to_str}: **{cost:.2f} ₽**"
        if set_by:
            text += f" (установил {user_name})"
        text += "\n"
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
    record_id = int(query.data.split("_")[2])
    success = delete_cost_history(record_id)
    if success:
        await query.edit_message_text("✅ Запись удалена из истории.")
        await menu_costs_callback(update, context)
    else:
        await query.edit_message_text("❌ Не удалось удалить запись (возможно, она активна или уже удалена).")

async def cost_delete_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    article = query.data.split("_")[3]
    keyboard = [
        [InlineKeyboardButton("✅ Да, удалить всё", callback_data=f"cost_confirm_delete_all_{article}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"cost_history_{article}")]
    ]
    await query.edit_message_text(
        f"⚠️ Вы уверены, что хотите удалить **все** записи себестоимости для артикула `{article}`?\n"
        "Это действие необратимо.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def cost_confirm_delete_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    article = query.data.split("_")[4]
    deleted = delete_all_costs_for_article(article)
    if deleted > 0:
        await query.edit_message_text(f"✅ Удалено {deleted} записей для артикула `{article}`.", parse_mode='Markdown')
    else:
        await query.edit_message_text(f"❌ Не найдено записей для артикула `{article}`.", parse_mode='Markdown')
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
            f"✅ Себестоимость для артикула `{article}` установлена: **{cost:.2f} ₽**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except ValueError:
        await update.message.reply_text("❌ Введите корректное число (например, 450.50).")

# ===== АНАЛИТИКА ПО АРТИКУЛАМ =====
async def show_analytics_selection(query, context, page):
    reports, total = get_all_reports(page=page, per_page=10)
    if not reports:
        await query.edit_message_text("📭 Нет отчётов для анализа.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))
        return

    selected = context.user_data.get('analytics_selected', [])
    total_pages = (total + 9) // 10 if total > 0 else 1
    current_page = page

    msg = f"📊 **Выберите отчёты для анализа**\n"
    msg += f"Выбрано: {len(selected)} из {total}\n"
    msg += f"\n*Страница {current_page+1} из {total_pages}*\n\n"

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
        await query.edit_message_text("❌ Не удалось загрузить выбранные отчёты.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))
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
        await query.edit_message_text("❌ В выбранных отчётах нет данных по артикулам.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))
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
    msg = f"📊 **Аналитика по артикулам**\n"
    msg += f"📅 Период: {period_str}\n"
    msg += f"📦 Всего заказов: {total_orders}\n"
    msg += f"💰 Общая выручка: {total_revenue:,.2f} ₽\n\n"

    sorted_articles = sorted(articles_agg.items(), key=lambda x: x[1]['revenue'], reverse=True)
    top_articles = sorted_articles[:20]

    msg += "**Топ-20 артикулов по выручке:**\n"
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
        msg += f"• **{art}**: {qty} шт. | {rev:,.2f} ₽{change_str}\n"

    if len(sorted_articles) > 20:
        msg += f"\n… и еще {len(sorted_articles)-20} артикулов."

    keyboard = [
        [InlineKeyboardButton("◀️ Назад к выбору отчётов", callback_data="menu_analytics")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ===== ИСТОРИЯ (АРХИВ) С ВОЗМОЖНОСТЬЮ УДАЛЕНИЯ =====
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
    msg = f"📊 **Всего отчетов: {total}**\n"
    if min_date and max_date:
        msg += f"📅 Данные доступны с **{min_date}** по **{max_date}**\n"
    msg += f"\n*Страница {current_page+1} из {total_pages}*\n"

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

    # Навигация
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
    metrics = get_report_metrics(report_id)
    text = f"📊 **Отчёт ID {report_id}**\n\n"
    if metrics:
        for k, v in metrics.items():
            text += f"{k}: {v:,.2f}\n"
    else:
        text += "Нет метрик.\n"
    buttons = [
        [InlineKeyboardButton("🔙 Назад к списку", callback_data="menu_history")],
        [InlineKeyboardButton("🗑 Удалить отчёт", callback_data=f"history_confirm_delete_{report_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)

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
        # множественное удаление
        selected = context.user_data.get('history_selected_for_delete', [])
        if not selected:
            await query.edit_message_text("Нет выбранных отчётов.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад", callback_data="menu_history")]
            ]))
            return
        deleted = delete_reports(selected)
        context.user_data['history_delete_mode'] = False
        context.user_data['history_selected_for_delete'] = []
        await query.edit_message_text(f"✅ Удалено {deleted} отчётов.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад к списку", callback_data="menu_history")]
        ]))
    elif data.startswith("history_confirm_delete_"):
        # одиночное удаление
        report_id = int(data.split("_")[-1])
        if delete_report(report_id):
            await query.edit_message_text("✅ Отчёт удалён.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад к списку", callback_data="menu_history")]
            ]))
        else:
            await query.edit_message_text("❌ Ошибка удаления.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад к списку", callback_data="menu_history")]
            ]))

# ===== ОБРАБОТЧИК ФАЙЛОВ (ОРИГИНАЛЬНАЯ ЛОГИКА) =====
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
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
    report_type = detect_report_type(file_name)
    if not report_type:
        await update.message.reply_text("❌ Не удалось определить тип файла. В имени должно быть «осн» или «вык».")
        temp_path.unlink(missing_ok=True)
        return

    # Сохраняем в user_data
    if 'files' not in context.user_data:
        context.user_data['files'] = {}
    context.user_data['files'][report_type] = str(temp_path)

    # Проверяем, есть ли второй файл
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        # Оба файла загружены — запускаем обработку
        await process_and_send(update, context)
    else:
        await update.message.reply_text(f"✅ Файл «{file_name}» загружен. Жду второй файл (для {'вык' if report_type == 'osn' else 'осн'}).")

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
        start_date, end_date = parse_date_from_period(date_period)
        
        # Сохраняем в БД
        file_hash = calculate_file_hash(osn_path) + calculate_hash(vyk_path)
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
        await update.message.reply_text(summary, parse_mode='Markdown')
        
        # Очищаем временные файлы
        for p in [osn_path, vyk_path]:
            Path(p).unlink(missing_ok=True)
        context.user_data['files'] = {}
        
    except Exception as e:
        logger.error(f"Ошибка обработки: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {e}")

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ОБРАБОТКИ =====
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
                cost = get_active_cost(article, end_date)
                if cost is None:
                    cost = 0
                profit = revenue - (cost * quantity)
                total_profit += profit
                total_revenue += revenue
    margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0
    return total_profit, margin

# ===== ОБРАБОТЧИК ТЕКСТА =====
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    # Если ожидается ввод себестоимости
    if context.user_data.get('waiting_for_cost'):
        await handle_cost_input(update, context)
        return
    # Если ожидается ввод запроса новостей (не обрабатываем здесь, т.к. делаем через колбэки)
    await update.message.reply_text("Используйте кнопки меню или команды из /help")

# ===== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ СПП =====
async def spp_subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подписка на отслеживание СПП по артикулу (nm_id)"""
    if not await check_access(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Укажите артикул (nm_id).\n"
            "Пример: /spp_subscribe 123456789 5\n"
            "где 5 — порог изменения в процентных пунктах (по умолчанию 5)"
        )
        return
    try:
        nm_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Артикул должен быть числом.")
        return

    threshold = 5.0
    if len(args) > 1:
        try:
            threshold = float(args[1])
        except ValueError:
            pass

    user_id = update.effective_user.id
    subscribe_user(user_id, nm_id, threshold)
    await update.message.reply_text(
        f"✅ Вы подписались на отслеживание СПП для артикула {nm_id}.\n"
        f"Порог изменения: {threshold} п.п."
    )

async def spp_unsubscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отписка от отслеживания СПП"""
    if not await check_access(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("❌ Укажите артикул (nm_id).")
        return
    try:
        nm_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Артикул должен быть числом.")
        return

    user_id = update.effective_user.id
    unsubscribe_user(user_id, nm_id)
    await update.message.reply_text(f"✅ Вы отписались от отслеживания СПП для артикула {nm_id}.")

async def spp_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список подписок пользователя"""
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    subs = get_user_subscriptions(user_id)
    if not subs:
        await update.message.reply_text("📭 У вас нет активных подписок на СПП.")
        return
    text = "📋 Ваши подписки на СПП:\n\n"
    for sub in subs:
        text += f"• {sub['nm_id']} (порог: {sub['threshold']} п.п.)\n"
    await update.message.reply_text(text)

# ===== КОЛБЭКИ ДЛЯ КНОПОК УВЕДОМЛЕНИЙ О СПП =====
async def spp_mute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Глушить на 2ч'"""
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    nm_id = int(query.data.split("_")[2])
    user_id = update.effective_user.id

    mute_article(user_id, nm_id, hours=2)
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔇 Заглушено на 2ч", callback_data="spp_muted")]
        ])
    )

async def spp_graph_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'График' — отправляет изображение"""
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    nm_id = int(query.data.split("_")[2])

    img_base64 = generate_spp_graph(nm_id)
    if not img_base64:
        await query.edit_message_text("❌ Недостаточно данных для построения графика.")
        return

    # Отправляем изображение
    await query.message.reply_photo(
        photo=img_base64,
        caption=f"📈 График изменения СПП для артикула {nm_id}"
    )
    # Редактируем исходное сообщение (убираем кнопки или оставляем)
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📈 Обновить график", callback_data=f"spp_graph_{nm_id}")],
            [InlineKeyboardButton("🔇 Глушить на 2ч", callback_data=f"spp_mute_{nm_id}")]
        ])
    )

async def menu_spp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления подписками на СПП"""
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    subs = get_user_subscriptions(user_id)
    text = "📊 **Мониторинг СПП**\n\n"
    if subs:
        text += "Ваши подписки:\n"
        for sub in subs:
            text += f"• Артикул {sub['nm_id']} (порог {sub['threshold']} п.п.)\n"
        text += "\nИспользуйте команды для управления:\n"
    else:
        text += "У вас нет подписок.\n"
    text += "/spp_subscribe <nm_id> [порог] — подписаться\n"
    text += "/spp_unsubscribe <nm_id> — отписаться\n"
    text += "/spp_list — список подписок\n"
    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="menu_settings")]]
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
