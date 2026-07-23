#!/usr/bin/env python3
"""
ФИНАЛЬНЫЙ Telegram бот для обработки еженедельных отчетов
Деплой на Railway (бесплатно, 24/7)
С SQLite базой данных и защитой от дубликатов
Поддержка постоянного тома (Volume) для сохранения данных
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
# Токен берем из переменных окружения Railway
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ Токен не найден! Установите переменную TELEGRAM_BOT_TOKEN в Railway")

# ===== ПУТИ ДЛЯ ХРАНЕНИЯ ДАННЫХ =====
# Для постоянного хранения используем /data/ (том в Railway)
DATA_DIR = Path("/data")
TEMP_DIR = DATA_DIR / "temp"
DB_PATH = DATA_DIR / "reports.db"

# Если том не примонтирован, используем /tmp/ как запасной вариант
if not DATA_DIR.exists():
    print("⚠️ Том /data/ не найден! Использую временное хранилище /tmp/")
    DATA_DIR = Path("/tmp/telegram_data")
    TEMP_DIR = DATA_DIR / "temp"
    DB_PATH = DATA_DIR / "reports.db"

# Создаем необходимые папки
DATA_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

# Настройка логирования
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
    """Создает таблицу для хранения отчетов, если она не существует"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT NOT NULL,
            file_hash TEXT UNIQUE NOT NULL,
            date_period TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            -- Основной отчет - ЦАП
            carp_sales NUMERIC DEFAULT 0,
            carp_returns NUMERIC DEFAULT 0,
            carp_delivery NUMERIC DEFAULT 0,
            carp_receiving NUMERIC DEFAULT 0,
            carp_fines NUMERIC DEFAULT 0,
            carp_withholding NUMERIC DEFAULT 0,
            carp_storage NUMERIC DEFAULT 0,
            carp_one_time_change NUMERIC DEFAULT 0,
            carp_retail_price NUMERIC DEFAULT 0,
            -- Основной отчет - HARAKIRI
            hara_sales NUMERIC DEFAULT 0,
            hara_returns NUMERIC DEFAULT 0,
            hara_delivery NUMERIC DEFAULT 0,
            hara_receiving NUMERIC DEFAULT 0,
            hara_fines NUMERIC DEFAULT 0,
            hara_withholding NUMERIC DEFAULT 0,
            -- Выкупы - ЦАП
            carp_vyk_sales NUMERIC DEFAULT 0,
            carp_vyk_returns NUMERIC DEFAULT 0,
            carp_vyk_delivery NUMERIC DEFAULT 0,
            carp_vyk_receiving NUMERIC DEFAULT 0,
            carp_vyk_fines NUMERIC DEFAULT 0,
            carp_vyk_retail_price NUMERIC DEFAULT 0,
            -- Выкупы - HARAKIRI
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

# Инициализируем БД при старте
try:
    init_db()
    logger.info("✅ База данных подключена")
except Exception as e:
    logger.error(f"❌ Ошибка при инициализации БД: {e}")

# ===== РАБОТА С БАЗОЙ ДАННЫХ =====
def calculate_file_hash(file_path):
    """Вычисляет MD5 хеш файла"""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def is_file_duplicate(file_hash):
    """Проверяет, есть ли файл с таким хешем в БД"""
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
    """Сохраняет отчет в БД"""
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
            file_name, 
            file_hash, 
            date_period,
            values.get('B4', 0), 
            values.get('B5', 0), 
            values.get('B7', 0), 
            values.get('B9', 0),
            values.get('B10', 0), 
            values.get('B11', 0), 
            values.get('B26', 0), 
            values.get('B29', 0), 
            values.get('B32', 0),
            values.get('F4', 0), 
            values.get('F5', 0), 
            values.get('F7', 0), 
            values.get('F9', 0), 
            values.get('F10', 0), 
            values.get('F11', 0),
            values.get('M4', 0), 
            values.get('M5', 0), 
            values.get('M7', 0), 
            values.get('M8', 0),
            values.get('M9', 0), 
            values.get('B47', 0),
            values.get('Q4', 0), 
            values.get('Q5', 0), 
            values.get('Q7', 0), 
            values.get('Q8', 0), 
            values.get('Q9', 0),
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
    """Получает все отчеты из БД"""
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
    """Получает статистику по всем отчетам"""
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

# ===== FLASK ДЛЯ ПИНГОВ (чтобы бот не засыпал) =====
flask_app = Flask(__name__)

@flask_app.route("/")
def health_check():
    """Эндпоинт для проверки здоровья бота"""
    return "🤖 Бот работает!", 200

@flask_app.route("/ping")
def ping():
    """Эндпоинт для пингов от UptimeRobot/Kuma"""
    return "pong", 200

def run_flask():
    """Запускает Flask в отдельном потоке"""
    from threading import Thread
    def _run():
        flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
    Thread(target=_run, daemon=True).start()

# ===== КЛАСС ДЛЯ ОБРАБОТКИ ОТЧЕТОВ =====
class ReportProcessor:
    """Обрабатывает отчеты и заполняет шаблон"""
    
    def process_files(self, osn_path, vyk_path, template_path):
        """Основной метод обработки"""
        try:
            df_osn = pd.read_excel(osn_path)
            df_vyk = pd.read_excel(vyk_path)
            
            # Парсим дату из названия файла осн
            filename = Path(osn_path).name
            logger.info(f"📄 Имя файла: {filename}")
            
            # Новый паттерн: ищем дату в формате ДД.ММ-ДД.ММ
            match = re.search(r'(\d{1,2})\.(\d{2})-(\d{1,2})\.(\d{2})', filename)
            if match:
                date_range = f"{match.group(1)}.{match.group(2)}-{match.group(3)}.{match.group(4)}"
                logger.info(f"✅ Дата извлечена из имени файла: {date_range}")
            else:
                date_range = datetime.now().strftime("%d.%m")
                logger.warning(f"⚠️ Дата НЕ найдена в имени файла. Использую текущую: {date_range}")
            
            # Вычисляем все значения
            values = self._calculate_all_values(df_osn, df_vyk, date_range)
            
            # Заполняем шаблон
            self._fill_template(template_path, values)
            
            return True, values
        except Exception as e:
            logger.error(f"Ошибка обработки: {e}")
            return False, str(e)
    
    def _calculate_all_values(self, df_osn, df_vyk, date_range):
        """Вычисляет все значения"""
        values = {'B1': date_range, 'F1': date_range}
        
        # ===== ОСНОВНОЙ ОТЧЕТ - ЦАП ЦАРАПКИН =====
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
        
        # ===== B32 (основной отчет - ЦАП ЦАРАПКИН) =====
        values['B32'] = df_osn[filter_carp_all]['Цена розничная'].sum()
        
        # ===== ОСНОВНОЙ ОТЧЕТ - HARAKIRI =====
        filter_hara_sale = (df_osn['Бренд'] == 'Harakiri') & (df_osn['Тип документа'] == 'Продажа')
        values['F4'] = df_osn[filter_hara_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        values['F5'] = df_osn[filter_return]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_hara_all = df_osn['Бренд'] == 'Harakiri'
        values['F7'] = df_osn[filter_hara_all]['Услуги по доставке товара покупателю'].sum()
        values['F9'] = df_osn[filter_hara_all]['Операции на приемке'].sum()
        values['F10'] = df_osn[filter_hara_all]['Общая сумма штрафов'].sum()
        values['F11'] = df_osn[filter_hara_all]['Удержания'].sum()
        
        # ===== ПО ВЫКУПАМ - ЦАП ЦАРАПКИН =====
        filter_carp_vyk_sale = ((df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())) & (df_vyk['Тип документа'] == 'Продажа')
        values['M4'] = df_vyk[filter_carp_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_return_vyk = df_vyk['Тип документа'] == 'Возврат'
        values['M5'] = df_vyk[filter_return_vyk]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_carp_vyk_all = (df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())
        values['M7'] = df_vyk[filter_carp_vyk_all]['Услуги по доставке товара покупателю'].sum()
        values['M8'] = df_vyk[filter_carp_vyk_all]['Операции на приемке'].sum()
        values['M9'] = df_vyk['Общая сумма штрафов'].sum()
        values['B47'] = df_vyk['Цена розничная'].sum()
        
        # ===== ПО ВЫКУПАМ - HARAKIRI =====
        filter_hara_vyk_sale = (df_vyk['Бренд'] == 'Harakiri') & (df_vyk['Тип документа'] == 'Продажа')
        values['Q4'] = df_vyk[filter_hara_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        
        values['Q5'] = df_vyk[filter_return_vyk]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_hara_vyk_all = df_vyk['Бренд'] == 'Harakiri'
        values['Q7'] = df_vyk[filter_hara_vyk_all]['Услуги по доставке товара покупателю'].sum()
        values['Q8'] = df_vyk[filter_hara_vyk_all]['Операции на приемке'].sum()
        values['Q9'] = df_vyk[filter_hara_vyk_all]['Общая сумма штрафов'].sum()
        
        # ===== B41 берется из df_vyk (по выкупам) для Harakiri =====
        values['B41'] = df_vyk[filter_hara_vyk_all]['Цена розничная'].sum()
        
        return values
    
    def _fill_template(self, template_path, values):
        """Заполняет шаблон значениями, сохраняя формулы"""
        # Проверка: нельзя сохранять в /app/
        if str(template_path).startswith("/app/"):
            raise ValueError("❌ НЕЛЬЗЯ сохранять в /app/! Это read-only папка!")
        
        # Загружаем шаблон, НЕ вычисляя формулы
        wb = openpyxl.load_workbook(
            template_path,
            data_only=False,
            keep_links=False,
            keep_vba=False
        )
        ws = wb.active
        
        # Заполняем значения
        for cell, value in values.items():
            ws[cell] = value
            if isinstance(value, float) and value != int(value):
                ws[cell].number_format = '0.00'
        
        # Отключаем авто-пересчёт при открытии
        ws.sheet_view.calcMode = 'manual'
        
        # Сохраняем
        wb.save(template_path)
        logger.info(f"Шаблон сохранен: {template_path}")


# ===== ОБРАБОТЧИКИ КОМАНД =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text(
        "👋 Привет! Я бот для обработки еженедельных отчетов.\n\n"
        "📤 Как пользоваться:\n"
        "1️⃣ Отправь мне первый файл отчета\n"
        "2️⃣ Я спрошу: это основной или по выкупам?\n"
        "3️⃣ Напиши /osn или /vyk\n"
        "4️⃣ Отправь второй файл\n"
        "5️⃣ Напиши тип второго файла\n"
        "6️⃣ Готово! Получишь заполненный шаблон! ✅\n\n"
        "📊 Команды аналитики:\n"
        "/history - показать все загруженные отчеты\n"
        "/stats - показать общую статистику по отчетам\n\n"
        "⚠️ Файлы можно называть как угодно - просто указываешь тип!"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    await update.message.reply_text(
        "📋 Команды:\n"
        "/start - начать\n"
        "/help - помощь\n"
        "/osn - текущий файл это основной отчет\n"
        "/vyk - текущий файл это отчет по выкупам\n"
        "/history - показать все загруженные отчеты\n"
        "/stats - показать общую статистику по отчетам\n\n"
        "Просто отправляй файлы и указывай тип! 📁"
    )


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик загруженных файлов"""
    try:
        document = update.message.document
        
        if not document.file_name.endswith(('.xlsx', '.xls')):
            await update.message.reply_text("❌ Нужен Excel файл (.xlsx или .xls)")
            return
        
        # Скачиваем файл
        file = await context.bot.get_file(document.file_id)
        file_path = TEMP_DIR / document.file_name
        await file.download_to_drive(file_path)
        
        # Вычисляем хеш файла для проверки дубликатов
        file_hash = calculate_file_hash(file_path)
        duplicate = is_file_duplicate(file_hash)
        
        if duplicate:
            # Файл уже был загружен ранее
            dup_id, dup_name, dup_date, dup_time = duplicate
            await update.message.reply_text(
                f"⚠️ Этот отчет уже был загружен ранее!\n\n"
                f"📄 Имя: {dup_name}\n"
                f"📅 Период: {dup_date}\n"
                f"🕐 Загружен: {dup_time}\n\n"
                f"Пожалуйста, отправьте другой файл."
            )
            # Удаляем временный файл
            try:
                os.remove(file_path)
            except:
                pass
            return
        
        # Инициализируем контекст пользователя
        if 'files' not in context.user_data:
            context.user_data['files'] = {}
        
        # Сохраняем текущий файл и его хеш
        context.user_data['current_file'] = str(file_path)
        context.user_data['current_file_hash'] = file_hash
        
        await update.message.reply_text(
            f"📄 Файл получен: {document.file_name}\n\n"
            "Какой это отчет?\n"
            "/osn - Основной отчет\n"
            "/vyk - Отчет по выкупам"
        )
    except Exception as e:
        logger.error(f"Ошибка при загрузке файла: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")


async def handle_osn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /osn - это основной отчет"""
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправь файл!")
        return
    
    context.user_data['files']['osn'] = context.user_data['current_file']
    context.user_data['osn_hash'] = context.user_data['current_file_hash']
    await update.message.reply_text("✅ Основной отчет сохранен!\nТеперь отправь отчет по выкупам...")
    
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)


async def handle_vyk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /vyk - это отчет по выкупам"""
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправь файл!")
        return
    
    context.user_data['files']['vyk'] = context.user_data['current_file']
    context.user_data['vyk_hash'] = context.user_data['current_file_hash']
    await update.message.reply_text("✅ Отчет по выкупам сохранен!\nТеперь отправь основной отчет...")
    
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)


async def process_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает оба отчета и отправляет результат"""
    try:
        await update.message.reply_text("⏳ Обрабатываю отчеты...")
        
        osn_file = context.user_data['files']['osn']
        vyk_file = context.user_data['files']['vyk']
        osn_hash = context.user_data.get('osn_hash')
        vyk_hash = context.user_data.get('vyk_hash')
        
        # ВАЖНО: Используем оригинальный шаблон из /app/
        original_template = Path("/app/шаблон.xlsx")
        
        # Если шаблона нет, ищем в других местах
        if not original_template.exists():
            possible_paths = [
                Path("шаблон.xlsx"),
                TEMP_DIR / "template.xlsx",
            ]
            for path in possible_paths:
                if path.exists():
                    original_template = path
                    break
        
        # Создаем уникальное имя для временного файла
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        template_file = TEMP_DIR / f"шаблон_{timestamp}.xlsx"
        
        if original_template.exists():
            shutil.copy(original_template, template_file)
            logger.info(f"Шаблон скопирован из {original_template}")
        else:
            await update.message.reply_text("⚠️ Шаблон не найден. Создаю новый...")
            wb = openpyxl.Workbook()
            wb.save(template_file)
        
        # Обрабатываем
        processor = ReportProcessor()
        success, result = processor.process_files(osn_file, vyk_file, str(template_file))
        
        if success:
            # Сохраняем отчет в БД (используем хеш основного файла)
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
            
            # Удаляем временные файлы
            try:
                if template_file.exists():
                    os.remove(template_file)
                if osn_file and Path(osn_file).exists():
                    os.remove(osn_file)
                if vyk_file and Path(vyk_file).exists():
                    os.remove(vyk_file)
            except Exception as e:
                logger.warning(f"Не удалось удалить временные файлы: {e}")
            
            # Очищаем контекст пользователя
            context.user_data['files'] = {}
            context.user_data['current_file'] = None
            context.user_data['current_file_hash'] = None
        else:
            await update.message.reply_text(f"❌ Ошибка обработки: {result}")
    
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await update.message.reply_text(f"❌ Критическая ошибка: {str(e)}")


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /history - показывает все загруженные отчеты"""
    reports = get_all_reports()
    
    if not reports:
        await update.message.reply_text("📭 История пуста. Загрузите первый отчет!")
        return
    
    message = "📊 **История загруженных отчетов:**\n\n"
    for report in reports[:10]:  # Показываем последние 10
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
    """Команда /stats - показывает общую статистику по отчетам"""
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
    """Запуск бота"""
    print("🤖 Запускаю Telegram бот...")
    
    # Запускаем Flask для пингов
    run_flask()
    print("✅ Flask сервер запущен для пингов")
    
    # Создаем приложение Telegram
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("osn", handle_osn))
    app.add_handler(CommandHandler("vyk", handle_vyk))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    
    print("✅ Бот запущен и ждет сообщений...")
    
    # Запускаем бота
    app.run_polling(allowed_updates=[])


if __name__ == '__main__':
    main()
