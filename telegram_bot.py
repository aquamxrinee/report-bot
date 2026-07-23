#!/usr/bin/env python3
"""
ФИНАЛЬНЫЙ Telegram бот для обработки еженедельных отчетов
Деплой на Railway (бесплатно, 24/7)
С SQLite базой данных и защитой от дубликатов
Поддержка постоянного тома (Volume) для сохранения данных
АВТОМАТИЧЕСКОЕ РАСПОЗНАВАНИЕ ТИПА ОТЧЕТА
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
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ===== НАСТРОЙКИ =====
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ Токен не найден! Установите переменную TELEGRAM_BOT_TOKEN в Railway")

# ===== ПУТИ ДЛЯ ХРАНЕНИЯ ДАННЫХ =====
DATA_DIR = Path("/data")
TEMP_DIR = DATA_DIR / "temp"
DB_PATH = DATA_DIR / "reports.db"

if not DATA_DIR.exists():
    print("⚠️ Том /data/ не найден! Использую временное хранилище /tmp/")
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

# ===== ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ =====
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
    logger.info("✅ База данных подключена")
except Exception as e:
    logger.error(f"❌ Ошибка при инициализации БД: {e}")

# ===== РАБОТА С БАЗОЙ ДАННЫХ =====
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
        logger.info(f"✅ Отчет сохранен в БД: {file_name}")
        return True
    except sqlite3.IntegrityError:
        logger.warning(f"⚠️ Отчет уже существует в БД: {file_name}")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения в БД: {e}")
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

def get_report_stats():
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 
                COUNT(*) as total_reports,
                AVG(carp_sales) as avg_carp_sales,
                AVG(hara_sales) as avg_hara_sales,
                AVG(carp_vyk_sales) as avg_carp_vyk,
                AVG(hara_vyk_sales) as avg_hara_vyk,
                SUM(carp_sales) as total_carp_sales,
                SUM(hara_sales) as total_hara_sales
            FROM reports
        ''')
        result = cursor.fetchone()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        return None

# ===== ОПРЕДЕЛЕНИЕ ТИПА ОТЧЕТА ПО ИМЕНИ ФАЙЛА =====
def detect_report_type(filename):
    """
    Определяет тип отчета по имени файла
    Возвращает: 'osn' или 'vyk' или None
    """
    filename_lower = filename.lower()
    
    if 'осн' in filename_lower or 'osn' in filename_lower:
        return 'osn'
    elif 'вык' in filename_lower or 'vyk' in filename_lower:
        return 'vyk'
    else:
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

# ===== КЛАСС ДЛЯ ОБРАБОТКИ ОТЧЕТОВ =====
class ReportProcessor:
    def process_files(self, osn_path, vyk_path, template_path):
        try:
            df_osn = pd.read_excel(osn_path)
            df_vyk = pd.read_excel(vyk_path)
            
            filename = Path(osn_path).name
            logger.info(f"📄 Имя файла: {filename}")
            
            match = re.search(r'(\d{1,2})\.(\d{2})-(\d{1,2})\.(\d{2})', filename)
            if match:
                date_range = f"{match.group(1)}.{match.group(2)}-{match.group(3)}.{match.group(4)}"
                logger.info(f"✅ Дата извлечена из имени файла: {date_range}")
            else:
                date_range = datetime.now().strftime("%d.%m")
                logger.warning(f"⚠️ Дата НЕ найдена в имени файла. Использую текущую: {date_range}")
            
            values = self._calculate_all_values(df_osn, df_vyk, date_range)
            self._fill_template(template_path, values)
            
            return True, values
        except Exception as e:
            logger.error(f"Ошибка обработки: {e}")
            return False, str(e)
    
    def _calculate_all_values(self, df_osn, df_vyk, date_range):
        values = {'B1': date_range, 'F1': date_range}
        
        filter_carp_sale = ((df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа'] == 'Продажа')
        values['B4'] = df_osn[filter_carp_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_return = df_osn['Тип документа'] == 'Возврат'
        values['B5'] = df_osn[filter_return]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_carp_all = (df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())
        values['B7'] = df_osn[filter_carp_all]['Услуги по доставке товара покупателю'].sum()
        values['B9'] = df_osn[filter_carp_all]['Операции на приемке'].sum()
        values['B10'] = df_osn['Общая сумма штрафов'].sum()
        values['B11'] = df_osn[filter_carp_all]['Удержания'].sum()
        values['B26'] = df_osn[filter_carp_all]['Хранение'].sum()
        values['B29'] = df_osn[filter_carp_all]['Разовое изменение срока перечисления денежных средств'].sum()
        values['B44'] = df_osn['Цена розничная'].sum()
        values['B32'] = df_osn[filter_carp_all]['Цена розничная'].sum()
        
        filter_hara_sale = (df_osn['Бренд'] == 'Harakiri') & (df_osn['Тип документа'] == 'Продажа')
        values['F4'] = df_osn[filter_hara_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        values['F5'] = df_osn[filter_return]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_hara_all = df_osn['Бренд'] == 'Harakiri'
        values['F7'] = df_osn[filter_hara_all]['Услуги по доставке товара покупателю'].sum()
        values['F9'] = df_osn[filter_hara_all]['Операции на приемке'].sum()
        values['F10'] = df_osn[filter_hara_all]['Общая сумма штрафов'].sum()
        values['F11'] = df_osn[filter_hara_all]['Удержания'].sum()
        
        filter_carp_vyk_sale = ((df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())) & (df_vyk['Тип документа'] == 'Продажа')
        values['M4'] = df_vyk[filter_carp_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_return_vyk = df_vyk['Тип документа'] == 'Возврат'
        values['M5'] = df_vyk[filter_return_vyk]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_carp_vyk_all = (df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())
        values['M7'] = df_vyk[filter_carp_vyk_all]['Услуги по доставке товара покупателю'].sum()
        values['M8'] = df_vyk[filter_carp_vyk_all]['Операции на приемке'].sum()
        values['M9'] = df_vyk['Общая сумма штрафов'].sum()
        values['B47'] = df_vyk['Цена розничная'].sum()
        
        filter_hara_vyk_sale = (df_vyk['Бренд'] == 'Harakiri') & (df_vyk['Тип документа'] == 'Продажа')
        values['Q4'] = df_vyk[filter_hara_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        values['Q5'] = df_vyk[filter_return_vyk]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_hara_vyk_all = df_vyk['Бренд'] == 'Harakiri'
        values['Q7'] = df_vyk[filter_hara_vyk_all]['Услуги по доставке товара покупателю'].sum()
        values['Q8'] = df_vyk[filter_hara_vyk_all]['Операции на приемке'].sum()
        values['Q9'] = df_vyk[filter_hara_vyk_all]['Общая сумма штрафов'].sum()
        values['B41'] = df_vyk[filter_hara_vyk_all]['Цена розничная'].sum()
        
        return values
    
    def _fill_template(self, template_path, values):
        if str(template_path).startswith("/app/"):
            raise ValueError("❌ НЕЛЬЗЯ сохранять в /app/! Это read-only папка!")
        
        wb = openpyxl.load_workbook(template_path, data_only=False, keep_links=False, keep_vba=False)
        ws = wb.active
        
        for cell, value in values.items():
            ws[cell] = value
            if isinstance(value, float) and value != int(value):
                ws[cell].number_format = '0.00'
        
        ws.sheet_view.calcMode = 'manual'
        wb.save(template_path)
        logger.info(f"Шаблон сохранен: {template_path}")

# ===== ОБРАБОТЧИКИ КОМАНД =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для обработки еженедельных отчетов.\n\n"
        "📤 Как пользоваться:\n"
        "1️⃣ Отправь файл с названием, содержащим 'осн' (основной) или 'вык' (по выкупам)\n"
        "2️⃣ Бот автоматически определит тип и попросит второй файл\n"
        "3️⃣ Готово! Получишь заполненный шаблон! ✅\n\n"
        "📊 Команды аналитики:\n"
        "/history - показать все загруженные отчеты\n"
        "/stats - показать общую статистику по отчетам\n\n"
        "Если автоопределение не сработало, используй /osn или /vyk вручную."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 Команды:\n"
        "/start - начать\n"
        "/help - помощь\n"
        "/osn - отметить файл как основной (ручное управление)\n"
        "/vyk - отметить файл как отчет по выкупам (ручное управление)\n"
        "/history - показать все загруженные отчеты\n"
        "/stats - показать общую статистику по отчетам\n\n"
        "📁 Автоопределение:\n"
        "Файлы с 'осн' в названии → основной отчет\n"
        "Файлы с 'вык' в названии → отчет по выкупам"
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        document = update.message.document
        logger.info(f"📥 Получен файл: {document.file_name}")
        
        if not document.file_name.endswith(('.xlsx', '.xls')):
            await update.message.reply_text("❌ Нужен Excel файл (.xlsx или .xls)")
            return
        
        file = await context.bot.get_file(document.file_id)
        file_path = TEMP_DIR / document.file_name
        await file.download_to_drive(file_path)
        
        file_hash = calculate_file_hash(file_path)
        duplicate = is_file_duplicate(file_hash)
        
        if duplicate:
            dup_id, dup_name, dup_date, dup_time = duplicate
            await update.message.reply_text(
                f"⚠️ Этот отчет уже был загружен ранее!\n\n"
                f"📄 Имя: {dup_name}\n"
                f"📅 Период: {dup_date}\n"
                f"🕐 Загружен: {dup_time}\n\n"
                f"Пожалуйста, отправьте другой файл."
            )
            try:
                os.remove(file_path)
            except:
                pass
            return
        
        if 'files' not in context.user_data:
            context.user_data['files'] = {}
        
        context.user_data['current_file'] = str(file_path)
        context.user_data['current_file_hash'] = file_hash
        
        report_type = detect_report_type(document.file_name)
        logger.info(f"🔍 Определен тип: {report_type}")
        
        if report_type:
            context.user_data['files'][report_type] = str(file_path)
            if report_type == 'osn':
                context.user_data['osn_hash'] = file_hash
                await update.message.reply_text(
                    f"📄 Файл получен: {document.file_name}\n"
                    f"✅ Автоматически определен как **ОСНОВНОЙ** отчет\n\n"
                    f"📤 Теперь отправьте отчет **по выкупам** (в названии должно быть 'вык')"
                )
            else:
                context.user_data['vyk_hash'] = file_hash
                await update.message.reply_text(
                    f"📄 Файл получен: {document.file_name}\n"
                    f"✅ Автоматически определен как отчет **ПО ВЫКУПАМ**\n\n"
                    f"📤 Теперь отправьте **ОСНОВНОЙ** отчет (в названии должно быть 'осн')"
                )
            
            if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
                await process_and_send(update, context)
        else:
            await update.message.reply_text(
                f"📄 Файл получен: {document.file_name}\n\n"
                "❓ Не удалось автоматически определить тип отчета.\n"
                "Пожалуйста, укажите вручную:\n"
                "/osn - Основной отчет\n"
                "/vyk - Отчет по выкупам"
            )
    except Exception as e:
        logger.error(f"❌ Ошибка при загрузке файла: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def handle_osn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправь файл!")
        return
    
    context.user_data['files']['osn'] = context.user_data['current_file']
    context.user_data['osn_hash'] = context.user_data['current_file_hash']
    await update.message.reply_text("✅ Основной отчет сохранен!\nТеперь отправь отчет по выкупам...")
    
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)

async def handle_vyk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправь файл!")
        return
    
    context.user_data['files']['vyk'] = context.user_data['current_file']
    context.user_data['vyk_hash'] = context.user_data['current_file_hash']
    await update.message.reply_text("✅ Отчет по выкупам сохранен!\nТеперь отправь основной отчет...")
    
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
            for path in possible_paths:
                if path.exists():
                    original_template = path
                    break
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        template_file = TEMP_DIR / f"шаблон_{timestamp}.xlsx"
        
        if original_template.exists():
            shutil.copy(original_template, template_file)
            logger.info(f"Шаблон скопирован из {original_template}")
        else:
            await update.message.reply_text("⚠️ Шаблон не найден. Создаю новый...")
            wb = openpyxl.Workbook()
            wb.save(template_file)
        
        processor = ReportProcessor()
        success, result = processor.process_files(osn_file, vyk_file, str(template_file))
        
        if success:
            saved = save_report_to_db(
                file_name=Path(osn_file).name,
                file_hash=osn_hash if osn_hash else calculate_file_hash(osn_file),
                date_period=result.get('B1', ''),
                values=result
            )
            
            with open(template_file, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    caption="✅ Готово! Шаблон заполнен и готов к скачиванию."
                )
            
            status_message = (
                "📊 Статистика обработки:\n"
                "• Основной отчет: ЦАП + HARAKIRI ✅\n"
                "• По выкупам: ЦАП + HARAKIRI ✅\n"
                "• Ячеек заполнено: 31 ✅\n"
            )
            
            if saved:
                status_message += "• Отчет сохранен в историю ✅\n"
            else:
                status_message += "• Отчет уже был в истории (дубликат) ⚠️\n"
            
            status_message += "\nСпасибо за использование! 🚀"
            await update.message.reply_text(status_message)
            
            try:
                if template_file.exists():
                    os.remove(template_file)
                if osn_file and Path(osn_file).exists():
                    os.remove(osn_file)
                if vyk_file and Path(vyk_file).exists():
                    os.remove(vyk_file)
            except Exception as e:
                logger.warning(f"Не удалось удалить временные файлы: {e}")
            
            context.user_data['files'] = {}
            context.user_data['current_file'] = None
            context.user_data['current_file_hash'] = None
        else:
            await update.message.reply_text(f"❌ Ошибка обработки: {result}")
    
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await update.message.reply_text(f"❌ Критическая ошибка: {str(e)}")

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reports = get_all_reports()
    
    if not reports:
        await update.message.reply_text("📭 История пуста. Загрузите первый отчет!")
        return
    
    message = "📊 **История загруженных отчетов:**\n\n"
    for report in reports[:10]:
        id_, name, period, processed_at, carp_sales, hara_sales, carp_vyk, hara_vyk = report
        message += f"📄 **{name}**\n"
        message += f"   📅 Период: {period}\n"
        message += f"   🕐 Загружен: {processed_at[:16]}\n"
        message += f"   💰 Продажи ЦАП: {carp_sales:,.2f} ₽\n"
        message += f"   💰 Продажи Harakiri: {hara_sales:,.2f} ₽\n\n"
    
    if len(reports) > 10:
        message += f"… и еще {len(reports) - 10} отчетов. Всего: {len(reports)}"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_report_stats()
    
    if not stats or stats[0] == 0:
        await update.message.reply_text("📭 Нет данных для статистики. Загрузите отчеты!")
        return
    
    total, avg_carp, avg_hara, avg_carp_vyk, avg_hara_vyk, total_carp, total_hara = stats
    
    message = f"📊 **Общая статистика по отчетам:**\n\n"
    message += f"📄 Всего отчетов: **{total}**\n\n"
    message += f"**Продажи (средние):**\n"
    message += f"   🐱 ЦАП Царапкин: **{avg_carp:,.2f} ₽**\n"
    message += f"   ⚔️ Harakiri: **{avg_hara:,.2f} ₽**\n\n"
    message += f"**Продажи по выкупам (средние):**\n"
    message += f"   🐱 ЦАП Царапкин: **{avg_carp_vyk:,.2f} ₽**\n"
    message += f"   ⚔️ Harakiri: **{avg_hara_vyk:,.2f} ₽**\n\n"
    message += f"**Итого продаж:**\n"
    message += f"   🐱 ЦАП Царапкин: **{total_carp:,.2f} ₽**\n"
    message += f"   ⚔️ Harakiri: **{total_hara:,.2f} ₽**\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')


# ===== ЗАПУСК БОТА =====
def main():
    print("🤖 Запускаю Telegram бот...")
    run_flask()
    print("✅ Flask сервер запущен для пингов")
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("osn", handle_osn))
    app.add_handler(CommandHandler("vyk", handle_vyk))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    
    print("✅ Бот запущен и ждет сообщений...")
    app.run_polling(allowed_updates=[])

if __name__ == '__main__':
    main()
