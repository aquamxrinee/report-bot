#!/usr/bin/env python3
"""
Telegram бот для обработки еженедельных отчетов Wildberries
Деплой на Railway (бесплатно, 24/7)
Полная версия: эквайринг, обороты, вывод, реклама, налоги, количество заказов,
детализация по артикулам с историей и сравнением, история, статистика, удаление.
"""

import os
import re
import shutil
import logging
import sqlite3
import hashlib
from datetime import datetime, timedelta
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
    # Основная таблица отчетов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT NOT NULL,
            file_hash TEXT UNIQUE NOT NULL,
            date_period TEXT,
            start_date TEXT,
            end_date TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            carp_sales NUMERIC DEFAULT 0,
            carp_returns NUMERIC DEFAULT 0,
            carp_delivery NUMERIC DEFAULT 0,
            carp_receiving NUMERIC DEFAULT 0,
            carp_fines NUMERIC DEFAULT 0,
            carp_withholding NUMERIC DEFAULT 0,
            carp_storage NUMERIC DEFAULT 0,
            carp_one_time_change NUMERIC DEFAULT 0,
            carp_retail_price NUMERIC DEFAULT 0,
            hara_sales NUMERIC DEFAULT 0,
            hara_returns NUMERIC DEFAULT 0,
            hara_delivery NUMERIC DEFAULT 0,
            hara_receiving NUMERIC DEFAULT 0,
            hara_fines NUMERIC DEFAULT 0,
            hara_withholding NUMERIC DEFAULT 0,
            carp_vyk_sales NUMERIC DEFAULT 0,
            carp_vyk_returns NUMERIC DEFAULT 0,
            carp_vyk_delivery NUMERIC DEFAULT 0,
            carp_vyk_receiving NUMERIC DEFAULT 0,
            carp_vyk_fines NUMERIC DEFAULT 0,
            carp_vyk_retail_price NUMERIC DEFAULT 0,
            hara_vyk_sales NUMERIC DEFAULT 0,
            hara_vyk_returns NUMERIC DEFAULT 0,
            hara_vyk_delivery NUMERIC DEFAULT 0,
            hara_vyk_receiving NUMERIC DEFAULT 0,
            hara_vyk_fines NUMERIC DEFAULT 0,
            hara_vyk_retail_price NUMERIC DEFAULT 0
        )
    ''')
    # Добавляем колонки start_date и end_date, если их нет
    cursor.execute("PRAGMA table_info(reports)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'start_date' not in columns:
        cursor.execute("ALTER TABLE reports ADD COLUMN start_date TEXT")
    if 'end_date' not in columns:
        cursor.execute("ALTER TABLE reports ADD COLUMN end_date TEXT")

    # Таблица для детальной статистики по артикулам
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
        cursor.execute('SELECT id, file_name, date_period, processed_at FROM reports WHERE file_hash = ?', (file_hash,))
        result = cursor.fetchone()
        conn.close()
        return result
    except:
        return None

def save_report_to_db(file_name, file_hash, date_period, start_date, end_date, values, articles):
    """Сохраняет отчет и детали по артикулам"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO reports (
                file_name, file_hash, date_period, start_date, end_date,
                carp_sales, carp_returns, carp_delivery, carp_receiving,
                carp_fines, carp_withholding, carp_storage, carp_one_time_change, carp_retail_price,
                hara_sales, hara_returns, hara_delivery, hara_receiving, hara_fines, hara_withholding,
                carp_vyk_sales, carp_vyk_returns, carp_vyk_delivery, carp_vyk_receiving,
                carp_vyk_fines, carp_vyk_retail_price,
                hara_vyk_sales, hara_vyk_returns, hara_vyk_delivery, hara_vyk_receiving, hara_vyk_fines,
                hara_vyk_retail_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            file_name, file_hash, date_period, start_date, end_date,
            float(values.get('B4', 0)), float(values.get('B5', 0)),
            float(values.get('B7', 0)), float(values.get('B9', 0)),
            float(values.get('B10', 0)), float(values.get('B11', 0)),
            float(values.get('B26', 0)), float(values.get('B29', 0)),
            float(values.get('B32', 0)),
            float(values.get('F4', 0)), float(values.get('F5', 0)),
            float(values.get('F7', 0)), float(values.get('F9', 0)),
            float(values.get('F10', 0)), float(values.get('F11', 0)),
            float(values.get('M4', 0)), float(values.get('M5', 0)),
            float(values.get('M7', 0)), float(values.get('M8', 0)),
            float(values.get('M9', 0)), float(values.get('B47', 0)),
            float(values.get('Q4', 0)), float(values.get('Q5', 0)),
            float(values.get('Q7', 0)), float(values.get('Q8', 0)),
            float(values.get('Q9', 0)), float(values.get('B41', 0))
        ))
        report_id = cursor.lastrowid

        # Сохраняем артикулы, если они есть
        if articles:
            for brand, data in articles.items():
                for key, stats in data.get('sales', {}).items():
                    # key = article name
                    cursor.execute('''
                        INSERT INTO article_stats (report_id, brand, article, quantity, revenue)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (report_id, brand, key, stats['quantity'], stats['revenue']))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
        return False

def delete_report(report_id):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        # Сначала удаляем связанные артикулы (каскадное удаление сработает, если есть внешний ключ)
        cursor.execute('DELETE FROM article_stats WHERE report_id = ?', (report_id,))
        cursor.execute('DELETE FROM reports WHERE id = ?', (report_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted
    except:
        return False

def get_all_reports():
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, file_name, date_period, start_date, end_date, processed_at,
                   carp_sales, hara_sales, carp_vyk_sales, hara_vyk_sales
            FROM reports ORDER BY processed_at DESC
        ''')
        results = cursor.fetchall()
        conn.close()
        return results
    except:
        return []

def get_previous_reports(current_start_date, limit=12):
    """Возвращает список предыдущих отчетов (id, start_date, end_date) до указанной даты, отсортированных по start_date DESC"""
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
    """Возвращает словарь {article: {'quantity': q, 'revenue': r}} для отчёта, опционально по бренду"""
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

def get_articles_for_comparison(current_articles, previous_reports_ids):
    """
    Для каждого артикула из current_articles вычисляет средние показатели по предыдущим отчетам
    Возвращает словарь: {article: {'avg_quantity': q, 'avg_revenue': r}}
    """
    if not previous_reports_ids:
        return {}
    all_prev_articles = {}
    for rid in previous_reports_ids:
        stats = get_article_stats_for_report(rid)
        for art, data in stats.items():
            if art not in all_prev_articles:
                all_prev_articles[art] = {'quantity': [], 'revenue': []}
            all_prev_articles[art]['quantity'].append(data['quantity'])
            all_prev_articles[art]['revenue'].append(data['revenue'])
    # Усредняем
    result = {}
    for art, vals in all_prev_articles.items():
        if vals['quantity']:
            result[art] = {
                'avg_quantity': sum(vals['quantity']) / len(vals['quantity']),
                'avg_revenue': sum(vals['revenue']) / len(vals['revenue'])
            }
    return result

# ===== ОПРЕДЕЛЕНИЕ ТИПА ФАЙЛА =====
def detect_report_type(filename):
    name = filename.lower()
    if 'осн' in name or 'osn' in name:
        return 'osn'
    elif 'вык' in name or 'vyk' in name:
        return 'vyk'
    return None

def parse_date_from_period(date_period):
    """Парсит строку 'dd.mm-dd.mm' и возвращает (start_date, end_date) в формате YYYY-MM-DD"""
    try:
        parts = date_period.split('-')
        start = parts[0].strip()
        end = parts[1].strip()
        # Предполагаем текущий год, если не указан
        year = datetime.now().year
        # Преобразуем в дату
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

        filename = Path(osn_path).name
        match = re.search(r'(\d{1,2})\.(\d{2})-(\d{1,2})\.(\d{2})', filename)
        date_range = f"{match.group(1)}.{match.group(2)}-{match.group(3)}.{match.group(4)}" if match else datetime.now().strftime("%d.%m")

        values = self._calculate_all_values(df_osn, df_vyk, date_range)
        self._fill_template(template_path, values)

        articles = self._get_articles_stats(df_osn, df_vyk)
        return values, articles, date_range

    def _get_articles_stats(self, df_osn, df_vyk):
        result = {}
        qty_cols = ['Кол-во', 'Количество', 'Количество товара', 'Кол-во (шт.)', 'Кол-во шт']
        article_cols = ['Артикул поставщика', 'Артикул', 'Артикул товара', 'Номенклатура', 'SKU', 'Артикул (поставщика)']

        qty_col = None
        art_col = None
        for col in qty_cols:
            if col in df_osn.columns or col in df_vyk.columns:
                qty_col = col
                break
        for col in article_cols:
            if col in df_osn.columns or col in df_vyk.columns:
                art_col = col
                break

        if qty_col is None or art_col is None:
            logger.warning("Колонки количества или артикула не найдены")
            return result

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

# ===== КОМАНДЫ БОТА =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для обработки еженедельных отчетов WB.\n\n"
        "📤 Отправь файлы с 'осн' и 'вык' в названии — я автоматически их обработаю.\n"
        "📊 Команды: /history, /stats, /delete, /articles"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — начать\n"
        "/help — помощь\n"
        "/osn — отметить файл как основной (вручную)\n"
        "/vyk — отметить файл как выкупы (вручную)\n"
        "/history — история отчетов\n"
        "/stats — общая статистика\n"
        "/delete — удалить отчет\n"
        "/articles — детали по артикулам (текущий отчет)"
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

        # Шаблон
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

        # Коэффициенты
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

        # Копируем шаблон
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out_file = TEMP_DIR / f"шаблон_{timestamp}.xlsx"
        shutil.copy(template_path, out_file)

        processor = ReportProcessor()
        values, articles, date_period = processor.process_files(osn_file, vyk_file, str(out_file))

        # Приводим всё к float
        for k in values:
            try:
                values[k] = float(values[k])
            except:
                values[k] = 0.0

        # Парсим даты периода
        start_date, end_date = parse_date_from_period(date_period)
        if not start_date:
            start_date = end_date = datetime.now().strftime("%Y-%m-%d")

        # Сохраняем в БД (включая артикулы)
        if osn_hash is None:
            osn_hash = calculate_file_hash(Path(osn_file))
        saved = save_report_to_db(
            file_name=Path(osn_file).name,
            file_hash=osn_hash,
            date_period=date_period,
            start_date=start_date,
            end_date=end_date,
            values=values,
            articles=articles
        )

        # Отправляем файл
        with open(out_file, 'rb') as f:
            await update.message.reply_document(f, caption="✅ Готово!")

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

        # Количество заказов
        carp_orders = sum(a.get('quantity', 0) for a in articles.get('Цап царапкин', {}).get('sales', {}).values())
        hara_orders = sum(a.get('quantity', 0) for a in articles.get('Harakiri', {}).get('sales', {}).values())
        carp_vyk_orders = sum(a.get('quantity', 0) for a in articles.get('Цап царапкин', {}).get('vyk', {}).values())
        hara_vyk_orders = sum(a.get('quantity', 0) for a in articles.get('Harakiri', {}).get('vyk', {}).values())

        # Сохраняем в контекст для /articles
        context.user_data['articles_data'] = articles
        context.user_data['current_period'] = date_period
        context.user_data['current_report_id'] = None  # пока не знаем id, но потом получим

        # Получаем ID сохранённого отчёта (последний вставленный)
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute("SELECT last_insert_rowid()")
        report_id = cursor.fetchone()[0]
        conn.close()
        context.user_data['current_report_id'] = report_id

        # === СООБЩЕНИЕ ===
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

        # Кнопка "Детали по артикулам"
        keyboard = [[InlineKeyboardButton("📦 Детали по артикулам", callback_data="show_articles")]]
        await update.message.reply_text("Нажмите кнопку для деталей:", reply_markup=InlineKeyboardMarkup(keyboard))

        # Очистка
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

# === ИСТОРИЯ, СТАТИСТИКА, УДАЛЕНИЕ ===
async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reports = get_all_reports()
    if not reports:
        await update.message.reply_text("📭 История пуста.")
        return
    msg = "📊 **История отчетов:**\n\n"
    for r in reports[:10]:
        msg += f"📄 {r[1]} ({r[2]})\n   🐱 {r[6]:,.2f} ₽ | ⚔️ {r[7]:,.2f} ₽\n"
    if len(reports) > 10:
        msg += f"… и еще {len(reports)-10}"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reports = get_all_reports()
    if not reports:
        await update.message.reply_text("📭 Нет данных.")
        return
    total = len(reports)
    avg_carp = sum(r[6] for r in reports) / total
    avg_hara = sum(r[7] for r in reports) / total
    avg_carp_vyk = sum(r[8] for r in reports) / total
    avg_hara_vyk = sum(r[9] for r in reports) / total
    msg = f"📊 **Общая статистика** ({total} отч.)\n\n"
    msg += f"🐱 ЦАП осн: {avg_carp:,.2f} ₽\n"
    msg += f"⚔️ Харакири осн: {avg_hara:,.2f} ₽\n"
    msg += f"🐱 ЦАП вык: {avg_carp_vyk:,.2f} ₽\n"
    msg += f"⚔️ Харакири вык: {avg_hara_vyk:,.2f} ₽"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reports = get_all_reports()
    if not reports:
        await update.message.reply_text("📭 История пуста.")
        return
    keyboard = []
    for r in reports[:10]:
        keyboard.append([InlineKeyboardButton(f"❌ {r[1]} ({r[2]})", callback_data=f"del_{r[0]}")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="del_cancel")])
    await update.message.reply_text("🗑️ Выберите отчет для удаления:", reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "del_cancel":
        await query.edit_message_text("✅ Отменено.")
        return
    if data.startswith("del_"):
        rid = int(data.split("_")[1])
        if delete_report(rid):
            await query.edit_message_text(f"✅ Отчет #{rid} удален.")
        else:
            await query.edit_message_text("❌ Ошибка удаления.")

# === АРТИКУЛЫ ===
async def articles_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    report_id = context.user_data.get('current_report_id')
    if not report_id:
        await query.edit_message_text("❌ Нет данных по артикулам для текущего отчета.")
        return

    articles = get_article_stats_for_report(report_id)
    if not articles:
        await query.edit_message_text("❌ Нет данных по артикулам.")
        return

    # Преобразуем в список для сортировки
    all_items = [(art, data['quantity'], data['revenue']) for art, data in articles.items()]
    all_items.sort(key=lambda x: x[2], reverse=True)
    top = all_items[:10]
    period = context.user_data.get('current_period', '')

    msg = f"📦 **Топ-10 артикулов** ({period})\n\n"
    for art, qty, rev in top:
        msg += f"{art}\n   Продажи: {qty} шт. | {rev:,.2f} ₽\n"
    if len(all_items) > 10:
        msg += f"\n… и еще {len(all_items)-10}. Используйте /articles для полного списка."

    # Добавляем кнопку "Детальное сравнение"
    keyboard = [[InlineKeyboardButton("📊 Детальное сравнение", callback_data="compare_articles")]]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def articles_full_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    report_id = context.user_data.get('current_report_id')
    if not report_id:
        await update.message.reply_text("❌ Нет данных. Сначала загрузите отчет.")
        return

    articles = get_article_stats_for_report(report_id)
    if not articles:
        await update.message.reply_text("❌ Нет данных по артикулам.")
        return

    all_items = [(art, data['quantity'], data['revenue']) for art, data in articles.items()]
    all_items.sort(key=lambda x: x[2], reverse=True)
    period = context.user_data.get('current_period', '')

    msg = f"📦 **Все артикулы** ({period})\n\n"
    for art, qty, rev in all_items:
        msg += f"{art}\n   Продажи: {qty} шт. | {rev:,.2f} ₽\n"
        if len(msg) > 4000:
            msg += "\n… (сообщение обрезано)"
            break

    # Добавляем кнопку "Детальное сравнение"
    keyboard = [[InlineKeyboardButton("📊 Детальное сравнение", callback_data="compare_articles")]]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# === СРАВНЕНИЕ ===
async def compare_articles_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    report_id = context.user_data.get('current_report_id')
    if not report_id:
        await query.edit_message_text("❌ Нет данных для сравнения.")
        return

    # Получаем текущий отчёт
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

    # Получаем предыдущие отчёты (до current_start)
    prev_reports = get_previous_reports(current_start, limit=12)

    if not prev_reports:
        await query.edit_message_text("❌ Нет предыдущих отчетов для сравнения.")
        return

    # Идентификаторы предыдущих отчетов
    prev_ids = [r[0] for r in prev_reports]

    # Получаем текущие артикулы
    current_articles = get_article_stats_for_report(report_id)
    if not current_articles:
        await query.edit_message_text("❌ Нет данных по артикулам в текущем отчете.")
        return

    # Считаем средние по предыдущим периодам (2, 4, 12 недель)
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
        # Получаем все артикулы за эти периоды и усредняем
        all_articles = {}
        for pid in ids:
            arts = get_article_stats_for_report(pid)
            for art, data in arts.items():
                if art not in all_articles:
                    all_articles[art] = {'qty': [], 'rev': []}
                all_articles[art]['qty'].append(data['quantity'])
                all_articles[art]['rev'].append(data['revenue'])
        # Усредняем
        avg_articles = {}
        for art, vals in all_articles.items():
            avg_articles[art] = {
                'avg_quantity': sum(vals['qty']) / len(vals['qty']),
                'avg_revenue': sum(vals['rev']) / len(vals['rev'])
            }
        # Сравниваем с текущими
        msg += f"**{period_name}** (среднее по {len(ids)} отчетам):\n"
        for art, data in sorted(current_articles.items(), key=lambda x: x[1]['revenue'], reverse=True)[:5]:
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

    await query.edit_message_text(msg, parse_mode='Markdown')

# ===== ЗАПУСК =====
def main():
    print("🤖 Запуск бота...")
    run_flask()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Меню команд
    async def set_commands(app_instance):
        await app_instance.bot.set_my_commands([
            BotCommand("start", "Начать"),
            BotCommand("help", "Помощь"),
            BotCommand("osn", "Отметить как основной"),
            BotCommand("vyk", "Отметить как выкупы"),
            BotCommand("history", "История"),
            BotCommand("stats", "Статистика"),
            BotCommand("delete", "Удалить отчет"),
            BotCommand("articles", "Все артикулы"),
        ])
    app.post_init = set_commands

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("osn", handle_osn))
    app.add_handler(CommandHandler("vyk", handle_vyk))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("articles", articles_full_cmd))
    app.add_handler(CallbackQueryHandler(articles_callback, pattern="^show_articles$"))
    app.add_handler(CallbackQueryHandler(compare_articles_callback, pattern="^compare_articles$"))
    app.add_handler(CallbackQueryHandler(delete_callback, pattern="^del_"))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    print("✅ Бот готов")
    app.run_polling(allowed_updates=[])

if __name__ == "__main__":
    main()
