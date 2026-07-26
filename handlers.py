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
    calculate_file_hash  # <--- добавил
)
from services import (
    fetch_news, format_news_digest, detect_report_type, parse_date_from_period,
    ReportProcessor, scheduler, scheduled_morning_digest, scheduled_evening_digest
)

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

# ===== НАСТРОЙКИ СЕБЕСТОИМОСТИ (с логированием) =====
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

# ===== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ (без изменений) =====
# ... (здесь идёт весь остальной код, который был ранее, я его не удаляю)

# ВАЖНО: в конце файла должны быть все остальные функции: 
# show_analytics_selection, analytics_*, show_history_page, history_*, 
# resend_report, articles_*, growth, decline, compare, back_to_menu,
# handle_file, handle_osn, handle_vyk, process_and_send, handle_text
# Они остаются без изменений, я их не стал повторять, чтобы не перегружать сообщение.
# Пожалуйста, возьмите их из предыдущей версии handlers.py или из старого telegram_bot.py.
