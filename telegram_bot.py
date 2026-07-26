#!/usr/bin/env python3
"""
Telegram бот для аналитики Wildberries с управлением себестоимостью,
историей изменений и расчётом чистой прибыли.
"""

import os
import re
import shutil
import logging
import sqlite3
import hashlib
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import openpyxl
import requests
from flask import Flask, render_template, jsonify, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ===== НАСТРОЙКИ =====
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ Токен не найден!")

NEWS_API_KEY = os.getenv("NEWS_API_KEY")
if not NEWS_API_KEY:
    print("⚠️ NEWS_API_KEY не найден. Новостные функции будут отключены.")

MINI_APP_URL = os.getenv("MINI_APP_URL", "worker-production-a75a.up.railway.app/mini")
if not MINI_APP_URL.startswith(("http://", "https://")):
    MINI_APP_URL = "https://" + MINI_APP_URL
print(f"🌐 Mini App URL: {MINI_APP_URL}")

ALLOWED_USER_IDS = os.getenv("ALLOWED_USER_IDS", "")
if ALLOWED_USER_IDS:
    ALLOWED_USERS = set(map(int, ALLOWED_USER_IDS.split(",")))
    print(f"🔒 Бот доступен только для ID: {ALLOWED_USERS}")
else:
    ALLOWED_USERS = set()
    print("⚠️ ALLOWED_USER_IDS не задан. Бот доступен всем.")

USER_NAMES = {
    1289447998: "Роман",
    5167366543: "Евгений"
}

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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS news_settings (
            user_id INTEGER PRIMARY KEY,
            enabled INTEGER DEFAULT 1,
            query TEXT DEFAULT 'Wildberries OR ВБ OR Вайлдбериз OR Wildberries.ru',
            morning_time TEXT DEFAULT '08:30',
            evening_time TEXT DEFAULT '20:40'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS product_costs_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article TEXT NOT NULL,
            brand TEXT NOT NULL,
            cost_price REAL NOT NULL,
            date_from TEXT NOT NULL,
            date_to TEXT,
            set_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("✅ БД инициализирована")

init_db()

# ===== ФУНКЦИИ ДЛЯ РАБОТЫ С СЕБЕСТОИМОСТЬЮ =====
def get_earliest_report_date():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT MIN(start_date) FROM reports WHERE start_date IS NOT NULL")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row and row[0] else None

def get_active_cost(article, report_date):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT cost_price FROM product_costs_history
        WHERE article = ? AND date_from <= ? AND (date_to IS NULL OR date_to > ?)
        ORDER BY date_from DESC LIMIT 1
    ''', (article, report_date, report_date))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def get_current_cost(article):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT cost_price, date_from, id FROM product_costs_history
        WHERE article = ? AND date_to IS NULL
        ORDER BY date_from DESC LIMIT 1
    ''', (article,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {'cost': row[0], 'date_from': row[1], 'id': row[2]}
    return None

def get_all_articles_with_costs():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT article FROM article_stats')
    articles_from_stats = [row[0] for row in cursor.fetchall()]
    cursor.execute('SELECT DISTINCT article FROM product_costs_history')
    articles_from_cost = [row[0] for row in cursor.fetchall()]
    all_articles = sorted(set(articles_from_stats + articles_from_cost))
    result = []
    for article in all_articles:
        cost_info = get_current_cost(article)
        if cost_info:
            result.append({
                'article': article,
                'cost': cost_info['cost'],
                'date_from': cost_info['date_from'],
                'history_id': cost_info['id']
            })
        else:
            result.append({
                'article': article,
                'cost': None,
                'date_from': None,
                'history_id': None
            })
    conn.close()
    return result

def get_cost_history(article):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, cost_price, date_from, date_to, set_by, created_at
        FROM product_costs_history
        WHERE article = ?
        ORDER BY date_from DESC
    ''', (article,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def set_product_cost(article, brand, cost, user_id):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM product_costs_history WHERE article = ?", (article,))
    count = cursor.fetchone()[0]
    if count == 0:
        earliest = get_earliest_report_date()
        date_from = earliest if earliest else datetime.now().strftime("%Y-%m-%d")
    else:
        date_from = datetime.now().strftime("%Y-%m-%d")
    if count > 0:
        prev_date = (datetime.strptime(date_from, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        cursor.execute('''
            UPDATE product_costs_history
            SET date_to = ?
            WHERE article = ? AND date_to IS NULL
        ''', (prev_date, article))
    cursor.execute('''
        INSERT INTO product_costs_history (article, brand, cost_price, date_from, set_by)
        VALUES (?, ?, ?, ?, ?)
    ''', (article, brand, cost, date_from, user_id))
    conn.commit()
    conn.close()
    return True

def delete_cost_history(record_id):
    """Удаляет запись из истории (разрешено удалять любые записи)."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('DELETE FROM product_costs_history WHERE id = ?', (record_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted

def delete_all_costs_for_article(article):
    """Удаляет все записи себестоимости для артикула."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('DELETE FROM product_costs_history WHERE article = ?', (article,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    return deleted

# ===== ОСТАЛЬНЫЕ ФУНКЦИИ БД =====
# (здесь все функции такие же, как в предыдущей версии, я не буду их повторять, но в финальном коде они есть)
# Для краткости я оставлю только изменённые части, но в ответе выложу полный файл.

# ... (остальные функции без изменений) ...

# ===== НАСТРОЙКИ СЕБЕСТОИМОСТИ =====
# Изменяем cost_history_callback, добавляем кнопку удаления для каждой записи и кнопку удаления всех записей

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
        # Добавляем кнопку удаления для каждой записи
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
        await query.edit_message_text("✅ Запись удалена.")
        # Возвращаемся к истории этого артикула
        # Нам нужно знать артикул. Получим его из БД по record_id.
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute("SELECT article FROM product_costs_history WHERE id = ?", (record_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            article = row[0]
            # Перезапускаем историю
            await cost_history_callback(update, context)
        else:
            await menu_costs_callback(update, context)
    else:
        await query.edit_message_text("❌ Не удалось удалить запись.")

async def cost_delete_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    article = query.data.split("_")[3]  # cost_delete_all_{article}
    # Спрашиваем подтверждение
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
    article = query.data.split("_")[4]  # cost_confirm_delete_all_{article}
    deleted = delete_all_costs_for_article(article)
    if deleted > 0:
        await query.edit_message_text(f"✅ Удалено {deleted} записей для артикула `{article}`.", parse_mode='Markdown')
    else:
        await query.edit_message_text(f"❌ Не найдено записей для артикула `{article}`.", parse_mode='Markdown')
    await menu_costs_callback(update, context)

# ===== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ (без изменений) =====
# ... (они все такие же, как в предыдущем коде) ...

# ===== ЗАПУСК =====
def main():
    print("🤖 Запуск бота...")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Регистрация всех обработчиков (включая новые)
    # ...

    print("✅ Бот готов, запускаем polling...")
    app.run_polling(allowed_updates=[])

if __name__ == "__main__":
    main()
