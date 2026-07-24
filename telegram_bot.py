#!/usr/bin/env python3
"""
Telegram бот для обработки еженедельных отчетов Wildberries
Деплой на Railway (бесплатно, 24/7)
С SQLite базой данных, защитой от дубликатов и постоянным томом
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
    raise ValueError("❌ Токен не найден! Установите TELEGRAM_BOT_TOKEN")

# ===== ПУТИ ДЛЯ ХРАНЕНИЯ =====
DATA_DIR = Path("/data")
TEMP_DIR = DATA_DIR / "temp"
DB_PATH = DATA_DIR / "reports.db"

if not DATA_DIR.exists():
    print("⚠️ Том /data/ не найден, использую /tmp/")
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

print(f"📁 Папка для данных: {DATA_DIR}")
print(f"📁 Временная папка: {TEMP_DIR}")
print(f"📊 База данных: {DB_PATH}")

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
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

try:
    init_db()
except Exception as e:
    logger.error(f"❌ Ошибка инициализации БД: {e}")

# ===== ФУНКЦИИ РАБОТЫ С БД =====
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
    except Exception as e:
        logger.error(f"Ошибка проверки дубликата: {e}")
        return None

def save_report_to_db(file_name, file_hash, date_period, values):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO reports (
                file_name, file_hash, date_period,
                carp_sales, carp_returns, carp_delivery, carp_receiving,
                carp_fines, carp_withholding, carp_storage, carp_one_time_change, carp_retail_price,
                hara_sales, hara_returns, hara_delivery, hara_receiving, hara_fines, hara_withholding,
                carp_vyk_sales, carp_vyk_returns, carp_vyk_delivery, carp_vyk_receiving,
                carp_vyk_fines, carp_vyk_retail_price,
                hara_vyk_sales, hara_vyk_returns, hara_vyk_delivery, hara_vyk_receiving, hara_vyk_fines,
                hara_vyk_retail_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            file_name, file_hash, date_period,
            values.get('B4', 0), values.get('B5', 0), values.get('B7', 0), values.get('B9', 0),
            values.get('B10', 0), values.get('B11', 0), values.get('B26', 0), values.get('B29', 0), values.get('B32', 0),
            values.get('F4', 0), values.get('F5', 0), values.get('F7', 0), values.get('F9', 0),
            values.get('F10', 0), values.get('F11', 0),
            values.get('M4', 0), values.get('M5', 0), values.get('M7', 0), values.get('M8', 0),
            values.get('M9', 0), values.get('B47', 0),
            values.get('Q4', 0), values.get('Q5', 0), values.get('Q7', 0), values.get('Q8', 0), values.get('Q9', 0),
            values.get('B41', 0)
        ))
        conn.commit()
        conn.close()
        logger.info(f"✅ Отчет сохранен: {file_name}")
        return True
    except sqlite3.IntegrityError:
        logger.warning(f"⚠️ Дубликат: {file_name}")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения: {e}")
        return False

def delete_report(report_id):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('DELETE FROM reports WHERE id = ?', (report_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
        return False

def get_all_reports():
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, file_name, date_period, processed_at,
                   carp_sales, hara_sales, carp_vyk_sales, hara_vyk_sales
            FROM reports
            ORDER BY processed_at DESC
        ''')
        results = cursor.fetchall()
        conn.close()
        return results
    except Exception as e:
        logger.error(f"Ошибка получения отчетов: {e}")
        return []

# ===== ОПРЕДЕЛЕНИЕ ТИПА ФАЙЛА =====
def detect_report_type(filename):
    """Возвращает 'osn' или 'vyk' по наличию подстроки в имени"""
    name = filename.lower()
    if 'осн' in name or 'osn' in name:
        return 'osn'
    elif 'вык' in name or 'vyk' in name:
        return 'vyk'
    return None

# ===== FLASK ДЛЯ ПИНГОВ =====
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
        try:
            df_osn = pd.read_excel(osn_path)
            df_vyk = pd.read_excel(vyk_path)

            filename = Path(osn_path).name
            match = re.search(r'(\d{1,2})\.(\d{2})-(\d{1,2})\.(\d{2})', filename)
            if match:
                date_range = f"{match.group(1)}.{match.group(2)}-{match.group(3)}.{match.group(4)}"
            else:
                date_range = datetime.now().strftime("%d.%m")

            values = self._calculate_all_values(df_osn, df_vyk, date_range)
            self._fill_template(template_path, values)
            
            # Собираем детальную статистику по артикулам
            articles = self._get_articles_stats(df_osn, df_vyk)
            
            return values, articles
        except Exception as e:
            logger.error(f"Ошибка обработки: {e}")
            raise

    def _get_articles_stats(self, df_osn, df_vyk):
        """
        Агрегирует данные по артикулам из основного отчёта и отчёта по выкупам.
        Возвращает словарь:
        {
            'Цап царапкин': {
                'sales': {article: {'quantity': int, 'revenue': float, 'returns': int, 'return_revenue': float}},
                'vyk': {...}
            },
            'Harakiri': {...}
        }
        """
        result = {}
        # Определяем список возможных колонок для количества и артикула
        qty_cols = ['Количество', 'Количество товара', 'Кол-во', 'Quantity']
        article_cols = ['Артикул', 'Артикул товара', 'Номенклатура', 'SKU']
        
        # Проверяем, есть ли нужные колонки в df_osn
        qty_col = None
        art_col = None
        for col in qty_cols:
            if col in df_osn.columns:
                qty_col = col
                break
        for col in article_cols:
            if col in df_osn.columns:
                art_col = col
                break
        
        if qty_col is None or art_col is None:
            logger.warning("Не найдены колонки для количества или артикула")
            return {}
        
        # Обрабатываем основной отчёт
        for bren in ['Цап царапкин', 'Harakiri']:
            if bren == 'Цап царапкин':
                mask = (df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())
            else:
                mask = (df_osn['Бренд'] == 'Harakiri')
            
            df_bren = df_osn[mask]
            if df_bren.empty:
                continue
            
            # Продажи
            sales = df_bren[df_bren['Тип документа'] == 'Продажа']
            sales_agg = sales.groupby(art_col).agg(
                quantity=(qty_col, 'sum'),
                revenue=('К перечислению Продавцу за реализованный Товар', 'sum')
            ).to_dict('index')
            
            # Возвраты
            returns = df_bren[df_bren['Тип документа'] == 'Возврат']
            returns_agg = returns.groupby(art_col).agg(
                return_quantity=(qty_col, 'sum'),
                return_revenue=('К перечислению Продавцу за реализованный Товар', 'sum')
            ).to_dict('index')
            
            # Объединяем
            articles = {}
            all_articles = set(sales_agg.keys()) | set(returns_agg.keys())
            for art in all_articles:
                articles[art] = {
                    'quantity': sales_agg.get(art, {}).get('quantity', 0),
                    'revenue': sales_agg.get(art, {}).get('revenue', 0),
                    'return_quantity': returns_agg.get(art, {}).get('return_quantity', 0),
                    'return_revenue': returns_agg.get(art, {}).get('return_revenue', 0)
                }
            result[bren] = {'sales': articles}
        
        # Аналогично для df_vyk (отчёт по выкупам) — добавим в ту же структуру
        for bren in ['Цап царапкин', 'Harakiri']:
            if bren == 'Цап царапкин':
                mask = (df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())
            else:
                mask = (df_vyk['Бренд'] == 'Harakiri')
            
            df_bren = df_vyk[mask]
            if df_bren.empty:
                continue
            
            sales = df_bren[df_bren['Тип документа'] == 'Продажа']
            sales_agg = sales.groupby(art_col).agg(
                quantity=(qty_col, 'sum'),
                revenue=('К перечислению Продавцу за реализованный Товар', 'sum')
            ).to_dict('index')
            
            returns = df_bren[df_bren['Тип документа'] == 'Возврат']
            returns_agg = returns.groupby(art_col).agg(
                return_quantity=(qty_col, 'sum'),
                return_revenue=('К перечислению Продавцу за реализованный Товар', 'sum')
            ).to_dict('index')
            
            # Объединяем
            articles = {}
            all_articles = set(sales_agg.keys()) | set(returns_agg.keys())
            for art in all_articles:
                articles[art] = {
                    'quantity': sales_agg.get(art, {}).get('quantity', 0),
                    'revenue': sales_agg.get(art, {}).get('revenue', 0),
                    'return_quantity': returns_agg.get(art, {}).get('return_quantity', 0),
                    'return_revenue': returns_agg.get(art, {}).get('return_revenue', 0)
                }
            # Если бренд уже есть в result, добавляем ключ 'vyk', иначе создаём
            if bren in result:
                result[bren]['vyk'] = articles
            else:
                result[bren] = {'vyk': articles}
        
        return result

    def _calculate_all_values(self, df_osn, df_vyk, date_range):
        values = {'B1': date_range, 'F1': date_range}

        # ===== ОСНОВНОЙ ОТЧЕТ - ЦАП =====
        filter_carp_sale = ((df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа'] == 'Продажа')
        values['B4'] = df_osn[filter_carp_sale]['К перечислению Продавцу за реализованный Товар'].sum()

        filter_carp_return = ((df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа'] == 'Возврат')
        values['B5'] = df_osn[filter_carp_return]['К перечислению Продавцу за реализованный Товар'].sum()

        filter_carp_all = (df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())
        values['B7'] = df_osn[filter_carp_all]['Услуги по доставке товара покупателю'].sum()
        values['B9'] = df_osn[filter_carp_all]['Операции на приемке'].sum()
        values['B10'] = df_osn['Общая сумма штрафов'].sum()
        values['B11'] = df_osn[filter_carp_all]['Удержания'].sum()
        values['B26'] = df_osn[filter_carp_all]['Хранение'].sum()
        values['B29'] = df_osn[filter_carp_all]['Разовое изменение срока перечисления денежных средств'].sum()
        values['B44'] = df_osn['Цена розничная'].sum()
        values['B32'] = df_osn[filter_carp_all]['Цена розничная'].sum()

        # ===== ОСНОВНОЙ ОТЧЕТ - HARAKIRI =====
        filter_hara_sale = (df_osn['Бренд'] == 'Harakiri') & (df_osn['Тип документа'] == 'Продажа')
        values['F4'] = df_osn[filter_hara_sale]['К перечислению Продавцу за реализованный Товар'].sum()

        filter_hara_return = (df_osn['Бренд'] == 'Harakiri') & (df_osn['Тип документа'] == 'Возврат')
        values['F5'] = df_osn[filter_hara_return]['К перечислению Продавцу за реализованный Товар'].sum()

        filter_hara_all = df_osn['Бренд'] == 'Harakiri'
        values['F7'] = df_osn[filter_hara_all]['Услуги по доставке товара покупателю'].sum()
        values['F9'] = df_osn[filter_hara_all]['Операции на приемке'].sum()
        values['F10'] = df_osn[filter_hara_all]['Общая сумма штрафов'].sum()
        values['F11'] = df_osn[filter_hara_all]['Удержания'].sum()

        # ===== ВЫКУПЫ - ЦАП =====
        filter_carp_vyk_sale = (df_vyk['Бренд'] == 'Цап царапкин') & (df_vyk['Тип документа'] == 'Продажа')
        values['M4'] = df_vyk[filter_carp_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()

        filter_carp_vyk_return = (df_vyk['Бренд'] == 'Цап царапкин') & (df_vyk['Тип документа'] == 'Возврат')
        values['M5'] = df_vyk[filter_carp_vyk_return]['К перечислению Продавцу за реализованный Товар'].sum()

        filter_carp_vyk_all = (df_vyk['Бренд'] == 'Цап царапкин')
        values['M7'] = df_vyk[filter_carp_vyk_all]['Услуги по доставке товара покупателю'].sum()
        values['M8'] = df_vyk[filter_carp_vyk_all]['Операции на приемке'].sum()
        values['M9'] = df_vyk['Общая сумма штрафов'].sum()
        values['B47'] = df_vyk[filter_carp_vyk_all]['Цена розничная'].sum()

        # ===== ВЫКУПЫ - HARAKIRI =====
        filter_hara_vyk_sale = (df_vyk['Бренд'] == 'Harakiri') & (df_vyk['Тип документа'] == 'Продажа')
        values['Q4'] = df_vyk[filter_hara_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()

        filter_hara_vyk_return = (df_vyk['Бренд'] == 'Harakiri') & (df_vyk['Тип документа'] == 'Возврат')
        values['Q5'] = df_vyk[filter_hara_vyk_return]['К перечислению Продавцу за реализованный Товар'].sum()

        filter_hara_vyk_all = (df_vyk['Бренд'] == 'Harakiri')
        values['Q7'] = df_vyk[filter_hara_vyk_all]['Услуги по доставке товара покупателю'].sum()
        values['Q8'] = df_vyk[filter_hara_vyk_all]['Операции на приемке'].sum()
        values['Q9'] = df_vyk[filter_hara_vyk_all]['Общая сумма штрафов'].sum()
        values['B41'] = df_vyk[filter_hara_vyk_all]['Цена розничная'].sum()

        # ===== ЭКВАЙРИНГ =====
        col_name = "Размер компенсации платёжных услуг/Комиссии за интеграцию платёжных сервисов, %"
        if col_name in df_osn.columns:
            series = df_osn[col_name]
            filtered = series[series.notna() & (series > 0)]
            if not filtered.empty:
                values['B56'] = filtered.mean()
                values['B59'] = filtered.median()
                values['B62'] = filtered.min()
                values['B65'] = filtered.max()
            else:
                values['B56'] = 0
                values['B59'] = 0
                values['B62'] = 0
                values['B65'] = 0
        else:
            values['B56'] = 0
            values['B59'] = 0
            values['B62'] = 0
            values['B65'] = 0

        return values

    def _fill_template(self, template_path, values):
        if str(template_path).startswith("/app/"):
            raise ValueError("❌ НЕЛЬЗЯ сохранять в /app/!")
        wb = openpyxl.load_workbook(template_path, data_only=False, keep_links=False, keep_vba=False)
        ws = wb.active
        for cell, value in values.items():
            ws[cell] = value
            if isinstance(value, float) and value != int(value):
                ws[cell].number_format = '0.00'
        ws.sheet_view.calcMode = 'manual'
        wb.save(template_path)
        logger.info(f"Шаблон сохранен: {template_path}")

# ===== КОМАНДЫ БОТА =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для обработки еженедельных отчетов.\n\n"
        "📤 Как пользоваться:\n"
        "1️⃣ Отправь файл с названием, содержащим 'осн' (основной) или 'вык' (по выкупам)\n"
        "2️⃣ Бот автоматически определит тип и попросит второй файл\n"
        "3️⃣ Готово! Получишь заполненный шаблон! ✅\n\n"
        "📊 Команды:\n"
        "/history - показать все загруженные отчеты\n"
        "/stats - показать общую статистику\n"
        "/delete - удалить отчет из истории"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 Команды:\n"
        "/start - приветствие\n"
        "/help - помощь\n"
        "/history - история отчетов\n"
        "/stats - статистика\n"
        "/delete - удалить отчет\n"
        "/osn - отметить файл как основной (вручную)\n"
        "/vyk - отметить файл как отчет по выкупам (вручную)"
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        doc = update.message.document
        if not doc.file_name.endswith(('.xlsx', '.xls')):
            await update.message.reply_text("❌ Нужен Excel файл (.xlsx или .xls)")
            return

        file = await context.bot.get_file(doc.file_id)
        file_path = TEMP_DIR / doc.file_name
        await file.download_to_drive(file_path)

        file_hash = calculate_file_hash(file_path)
        duplicate = is_file_duplicate(file_hash)
        if duplicate:
            dup_id, dup_name, dup_date, dup_time = duplicate
            await update.message.reply_text(
                f"⚠️ Этот отчет уже был загружен ранее!\n"
                f"📄 {dup_name}\n"
                f"📅 {dup_date}\n"
                f"🕐 {dup_time}"
            )
            os.remove(file_path)
            return

        report_type = detect_report_type(doc.file_name)
        if not report_type:
            context.user_data['current_file'] = str(file_path)
            context.user_data['current_file_hash'] = file_hash
            await update.message.reply_text(
                "❓ Не удалось определить тип отчета.\n"
                "Укажите вручную:\n"
                "/osn - Основной отчет\n"
                "/vyk - Отчет по выкупам"
            )
            return

        if 'files' not in context.user_data:
            context.user_data['files'] = {}
        context.user_data['files'][report_type] = str(file_path)
        if report_type == 'osn':
            context.user_data['osn_hash'] = file_hash
            await update.message.reply_text(
                f"✅ Основной отчет получен!\nТеперь отправьте отчет по выкупам ('вык')"
            )
        else:
            context.user_data['vyk_hash'] = file_hash
            await update.message.reply_text(
                f"✅ Отчет по выкупам получен!\nТеперь отправьте основной отчет ('осн')"
            )

        if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
            await process_and_send(update, context)

    except Exception as e:
        logger.error(f"Ошибка при загрузке: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def handle_osn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправьте файл!")
        return
    context.user_data['files']['osn'] = context.user_data['current_file']
    context.user_data['osn_hash'] = context.user_data['current_file_hash']
    await update.message.reply_text("✅ Основной отчет сохранен!")
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)

async def handle_vyk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправьте файл!")
        return
    context.user_data['files']['vyk'] = context.user_data['current_file']
    context.user_data['vyk_hash'] = context.user_data['current_file_hash']
    await update.message.reply_text("✅ Отчет по выкупам сохранен!")
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)

async def process_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("⏳ Обрабатываю отчеты...")

        osn_file = context.user_data['files']['osn']
        vyk_file = context.user_data['files']['vyk']
        osn_hash = context.user_data.get('osn_hash')

        original_template = Path("/app/шаблон.xlsx")
        if not original_template.exists():
            possible_paths = [Path("шаблон.xlsx"), TEMP_DIR / "template.xlsx"]
            for p in possible_paths:
                if p.exists():
                    original_template = p
                    break

        # Читаем коэффициенты хранения из шаблона (приводим к float)
        wb_coeff = openpyxl.load_workbook(original_template, data_only=True)
        ws_coeff = wb_coeff.active
        b23_val = ws_coeff['B23'].value
        c23_val = ws_coeff['C23'].value
        wb_coeff.close()
        try:
            b23 = float(b23_val) if b23_val is not None else 0.0
        except (ValueError, TypeError):
            b23 = 0.0
        try:
            c23 = float(c23_val) if c23_val is not None else 0.0
        except (ValueError, TypeError):
            c23 = 0.0
        logger.info(f"📊 Коэффициенты хранения: B23={b23}, C23={c23}")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        template_file = TEMP_DIR / f"шаблон_{timestamp}.xlsx"

        if original_template.exists():
            shutil.copy(original_template, template_file)
        else:
            await update.message.reply_text("⚠️ Шаблон не найден, создаю новый...")
            wb = openpyxl.Workbook()
            wb.save(template_file)

        processor = ReportProcessor()
        values, articles_data = processor.process_files(osn_file, vyk_file, str(template_file))

        # Приводим все числовые значения из values к float
        for key in values:
            try:
                values[key] = float(values[key])
            except (ValueError, TypeError):
                values[key] = 0.0

        # Сохранение в БД
        if osn_hash is None:
            osn_hash = calculate_file_hash(Path(osn_file))
        saved = save_report_to_db(
            file_name=Path(osn_file).name,
            file_hash=osn_hash,
            date_period=values.get('B1', ''),
            values=values
        )

        # Отправка файла
        with open(template_file, 'rb') as f:
            await update.message.reply_document(
                document=f,
                caption="✅ Готово! Шаблон заполнен и готов к скачиванию."
            )

        # === ВЫЧИСЛЕНИЕ ДОПОЛНИТЕЛЬНЫХ МЕТРИК ===
        def get_float(key):
            val = values.get(key, 0)
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0

        b4 = get_float('B4')
        b5 = get_float('B5')
        b7 = get_float('B7')
        b9 = get_float('B9')
        b10 = get_float('B10')
        b11 = get_float('B11')
        b26 = get_float('B26')
        b29 = get_float('B29')
        b32 = get_float('B32')
        b44 = get_float('B44')
        b47 = get_float('B47')
        b41 = get_float('B41')

        f4 = get_float('F4')
        f5 = get_float('F5')
        f7 = get_float('F7')
        f9 = get_float('F9')
        f10 = get_float('F10')
        f11 = get_float('F11')

        m4 = get_float('M4')
        m5 = get_float('M5')
        m7 = get_float('M7')
        m8 = get_float('M8')
        m9 = get_float('M9')

        q4 = get_float('Q4')
        q5 = get_float('Q5')
        q7 = get_float('Q7')
        q8 = get_float('Q8')
        q9 = get_float('Q9')

        # Промежуточные
        b6 = b4 - b5
        f6 = f4 - f5
        m6 = m4 - m5
        q6 = q4 - q5

        b8 = b26 * b23
        f8 = b26 * c23
        b12 = b29 * b23
        f12 = b29 * c23

        # Итоговые метрики
        b13 = b6 - b7 - b8 - b9 - b10 - b11 - b12
        f13 = f6 - f7 - f8 - f9 - f10 - f11 - f12
        m10 = m6 - m7 - m8 - m9
        q10 = q6 - q7 - q8 - q9

        b35 = (b32 + b41) * 0.01
        b50 = (b44 + b47) * 0.01
        b38 = f13 - b35

        # Метрики для статистики
        b56 = get_float('B56')
        b59 = get_float('B59')

        wb_oborot_total = b44 + b47 + b32 + b41
        wb_oborot_carp = b44 + b47
        wb_oborot_hara = b32 + b41
        k_vyvodu_carp = b13 + m10
        k_vyvodu_hara = f13 + q10
        reklama_carp = b11
        reklama_hara = f11
        shtrafy = b10 + f10
        nalog_total = b35 + b50

        # === КОЛИЧЕСТВО ЗАКАЗОВ ПО БРЕНДАМ ===
        # Пытаемся извлечь количество из articles_data
        carp_orders = 0
        hara_orders = 0
        carp_orders_vyk = 0
        hara_orders_vyk = 0

        # Суммируем quantity по артикулам для каждого бренда
        if 'Цап царапкин' in articles_data:
            for art, data in articles_data['Цап царапкин'].get('sales', {}).items():
                carp_orders += data.get('quantity', 0)
            for art, data in articles_data['Цап царапкин'].get('vyk', {}).items():
                carp_orders_vyk += data.get('quantity', 0)
        if 'Harakiri' in articles_data:
            for art, data in articles_data['Harakiri'].get('sales', {}).items():
                hara_orders += data.get('quantity', 0)
            for art, data in articles_data['Harakiri'].get('vyk', {}).items():
                hara_orders_vyk += data.get('quantity', 0)

        # Сохраняем детальные данные в контекст (для кнопки)
        context.user_data['articles_data'] = articles_data
        context.user_data['current_period'] = values.get('B1', '')

        # === ФОРМИРУЕМ СООБЩЕНИЕ ===
        status = (
            "📊 **Статистика обработки:**\n\n"
            "• Основной отчет: ЦАП + HARAKIRI ✅\n"
            "• По выкупам: ЦАП + HARAKIRI ✅\n\n"
            f"💳 **Средний эквайринг по неделе:** {b56:,.2f} %\n"
            f"📊 **Медианный эквайринг по неделе:** {b59:,.2f} %\n\n"
            f"💰 **ВБшный оборот общий:** {wb_oborot_total:,.2f} ₽\n"
            f"   🐱 ЦАП: {wb_oborot_carp:,.2f} ₽\n"
            f"   ⚔️ Харакири: {wb_oborot_hara:,.2f} ₽\n\n"
            f"📦 **Количество заказов (осн):**\n"
            f"   🐱 ЦАП: {carp_orders} шт.\n"
            f"   ⚔️ Харакири: {hara_orders} шт.\n"
            f"📦 **Количество заказов (вык):**\n"
            f"   🐱 ЦАП: {carp_orders_vyk} шт.\n"
            f"   ⚔️ Харакири: {hara_orders_vyk} шт.\n\n"
            f"💸 **К выводу ЦАП:** {k_vyvodu_carp:,.2f} ₽\n"
            f"💸 **К выводу Харакири:** {k_vyvodu_hara:,.2f} ₽\n"
            f"💸 **Итого к выводу:** {k_vyvodu_carp + k_vyvodu_hara:,.2f} ₽\n"
            f"💸 **К выводу Харакири (с вычетом налога):** {b38:,.2f} ₽\n\n"
            f"📢 **Реклама за неделю:**\n"
            f"   🐱 ЦАП: {reklama_carp:,.2f} ₽\n"
            f"   ⚔️ Харакири: {reklama_hara:,.2f} ₽\n\n"
            f"⚠️ **Штрафы:** {shtrafy:,.2f} ₽\n"
            f"🧾 **Налог за неделю общий:** {nalog_total:,.2f} ₽\n\n"
            "✅ Отчет сохранен в историю"
        )

        await update.message.reply_text(status, parse_mode='Markdown')

        # Отправляем кнопку для деталей по артикулам
        keyboard = [
            [InlineKeyboardButton("📦 Детали по артикулам", callback_data="show_articles")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Нажмите кнопку, чтобы увидеть детальную статистику по артикулам:",
            reply_markup=reply_markup
        )

        # Очистка временных файлов
        try:
            os.remove(template_file)
            os.remove(osn_file)
            os.remove(vyk_file)
        except:
            pass
        context.user_data['files'] = {}
        context.user_data['current_file'] = None
        context.user_data['current_file_hash'] = None

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def articles_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Детали по артикулам'"""
    query = update.callback_query
    await query.answer()
    
    articles_data = context.user_data.get('articles_data', {})
    period = context.user_data.get('current_period', '')
    
    if not articles_data:
        await query.edit_message_text("❌ Нет данных по артикулам для этого отчета.")
        return
    
    # Формируем сообщение
    message = f"📦 **Детальная статистика по артикулам**\nПериод: {period}\n\n"
    
    # Сортируем артикулы по выручке (убывание) и показываем топ-10
    all_articles = []
    for bren, data in articles_data.items():
        for art, stats in data.get('sales', {}).items():
            all_articles.append({
                'brand': bren,
                'article': art,
                'quantity': stats.get('quantity', 0),
                'revenue': stats.get('revenue', 0),
                'return_quantity': stats.get('return_quantity', 0),
                'return_revenue': stats.get('return_revenue', 0)
            })
    
    # Сортируем по выручке
    all_articles.sort(key=lambda x: x['revenue'], reverse=True)
    
    # Ограничим 10
    top_articles = all_articles[:10]
    
    if not top_articles:
        await query.edit_message_text("❌ Нет данных по артикулам.")
        return
    
    for item in top_articles:
        message += (
            f"**{item['brand']}** — {item['article']}\n"
            f"   Продано: {item['quantity']} шт. | Выручка: {item['revenue']:,.2f} ₽\n"
            f"   Возвраты: {item['return_quantity']} шт. | Сумма возвратов: {item['return_revenue']:,.2f} ₽\n\n"
        )
    
    if len(all_articles) > 10:
        message += f"… и еще {len(all_articles) - 10} артикулов. Используйте `/articles` для полного списка."
    
    await query.edit_message_text(message, parse_mode='Markdown')

async def articles_full_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /articles — выводит полный список артикулов (можно добавить пагинацию)"""
    articles_data = context.user_data.get('articles_data', {})
    period = context.user_data.get('current_period', '')
    
    if not articles_data:
        await update.message.reply_text("❌ Нет данных по артикулам. Сначала загрузите отчет.")
        return
    
    # Аналогично, но без ограничения
    message = f"📦 **Полная статистика по артикулам**\nПериод: {period}\n\n"
    all_articles = []
    for bren, data in articles_data.items():
        for art, stats in data.get('sales', {}).items():
            all_articles.append({
                'brand': bren,
                'article': art,
                'quantity': stats.get('quantity', 0),
                'revenue': stats.get('revenue', 0),
                'return_quantity': stats.get('return_quantity', 0),
                'return_revenue': stats.get('return_revenue', 0)
            })
    
    all_articles.sort(key=lambda x: x['revenue'], reverse=True)
    
    if not all_articles:
        await update.message.reply_text("❌ Нет данных.")
        return
    
    for item in all_articles:
        message += (
            f"**{item['brand']}** — {item['article']}\n"
            f"   Продано: {item['quantity']} шт. | Выручка: {item['revenue']:,.2f} ₽\n"
            f"   Возвраты: {item['return_quantity']} шт. | Сумма возвратов: {item['return_revenue']:,.2f} ₽\n\n"
        )
    
    # Telegram имеет ограничение на длину сообщения ~4096 символов, обрезаем если нужно
    if len(message) > 4000:
        message = message[:3900] + "\n… (сообщение обрезано)"
    
    await update.message.reply_text(message, parse_mode='Markdown')

# ===== ЗАПУСК =====
def main():
    print("🤖 Запускаю Telegram бот...")
    run_flask()
    print("✅ Flask сервер запущен")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Установка меню команд через post_init
    async def set_commands(app_instance):
        commands = [
            BotCommand("start", "Начать работу с ботом"),
            BotCommand("help", "Помощь и список команд"),
            BotCommand("osn", "Отметить файл как основной отчет"),
            BotCommand("vyk", "Отметить файл как отчет по выкупам"),
            BotCommand("history", "Показать все загруженные отчеты"),
            BotCommand("stats", "Показать общую статистику"),
            BotCommand("delete", "Удалить отчет из истории"),
            BotCommand("articles", "Показать полную статистику по артикулам"),
        ]
        await app_instance.bot.set_my_commands(commands)
        print("✅ Меню команд установлено")

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
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    print("✅ Бот запущен и ждет сообщений...")
    app.run_polling(allowed_updates=[])

if __name__ == "__main__":
    main()
