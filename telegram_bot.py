#!/usr/bin/env python3
"""
Telegram бот для обработки еженедельных отчетов Wildberries
Деплой на Railway (бесплатно, 24/7)
Полная версия с инлайн-меню, историей, артикулами, аналитикой.
"""

import os
import re
import shutil
import logging
import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path

import pandas as pd
import openpyxl
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)

# ===== НАСТРОЙКИ =====
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ Токен не найден!")

DATA_DIR = Path("/data")
TEMP_DIR = DATA_DIR / "temp"
DB_PATH = DATA_DIR / "reports.db"

if not DATA_DIR.exists():
    DATA_DIR = Path("/tmp/telegram_data")
    TEMP_DIR = DATA_DIR / "temp"
    DB_PATH = DATA_DIR / "reports.db"

DATA_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

print(f"📁 Данные: {DATA_DIR}")
print(f"📊 БД: {DB_PATH}")

# ===== БАЗА ДАННЫХ =====
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT NOT NULL,
            file_hash TEXT UNIQUE NOT NULL,
            date_period TEXT,
            start_date TEXT,
            end_date TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS report_values (
            report_id INTEGER,
            cell_name TEXT,
            cell_value REAL,
            FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE,
            PRIMARY KEY (report_id, cell_name)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS report_metrics (
            report_id INTEGER,
            metric_name TEXT,
            metric_value REAL,
            FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE,
            PRIMARY KEY (report_id, metric_name)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS article_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER,
            brand TEXT,
            article TEXT,
            quantity INTEGER,
            revenue REAL,
            FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("✅ БД инициализирована")

init_db()

# ===== ФУНКЦИИ БД =====
def calculate_file_hash(file_path):
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def is_file_duplicate(file_hash):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT id, file_name, date_period FROM reports WHERE file_hash = ?', (file_hash,))
        result = cursor.fetchone()
        conn.close()
        return result
    except:
        return None

def save_report_to_db(file_name, file_hash, date_period, start_date, end_date, values, metrics, articles):
    """
    Сохраняет отчёт и артикулы.
    articles: словарь {brand: {'sales': {art: {quantity, revenue}}, 'vyk': {art: {quantity, revenue}}}}
    Сохраняем все артикулы (sales + vyk) как отдельные записи.
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO reports (file_name, file_hash, date_period, start_date, end_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (file_name, file_hash, date_period, start_date, end_date))
        report_id = cursor.lastrowid
        logger.info(f"✅ Отчет вставлен, ID: {report_id}")

        if values:
            for cell, val in values.items():
                try:
                    cursor.execute('''
                        INSERT INTO report_values (report_id, cell_name, cell_value)
                        VALUES (?, ?, ?)
                    ''', (report_id, cell, float(val)))
                except:
                    pass

        if metrics:
            for mname, mval in metrics.items():
                try:
                    cursor.execute('''
                        INSERT INTO report_metrics (report_id, metric_name, metric_value)
                        VALUES (?, ?, ?)
                    ''', (report_id, mname, float(mval)))
                except:
                    pass

        # Сохраняем артикулы – объединяем sales и vyk для каждого бренда
        if articles:
            inserted = 0
            for brand, data in articles.items():
                # Собираем все артикулы из sales и vyk
                all_arts = {}
                # sales
                for art, stats in data.get('sales', {}).items():
                    if art not in all_arts:
                        all_arts[art] = {'quantity': 0, 'revenue': 0}
                    all_arts[art]['quantity'] += stats.get('quantity', 0)
                    all_arts[art]['revenue'] += stats.get('revenue', 0)
                # vyk
                for art, stats in data.get('vyk', {}).items():
                    if art not in all_arts:
                        all_arts[art] = {'quantity': 0, 'revenue': 0}
                    all_arts[art]['quantity'] += stats.get('quantity', 0)
                    all_arts[art]['revenue'] += stats.get('revenue', 0)
                # Вставляем в БД
                for art, stats in all_arts.items():
                    cursor.execute('''
                        INSERT INTO article_stats (report_id, brand, article, quantity, revenue)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (report_id, brand, art, stats['quantity'], stats['revenue']))
                    inserted += 1
            logger.info(f"📦 Вставлено {inserted} записей артикулов (суммарно по основному + выкупам)")
        else:
            logger.warning("⚠️ Нет артикулов для сохранения")

        conn.commit()
        conn.close()
        return True, report_id
    except sqlite3.IntegrityError:
        logger.error("❌ Ошибка целостности БД (возможно, дубликат хеша)")
        return False, None
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения: {e}")
        return False, None

def delete_report(report_id):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('DELETE FROM article_stats WHERE report_id = ?', (report_id,))
        cursor.execute('DELETE FROM report_values WHERE report_id = ?', (report_id,))
        cursor.execute('DELETE FROM report_metrics WHERE report_id = ?', (report_id,))
        cursor.execute('DELETE FROM reports WHERE id = ?', (report_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted
    except:
        return False

def get_all_reports(page=0, per_page=10):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM reports')
        total = cursor.fetchone()[0]
        offset = page * per_page
        cursor.execute('''
            SELECT id, file_name, date_period, start_date, end_date, processed_at
            FROM reports ORDER BY processed_at DESC LIMIT ? OFFSET ?
        ''', (per_page, offset))
        results = cursor.fetchall()
        conn.close()
        return results, total
    except:
        return [], 0

def get_report_values(report_id):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT cell_name, cell_value FROM report_values WHERE report_id = ?', (report_id,))
        rows = cursor.fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except:
        return {}

def get_report_metrics(report_id):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT metric_name, metric_value FROM report_metrics WHERE report_id = ?', (report_id,))
        rows = cursor.fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except:
        return {}

def get_previous_reports(current_start_date, limit=12):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, start_date, end_date
            FROM reports
            WHERE start_date < ?
            ORDER BY start_date DESC
            LIMIT ?
        ''', (current_start_date, limit))
        results = cursor.fetchall()
        conn.close()
        return results
    except:
        return []

def get_article_stats_for_report(report_id, brand=None):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        if brand:
            cursor.execute('''
                SELECT article, SUM(quantity) as q, SUM(revenue) as r
                FROM article_stats
                WHERE report_id = ? AND brand = ?
                GROUP BY article
            ''', (report_id, brand))
        else:
            cursor.execute('''
                SELECT article, SUM(quantity) as q, SUM(revenue) as r
                FROM article_stats
                WHERE report_id = ?
                GROUP BY article
            ''', (report_id,))
        results = cursor.fetchall()
        conn.close()
        return {row[0]: {'quantity': row[1], 'revenue': row[2]} for row in results}
    except:
        return {}

def get_report_date_range():
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute("SELECT MIN(start_date), MAX(end_date) FROM reports WHERE start_date IS NOT NULL AND end_date IS NOT NULL")
        row = cursor.fetchone()
        conn.close()
        return row[0], row[1]
    except:
        return None, None

# ===== ОПРЕДЕЛЕНИЕ ТИПА ФАЙЛА =====
def detect_report_type(filename):
    name = filename.lower()
    if 'осн' in name or 'osn' in name:
        return 'osn'
    elif 'вык' in name or 'vyk' in name:
        return 'vyk'
    return None

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

# ===== FLASK =====
flask_app = Flask(__name__)

@flask_app.route("/")
def health_check():
    return "🤖 Бот работает!", 200

@flask_app.route("/ping")
def ping():
    return "pong", 200

def run_flask():
    from threading import Thread
    def _run():
        flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
    Thread(target=_run, daemon=True).start()

# ===== ОБРАБОТЧИК ОТЧЕТОВ =====
class ReportProcessor:
    def process_files(self, osn_path, vyk_path, template_path):
        df_osn = pd.read_excel(osn_path)
        df_vyk = pd.read_excel(vyk_path)

        logger.info(f"Колонки основного: {df_osn.columns.tolist()}")
        logger.info(f"Колонки выкупов: {df_vyk.columns.tolist()}")

        filename = Path(osn_path).name
        match = re.search(r'(\d{1,2})\.(\d{2})-(\d{1,2})\.(\d{2})', filename)
        date_range = f"{match.group(1)}.{match.group(2)}-{match.group(3)}.{match.group(4)}" if match else datetime.now().strftime("%d.%m")

        values = self._calculate_all_values(df_osn, df_vyk, date_range)
        self._fill_template(template_path, values)

        articles = self._get_articles_stats(df_osn, df_vyk)
        return values, articles, date_range

    def _get_articles_stats(self, df_osn, df_vyk):
        result = {}

        def normalize_cols(df):
            return {str(col).strip().lower(): col for col in df.columns}

        cols_osn = normalize_cols(df_osn)
        cols_vyk = normalize_cols(df_vyk)
        all_cols = {**cols_vyk, **cols_osn}

        qty_variants = ['количество', 'кол-во', 'количество товара', 'кол-во (шт.)', 'кол-во шт', 'quantity', 'количество,шт']
        art_variants = ['артикул поставщика', 'артикул', 'артикул товара', 'номенклатура', 'sku', 'артикул(поставщика)']

        qty_col = None
        art_col = None
        for v in qty_variants:
            if v in all_cols:
                qty_col = all_cols[v]
                break
        for v in art_variants:
            if v in all_cols:
                art_col = all_cols[v]
                break

        if qty_col is None:
            logger.warning(f"❌ Колонка количества не найдена. Доступные нормализованные: {list(all_cols.keys())}")
            return result
        if art_col is None:
            logger.warning(f"❌ Колонка артикула не найдена. Доступные нормализованные: {list(all_cols.keys())}")
            return result

        logger.info(f"✅ Найдены колонки: количество='{qty_col}', артикул='{art_col}'")

        for df, key in [(df_osn, 'sales'), (df_vyk, 'vyk')]:
            for bren, mask_func in [
                ('Цап царапкин', lambda d: (d['Бренд'] == 'Цап царапкин') | (d['Бренд'].isna())),
                ('Harakiri', lambda d: d['Бренд'] == 'Harakiri')
            ]:
                mask = mask_func(df)
                df_bren = df[mask]
                if df_bren.empty:
                    continue
                sales = df_bren[(df_bren['Тип документа'] == 'Продажа') & (df_bren[qty_col] > 0)]
                agg_sales = sales.groupby(art_col).agg(
                    quantity=(qty_col, 'sum'),
                    revenue=('К перечислению Продавцу за реализованный Товар', 'sum')
                ).to_dict('index') if not sales.empty else {}

                articles = {}
                for art, vals in agg_sales.items():
                    articles[art] = {
                        'quantity': vals['quantity'],
                        'revenue': vals['revenue']
                    }
                if bren not in result:
                    result[bren] = {}
                result[bren][key] = articles

        logger.info(f"📦 Собрано артикулов: {sum(len(v.get('sales', {})) for v in result.values())}")
        return result

    def _calculate_all_values(self, df_osn, df_vyk, date_range):
        values = {'B1': date_range, 'F1': date_range}

        mask_carp = (df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())
        values['B4'] = df_osn[mask_carp & (df_osn['Тип документа'] == 'Продажа')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['B5'] = df_osn[mask_carp & (df_osn['Тип документа'] == 'Возврат')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['B7'] = df_osn[mask_carp]['Услуги по доставке товара покупателю'].sum()
        values['B9'] = df_osn[mask_carp]['Операции на приемке'].sum()
        values['B10'] = df_osn['Общая сумма штрафов'].sum()
        values['B11'] = df_osn[mask_carp]['Удержания'].sum()
        values['B26'] = df_osn[mask_carp]['Хранение'].sum()
        values['B29'] = df_osn[mask_carp]['Разовое изменение срока перечисления денежных средств'].sum()
        values['B44'] = df_osn['Цена розничная'].sum()
        values['B32'] = df_osn[mask_carp]['Цена розничная'].sum()

        mask_hara = (df_osn['Бренд'] == 'Harakiri')
        values['F4'] = df_osn[mask_hara & (df_osn['Тип документа'] == 'Продажа')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['F5'] = df_osn[mask_hara & (df_osn['Тип документа'] == 'Возврат')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['F7'] = df_osn[mask_hara]['Услуги по доставке товара покупателю'].sum()
        values['F9'] = df_osn[mask_hara]['Операции на приемке'].sum()
        values['F10'] = df_osn[mask_hara]['Общая сумма штрафов'].sum()
        values['F11'] = df_osn[mask_hara]['Удержания'].sum()

        mask_carp_vyk = (df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())
        values['M4'] = df_vyk[mask_carp_vyk & (df_vyk['Тип документа'] == 'Продажа')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['M5'] = df_vyk[mask_carp_vyk & (df_vyk['Тип документа'] == 'Возврат')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['M7'] = df_vyk[mask_carp_vyk]['Услуги по доставке товара покупателю'].sum()
        values['M8'] = df_vyk[mask_carp_vyk]['Операции на приемке'].sum()
        values['M9'] = df_vyk['Общая сумма штрафов'].sum()
        values['B47'] = df_vyk[mask_carp_vyk]['Цена розничная'].sum()

        mask_hara_vyk = (df_vyk['Бренд'] == 'Harakiri')
        values['Q4'] = df_vyk[mask_hara_vyk & (df_vyk['Тип документа'] == 'Продажа')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['Q5'] = df_vyk[mask_hara_vyk & (df_vyk['Тип документа'] == 'Возврат')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['Q7'] = df_vyk[mask_hara_vyk]['Услуги по доставке товара покупателю'].sum()
        values['Q8'] = df_vyk[mask_hara_vyk]['Операции на приемке'].sum()
        values['Q9'] = df_vyk[mask_hara_vyk]['Общая сумма штрафов'].sum()
        values['B41'] = df_vyk[mask_hara_vyk]['Цена розничная'].sum()

        col = "Размер компенсации платёжных услуг/Комиссии за интеграцию платёжных сервисов, %"
        if col in df_osn.columns:
            filtered = df_osn[col][df_osn[col].notna() & (df_osn[col] > 0)]
            if not filtered.empty:
                values['B56'] = filtered.mean()
                values['B59'] = filtered.median()
                values['B62'] = filtered.min()
                values['B65'] = filtered.max()
            else:
                values['B56'] = values['B59'] = values['B62'] = values['B65'] = 0
        else:
            values['B56'] = values['B59'] = values['B62'] = values['B65'] = 0

        return values

    def _fill_template(self, template_path, values):
        wb = openpyxl.load_workbook(template_path, data_only=False, keep_links=False, keep_vba=False)
        ws = wb.active
        for cell, value in values.items():
            ws[cell] = value
            if isinstance(value, float) and value != int(value):
                ws[cell].number_format = '0.00'
        ws.sheet_view.calcMode = 'manual'
        wb.save(template_path)

# ===== ГЛАВНОЕ МЕНЮ =====
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="menu_stats")],
        [InlineKeyboardButton("📂 История", callback_data="menu_history")],
        [InlineKeyboardButton("📦 Артикулы", callback_data="menu_articles")],
        [InlineKeyboardButton("📊 Аналитика по артикулам", callback_data="menu_analytics")],
        [InlineKeyboardButton("🗑️ Удалить отчёт", callback_data="menu_delete")],
        [InlineKeyboardButton("❓ Помощь", callback_data="menu_help")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ===== КОМАНДЫ БОТА =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для обработки еженедельных отчетов WB.\n\n"
        "📤 Отправь файлы с 'осн' и 'вык' в названии — я автоматически их обработаю.\n"
        "📊 Используй меню ниже для быстрого доступа к командам.",
        reply_markup=get_main_menu()
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 **Доступные команды:**\n"
        "/start — начать\n"
        "/help — помощь\n"
        "/osn — отметить файл как основной (вручную)\n"
        "/vyk — отметить файл как выкупы (вручную)\n"
        "/stats — общая статистика\n"
        "/delete — удалить отчет\n"
        "/articles — детали по артикулам (текущий отчет)\n\n"
        "Также можно использовать кнопки меню.",
        parse_mode='Markdown',
        reply_markup=get_main_menu()
    )

# === ОБРАБОТЧИКИ МЕНЮ ===
async def menu_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    reports, total = get_all_reports()
    if not reports:
        text = "📭 Нет данных."
    else:
        text = f"📊 Всего отчетов: {total}. Используйте /history для деталей."
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]))

async def menu_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['history_page'] = 0
    await show_history_page(query, context, page=0)

async def menu_articles_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await articles_full_cmd(update, context, is_callback=True)

async def menu_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await delete_cmd(update, context, is_callback=True)

async def menu_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await help_cmd(update, context)

# ===== АНАЛИТИКА ПО АРТИКУЛАМ =====
async def menu_analytics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['analytics_selected'] = []
    context.user_data['analytics_page'] = 0
    await show_analytics_selection(query, context, page=0)

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
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("analytics_page_"):
        page = int(data.split("_")[2])
        context.user_data['analytics_page'] = page
        await show_analytics_selection(query, context, page)

async def analytics_select_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    reports, total = get_all_reports(page=0, per_page=total)
    all_ids = [r[0] for r in reports]
    context.user_data['analytics_selected'] = all_ids
    page = context.user_data.get('analytics_page', 0)
    await show_analytics_selection(query, context, page)

async def analytics_quick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    # Агрегируем артикулы по всем выбранным отчётам
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

    # Находим предыдущий период той же длины
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
    top_articles = sorted_articles[:20]  # увеличил до 20

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
                change_str = " (нет данных за прошлый период)"
        else:
            change_str = " (нет данных за прошлый период)"
        msg += f"• **{art}**: {qty} шт. | {rev:,.2f} ₽{change_str}\n"

    if len(sorted_articles) > 20:
        msg += f"\n… и еще {len(sorted_articles)-20} артикулов."

    keyboard = [
        [InlineKeyboardButton("◀️ Назад к выбору отчётов", callback_data="menu_analytics")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# === ИСТОРИЯ С ПАГИНАЦИЕЙ ===
async def show_history_page(query, context, page):
    reports, total = get_all_reports(page=page, per_page=10)
    if not reports:
        await query.edit_message_text("📭 История пуста.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))
        return

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
        short_name = file_name if len(file_name) <= 25 else file_name[:22] + "..."
        button_text = f"📄 {short_name} ({date_period})"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"history_report_{report_id}")])

    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"history_page_{current_page-1}"))
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"history_page_{current_page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")])
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def history_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("history_page_"):
        page = int(data.split("_")[2])
        await show_history_page(query, context, page)

# === ПЕРЕХОД К ОТЧЁТУ (callback) ===
async def history_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    context.user_data['current_report_id'] = report_id
    context.user_data['current_period'] = date_period

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

    msg = (
        "📊 **Статистика отчёта**\n\n"
        f"📄 **{file_name}**\n"
        f"📅 Период: {date_period}\n\n"
        f"💳 **Средний эквайринг:** {metrics.get('avg_acquiring', 0):,.2f} %\n"
        f"📊 **Медианный эквайринг:** {metrics.get('median_acquiring', 0):,.2f} %\n\n"
        f"💰 **ВБшный оборот общий:** {metrics.get('wb_total', 0):,.2f} ₽\n"
        f"   🐱 ЦАП: {metrics.get('wb_carp', 0):,.2f} ₽\n"
        f"   ⚔️ Харакири: {metrics.get('wb_hara', 0):,.2f} ₽\n\n"
        f"📦 **Заказы (осн):** ЦАП {metrics.get('carp_orders', 0)} шт., Харакири {metrics.get('hara_orders', 0)} шт.\n"
        f"📦 **Заказы (вык):** ЦАП {metrics.get('carp_vyk_orders', 0)} шт., Харакири {metrics.get('hara_vyk_orders', 0)} шт.\n\n"
        f"💸 **К выводу ЦАП:** {metrics.get('k_vyvodu_carp', 0):,.2f} ₽\n"
        f"💸 **К выводу Харакири:** {metrics.get('k_vyvodu_hara', 0):,.2f} ₽\n"
        f"💸 **Итого к выводу:** {metrics.get('k_vyvodu_total', 0):,.2f} ₽\n"
        f"💸 **Харакири (с налогом):** {metrics.get('b38', 0):,.2f} ₽\n\n"
        f"📢 **Реклама:** ЦАП {metrics.get('reklama_carp', 0):,.2f} ₽, Харакири {metrics.get('reklama_hara', 0):,.2f} ₽\n"
        f"⚠️ **Штрафы:** {metrics.get('shtrafy', 0):,.2f} ₽\n"
        f"🧾 **Налог общий:** {metrics.get('nalog', 0):,.2f} ₽\n"
    )
    await query.message.reply_text(msg, parse_mode='Markdown')

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

# === СТАТИСТИКА И УДАЛЕНИЕ ===
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reports, total = get_all_reports()
    if not reports:
        text = "📭 Нет данных."
    else:
        text = f"📊 Всего отчетов: {total}. Используйте /history для деталей."
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback=False):
    reports, total = get_all_reports()
    if not reports:
        text = "📭 История пуста."
        if is_callback:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
            ]))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
            ]))
        return

    keyboard = []
    for r in reports:
        report_id, file_name, date_period, start_date, end_date, processed_at = r
        keyboard.append([InlineKeyboardButton(f"❌ {file_name} ({date_period})", callback_data=f"del_{report_id}")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="back_to_menu")])
    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    if is_callback:
        await update.callback_query.edit_message_text("🗑️ Выберите отчет для удаления:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("🗑️ Выберите отчет для удаления:", reply_markup=reply_markup)

async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("del_"):
        rid = int(data.split("_")[1])
        if delete_report(rid):
            await query.edit_message_text("✅ Отчет удален.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
            ]))
        else:
            await query.edit_message_text("❌ Ошибка удаления.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
            ]))

# === АРТИКУЛЫ ===
async def articles_full_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback=False):
    report_id = context.user_data.get('current_report_id')
    if not report_id:
        text = "❌ Нет активного отчёта.\n\nПожалуйста, загрузите новый отчёт или выберите существующий из истории."
        keyboard = [
            [InlineKeyboardButton("📂 Перейти в историю", callback_data="menu_history")],
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
        msg += f"**{art}**\n   Продажи: {cur_q} шт. | {cur_r:,.2f} ₽\n   Изм.: {delta_str}\n\n"
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
        msg += f"**{art}**\n   Продажи: {cur_q} шт. | {cur_r:,.2f} ₽\n   Изм.: {delta_str}\n\n"

    if len(all_items) > 10:
        msg += f"… и еще {len(all_items)-10}. Используйте /articles для полного списка."

    keyboard = [
        [InlineKeyboardButton("📈 Топ-10 по росту", callback_data="growth")],
        [InlineKeyboardButton("📉 Топ-10 по падению", callback_data="decline")],
        [InlineKeyboardButton("📊 Детальное сравнение", callback_data="compare_articles")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# === ОБРАБОТЧИКИ РОСТА И ПАДЕНИЯ ===
async def growth_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_sorted_articles(update, context, reverse=True)

async def decline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_sorted_articles(update, context, reverse=False)

async def _show_sorted_articles(update, context, reverse=True):
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
        msg += f"**{art}**\n   Продажи: {cur_q} шт. | {cur_r:,.2f} ₽\n   Изм.: {delta_str}\n\n"

    if not top:
        msg = "Нет данных для отображения."

    keyboard = [
        [InlineKeyboardButton("◀️ Назад к списку", callback_data="show_articles")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# === ДЕТАЛЬНОЕ СРАВНЕНИЕ ===
async def compare_articles_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                msg += f"• {art}: {cur_q} шт. (Δ {change_q:+.1f}%) | {cur_r:,.2f} ₽ (Δ {change_r:+.1f}%)\n"
            else:
                msg += f"• {art}: {cur_q} шт. (новинка) | {cur_r:,.2f} ₽\n"
        msg += "\n"

    keyboard = [
        [InlineKeyboardButton("◀️ Назад к списку", callback_data="show_articles")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# === ОБРАБОТЧИК "НАЗАД В МЕНЮ" ===
async def back_to_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🏠 Главное меню. Выберите действие:",
        reply_markup=get_main_menu()
    )

# === ОБРАБОТКА ФАЙЛОВ ===
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        doc = update.message.document
        if not doc.file_name.endswith(('.xlsx', '.xls')):
            await update.message.reply_text("❌ Нужен Excel файл")
            return

        file = await context.bot.get_file(doc.file_id)
        file_path = TEMP_DIR / doc.file_name
        await file.download_to_drive(file_path)

        file_hash = calculate_file_hash(file_path)
        dup = is_file_duplicate(file_hash)
        if dup:
            await update.message.reply_text(f"⚠️ Отчет уже загружен: {dup[1]}")
            os.remove(file_path)
            return

        report_type = detect_report_type(doc.file_name)
        if not report_type:
            context.user_data['current_file'] = str(file_path)
            context.user_data['current_file_hash'] = file_hash
            await update.message.reply_text("❓ Тип не определен. Используйте /osn или /vyk")
            return

        if 'files' not in context.user_data:
            context.user_data['files'] = {}
        context.user_data['files'][report_type] = str(file_path)
        if report_type == 'osn':
            context.user_data['osn_hash'] = file_hash
            await update.message.reply_text("✅ Основной отчет получен. Теперь отправьте выкупы ('вык')")
        else:
            context.user_data['vyk_hash'] = file_hash
            await update.message.reply_text("✅ Отчет по выкупам получен. Теперь отправьте основной ('осн')")

        if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
            await process_and_send(update, context)
    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def handle_osn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправьте файл!")
        return
    context.user_data['files']['osn'] = context.user_data['current_file']
    context.user_data['osn_hash'] = context.user_data['current_file_hash']
    await update.message.reply_text("✅ Основной отчет сохранен")
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)

async def handle_vyk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправьте файл!")
        return
    context.user_data['files']['vyk'] = context.user_data['current_file']
    context.user_data['vyk_hash'] = context.user_data['current_file_hash']
    await update.message.reply_text("✅ Отчет по выкупам сохранен")
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)

async def process_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

        # === ВЫЧИСЛЯЕМ МЕТРИКИ ===
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
        b38 = f13 - b35

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

        # Формируем словарь метрик для сохранения
        metrics = {
            'avg_acquiring': values.get('B56', 0),
            'median_acquiring': values.get('B59', 0),
            'wb_total': wb_total,
            'wb_carp': wb_carp,
            'wb_hara': wb_hara,
            'k_vyvodu_carp': k_carp,
            'k_vyvodu_hara': k_hara,
            'k_vyvodu_total': k_carp + k_hara,
            'b38': b38,
            'reklama_carp': reklama_carp,
            'reklama_hara': reklama_hara,
            'shtrafy': shtrafy,
            'nalog': nalog,
            'carp_orders': carp_orders,
            'hara_orders': hara_orders,
            'carp_vyk_orders': carp_vyk_orders,
            'hara_vyk_orders': hara_vyk_orders
        }

        if osn_hash is None:
            osn_hash = calculate_file_hash(Path(osn_file))
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

        with open(out_file, 'rb') as f:
            await update.message.reply_document(f, caption="✅ Готово!")

        context.user_data['articles_data'] = articles
        context.user_data['current_period'] = date_period
        context.user_data['current_report_id'] = report_id

        msg = (
            "📊 **Статистика обработки:**\n\n"
            "• Основной отчет: ЦАП + HARAKIRI ✅\n"
            "• По выкупам: ЦАП + HARAKIRI ✅\n\n"
            f"💳 **Средний эквайринг:** {values.get('B56', 0):,.2f} %\n"
            f"📊 **Медианный эквайринг:** {values.get('B59', 0):,.2f} %\n\n"
            f"💰 **ВБшный оборот общий:** {wb_total:,.2f} ₽\n"
            f"   🐱 ЦАП: {wb_carp:,.2f} ₽\n"
            f"   ⚔️ Харакири: {wb_hara:,.2f} ₽\n\n"
            f"📦 **Заказы (осн):** ЦАП {carp_orders} шт., Харакири {hara_orders} шт.\n"
            f"📦 **Заказы (вык):** ЦАП {carp_vyk_orders} шт., Харакири {hara_vyk_orders} шт.\n\n"
            f"💸 **К выводу ЦАП:** {k_carp:,.2f} ₽\n"
            f"💸 **К выводу Харакири:** {k_hara:,.2f} ₽\n"
            f"💸 **Итого к выводу:** {k_carp + k_hara:,.2f} ₽\n"
            f"💸 **Харакири (с налогом):** {b38:,.2f} ₽\n\n"
            f"📢 **Реклама:** ЦАП {reklama_carp:,.2f} ₽, Харакири {reklama_hara:,.2f} ₽\n"
            f"⚠️ **Штрафы:** {shtrafy:,.2f} ₽\n"
            f"🧾 **Налог общий:** {nalog:,.2f} ₽\n\n"
            "✅ Отчет сохранен"
        )

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

# === ОБРАБОТЧИК ТЕКСТА ===
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.startswith('/'):
        return
    await update.message.reply_text("Используйте кнопки меню или команды.", reply_markup=get_main_menu())

# ===== ЗАПУСК =====
def main():
    print("🤖 Запуск бота...")
    run_flask()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    async def set_commands(app_instance):
        await app_instance.bot.set_my_commands([
            BotCommand("start", "Начать"),
            BotCommand("help", "Помощь"),
            BotCommand("osn", "Отметить как основной"),
            BotCommand("vyk", "Отметить как выкупы"),
            BotCommand("stats", "Статистика"),
            BotCommand("delete", "Удалить отчет"),
            BotCommand("articles", "Все артикулы"),
        ])
    app.post_init = set_commands

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("osn", handle_osn))
    app.add_handler(CommandHandler("vyk", handle_vyk))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("articles", articles_full_cmd))

    # Callbacks для меню
    app.add_handler(CallbackQueryHandler(menu_stats_callback, pattern="^menu_stats$"))
    app.add_handler(CallbackQueryHandler(menu_history_callback, pattern="^menu_history$"))
    app.add_handler(CallbackQueryHandler(menu_articles_callback, pattern="^menu_articles$"))
    app.add_handler(CallbackQueryHandler(menu_delete_callback, pattern="^menu_delete$"))
    app.add_handler(CallbackQueryHandler(menu_help_callback, pattern="^menu_help$"))
    app.add_handler(CallbackQueryHandler(menu_analytics_callback, pattern="^menu_analytics$"))
    app.add_handler(CallbackQueryHandler(back_to_menu_callback, pattern="^back_to_menu$"))

    # Callbacks для аналитики
    app.add_handler(CallbackQueryHandler(analytics_toggle_callback, pattern="^analytics_toggle_"))
    app.add_handler(CallbackQueryHandler(analytics_page_callback, pattern="^analytics_page_"))
    app.add_handler(CallbackQueryHandler(analytics_select_all_callback, pattern="^analytics_select_all$"))
    app.add_handler(CallbackQueryHandler(analytics_quick_callback, pattern="^analytics_quick_"))
    app.add_handler(CallbackQueryHandler(analytics_show_callback, pattern="^analytics_show$"))

    # Callbacks для истории и удаления
    app.add_handler(CallbackQueryHandler(history_page_callback, pattern="^history_page_"))
    app.add_handler(CallbackQueryHandler(history_report_callback, pattern="^history_report_"))
    app.add_handler(CallbackQueryHandler(delete_callback, pattern="^del_"))

    # Callbacks для артикулов
    app.add_handler(CallbackQueryHandler(articles_callback, pattern="^show_articles$"))
    app.add_handler(CallbackQueryHandler(growth_callback, pattern="^growth$"))
    app.add_handler(CallbackQueryHandler(decline_callback, pattern="^decline$"))
    app.add_handler(CallbackQueryHandler(compare_articles_callback, pattern="^compare_articles$"))

    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ Бот готов")
    app.run_polling(allowed_updates=[])

if __name__ == "__main__":
    main()
