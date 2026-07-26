import os
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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
    get_all_articles_with_costs
)
from services import (
    fetch_news, format_news_digest, detect_report_type, parse_date_from_period,
    ReportProcessor, scheduler, scheduled_morning_digest, scheduled_evening_digest
)
from wb_api import get_aggregated_stats  # <-- импорт для команды

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
        "📊 Используй меню ниже для быстрого доступа к функциям.\n"
        "📈 Также доступна команда /wb_stats — статистика из API Wildberries.",
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
        "/set_news_query — изменить поисковый запрос\n"
        "/wb_stats — статистика из Wildberries API (выручка, заказы, остатки, воронка)\n\n"
        "Также можно использовать кнопки меню.",
        parse_mode='Markdown',
        reply_markup=get_main_menu()
    )

# ===== КОМАНДА /wb_stats =====
async def wb_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    await update.message.reply_text("⏳ Загрузка статистики из Wildberries API...")
    data = get_aggregated_stats()
    if "error" in data:
        await update.message.reply_text(f"❌ Ошибка: {data['error']}")
        return
    msg = f"📊 **Статистика WB за 7 дней**\n\n"
    msg += f"💰 Выручка: {data.get('total_revenue', 0):,.0f} ₽\n"
    msg += f"📦 Заказов: {data.get('total_orders', 0)}\n"
    msg += f"📊 Средний чек: {data.get('avg_order_value', 0):,.0f} ₽\n"
    msg += f"📦 На складе: {data.get('total_stock', 0)} шт.\n"
    msg += f"📦 Уникальных артикулов: {data.get('unique_articles', 0)}\n\n"
    msg += f"👁️ Просмотры: {data.get('views', 0)}\n"
    msg += f"🛒 В корзину: {data.get('cart_adds', 0)}\n"
    msg += f"📋 Заказы: {data.get('orders', 0)}\n"
    msg += f"✅ Выкупы: {data.get('purchases', 0)}\n\n"
    msg += f"📈 Конверсия просмотр→корзина: {data.get('conversion_view_to_cart', 0):.1f}%\n"
    msg += f"📈 Конверсия корзина→заказ: {data.get('conversion_cart_to_order', 0):.1f}%\n"
    msg += f"📈 Конверсия заказ→выкуп: {data.get('conversion_order_to_purchase', 0):.1f}%"
    await update.message.reply_text(msg, parse_mode='Markdown')

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
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    await query.edit_message_text("⚙️ **Настройки**\n\nВыберите раздел:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

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

    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"history_page_{current_page-1}"))
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"history_page_{current_page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    if delete_mode:
        keyboard.append([InlineKeyboardButton("🗑️ Удалить выбранные", callback_data="history_confirm_delete")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="history_cancel_delete")])
    else:
        keyboard.append([InlineKeyboardButton("🗑️ Удалить отчеты", callback_data="history_enable_delete")])

    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")])
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def history_toggle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("history_toggle_delete_"):
        report_id = int(data.split("_")[3])
        selected = context.user_data.get('history_selected_for_delete', [])
        if report_id in selected:
            selected.remove(report_id)
        else:
            selected.append(report_id)
        context.user_data['history_selected_for_delete'] = selected
        page = context.user_data.get('history_page', 0)
        await show_history_page(query, context, page)

async def history_enable_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    context.user_data['history_delete_mode'] = True
    context.user_data['history_selected_for_delete'] = []
    page = context.user_data.get('history_page', 0)
    await show_history_page(query, context, page)

async def history_cancel_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    context.user_data['history_delete_mode'] = False
    context.user_data['history_selected_for_delete'] = []
    page = context.user_data.get('history_page', 0)
    await show_history_page(query, context, page)

async def history_confirm_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    selected = context.user_data.get('history_selected_for_delete', [])
    if not selected:
        await query.answer("⚠️ Не выбрано ни одного отчёта.", show_alert=True)
        return
    deleted = delete_reports(selected)
    context.user_data['history_delete_mode'] = False
    context.user_data['history_selected_for_delete'] = []
    page = context.user_data.get('history_page', 0)
    await show_history_page(query, context, page)
    await query.message.reply_text(f"🗑️ Удалено {deleted} отчётов.")

async def history_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("history_page_"):
        page = int(data.split("_")[2])
        context.user_data['history_page'] = page
        await show_history_page(query, context, page)

async def history_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("history_report_"):
        report_id = int(data.split("_")[2])
        await resend_report(query, context, report_id)

async def resend_report(query, context, report_id):
    values = get_report_values(report_id)
    metrics = get_report_metrics(report_id)
    if not values or not metrics:
        await query.edit_message_text("❌ Данные отчёта не найдены.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))
        return

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT file_name, date_period FROM reports WHERE id = ?', (report_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        await query.edit_message_text("❌ Отчёт не найден.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))
        return
    file_name, date_period = row

    prev_id = get_previous_report_id(report_id)
    prev_metrics = get_report_metrics(prev_id) if prev_id else None

    context.user_data['current_report_id'] = report_id
    context.user_data['current_period'] = date_period

    msg = f"📊 **Статистика отчёта**\n\n"
    msg += f"📄 **{file_name}**\n"
    msg += f"📅 Период: {date_period}\n\n"

    avg_acquiring = metrics.get('avg_acquiring', 0)
    median_acquiring = metrics.get('median_acquiring', 0)
    wb_total = metrics.get('wb_total', 0)
    wb_carp = metrics.get('wb_carp', 0)
    wb_hara = metrics.get('wb_hara', 0)
    carp_orders = metrics.get('carp_orders', 0)
    hara_orders = metrics.get('hara_orders', 0)
    carp_vyk_orders = metrics.get('carp_vyk_orders', 0)
    hara_vyk_orders = metrics.get('hara_vyk_orders', 0)
    k_carp = metrics.get('k_vyvodu_carp', 0)
    k_hara = metrics.get('k_vyvodu_hara', 0)
    k_total = metrics.get('k_vyvodu_total', 0)
    reklama_carp = metrics.get('reklama_carp', 0)
    reklama_hara = metrics.get('reklama_hara', 0)
    shtrafy = metrics.get('shtrafy', 0)
    nalog = metrics.get('nalog', 0)
    profit = metrics.get('total_profit', 0)
    margin = metrics.get('margin', 0)
    profit_carp = metrics.get('profit_carp', 0)
    profit_hara = metrics.get('profit_hara', 0)
    margin_carp = metrics.get('margin_carp', 0)
    margin_hara = metrics.get('margin_hara', 0)

    tax_hara = wb_hara * 0.01
    k_hara_after_tax = k_hara - tax_hara

    def fmt_change(current, previous, unit='₽', is_percent=False):
        if previous is None:
            return ""
        if is_percent:
            diff_pp = current - previous
            diff_percent = (diff_pp / previous * 100) if previous != 0 else 0
            return f"(было {previous:.2f}%, {diff_pp:+.2f} п.п., {diff_percent:+.1f}%)"
        else:
            diff_abs = current - previous
            diff_percent = (diff_abs / previous * 100) if previous != 0 else 0
            return f"(было {previous:,.2f} {unit}, {diff_abs:+.2f} {unit}, {diff_percent:+.1f}%)"

    msg += f"💳 **Средний эквайринг:** {avg_acquiring:.2f}%"
    if prev_metrics:
        prev_avg = prev_metrics.get('avg_acquiring', 0)
        msg += " " + fmt_change(avg_acquiring, prev_avg, is_percent=True)
    msg += "\n"
    msg += f"📊 **Медианный эквайринг:** {median_acquiring:.2f}%"
    if prev_metrics:
        prev_med = prev_metrics.get('median_acquiring', 0)
        msg += " " + fmt_change(median_acquiring, prev_med, is_percent=True)
    msg += "\n\n"

    msg += f"💰 **ВБшный оборот общий:** {wb_total:,.2f} ₽"
    if prev_metrics:
        prev_wb = prev_metrics.get('wb_total', 0)
        msg += " " + fmt_change(wb_total, prev_wb, '₽')
    msg += "\n"
    msg += f"   🐱 ЦАП: {wb_carp:,.2f} ₽"
    if prev_metrics:
        prev_carp = prev_metrics.get('wb_carp', 0)
        msg += " " + fmt_change(wb_carp, prev_carp, '₽')
    msg += "\n"
    msg += f"   ⚔️ Харакири: {wb_hara:,.2f} ₽"
    if prev_metrics:
        prev_hara = prev_metrics.get('wb_hara', 0)
        msg += " " + fmt_change(wb_hara, prev_hara, '₽')
    msg += "\n\n"

    msg += f"📦 **Заказы (осн):** ЦАП {carp_orders:.0f} шт."
    if prev_metrics:
        prev_carp_ord = prev_metrics.get('carp_orders', 0)
        diff = carp_orders - prev_carp_ord
        diff_percent = (diff / prev_carp_ord * 100) if prev_carp_ord != 0 else 0
        msg += f" (было {prev_carp_ord:.0f} шт., {diff:+.0f} шт., {diff_percent:+.1f}%)"
    msg += f", Харакири {hara_orders:.0f} шт."
    if prev_metrics:
        prev_hara_ord = prev_metrics.get('hara_orders', 0)
        diff = hara_orders - prev_hara_ord
        diff_percent = (diff / prev_hara_ord * 100) if prev_hara_ord != 0 else 0
        msg += f" (было {prev_hara_ord:.0f} шт., {diff:+.0f} шт., {diff_percent:+.1f}%)"
    msg += "\n"
    msg += f"📦 **Заказы (вык):** ЦАП {carp_vyk_orders:.0f} шт."
    if prev_metrics:
        prev_carp_vyk = prev_metrics.get('carp_vyk_orders', 0)
        diff = carp_vyk_orders - prev_carp_vyk
        diff_percent = (diff / prev_carp_vyk * 100) if prev_carp_vyk != 0 else 0
        msg += f" (было {prev_carp_vyk:.0f} шт., {diff:+.0f} шт., {diff_percent:+.1f}%)"
    msg += f", Харакири {hara_vyk_orders:.0f} шт."
    if prev_metrics:
        prev_hara_vyk = prev_metrics.get('hara_vyk_orders', 0)
        diff = hara_vyk_orders - prev_hara_vyk
        diff_percent = (diff / prev_hara_vyk * 100) if prev_hara_vyk != 0 else 0
        msg += f" (было {prev_hara_vyk:.0f} шт., {diff:+.0f} шт., {diff_percent:+.1f}%)"
    msg += "\n\n"

    msg += f"💸 **К выводу ЦАП:** {k_carp:,.2f} ₽"
    if prev_metrics:
        prev_k_carp = prev_metrics.get('k_vyvodu_carp', 0)
        msg += " " + fmt_change(k_carp, prev_k_carp, '₽')
    msg += "\n"
    msg += f"💸 **К выводу Харакири:** {k_hara:,.2f} ₽"
    if prev_metrics:
        prev_k_hara = prev_metrics.get('k_vyvodu_hara', 0)
        msg += " " + fmt_change(k_hara, prev_k_hara, '₽')
    msg += "\n"
    msg += f"💸 **Итого к выводу:** {k_total:,.2f} ₽"
    if prev_metrics:
        prev_k_total = prev_metrics.get('k_vyvodu_total', 0)
        msg += " " + fmt_change(k_total, prev_k_total, '₽')
    msg += "\n"
    msg += f"💸 **Харакири (с вычетом налога):** {k_hara_after_tax:,.2f} ₽"
    if prev_metrics:
        prev_k_hara_after = prev_metrics.get('k_vyvodu_hara', 0) - (prev_metrics.get('wb_hara', 0) * 0.01)
        msg += " " + fmt_change(k_hara_after_tax, prev_k_hara_after, '₽')
    msg += "\n\n"

    msg += f"📢 **Реклама:** ЦАП {reklama_carp:,.2f} ₽"
    if prev_metrics:
        prev_reklama_carp = prev_metrics.get('reklama_carp', 0)
        msg += " " + fmt_change(reklama_carp, prev_reklama_carp, '₽')
    msg += f", Харакири {reklama_hara:,.2f} ₽"
    if prev_metrics:
        prev_reklama_hara = prev_metrics.get('reklama_hara', 0)
        msg += " " + fmt_change(reklama_hara, prev_reklama_hara, '₽')
    msg += "\n"

    msg += f"⚠️ **Штрафы:** {shtrafy:,.2f} ₽"
    if prev_metrics:
        prev_shtrafy = prev_metrics.get('shtrafy', 0)
        msg += " " + fmt_change(shtrafy, prev_shtrafy, '₽')
    msg += "\n"

    msg += f"🧾 **Налог общий:** {nalog:,.2f} ₽"
    if prev_metrics:
        prev_nalog = prev_metrics.get('nalog', 0)
        msg += " " + fmt_change(nalog, prev_nalog, '₽')
    msg += "\n"

    msg += f"\n💰 **Чистая прибыль:** {profit:,.2f} ₽"
    if prev_metrics:
        prev_profit = prev_metrics.get('total_profit', 0)
        msg += " " + fmt_change(profit, prev_profit, '₽')
    msg += f"\n📈 **Маржинальность:** {margin:.2f} %"
    if prev_metrics:
        prev_margin = prev_metrics.get('margin', 0)
        msg += " " + fmt_change(margin, prev_margin, is_percent=True)
    msg += f"\n   🐱 ЦАП прибыль: {profit_carp:,.2f} ₽, марж. {margin_carp:.2f}%"
    if prev_metrics:
        prev_profit_carp = prev_metrics.get('profit_carp', 0)
        prev_margin_carp = prev_metrics.get('margin_carp', 0)
        msg += " " + fmt_change(profit_carp, prev_profit_carp, '₽')
        msg += f", марж. {fmt_change(margin_carp, prev_margin_carp, is_percent=True)}"
    msg += f"\n   ⚔️ Харакири прибыль: {profit_hara:,.2f} ₽, марж. {margin_hara:.2f}%"
    if prev_metrics:
        prev_profit_hara = prev_metrics.get('profit_hara', 0)
        prev_margin_hara = prev_metrics.get('margin_hara', 0)
        msg += " " + fmt_change(profit_hara, prev_profit_hara, '₽')
        msg += f", марж. {fmt_change(margin_hara, prev_margin_hara, is_percent=True)}"
    msg += "\n"

    await query.message.reply_text(msg, parse_mode='Markdown')

    # Восстановление шаблона
    template_path = Path("/app/шаблон.xlsx")
    if not template_path.exists():
        for p in [Path("шаблон.xlsx"), TEMP_DIR / "template.xlsx"]:
            if p.exists():
                template_path = p
                break
    if not template_path.exists():
        wb = openpyxl.Workbook()
        template_path = TEMP_DIR / "template.xlsx"
        wb.save(template_path)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_file = TEMP_DIR / f"шаблон_{timestamp}.xlsx"
    shutil.copy(template_path, out_file)

    wb = openpyxl.load_workbook(out_file, data_only=False, keep_links=False, keep_vba=False)
    ws = wb.active
    for cell, val in values.items():
        ws[cell] = val
        if isinstance(val, float) and val != int(val):
            ws[cell].number_format = '0.00'
    ws.sheet_view.calcMode = 'manual'
    wb.save(out_file)

    with open(out_file, 'rb') as f:
        await query.message.reply_document(f, caption="✅ Шаблон восстановлен")

    articles = get_article_stats_for_report(report_id)
    if articles:
        context.user_data['articles_data'] = articles
        keyboard = [
            [InlineKeyboardButton("📦 Детали по артикулам", callback_data="show_articles")],
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]
        await query.message.reply_text("Выберите действие:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.message.reply_text("Выберите действие:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))

    try:
        os.remove(out_file)
    except:
        pass

    try:
        await query.delete_message()
    except:
        pass

# ===== АРТИКУЛЫ (только для текущего отчёта) =====
async def articles_full_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback=False):
    if not await check_access(update):
        return
    report_id = context.user_data.get('current_report_id')
    if not report_id:
        text = "❌ Нет активного отчёта.\n\nПожалуйста, загрузите новый отчёт или выберите существующий из архива."
        keyboard = [
            [InlineKeyboardButton("📂 Перейти в архив", callback_data="menu_history")],
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]
        if is_callback:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    current_articles = get_article_stats_for_report(report_id)
    if not current_articles:
        text = "❌ Нет данных по артикулам для этого отчёта."
        if is_callback:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
            ]))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
            ]))
        return

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT start_date FROM reports WHERE id = ?", (report_id,))
    row = cursor.fetchone()
    prev_start_date = row[0] if row else None
    conn.close()

    previous_articles = {}
    if prev_start_date:
        prev_reports = get_previous_reports(prev_start_date, limit=1)
        if prev_reports:
            prev_id = prev_reports[0][0]
            previous_articles = get_article_stats_for_report(prev_id)

    all_items = []
    for art, data in current_articles.items():
        cur_q = data['quantity']
        cur_r = data['revenue']
        prev_q = previous_articles.get(art, {}).get('quantity', 0)
        prev_r = previous_articles.get(art, {}).get('revenue', 0)
        change_q = cur_q - prev_q
        change_r_percent = ((cur_r - prev_r) / prev_r * 100) if prev_r else 0 if cur_q == 0 else float('inf')
        all_items.append((art, cur_q, cur_r, change_q, change_r_percent, prev_q, prev_r))

    all_items.sort(key=lambda x: x[2], reverse=True)
    period = context.user_data.get('current_period', '')

    msg = f"📦 **Все артикулы** ({period})\n\n"
    for art, cur_q, cur_r, change_q, change_r_percent, prev_q, prev_r in all_items:
        if prev_q == 0 and cur_q == 0:
            delta_str = "нет данных"
        elif prev_q == 0:
            delta_str = f"🆕 +{cur_q} шт."
        else:
            arrow = "📈" if change_q > 0 else "📉" if change_q < 0 else "➖"
            delta_str = f"{arrow} {change_q:+.0f} шт. ({change_r_percent:+.1f}%)"
        msg += f"**{art}**\n   Продажи: {cur_q:,.0f} шт. | {cur_r:,.2f} ₽\n   Изм.: {delta_str}\n\n"
        if len(msg) > 4000:
            msg += "\n… (сообщение обрезано)"
            break

    keyboard = [
        [InlineKeyboardButton("📈 Топ-10 по росту", callback_data="growth")],
        [InlineKeyboardButton("📉 Топ-10 по падению", callback_data="decline")],
        [InlineKeyboardButton("📊 Детальное сравнение", callback_data="compare_articles")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if is_callback:
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')

async def articles_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    report_id = context.user_data.get('current_report_id')
    if not report_id:
        await query.edit_message_text("❌ Нет данных по артикулам для текущего отчета.")
        return

    current_articles = get_article_stats_for_report(report_id)
    if not current_articles:
        await query.edit_message_text("❌ Нет данных по артикулам.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT start_date FROM reports WHERE id = ?", (report_id,))
    row = cursor.fetchone()
    prev_start_date = row[0] if row else None
    conn.close()

    previous_articles = {}
    if prev_start_date:
        prev_reports = get_previous_reports(prev_start_date, limit=1)
        if prev_reports:
            prev_id = prev_reports[0][0]
            previous_articles = get_article_stats_for_report(prev_id)

    all_items = []
    for art, data in current_articles.items():
        cur_q = data['quantity']
        cur_r = data['revenue']
        prev_q = previous_articles.get(art, {}).get('quantity', 0)
        prev_r = previous_articles.get(art, {}).get('revenue', 0)
        change_q = cur_q - prev_q
        change_r_percent = ((cur_r - prev_r) / prev_r * 100) if prev_r else 0 if cur_q == 0 else float('inf')
        all_items.append((art, cur_q, cur_r, change_q, change_r_percent, prev_q, prev_r))

    all_items.sort(key=lambda x: x[2], reverse=True)
    top = all_items[:10]
    period = context.user_data.get('current_period', '')

    msg = f"📦 **Топ-10 артикулов** ({period})\n\n"
    for art, cur_q, cur_r, change_q, change_r_percent, prev_q, prev_r in top:
        if prev_q == 0 and cur_q == 0:
            delta_str = "нет данных"
        elif prev_q == 0:
            delta_str = f"🆕 +{cur_q} шт."
        else:
            arrow = "📈" if change_q > 0 else "📉" if change_q < 0 else "➖"
            delta_str = f"{arrow} {change_q:+.0f} шт. ({change_r_percent:+.1f}%)"
        msg += f"**{art}**\n   Продажи: {cur_q:,.0f} шт. | {cur_r:,.2f} ₽\n   Изм.: {delta_str}\n\n"

    if len(all_items) > 10:
        msg += f"… и еще {len(all_items)-10}. Используйте /articles для полного списка."

    keyboard = [
        [InlineKeyboardButton("📈 Топ-10 по росту", callback_data="growth")],
        [InlineKeyboardButton("📉 Топ-10 по падению", callback_data="decline")],
        [InlineKeyboardButton("📊 Детальное сравнение", callback_data="compare_articles")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ===== ОБРАБОТЧИКИ РОСТА И ПАДЕНИЯ =====
async def growth_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    await _show_sorted_articles(update, context, reverse=True)

async def decline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    await _show_sorted_articles(update, context, reverse=False)

async def _show_sorted_articles(update, context, reverse=True):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    report_id = context.user_data.get('current_report_id')
    if not report_id:
        await query.edit_message_text("❌ Нет данных для текущего отчета.")
        return

    current_articles = get_article_stats_for_report(report_id)
    if not current_articles:
        await query.edit_message_text("❌ Нет данных по артикулам.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT start_date FROM reports WHERE id = ?", (report_id,))
    row = cursor.fetchone()
    prev_start_date = row[0] if row else None
    conn.close()

    previous_articles = {}
    if prev_start_date:
        prev_reports = get_previous_reports(prev_start_date, limit=1)
        if prev_reports:
            prev_id = prev_reports[0][0]
            previous_articles = get_article_stats_for_report(prev_id)

    items = []
    for art, data in current_articles.items():
        cur_q = data['quantity']
        cur_r = data['revenue']
        prev_q = previous_articles.get(art, {}).get('quantity', 0)
        prev_r = previous_articles.get(art, {}).get('revenue', 0)
        if prev_q == 0 and cur_q == 0:
            continue
        change_q = cur_q - prev_q
        change_r_percent = ((cur_r - prev_r) / prev_r * 100) if prev_r else 0 if cur_q == 0 else float('inf')
        items.append((art, cur_q, cur_r, change_q, change_r_percent, prev_q, prev_r))

    items.sort(key=lambda x: x[4], reverse=reverse)
    top = items[:10]
    period = context.user_data.get('current_period', '')

    label = "росту" if reverse else "падению"
    msg = f"📈 **Топ-10 по {label}** ({period})\n\n"
    for art, cur_q, cur_r, change_q, change_r_percent, prev_q, prev_r in top:
        if prev_q == 0:
            delta_str = f"🆕 +{cur_q} шт."
        else:
            arrow = "📈" if change_q > 0 else "📉" if change_q < 0 else "➖"
            delta_str = f"{arrow} {change_q:+.0f} шт. ({change_r_percent:+.1f}%)"
        msg += f"**{art}**\n   Продажи: {cur_q:,.0f} шт. | {cur_r:,.2f} ₽\n   Изм.: {delta_str}\n\n"

    if not top:
        msg = "Нет данных для отображения."

    keyboard = [
        [InlineKeyboardButton("◀️ Назад к списку", callback_data="show_articles")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ===== ДЕТАЛЬНОЕ СРАВНЕНИЕ =====
async def compare_articles_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    report_id = context.user_data.get('current_report_id')
    if not report_id:
        await query.edit_message_text("❌ Нет данных для сравнения.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT start_date FROM reports WHERE id = ?", (report_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        await query.edit_message_text("❌ Ошибка: отчёт не найден.")
        return
    current_start = row[0]
    conn.close()

    prev_reports = get_previous_reports(current_start, limit=12)
    if not prev_reports:
        await query.edit_message_text("❌ Нет предыдущих отчетов для сравнения.")
        return

    prev_ids = [r[0] for r in prev_reports]
    current_articles = get_article_stats_for_report(report_id)
    if not current_articles:
        await query.edit_message_text("❌ Нет данных по артикулам в текущем отчете.")
        return

    periods = {
        '2 недели': prev_ids[:2],
        'месяц': prev_ids[:4],
        'квартал': prev_ids[:12]
    }

    msg = f"📊 **Сравнение со средними показателями**\n(период: {context.user_data.get('current_period', '')})\n\n"

    for period_name, ids in periods.items():
        if not ids:
            msg += f"**{period_name}:** Нет данных\n\n"
            continue
        all_articles = {}
        for pid in ids:
            arts = get_article_stats_for_report(pid)
            for art, data in arts.items():
                if art not in all_articles:
                    all_articles[art] = {'qty': [], 'rev': []}
                all_articles[art]['qty'].append(data['quantity'])
                all_articles[art]['rev'].append(data['revenue'])
        avg_articles = {}
        for art, vals in all_articles.items():
            avg_articles[art] = {
                'avg_quantity': sum(vals['qty']) / len(vals['qty']),
                'avg_revenue': sum(vals['rev']) / len(vals['rev'])
            }
        msg += f"**{period_name}** (среднее по {len(ids)} отчетам):\n"
        top_cur = sorted(current_articles.items(), key=lambda x: x[1]['revenue'], reverse=True)[:5]
        for art, data in top_cur:
            cur_q = data['quantity']
            cur_r = data['revenue']
            if art in avg_articles:
                avg_q = avg_articles[art]['avg_quantity']
                avg_r = avg_articles[art]['avg_revenue']
                change_q = ((cur_q - avg_q) / avg_q * 100) if avg_q else 0
                change_r = ((cur_r - avg_r) / avg_r * 100) if avg_r else 0
                msg += f"• {art}: {cur_q:,.0f} шт. (Δ {change_q:+.1f}%) | {cur_r:,.2f} ₽ (Δ {change_r:+.1f}%)\n"
            else:
                msg += f"• {art}: {cur_q:,.0f} шт. (новинка) | {cur_r:,.2f} ₽\n"
        msg += "\n"

    keyboard = [
        [InlineKeyboardButton("◀️ Назад к списку", callback_data="show_articles")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ===== ОБРАБОТЧИК "НАЗАД В МЕНЮ" =====
async def back_to_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🏠 Главное меню. Выберите действие:",
        reply_markup=get_main_menu()
    )

# ===== ОБРАБОТКА ФАЙЛОВ =====
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    try:
        doc = update.message.document
        if not doc.file_name.endswith(('.xlsx', '.xls')):
            await update.message.reply_text("❌ Нужен Excel файл")
            return

        file = await context.bot.get_file(doc.file_id)
        file_path = TEMP_DIR / doc.file_name
        await file.download_to_drive(file_path)

        report_type = detect_report_type(doc.file_name)
        if not report_type:
            context.user_data['current_file'] = str(file_path)
            context.user_data['current_file_hash'] = calculate_file_hash(file_path)
            await update.message.reply_text("❓ Тип не определен. Используйте /osn или /vyk")
            return

        if 'files' not in context.user_data:
            context.user_data['files'] = {}
        context.user_data['files'][report_type] = str(file_path)
        if report_type == 'osn':
            context.user_data['osn_hash'] = calculate_file_hash(file_path)
            await update.message.reply_text("✅ Основной отчет получен")
        else:
            context.user_data['vyk_hash'] = calculate_file_hash(file_path)
            await update.message.reply_text("✅ Отчет по выкупам получен")

        if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
            await process_and_send(update, context)

    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

# ===== РУЧНЫЕ КОМАНДЫ osn/vyk =====
async def handle_osn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправьте файл!")
        return
    context.user_data['files']['osn'] = context.user_data['current_file']
    context.user_data['osn_hash'] = context.user_data['current_file_hash']
    await update.message.reply_text("✅ Основной отчет сохранен")
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)

async def handle_vyk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправьте файл!")
        return
    context.user_data['files']['vyk'] = context.user_data['current_file']
    context.user_data['vyk_hash'] = context.user_data['current_file_hash']
    await update.message.reply_text("✅ Отчет по выкупам сохранен")
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)

# ===== ОСНОВНАЯ ОБРАБОТКА =====
async def process_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    try:
        await update.message.reply_text("⏳ Обработка...")

        osn_file = context.user_data['files']['osn']
        vyk_file = context.user_data['files']['vyk']
        osn_hash = context.user_data.get('osn_hash')

        template_path = Path("/app/шаблон.xlsx")
        if not template_path.exists():
            for p in [Path("шаблон.xlsx"), TEMP_DIR / "template.xlsx"]:
                if p.exists():
                    template_path = p
                    break
        if not template_path.exists():
            wb = openpyxl.Workbook()
            template_path = TEMP_DIR / "template.xlsx"
            wb.save(template_path)

        wb_coeff = openpyxl.load_workbook(template_path, data_only=True)
        ws_coeff = wb_coeff.active
        b23_val = ws_coeff['B23'].value
        c23_val = ws_coeff['C23'].value
        wb_coeff.close()

        try:
            b23 = float(b23_val) if b23_val is not None and isinstance(b23_val, (int, float)) else 0.0
        except:
            b23 = 0.0
        try:
            c23 = float(c23_val) if c23_val is not None and isinstance(c23_val, (int, float)) else 0.0
        except:
            c23 = 0.0

        logger.info(f"Коэффициенты: B23={b23}, C23={c23}")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out_file = TEMP_DIR / f"шаблон_{timestamp}.xlsx"
        shutil.copy(template_path, out_file)

        processor = ReportProcessor()
        values, articles, date_period = processor.process_files(osn_file, vyk_file, str(out_file))

        for k in values:
            try:
                values[k] = float(values[k])
            except:
                values[k] = 0.0

        start_date, end_date = parse_date_from_period(date_period)
        if not start_date:
            start_date = end_date = datetime.now().strftime("%Y-%m-%d")

        def f(key):
            return values.get(key, 0.0)

        b4, b5, b7, b9, b10, b11 = f('B4'), f('B5'), f('B7'), f('B9'), f('B10'), f('B11')
        b26, b29, b32, b44, b47, b41 = f('B26'), f('B29'), f('B32'), f('B44'), f('B47'), f('B41')
        f4, f5, f7, f9, f10, f11 = f('F4'), f('F5'), f('F7'), f('F9'), f('F10'), f('F11')
        m4, m5, m7, m8, m9 = f('M4'), f('M5'), f('M7'), f('M8'), f('M9')
        q4, q5, q7, q8, q9 = f('Q4'), f('Q5'), f('Q7'), f('Q8'), f('Q9')

        b6 = b4 - b5
        f6 = f4 - f5
        m6 = m4 - m5
        q6 = q4 - q5
        b8 = b26 * b23
        f8 = b26 * c23
        b12 = b29 * b23
        f12 = b29 * c23

        b13 = b6 - b7 - b8 - b9 - b10 - b11 - b12
        f13 = f6 - f7 - f8 - f9 - f10 - f11 - f12
        m10 = m6 - m7 - m8 - m9
        q10 = q6 - q7 - q8 - q9

        b35 = (b32 + b41) * 0.01
        b50 = (b44 + b47) * 0.01

        wb_total = b44 + b47 + b32 + b41
        wb_carp = b44 + b47
        wb_hara = b32 + b41
        k_carp = b13 + m10
        k_hara = f13 + q10
        reklama_carp = b11
        reklama_hara = f11
        shtrafy = b10 + f10
        nalog = b35 + b50

        carp_orders = sum(a.get('quantity', 0) for a in articles.get('Цап царапкин', {}).get('sales', {}).values())
        hara_orders = sum(a.get('quantity', 0) for a in articles.get('Harakiri', {}).get('sales', {}).values())
        carp_vyk_orders = sum(a.get('quantity', 0) for a in articles.get('Цап царапкин', {}).get('vyk', {}).values())
        hara_vyk_orders = sum(a.get('quantity', 0) for a in articles.get('Harakiri', {}).get('vyk', {}).values())

        # ===== РАСЧЁТ ПРИБЫЛИ =====
        total_profit = 0
        total_revenue = 0
        profit_by_brand = {'Цап царапкин': 0, 'Harakiri': 0}
        revenue_by_brand = {'Цап царапкин': 0, 'Harakiri': 0}
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        for brand, data in articles.items():
            for art, stats in data.get('sales', {}).items():
                qty = stats.get('quantity', 0)
                rev = stats.get('revenue', 0)
                cost = get_active_cost(art, end_date)
                if cost is None:
                    cost = 0
                profit = rev - (cost * qty)
                total_profit += profit
                total_revenue += rev
                if brand in profit_by_brand:
                    profit_by_brand[brand] += profit
                    revenue_by_brand[brand] += rev
        conn.close()
        total_margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0
        margin_carp = (profit_by_brand['Цап царапкин'] / revenue_by_brand['Цап царапкин'] * 100) if revenue_by_brand['Цап царапкин'] > 0 else 0
        margin_hara = (profit_by_brand['Harakiri'] / revenue_by_brand['Harakiri'] * 100) if revenue_by_brand['Harakiri'] > 0 else 0

        logger.info(f"💾 Метрики для сохранения: total_profit={total_profit}, margin={total_margin}")

        metrics = {
            'avg_acquiring': values.get('B56', 0),
            'median_acquiring': values.get('B59', 0),
            'min_acquiring': values.get('B62', 0),
            'max_acquiring': values.get('B65', 0),
            'wb_total': wb_total,
            'wb_carp': wb_carp,
            'wb_hara': wb_hara,
            'k_vyvodu_carp': k_carp,
            'k_vyvodu_hara': k_hara,
            'k_vyvodu_total': k_carp + k_hara,
            'reklama_carp': reklama_carp,
            'reklama_hara': reklama_hara,
            'shtrafy': shtrafy,
            'nalog': nalog,
            'carp_orders': carp_orders,
            'hara_orders': hara_orders,
            'carp_vyk_orders': carp_vyk_orders,
            'hara_vyk_orders': hara_vyk_orders,
            'total_profit': total_profit,
            'margin': total_margin,
            'profit_carp': profit_by_brand['Цап царапкин'],
            'profit_hara': profit_by_brand['Harakiri'],
            'margin_carp': margin_carp,
            'margin_hara': margin_hara
        }

        if osn_hash is None:
            osn_hash = calculate_file_hash(Path(osn_file))

        existing_id = get_report_id_by_period(start_date, end_date)
        if existing_id:
            delete_report(existing_id)
            logger.info(f"🗑️ Удалён старый отчёт за период {start_date} — {end_date} (ID {existing_id})")

        logger.info(f"📤 Передаём в БД: total_profit={total_profit}, margin={total_margin}")
        saved, report_id = save_report_to_db(
            file_name=Path(osn_file).name,
            file_hash=osn_hash,
            date_period=date_period,
            start_date=start_date,
            end_date=end_date,
            values=values,
            metrics=metrics,
            articles=articles
        )
        logger.info(f"✅ Отчёт {report_id} сохранён, метрики записаны")

        with open(out_file, 'rb') as f:
            await update.message.reply_document(f, caption="✅ Готово!")

        context.user_data['articles_data'] = articles
        context.user_data['current_period'] = date_period
        context.user_data['current_report_id'] = report_id

        prev_id = get_previous_report_id(report_id)
        prev_metrics = get_report_metrics(prev_id) if prev_id else None

        def fmt_change(current, previous, unit='₽', is_percent=False):
            if previous is None:
                return ""
            if is_percent:
                diff_pp = current - previous
                diff_percent = (diff_pp / previous * 100) if previous != 0 else 0
                return f"(было {previous:.2f}%, {diff_pp:+.2f} п.п., {diff_percent:+.1f}%)"
            else:
                diff_abs = current - previous
                diff_percent = (diff_abs / previous * 100) if previous != 0 else 0
                return f"(было {previous:,.2f} {unit}, {diff_abs:+.2f} {unit}, {diff_percent:+.1f}%)"

        msg = "📊 **Статистика обработки:**\n\n"
        msg += "• Основной отчет: ЦАП + HARAKIRI ✅\n"
        msg += "• По выкупам: ЦАП + HARAKIRI ✅\n\n"

        avg_acquiring = values.get('B56', 0)
        msg += f"💳 **Средний эквайринг:** {avg_acquiring:.2f} %"
        if prev_metrics:
            prev_avg = prev_metrics.get('avg_acquiring', 0)
            msg += " " + fmt_change(avg_acquiring, prev_avg, is_percent=True)
        msg += "\n"
        median_acquiring = values.get('B59', 0)
        msg += f"📊 **Медианный эквайринг:** {median_acquiring:.2f} %"
        if prev_metrics:
            prev_med = prev_metrics.get('median_acquiring', 0)
            msg += " " + fmt_change(median_acquiring, prev_med, is_percent=True)
        msg += "\n\n"

        msg += f"💰 **ВБшный оборот общий:** {wb_total:,.2f} ₽"
        if prev_metrics:
            prev_wb = prev_metrics.get('wb_total', 0)
            msg += " " + fmt_change(wb_total, prev_wb, '₽')
        msg += "\n"
        msg += f"   🐱 ЦАП: {wb_carp:,.2f} ₽"
        if prev_metrics:
            prev_carp = prev_metrics.get('wb_carp', 0)
            msg += " " + fmt_change(wb_carp, prev_carp, '₽')
        msg += "\n"
        msg += f"   ⚔️ Харакири: {wb_hara:,.2f} ₽"
        if prev_metrics:
            prev_hara = prev_metrics.get('wb_hara', 0)
            msg += " " + fmt_change(wb_hara, prev_hara, '₽')
        msg += "\n\n"

        msg += f"📦 **Заказы (осн):** ЦАП {carp_orders:.0f} шт."
        if prev_metrics:
            prev_carp_ord = prev_metrics.get('carp_orders', 0)
            diff = carp_orders - prev_carp_ord
            diff_percent = (diff / prev_carp_ord * 100) if prev_carp_ord != 0 else 0
            msg += f" (было {prev_carp_ord:.0f} шт., {diff:+.0f} шт., {diff_percent:+.1f}%)"
        msg += f", Харакири {hara_orders:.0f} шт."
        if prev_metrics:
            prev_hara_ord = prev_metrics.get('hara_orders', 0)
            diff = hara_orders - prev_hara_ord
            diff_percent = (diff / prev_hara_ord * 100) if prev_hara_ord != 0 else 0
            msg += f" (было {prev_hara_ord:.0f} шт., {diff:+.0f} шт., {diff_percent:+.1f}%)"
        msg += "\n"
        msg += f"📦 **Заказы (вык):** ЦАП {carp_vyk_orders:.0f} шт."
        if prev_metrics:
            prev_carp_vyk = prev_metrics.get('carp_vyk_orders', 0)
            diff = carp_vyk_orders - prev_carp_vyk
            diff_percent = (diff / prev_carp_vyk * 100) if prev_carp_vyk != 0 else 0
            msg += f" (было {prev_carp_vyk:.0f} шт., {diff:+.0f} шт., {diff_percent:+.1f}%)"
        msg += f", Харакири {hara_vyk_orders:.0f} шт."
        if prev_metrics:
            prev_hara_vyk = prev_metrics.get('hara_vyk_orders', 0)
            diff = hara_vyk_orders - prev_hara_vyk
            diff_percent = (diff / prev_hara_vyk * 100) if prev_hara_vyk != 0 else 0
            msg += f" (было {prev_hara_vyk:.0f} шт., {diff:+.0f} шт., {diff_percent:+.1f}%)"
        msg += "\n\n"

        tax_hara = wb_hara * 0.01
        k_hara_after_tax = k_hara - tax_hara

        msg += f"💸 **К выводу ЦАП:** {k_carp:,.2f} ₽"
        if prev_metrics:
            prev_k_carp = prev_metrics.get('k_vyvodu_carp', 0)
            msg += " " + fmt_change(k_carp, prev_k_carp, '₽')
        msg += "\n"
        msg += f"💸 **К выводу Харакири:** {k_hara:,.2f} ₽"
        if prev_metrics:
            prev_k_hara = prev_metrics.get('k_vyvodu_hara', 0)
            msg += " " + fmt_change(k_hara, prev_k_hara, '₽')
        msg += "\n"
        msg += f"💸 **Итого к выводу:** {k_carp + k_hara:,.2f} ₽"
        if prev_metrics:
            prev_k_total = prev_metrics.get('k_vyvodu_total', 0)
            msg += " " + fmt_change(k_carp + k_hara, prev_k_total, '₽')
        msg += "\n"
        msg += f"💸 **Харакири (с вычетом налога):** {k_hara_after_tax:,.2f} ₽"
        if prev_metrics:
            prev_k_hara_after = prev_metrics.get('k_vyvodu_hara', 0) - (prev_metrics.get('wb_hara', 0) * 0.01)
            msg += " " + fmt_change(k_hara_after_tax, prev_k_hara_after, '₽')
        msg += "\n\n"

        msg += f"📢 **Реклама:** ЦАП {reklama_carp:,.2f} ₽"
        if prev_metrics:
            prev_reklama_carp = prev_metrics.get('reklama_carp', 0)
            msg += " " + fmt_change(reklama_carp, prev_reklama_carp, '₽')
        msg += f", Харакири {reklama_hara:,.2f} ₽"
        if prev_metrics:
            prev_reklama_hara = prev_metrics.get('reklama_hara', 0)
            msg += " " + fmt_change(reklama_hara, prev_reklama_hara, '₽')
        msg += "\n"

        msg += f"⚠️ **Штрафы:** {shtrafy:,.2f} ₽"
        if prev_metrics:
            prev_shtrafy = prev_metrics.get('shtrafy', 0)
            msg += " " + fmt_change(shtrafy, prev_shtrafy, '₽')
        msg += "\n"

        msg += f"🧾 **Налог общий:** {nalog:,.2f} ₽"
        if prev_metrics:
            prev_nalog = prev_metrics.get('nalog', 0)
            msg += " " + fmt_change(nalog, prev_nalog, '₽')
        msg += "\n"

        msg += f"\n💰 **Чистая прибыль:** {total_profit:,.2f} ₽"
        if prev_metrics:
            prev_profit = prev_metrics.get('total_profit', 0)
            msg += " " + fmt_change(total_profit, prev_profit, '₽')
        msg += f"\n📈 **Маржинальность:** {total_margin:.2f} %"
        if prev_metrics:
            prev_margin = prev_metrics.get('margin', 0)
            msg += " " + fmt_change(total_margin, prev_margin, is_percent=True)
        msg += f"\n   🐱 ЦАП прибыль: {profit_by_brand['Цап царапкин']:,.2f} ₽, марж. {margin_carp:.2f}%"
        if prev_metrics:
            prev_profit_carp = prev_metrics.get('profit_carp', 0)
            prev_margin_carp = prev_metrics.get('margin_carp', 0)
            msg += " " + fmt_change(profit_by_brand['Цап царапкин'], prev_profit_carp, '₽')
            msg += f", марж. {fmt_change(margin_carp, prev_margin_carp, is_percent=True)}"
        msg += f"\n   ⚔️ Харакири прибыль: {profit_by_brand['Harakiri']:,.2f} ₽, марж. {margin_hara:.2f}%"
        if prev_metrics:
            prev_profit_hara = prev_metrics.get('profit_hara', 0)
            prev_margin_hara = prev_metrics.get('margin_hara', 0)
            msg += " " + fmt_change(profit_by_brand['Harakiri'], prev_profit_hara, '₽')
            msg += f", марж. {fmt_change(margin_hara, prev_margin_hara, is_percent=True)}"
        msg += "\n\n✅ Отчет сохранен"

        await update.message.reply_text(msg, parse_mode='Markdown')

        keyboard = [
            [InlineKeyboardButton("📦 Детали по артикулам", callback_data="show_articles")],
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]
        await update.message.reply_text("Выберите действие:", reply_markup=InlineKeyboardMarkup(keyboard))

        for f in [out_file, osn_file, vyk_file]:
            try:
                os.remove(f)
            except:
                pass
        context.user_data['files'] = {}
        context.user_data['current_file'] = None
        context.user_data['current_file_hash'] = None

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

# ===== ОБРАБОТЧИК ТЕКСТА =====
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    text = update.message.text
    if text.startswith('/'):
        return
    if context.user_data.get('waiting_for_cost'):
        await handle_cost_input(update, context)
        return
    await update.message.reply_text("Используйте кнопки меню или команды.", reply_markup=get_main_menu())
