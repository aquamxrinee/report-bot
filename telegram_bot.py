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
    # Таблица для хранения истории себестоимости
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
def get_active_cost(article, report_date):
    """Возвращает себестоимость для артикула на указанную дату (формат YYYY-MM-DD)."""
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
    """Возвращает текущую активную себестоимость для артикула (без учёта даты)."""
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
    """Возвращает список всех уникальных артикулов из article_stats и product_costs_history
       с текущей себестоимостью и датой последнего изменения."""
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
    """Возвращает полную историю изменений себестоимости для артикула (все записи)."""
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
    """Устанавливает новую себестоимость для артикула.
       Закрывает текущую активную запись (date_to = сегодня) и создаёт новую с date_from = сегодня."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    # Закрываем активную запись, если есть
    cursor.execute('''
        UPDATE product_costs_history
        SET date_to = ?
        WHERE article = ? AND date_to IS NULL
    ''', (today, article))
    # Создаём новую запись
    cursor.execute('''
        INSERT INTO product_costs_history (article, brand, cost_price, date_from, set_by)
        VALUES (?, ?, ?, ?, ?)
    ''', (article, brand, cost, today, user_id))
    conn.commit()
    conn.close()
    return True

def delete_cost_history(record_id):
    """Удаляет запись из истории (только если она не активная)."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT date_to FROM product_costs_history WHERE id = ?', (record_id,))
    row = cursor.fetchone()
    if row and row[0] is not None:
        cursor.execute('DELETE FROM product_costs_history WHERE id = ?', (record_id,))
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False

# ===== ОСТАЛЬНЫЕ ФУНКЦИИ БД =====
def calculate_file_hash(file_path):
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def get_report_id_by_period(start_date, end_date):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM reports WHERE start_date = ? AND end_date = ?', (start_date, end_date))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except:
        return None

def save_report_to_db(file_name, file_hash, date_period, start_date, end_date, values, metrics, articles):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO reports (file_name, file_hash, date_period, start_date, end_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (file_name, file_hash, date_period, start_date, end_date))
        report_id = cursor.lastrowid
        logger.info(f"✅ Отчет вставлен, ID: {report_id}")

        values_inserted = 0
        if values:
            for cell, val in values.items():
                try:
                    cursor.execute('''
                        INSERT INTO report_values (report_id, cell_name, cell_value)
                        VALUES (?, ?, ?)
                    ''', (report_id, cell, float(val)))
                    values_inserted += 1
                except:
                    pass
            logger.info(f"📊 Вставлено {values_inserted} значений ячеек")

        metrics_inserted = 0
        if metrics:
            for mname, mval in metrics.items():
                try:
                    cursor.execute('''
                        INSERT INTO report_metrics (report_id, metric_name, metric_value)
                        VALUES (?, ?, ?)
                    ''', (report_id, mname, float(mval)))
                    metrics_inserted += 1
                except:
                    pass
            logger.info(f"📊 Вставлено {metrics_inserted} записей метрик")

        if articles:
            inserted = 0
            for brand, data in articles.items():
                all_arts = {}
                for art, stats in data.get('sales', {}).items():
                    if art not in all_arts:
                        all_arts[art] = {'quantity': 0, 'revenue': 0}
                    all_arts[art]['quantity'] += stats.get('quantity', 0)
                    all_arts[art]['revenue'] += stats.get('revenue', 0)
                for art, stats in data.get('vyk', {}).items():
                    if art not in all_arts:
                        all_arts[art] = {'quantity': 0, 'revenue': 0}
                    all_arts[art]['quantity'] += stats.get('quantity', 0)
                    all_arts[art]['revenue'] += stats.get('revenue', 0)
                for art, stats in all_arts.items():
                    cursor.execute('''
                        INSERT INTO article_stats (report_id, brand, article, quantity, revenue)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (report_id, brand, art, stats['quantity'], stats['revenue']))
                    inserted += 1
            logger.info(f"📦 Вставлено {inserted} записей артикулов")
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

def delete_reports(report_ids):
    if not report_ids:
        return 0
    deleted = 0
    for rid in report_ids:
        if delete_report(rid):
            deleted += 1
    return deleted

def get_all_reports(page=0, per_page=10):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM reports')
        total = cursor.fetchone()[0]
        offset = page * per_page
        cursor.execute('''
            SELECT id, file_name, date_period, start_date, end_date, processed_at
            FROM reports ORDER BY start_date DESC LIMIT ? OFFSET ?
        ''', (per_page, offset))
        results = cursor.fetchall()
        conn.close()
        return results, total
    except:
        return [], 0

def get_all_report_ids():
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM reports ORDER BY start_date DESC')
        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows]
    except:
        return []

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

def get_previous_report_id(report_id):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id
            FROM reports
            WHERE start_date < (SELECT start_date FROM reports WHERE id = ?)
            ORDER BY start_date DESC
            LIMIT 1
        ''', (report_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    except:
        return None

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

def get_aggregated_metrics():
    """Возвращает суммарные метрики по всем отчётам для мини-приложения, включая прибыль."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('''
            SELECT
                COUNT(DISTINCT report_id) as total_reports,
                SUM(CASE WHEN metric_name = 'wb_total' THEN metric_value ELSE 0 END) as wb_total,
                SUM(CASE WHEN metric_name = 'wb_carp' THEN metric_value ELSE 0 END) as wb_carp,
                SUM(CASE WHEN metric_name = 'wb_hara' THEN metric_value ELSE 0 END) as wb_hara,
                AVG(CASE WHEN metric_name = 'avg_acquiring' THEN metric_value ELSE NULL END) as avg_acquiring,
                SUM(CASE WHEN metric_name = 'total_profit' THEN metric_value ELSE 0 END) as total_profit,
                AVG(CASE WHEN metric_name = 'margin' THEN metric_value ELSE NULL END) as avg_margin
            FROM report_metrics
        ''')
        row = cursor.fetchone()
        conn.close()
        if row and row[0] is not None:
            return {
                'total_reports': row[0] or 0,
                'wb_total': row[1] or 0,
                'wb_carp': row[2] or 0,
                'wb_hara': row[3] or 0,
                'avg_acquiring': row[4] or 0,
                'total_profit': row[5] or 0,
                'avg_margin': row[6] or 0
            }
        else:
            return {
                'total_reports': 0,
                'wb_total': 0,
                'wb_carp': 0,
                'wb_hara': 0,
                'avg_acquiring': 0,
                'total_profit': 0,
                'avg_margin': 0
            }
    except Exception as e:
        logger.error(f"Ошибка агрегации метрик: {e}")
        return {
            'total_reports': 0,
            'wb_total': 0,
            'wb_carp': 0,
            'wb_hara': 0,
            'avg_acquiring': 0,
            'total_profit': 0,
            'avg_margin': 0
        }

# ===== НАСТРОЙКИ НОВОСТЕЙ =====
def get_news_settings(user_id):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT enabled, query, morning_time, evening_time FROM news_settings WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {'enabled': bool(row[0]), 'query': row[1], 'morning_time': row[2], 'evening_time': row[3]}
        else:
            return {'enabled': True, 'query': 'Wildberries OR ВБ OR Вайлдбериз OR Wildberries.ru', 'morning_time': '08:30', 'evening_time': '20:40'}
    except:
        return {'enabled': True, 'query': 'Wildberries OR ВБ OR Вайлдбериз OR Wildberries.ru', 'morning_time': '08:30', 'evening_time': '20:40'}

def set_news_settings(user_id, enabled=None, query=None, morning_time=None, evening_time=None):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM news_settings WHERE user_id = ?', (user_id,))
        if cursor.fetchone():
            updates = []
            params = []
            if enabled is not None:
                updates.append("enabled = ?")
                params.append(1 if enabled else 0)
            if query is not None:
                updates.append("query = ?")
                params.append(query)
            if morning_time is not None:
                updates.append("morning_time = ?")
                params.append(morning_time)
            if evening_time is not None:
                updates.append("evening_time = ?")
                params.append(evening_time)
            if updates:
                params.append(user_id)
                cursor.execute(f"UPDATE news_settings SET {', '.join(updates)} WHERE user_id = ?", params)
        else:
            cursor.execute('''
                INSERT INTO news_settings (user_id, enabled, query, morning_time, evening_time)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, 1 if enabled is None else (1 if enabled else 0),
                  query or 'Wildberries OR ВБ OR Вайлдбериз OR Wildberries.ru',
                  morning_time or '08:30', evening_time or '20:40'))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения настроек новостей: {e}")
        return False

# ===== НОВОСТИ =====
NEWS_CACHE = {}
CACHE_EXPIRY = timedelta(hours=2)

def fetch_news(query, limit=10):
    if not NEWS_API_KEY:
        return []
    cache_key = query
    now = datetime.now()
    if cache_key in NEWS_CACHE and now - NEWS_CACHE[cache_key]['timestamp'] < CACHE_EXPIRY:
        return NEWS_CACHE[cache_key]['articles'][:limit]

    url = 'https://newsapi.org/v2/everything'
    params = {
        'q': query,
        'apiKey': NEWS_API_KEY,
        'pageSize': limit,
        'language': 'ru',
        'sortBy': 'publishedAt'
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get('status') == 'ok':
            articles = data.get('articles', [])
            articles = [a for a in articles if a.get('title')]
            NEWS_CACHE[cache_key] = {'articles': articles, 'timestamp': now}
            return articles[:limit]
        else:
            logger.error(f"NewsAPI error: {data.get('message')}")
            return []
    except Exception as e:
        logger.error(f"Ошибка запроса к NewsAPI: {e}")
        return []

def format_news_digest(articles, prefix="📰 **Новости**"):
    if not articles:
        return "Нет свежих новостей по вашей теме."
    digest = f"{prefix}\n\n"
    for i, article in enumerate(articles, 1):
        title = article.get('title', '')
        source = article.get('source', {}).get('name', '')
        url = article.get('url', '')
        published = article.get('publishedAt', '')
        if published:
            try:
                dt = datetime.fromisoformat(published.replace('Z', '+00:00'))
                published = dt.strftime('%H:%M')
            except:
                published = ''
        digest += f"{i}. [{title}]({url}) — *{source}*"
        if published:
            digest += f" ({published})"
        digest += "\n"
    return digest

# ===== ПЛАНИРОВЩИК =====
scheduler = BackgroundScheduler()

async def send_news_digest(context, user_id, time_of_day):
    settings = get_news_settings(user_id)
    if not settings['enabled']:
        return
    query = settings['query']
    articles = fetch_news(query, limit=10)
    prefix = f"🌅 **Утренняя сводка**" if time_of_day == 'morning' else f"🌇 **Вечерняя сводка**"
    text = format_news_digest(articles, prefix)
    try:
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')
        logger.info(f"Новостная сводка ({time_of_day}) отправлена пользователю {user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки новостей пользователю {user_id}: {e}")

async def scheduled_morning_digest(context):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM news_settings WHERE enabled = 1')
        users = cursor.fetchall()
        conn.close()
    except:
        users = []
    for (user_id,) in users:
        await send_news_digest(context, user_id, 'morning')

async def scheduled_evening_digest(context):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM news_settings WHERE enabled = 1')
        users = cursor.fetchall()
        conn.close()
    except:
        users = []
    for (user_id,) in users:
        await send_news_digest(context, user_id, 'evening')

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

# ===== FLASK (Mini App) =====
flask_app = Flask(__name__, template_folder='templates')

@flask_app.before_request
def log_request_info():
    logger.info(f"📥 Запрос: {request.method} {request.path} от {request.remote_addr}")

@flask_app.after_request
def after_request(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@flask_app.route("/")
def health_check():
    return "🤖 Бот работает!", 200

@flask_app.route("/ping")
def ping():
    return "pong", 200

@flask_app.route('/mini')
def mini_app():
    logger.info(f"Запрос /mini от {request.remote_addr}")
    return render_template('dashboard.html')

@flask_app.route('/api/stats')
def api_stats():
    logger.info(f"Запрос /api/stats от {request.remote_addr}")
    try:
        data = get_aggregated_metrics()
        for key in ['total_reports', 'wb_total', 'wb_carp', 'wb_hara', 'avg_acquiring', 'total_profit', 'avg_margin']:
            if key not in data or not isinstance(data[key], (int, float)):
                data[key] = 0
        logger.info(f"API stats OK: {data}")
        return jsonify(data)
    except Exception as e:
        logger.error(f"Ошибка в /api/stats: {e}")
        return jsonify({'error': str(e)}), 500

@flask_app.route('/api/debug')
def debug_db():
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM reports")
        reports_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM report_metrics")
        metrics_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM article_stats")
        articles_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM product_costs_history")
        costs_count = cursor.fetchone()[0]
        conn.close()
        return jsonify({
            'reports_count': reports_count,
            'metrics_count': metrics_count,
            'articles_count': articles_count,
            'costs_count': costs_count
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

# ===== АВТОРИЗАЦИЯ =====
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
                    revenue=('Цена розничная', 'sum')
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

        # ===== ОСНОВНОЙ ОТЧЕТ - ЦАП ЦАРАПКИН (продажи) =====
        mask_carp_sale = ((df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа'] == 'Продажа')
        values['B4'] = df_osn[mask_carp_sale]['К перечислению Продавцу за реализованный Товар'].sum()

        mask_carp_return = ((df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа'] == 'Возврат')
        values['B5'] = df_osn[mask_carp_return]['К перечислению Продавцу за реализованный Товар'].sum()

        mask_carp_all = (df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())
        values['B7'] = df_osn[mask_carp_all]['Услуги по доставке товара покупателю'].sum()
        values['B9'] = df_osn[mask_carp_all]['Операции на приемке'].sum()
        values['B10'] = df_osn['Общая сумма штрафов'].sum()
        values['B11'] = df_osn[mask_carp_all]['Удержания'].sum()
        values['B26'] = df_osn[mask_carp_all]['Хранение'].sum()
        values['B29'] = df_osn[mask_carp_all]['Разовое изменение срока перечисления денежных средств'].sum()
        values['B44'] = df_osn[mask_carp_sale]['Цена розничная'].sum()

        # ===== ОСНОВНОЙ ОТЧЕТ - HARAKIRI =====
        mask_hara_sale = (df_osn['Бренд'] == 'Harakiri') & (df_osn['Тип документа'] == 'Продажа')
        values['F4'] = df_osn[mask_hara_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        mask_hara_return = (df_osn['Бренд'] == 'Harakiri') & (df_osn['Тип документа'] == 'Возврат')
        values['F5'] = df_osn[mask_hara_return]['К перечислению Продавцу за реализованный Товар'].sum()
        mask_hara_all = df_osn['Бренд'] == 'Harakiri'
        values['F7'] = df_osn[mask_hara_all]['Услуги по доставке товара покупателю'].sum()
        values['F9'] = df_osn[mask_hara_all]['Операции на приемке'].sum()
        values['F10'] = df_osn[mask_hara_all]['Общая сумма штрафов'].sum()
        values['F11'] = df_osn[mask_hara_all]['Удержания'].sum()
        values['B32'] = df_osn[mask_hara_sale]['Цена розничная'].sum()

        # ===== ВЫКУПЫ - ЦАП ЦАРАПКИН =====
        mask_carp_vyk_sale = ((df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())) & (df_vyk['Тип документа'] == 'Продажа')
        values['M4'] = df_vyk[mask_carp_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        mask_carp_vyk_return = ((df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())) & (df_vyk['Тип документа'] == 'Возврат')
        values['M5'] = df_vyk[mask_carp_vyk_return]['К перечислению Продавцу за реализованный Товар'].sum()
        mask_carp_vyk_all = (df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())
        values['M7'] = df_vyk[mask_carp_vyk_all]['Услуги по доставке товара покупателю'].sum()
        values['M8'] = df_vyk[mask_carp_vyk_all]['Операции на приемке'].sum()
        values['M9'] = df_vyk['Общая сумма штрафов'].sum()
        values['B47'] = df_vyk[mask_carp_vyk_sale]['Цена розничная'].sum()

        # ===== ВЫКУПЫ - HARAKIRI =====
        mask_hara_vyk_sale = (df_vyk['Бренд'] == 'Harakiri') & (df_vyk['Тип документа'] == 'Продажа')
        values['Q4'] = df_vyk[mask_hara_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        mask_hara_vyk_return = (df_vyk['Бренд'] == 'Harakiri') & (df_vyk['Тип документа'] == 'Возврат')
        values['Q5'] = df_vyk[mask_hara_vyk_return]['К перечислению Продавцу за реализованный Товар'].sum()
        mask_hara_vyk_all = df_vyk['Бренд'] == 'Harakiri'
        values['Q7'] = df_vyk[mask_hara_vyk_all]['Услуги по доставке товара покупателю'].sum()
        values['Q8'] = df_vyk[mask_hara_vyk_all]['Операции на приемке'].sum()
        values['Q9'] = df_vyk[mask_hara_vyk_all]['Общая сумма штрафов'].sum()
        values['B41'] = df_vyk[mask_hara_vyk_sale]['Цена розничная'].sum()

        # ===== ЭКВАЙРИНГ =====
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
        [InlineKeyboardButton("📱 Открыть приложение", web_app={"url": MINI_APP_URL})],
        [InlineKeyboardButton("📊 Аналитика", callback_data="menu_analytics_main")],
        [InlineKeyboardButton("📂 Архив отчетов", callback_data="menu_history")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="menu_settings")]
    ]
    return InlineKeyboardMarkup(keyboard)

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

# ===== НАСТРОЙКИ (НОВОСТИ + СЕБЕСТОИМОСТЬ) =====
async def menu_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("📰 Новости", callback_data="news_settings")],
        [InlineKeyboardButton("💰 Чистая прибыль", callback_data="menu_costs")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    await query.edit_message_text("⚙️ **Настройки**\n\nВыберите раздел:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# === НОВОСТНОЙ РАЗДЕЛ (внутри настроек) ===
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

# === ОБРАБОТЧИКИ ДЛЯ НОВОСТЕЙ (команды) ===
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
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    articles = get_all_articles_with_costs()
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
        "💰 **Управление чистой прибылью (себестоимостью)**\n\n"
        "Выберите артикул для редактирования. Текущая себестоимость указана на кнопке.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def cost_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    article = query.data.split("_")[2]
    context.user_data['editing_cost_article'] = article
    current = get_current_cost(article)
    keyboard = [
        [InlineKeyboardButton("➕ Установить новую себестоимость", callback_data=f"cost_set_{article}")],
        [InlineKeyboardButton("📜 История изменений", callback_data=f"cost_history_{article}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu_costs")]
    ]
    text = f"💰 **Артикул:** `{article}`\n\n"
    if current:
        text += f"Текущая себестоимость: **{current['cost']:.2f} ₽**\n"
        text += f"Установлена: {current['date_from']}\n"
    else:
        text += "Себестоимость не задана.\n"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def cost_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    article = query.data.split("_")[2]
    context.user_data['waiting_for_cost'] = article
    await query.edit_message_text(
        f"💵 Введите новую себестоимость для артикула `{article}` в рублях (только число):\n\n"
        "Например: `450.50`",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data=f"cost_edit_{article}")]])
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
        text += f"• {date_from} → {date_to_str}: **{cost:.2f} ₽**"
        if set_by:
            text += f" (установил {set_by})"
        text += "\n"
        # Если запись не активна, добавляем кнопку "Удалить"
        if date_to is not None:
            keyboard.append([InlineKeyboardButton(f"🗑️ Удалить запись от {date_from}", callback_data=f"cost_delete_{rec_id}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data=f"cost_edit_{article}")])
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
        # Возвращаемся к списку артикулов
        await menu_costs_callback(update, context)
    else:
        await query.edit_message_text("❌ Не удалось удалить запись (возможно, она активна или уже удалена).")

async def handle_cost_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ввод числа для себестоимости."""
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
        # Получаем бренд для артикула (можно взять из последнего отчёта или оставить пустым)
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('SELECT brand FROM article_stats WHERE article = ? LIMIT 1', (article,))
        row = cursor.fetchone()
        brand = row[0] if row else 'Unknown'
        conn.close()
        set_product_cost(article, brand, cost, update.effective_user.id)
        await update.message.reply_text(f"✅ Себестоимость для артикула `{article}` установлена: **{cost:.2f} ₽**", parse_mode='Markdown')
        context.user_data['waiting_for_cost'] = None
        # Возвращаемся в меню себестоимости
        await menu_costs_callback(update, context)
    except ValueError:
        await update.message.reply_text("❌ Введите корректное число (например, 450.50).")

# === АНАЛИТИКА ПО АРТИКУЛАМ =====
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

# === ИСТОРИЯ (АРХИВ) С ВОЗМОЖНОСТЬЮ УДАЛЕНИЯ ===
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

# === ПЕРЕХОД К ОТЧЁТУ ===
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
    # Новые метрики прибыли
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

    # Блок прибыли
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

# === АРТИКУЛЫ (только для текущего отчёта) ===
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

# === ОБРАБОТЧИКИ РОСТА И ПАДЕНИЯ ===
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

# === ДЕТАЛЬНОЕ СРАВНЕНИЕ ===
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

# === ОБРАБОТЧИК "НАЗАД В МЕНЮ" ===
async def back_to_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🏠 Главное меню. Выберите действие:",
        reply_markup=get_main_menu()
    )

# === ОБРАБОТКА ФАЙЛОВ ===
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

# === РУЧНЫЕ КОМАНДЫ osn/vyk ===
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

# === ОСНОВНАЯ ОБРАБОТКА ===
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
        profit_data = {}
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
                # Получаем себестоимость на дату окончания отчёта
                cost = get_active_cost(art, end_date)
                if cost is None:
                    cost = 0
                profit = rev - (cost * qty)
                total_profit += profit
                total_revenue += rev
                if brand in profit_by_brand:
                    profit_by_brand[brand] += profit
                    revenue_by_brand[brand] += rev
            # Также добавляем данные из выкупов (если нужно) - но для прибыли используем sales
        conn.close()
        total_margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0
        margin_carp = (profit_by_brand['Цап царапкин'] / revenue_by_brand['Цап царапкин'] * 100) if revenue_by_brand['Цап царапкин'] > 0 else 0
        margin_hara = (profit_by_brand['Harakiri'] / revenue_by_brand['Harakiri'] * 100) if revenue_by_brand['Harakiri'] > 0 else 0
        # ========================================

        metrics = {
            'avg_acquiring': values.get('B56', 0),
            'median_acquiring': values.get('B59', 0),
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
            # Добавляем прибыль
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

        # Блок прибыли
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

# === ОБРАБОТЧИК ТЕКСТА ===
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    text = update.message.text
    if text.startswith('/'):
        return
    # Проверяем, не ожидаем ли мы ввод себестоимости
    if context.user_data.get('waiting_for_cost'):
        await handle_cost_input(update, context)
        return
    await update.message.reply_text("Используйте кнопки меню или команды.", reply_markup=get_main_menu())

# ===== ЗАПУСК =====
def main():
    print("🤖 Запуск бота...")
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Создаём приложение бота
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Регистрируем все обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("osn", handle_osn))
    app.add_handler(CommandHandler("vyk", handle_vyk))
    app.add_handler(CommandHandler("articles", articles_full_cmd))
    app.add_handler(CommandHandler("news_now", news_now_cmd))
    app.add_handler(CommandHandler("set_news", set_news_cmd))
    app.add_handler(CommandHandler("set_news_query", set_news_query_cmd))

    app.add_handler(CallbackQueryHandler(menu_history_callback, pattern="^menu_history$"))
    app.add_handler(CallbackQueryHandler(menu_analytics_callback, pattern="^menu_analytics$"))
    app.add_handler(CallbackQueryHandler(menu_analytics_main_callback, pattern="^menu_analytics_main$"))
    app.add_handler(CallbackQueryHandler(menu_settings_callback, pattern="^menu_settings$"))
    app.add_handler(CallbackQueryHandler(back_to_menu_callback, pattern="^back_to_menu$"))

    app.add_handler(CallbackQueryHandler(news_settings_callback, pattern="^news_settings$"))
    app.add_handler(CallbackQueryHandler(news_now_callback, pattern="^news_now$"))
    app.add_handler(CallbackQueryHandler(news_toggle_callback, pattern="^news_toggle$"))
    app.add_handler(CallbackQueryHandler(news_query_callback, pattern="^news_query$"))
    app.add_handler(CallbackQueryHandler(news_time_callback, pattern="^news_time$"))
    app.add_handler(CallbackQueryHandler(news_time_set_callback, pattern="^news_time_"))

    app.add_handler(CallbackQueryHandler(menu_costs_callback, pattern="^menu_costs$"))
    app.add_handler(CallbackQueryHandler(cost_edit_callback, pattern="^cost_edit_"))
    app.add_handler(CallbackQueryHandler(cost_set_callback, pattern="^cost_set_"))
    app.add_handler(CallbackQueryHandler(cost_history_callback, pattern="^cost_history_"))
    app.add_handler(CallbackQueryHandler(cost_delete_callback, pattern="^cost_delete_"))

    app.add_handler(CallbackQueryHandler(analytics_toggle_callback, pattern="^analytics_toggle_"))
    app.add_handler(CallbackQueryHandler(analytics_page_callback, pattern="^analytics_page_"))
    app.add_handler(CallbackQueryHandler(analytics_select_all_callback, pattern="^analytics_select_all$"))
    app.add_handler(CallbackQueryHandler(analytics_deselect_all_callback, pattern="^analytics_deselect_all$"))
    app.add_handler(CallbackQueryHandler(analytics_quick_callback, pattern="^analytics_quick_"))
    app.add_handler(CallbackQueryHandler(analytics_show_callback, pattern="^analytics_show$"))

    app.add_handler(CallbackQueryHandler(history_page_callback, pattern="^history_page_"))
    app.add_handler(CallbackQueryHandler(history_report_callback, pattern="^history_report_"))
    app.add_handler(CallbackQueryHandler(history_toggle_delete_callback, pattern="^history_toggle_delete_"))
    app.add_handler(CallbackQueryHandler(history_enable_delete_callback, pattern="^history_enable_delete$"))
    app.add_handler(CallbackQueryHandler(history_cancel_delete_callback, pattern="^history_cancel_delete$"))
    app.add_handler(CallbackQueryHandler(history_confirm_delete_callback, pattern="^history_confirm_delete$"))

    app.add_handler(CallbackQueryHandler(articles_callback, pattern="^show_articles$"))
    app.add_handler(CallbackQueryHandler(growth_callback, pattern="^growth$"))
    app.add_handler(CallbackQueryHandler(decline_callback, pattern="^decline$"))
    app.add_handler(CallbackQueryHandler(compare_articles_callback, pattern="^compare_articles$"))

    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Планировщик новостей
    scheduler.add_job(scheduled_morning_digest, CronTrigger(hour=8, minute=30), args=[app])
    scheduler.add_job(scheduled_evening_digest, CronTrigger(hour=20, minute=40), args=[app])
    scheduler.start()

    print("✅ Бот готов, запускаем polling...")
    app.run_polling(allowed_updates=[])

if __name__ == "__main__":
    main()
