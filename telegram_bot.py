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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

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

logging.basicConfig(level=logging.INFO)
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
            hara_sales NUMERIC DEFAULT 0,
            carp_vyk_sales NUMERIC DEFAULT 0,
            hara_vyk_sales NUMERIC DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

try:
    init_db()
except Exception as e:
    logger.error(f"❌ Ошибка инициализации БД: {e}")

# ===== ФУНКЦИИ БД =====
def calculate_file_hash(file_path):
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            md5.update(chunk)
    return md5.hexdigest()

def is_duplicate(file_hash):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT id, file_name, date_period FROM reports WHERE file_hash = ?', (file_hash,))
        result = cursor.fetchone()
        conn.close()
        return result
    except:
        return None

def save_report(file_name, file_hash, date_period, values):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO reports (file_name, file_hash, date_period, carp_sales, hara_sales, carp_vyk_sales, hara_vyk_sales)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            file_name, file_hash, date_period,
            values.get('carp_sales', 0),
            values.get('hara_sales', 0),
            values.get('carp_vyk_sales', 0),
            values.get('hara_vyk_sales', 0)
        ))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def get_all_reports():
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT id, file_name, date_period, processed_at, carp_sales, hara_sales, carp_vyk_sales, hara_vyk_sales FROM reports ORDER BY processed_at DESC')
        result = cursor.fetchall()
        conn.close()
        return result
    except:
        return []

def get_stats():
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*), AVG(carp_sales), AVG(hara_sales), SUM(carp_sales), SUM(hara_sales) FROM reports')
        result = cursor.fetchone()
        conn.close()
        return result
    except:
        return None

# ===== ОПРЕДЕЛЕНИЕ ТИПА =====
def detect_type(filename):
    name = filename.lower()
    if 'осн' in name or 'osn' in name:
        return 'osn'
    elif 'вык' in name or 'vyk' in name:
        return 'vyk'
    return None

# ===== FLASK =====
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return "OK", 200

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
    def process(self, osn_path, vyk_path):
        df_osn = pd.read_excel(osn_path)
        df_vyk = pd.read_excel(vyk_path)

        # Извлечение даты
        filename = Path(osn_path).name
        match = re.search(r'(\d{1,2})\.(\d{2})-(\d{1,2})\.(\d{2})', filename)
        date_period = f"{match.group(1)}.{match.group(2)}-{match.group(3)}.{match.group(4)}" if match else datetime.now().strftime("%d.%m")

        # Вычисление метрик
        carp_sales = df_osn[((df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа'] == 'Продажа')]['К перечислению Продавцу за реализованный Товар'].sum()
        hara_sales = df_osn[(df_osn['Бренд'] == 'Harakiri') & (df_osn['Тип документа'] == 'Продажа')]['К перечислению Продавцу за реализованный Товар'].sum()
        carp_vyk = df_vyk[(df_vyk['Бренд'] == 'Цап царапкин') & (df_vyk['Тип документа'] == 'Продажа')]['К перечислению Продавцу за реализованный Товар'].sum()
        hara_vyk = df_vyk[(df_vyk['Бренд'] == 'Harakiri') & (df_vyk['Тип документа'] == 'Продажа')]['К перечислению Продавцу за реализованный Товар'].sum()

        values = {
            'carp_sales': carp_sales,
            'hara_sales': hara_sales,
            'carp_vyk_sales': carp_vyk,
            'hara_vyk_sales': hara_vyk,
            'date_period': date_period
        }
        return values

# ===== КОМАНДЫ =====
async def start(update: Update, context):
    await update.message.reply_text("👋 Бот работает! Отправь файл с 'осн' или 'вык' в названии.")

async def history(update: Update, context):
    reports = get_all_reports()
    if not reports:
        await update.message.reply_text("📭 История пуста.")
        return
    msg = "📊 История:\n"
    for r in reports[:5]:
        msg += f"📄 {r[1]} ({r[2]}) - ЦАП: {r[4]:.2f}, Харакири: {r[5]:.2f}\n"
    await update.message.reply_text(msg)

async def stats(update: Update, context):
    stats_data = get_stats()
    if not stats_data or stats_data[0] == 0:
        await update.message.reply_text("📭 Нет данных.")
        return
    total, avg_carp, avg_hara, sum_carp, sum_hara = stats_data
    msg = f"📊 Статистика:\nВсего отчетов: {total}\nСредние: ЦАП {avg_carp:.2f}, Харакири {avg_hara:.2f}\nИтого: ЦАП {sum_carp:.2f}, Харакири {sum_hara:.2f}"
    await update.message.reply_text(msg)

async def delete_cmd(update: Update, context):
    await update.message.reply_text("🗑️ Удаление пока не реализовано в упрощённой версии.")

async def handle_file(update: Update, context):
    try:
        doc = update.message.document
        if not doc.file_name.endswith(('.xlsx', '.xls')):
            await update.message.reply_text("❌ Нужен Excel файл")
            return
        file = await context.bot.get_file(doc.file_id)
        path = TEMP_DIR / doc.file_name
        await file.download_to_drive(path)

        h = calculate_file_hash(path)
        dup = is_duplicate(h)
        if dup:
            await update.message.reply_text(f"⚠️ Отчет уже был: {dup[1]}")
            return

        if 'files' not in context.user_data:
            context.user_data['files'] = {}

        t = detect_type(doc.file_name)
        if t:
            context.user_data['files'][t] = str(path)
            await update.message.reply_text(f"✅ {t.upper()} отчет получен")
            if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
                await process_and_send(update, context)
        else:
            context.user_data['current_file'] = str(path)
            await update.message.reply_text("❓ Тип не определен, используй /osn или /vyk")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def process_and_send(update: Update, context):
    try:
        osn_file = context.user_data['files']['osn']
        vyk_file = context.user_data['files']['vyk']
        processor = ReportProcessor()
        values = processor.process(osn_file, vyk_file)

        # Сохраняем в БД
        h = calculate_file_hash(Path(osn_file))
        save_report(Path(osn_file).name, h, values['date_period'], values)

        # Отправляем сообщение
        msg = (
            f"📊 **Статистика обработки:**\n\n"
            f"📅 Период: {values['date_period']}\n"
            f"🐱 ЦАП (осн): {values['carp_sales']:,.2f} ₽\n"
            f"⚔️ Харакири (осн): {values['hara_sales']:,.2f} ₽\n"
            f"🐱 ЦАП (вык): {values['carp_vyk_sales']:,.2f} ₽\n"
            f"⚔️ Харакири (вык): {values['hara_vyk_sales']:,.2f} ₽\n"
            "✅ Отчет сохранен в историю"
        )
        await update.message.reply_text(msg, parse_mode='Markdown')

        # Очистка
        context.user_data['files'] = {}
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def handle_osn(update: Update, context):
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправьте файл!")
        return
    context.user_data['files']['osn'] = context.user_data['current_file']
    await update.message.reply_text("✅ Основной отчет сохранен!")
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)

async def handle_vyk(update: Update, context):
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправьте файл!")
        return
    context.user_data['files']['vyk'] = context.user_data['current_file']
    await update.message.reply_text("✅ Отчет по выкупам сохранен!")
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)

# ===== ЗАПУСК =====
def main():
    print("🤖 Запускаю бота...")
    run_flask()
    print("✅ Flask запущен")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Установка команд
    async def set_commands(app_instance):
        commands = [
            BotCommand("start", "Начать работу"),
            BotCommand("history", "История отчетов"),
            BotCommand("stats", "Статистика"),
            BotCommand("osn", "Отметить файл как основной"),
            BotCommand("vyk", "Отметить файл как отчет по выкупам"),
        ]
        await app_instance.bot.set_my_commands(commands)
        print("✅ Меню установлено")
    app.post_init = set_commands

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("osn", handle_osn))
    app.add_handler(CommandHandler("vyk", handle_vyk))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    print("✅ Бот готов")
    app.run_polling()

if __name__ == "__main__":
    main()
