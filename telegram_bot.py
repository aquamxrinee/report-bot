#!/usr/bin/env python3
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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
    print("⚠️ Том /data/ не найден! Использую /tmp/")
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
    logger.info("✅ База данных подключена")
except Exception as e:
    logger.error(f"❌ Ошибка БД: {e}")

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
            FROM reports ORDER BY processed_at DESC
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
        logger.error(f"Ошибка статистики: {e}")
        return None

# ===== ОПРЕДЕЛЕНИЕ ТИПА =====
def detect_report_type(filename):
    filename_lower = filename.lower()
    if 'осн' in filename_lower or 'osn' in filename_lower:
        return 'osn'
    elif 'вык' in filename_lower or 'vyk' in filename_lower:
        return 'vyk'
    return None

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
            return values
        except Exception as e:
            logger.error(f"Ошибка обработки: {e}")
            raise

    def _calculate_all_values(self, df_osn, df_vyk, date_range):
        """Вычисляет все значения с правильными фильтрами"""
        values = {'B1': date_range, 'F1': date_range}

        # ============================================================
        # ОСНОВНОЙ ОТЧЕТ (df_osn) — ЦАП ЦАРАПКИН
        # ============================================================
        
        # Продажи ЦАП (B4)
        filter_carp_sale = ((df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа'] == 'Продажа')
        values['B4'] = df_osn[filter_carp_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        
        # Возвраты ЦАП (B5)
        filter_carp_return = ((df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа'] == 'Возврат')
        values['B5'] = df_osn[filter_carp_return]['К перечислению Продавцу за реализованный Товар'].sum()
        
        # Прочие показатели ЦАП
        filter_carp_all = (df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())
        values['B7'] = df_osn[filter_carp_all]['Услуги по доставке товара покупателю'].sum()
        values['B9'] = df_osn[filter_carp_all]['Операции на приемке'].sum()
        values['B10'] = df_osn['Общая сумма штрафов'].sum()
        values['B11'] = df_osn[filter_carp_all]['Удержания'].sum()
        values['B26'] = df_osn[filter_carp_all]['Хранение'].sum()
        values['B29'] = df_osn[filter_carp_all]['Разовое изменение срока перечисления денежных средств'].sum()
        values['B44'] = df_osn['Цена розничная'].sum()
        values['B32'] = df_osn[filter_carp_all]['Цена розничная'].sum()

        # ============================================================
        # ОСНОВНОЙ ОТЧЕТ (df_osn) — HARAKIRI
        # ============================================================
        
        # Продажи Harakiri (F4)
        filter_hara_sale = (df_osn['Бренд'] == 'Harakiri') & (df_osn['Тип документа'] == 'Продажа')
        values['F4'] = df_osn[filter_hara_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        
        # Возвраты Harakiri (F5)
        filter_hara_return = (df_osn['Бренд'] == 'Harakiri') & (df_osn['Тип документа'] == 'Возврат')
        values['F5'] = df_osn[filter_hara_return]['К перечислению Продавцу за реализованный Товар'].sum()
        
        # Прочие показатели Harakiri
        filter_hara_all = df_osn['Бренд'] == 'Harakiri'
        values['F7'] = df_osn[filter_hara_all]['Услуги по доставке товара покупателю'].sum()
        values['F9'] = df_osn[filter_hara_all]['Операции на приемке'].sum()
        values['F10'] = df_osn[filter_hara_all]['Общая сумма штрафов'].sum()
        values['F11'] = df_osn[filter_hara_all]['Удержания'].sum()

        # ============================================================
        # ОТЧЕТ ПО ВЫКУПАМ (df_vyk) — ЦАП ЦАРАПКИН
        # ============================================================
        
        # M4: Продажи ЦАП (выкупы) — фильтр по бренду и типу "Продажа"
        filter_carp_vyk_sale = (df_vyk['Бренд'] == 'Цап царапкин') & (df_vyk['Тип документа'] == 'Продажа')
        values['M4'] = df_vyk[filter_carp_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        
        # M5: Возвраты ЦАП (выкупы)
        filter_carp_vyk_return = (df_vyk['Бренд'] == 'Цап царапкин') & (df_vyk['Тип документа'] == 'Возврат')
        values['M5'] = df_vyk[filter_carp_vyk_return]['К перечислению Продавцу за реализованный Товар'].sum()
        
        # M7: Доставка ЦАП (выкупы) — фильтр только по бренду (все типы документов)
        filter_carp_vyk_all = (df_vyk['Бренд'] == 'Цап царапкин')
        values['M7'] = df_vyk[filter_carp_vyk_all]['Услуги по доставке товара покупателю'].sum()
        
        # M8: Приемка ЦАП (выкупы)
        values['M8'] = df_vyk[filter_carp_vyk_all]['Операции на приемке'].sum()
        
        # M9: Штрафы общие (по выкупам) — без фильтра по бренду
        values['M9'] = df_vyk['Общая сумма штрафов'].sum()
        
        # B47: Цена розничная ЦАП (выкупы)
        values['B47'] = df_vyk[filter_carp_vyk_all]['Цена розничная'].sum()

        # ============================================================
        # ОТЧЕТ ПО ВЫКУПАМ (df_vyk) — HARAKIRI
        # ============================================================
        
        # Q4: Продажи Harakiri (выкупы) — фильтр по бренду и типу "Продажа"
        filter_hara_vyk_sale = (df_vyk['Бренд'] == 'Harakiri') & (df_vyk['Тип документа'] == 'Продажа')
        values['Q4'] = df_vyk[filter_hara_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        
        # Q5: Возвраты Harakiri (выкупы)
        filter_hara_vyk_return = (df_vyk['Бренд'] == 'Harakiri') & (df_vyk['Тип документа'] == 'Возврат')
        values['Q5'] = df_vyk[filter_hara_vyk_return]['К перечислению Продавцу за реализованный Товар'].sum()
        
        # Q7: Доставка Harakiri (выкупы) — фильтр только по бренду (все типы документов)
        filter_hara_vyk_all = (df_vyk['Бренд'] == 'Harakiri')
        values['Q7'] = df_vyk[filter_hara_vyk_all]['Услуги по доставке товара покупателю'].sum()
        
        # Q8: Приемка Harakiri (выкупы)
        values['Q8'] = df_vyk[filter_hara_vyk_all]['Операции на приемке'].sum()
        
        # Q9: Штрафы Harakiri (выкупы)
        values['Q9'] = df_vyk[filter_hara_vyk_all]['Общая сумма штрафов'].sum()
        
        # B41: Цена розничная Harakiri (выкупы)
        values['B41'] = df_vyk[filter_hara_vyk_all]['Цена розничная'].sum()

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
        "👋 Привет! Я бот для обработки отчетов.\n\n"
        "📤 Отправь файл с 'осн' или 'вык' в названии\n"
        "📊 Команды: /history, /stats, /delete"
    )

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reports = get_all_reports()
    if not reports:
        await update.message.reply_text("📭 История пуста.")
        return
    msg = "📊 История загруженных отчетов:\n\n"
    for r in reports[:10]:
        msg += f"📄 {r[1]}\n   Период: {r[2]}\n   Продажи ЦАП: {r[4]:,.2f} ₽\n\n"
    if len(reports) > 10:
        msg += f"… и еще {len(reports) - 10} отчетов"
    await update.message.reply_text(msg)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reports = get_all_reports()
    if not reports:
        await update.message.reply_text("📭 Нет данных для статистики.")
        return
    total = len(reports)
    total_carp = sum(r[4] for r in reports)
    avg_carp = total_carp / total if total > 0 else 0
    
    msg = f"📊 Общая статистика:\n\n"
    msg += f"📄 Всего отчетов: {total}\n"
    msg += f"💰 Итого продаж ЦАП: {total_carp:,.2f} ₽\n"
    msg += f"📈 Средние продажи: {avg_carp:,.2f} ₽"
    await update.message.reply_text(msg)

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reports = get_all_reports()
    if not reports:
        await update.message.reply_text("📭 Нет отчетов для удаления.")
        return
    keyboard = []
    for report in reports[:10]:
        report_id, name, period, processed_at, carp_sales, hara_sales, carp_vyk, hara_vyk = report
        short_name = name[:25] + "..." if len(name) > 25 else name
        keyboard.append([InlineKeyboardButton(f"❌ {short_name} ({period})", callback_data=f"delete_{report_id}")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="delete_cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🗑️ Выберите отчет для удаления:", reply_markup=reply_markup)

async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "delete_cancel":
        await query.edit_message_text("✅ Удаление отменено.")
        return
    if data.startswith("delete_"):
        report_id = int(data.split("_")[1])
        if delete_report(report_id):
            await query.edit_message_text(f"✅ Отчет #{report_id} удален.")
        else:
            await query.edit_message_text(f"❌ Ошибка удаления.")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        doc = update.message.document
        if not doc.file_name.endswith(('.xlsx', '.xls')):
            await update.message.reply_text("❌ Нужен Excel файл")
            return
        
        file = await context.bot.get_file(doc.file_id)
        path = TEMP_DIR / doc.file_name
        await file.download_to_drive(path)
        logger.info(f"✅ Файл скачан: {path}")
        
        file_hash = calculate_file_hash(path)
        duplicate = is_file_duplicate(file_hash)
        if duplicate:
            await update.message.reply_text(f"⚠️ Этот отчет уже был загружен ранее!\n📄 {duplicate[1]}")
            try:
                os.remove(path)
            except:
                pass
            return
        
        if 'files' not in context.user_data:
            context.user_data['files'] = {}
        
        report_type = detect_report_type(doc.file_name)
        logger.info(f"🔍 Определен тип: {report_type}")
        
        if report_type:
            context.user_data['files'][report_type] = str(path)
            if report_type == 'osn':
                await update.message.reply_text(f"✅ Основной отчет получен!\n📤 Теперь отправьте отчет по выкупам ('вык')")
            else:
                await update.message.reply_text(f"✅ Отчет по выкупам получен!\n📤 Теперь отправьте основной отчет ('осн')")
            
            if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
                await process_and_send(update, context)
        else:
            context.user_data['current_file'] = str(path)
            await update.message.reply_text(
                f"❓ Не удалось определить тип отчета.\n"
                f"Пожалуйста, укажите вручную:\n"
                f"/osn - Основной отчет\n"
                f"/vyk - Отчет по выкупам"
            )
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def process_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("⏳ Обрабатываю отчеты...")
        
        osn_file = context.user_data['files']['osn']
        vyk_file = context.user_data['files']['vyk']
        
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
        values = processor.process_files(osn_file, vyk_file, str(template_file))
        
        # Сохраняем в БД
        osn_hash = calculate_file_hash(Path(osn_file))
        saved = save_report_to_db(
            file_name=Path(osn_file).name,
            file_hash=osn_hash,
            date_period=values.get('B1', ''),
            values=values
        )
        
        with open(template_file, 'rb') as f:
            await update.message.reply_document(
                document=f,
                caption="✅ Готово! Шаблон заполнен и готов к скачиванию."
            )
        
        status = "📊 Статистика обработки:\n"
        status += "• Основной отчет: ЦАП + HARAKIRI ✅\n"
        status += "• По выкупам: ЦАП + HARAKIRI ✅\n"
        status += f"• Ячеек заполнено: {len(values)} ✅\n"
        status += "• Отчет сохранен в историю ✅\n" if saved else "• Дубликат ⚠️\n"
        status += "\nСпасибо за использование! 🚀"
        await update.message.reply_text(status)
        
        # Удаляем временные файлы
        try:
            if template_file.exists():
                os.remove(template_file)
            if Path(osn_file).exists():
                os.remove(osn_file)
            if Path(vyk_file).exists():
                os.remove(vyk_file)
        except:
            pass
        
        context.user_data['files'] = {}
        context.user_data['current_file'] = None
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def handle_osn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправь файл!")
        return
    context.user_data['files']['osn'] = context.user_data['current_file']
    await update.message.reply_text("✅ Основной отчет сохранен!")
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)

async def handle_vyk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправь файл!")
        return
    context.user_data['files']['vyk'] = context.user_data['current_file']
    await update.message.reply_text("✅ Отчет по выкупам сохранен!")
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)

# ===== ЗАПУСК =====
def main():
    print("🤖 Запускаю Telegram бот...")
    run_flask()
    print("✅ Flask сервер запущен")
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("osn", handle_osn))
    app.add_handler(CommandHandler("vyk", handle_vyk))
    app.add_handler(CallbackQueryHandler(delete_callback, pattern="^delete_"))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    
    print("✅ Бот запущен и ждет сообщений...")
    app.run_polling(allowed_updates=[])

if __name__ == "__main__":
    main()
