import sqlite3
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any
import pandas as pd

from config import DB_PATH, logger

# ===== ПРОВЕРКА И ОБНОВЛЕНИЕ СХЕМЫ БД =====
def upgrade_db_schema():
    """Добавляет новые столбцы в существующие таблицы, если их нет."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(article_stats)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'nm_id' not in columns:
        logger.info("🔄 Добавляем столбец nm_id в таблицу article_stats")
        cursor.execute("ALTER TABLE article_stats ADD COLUMN nm_id INTEGER")
        conn.commit()
    
    conn.close()
    logger.info("✅ Схема БД обновлена")

# ===== ИНИЦИАЛИЗАЦИЯ БД =====
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
            nm_id INTEGER,
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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS spp_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nm_id INTEGER NOT NULL,
            article TEXT,
            current_price REAL,
            old_price REAL,
            spp_percent REAL,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS spp_mutes (
            user_id INTEGER NOT NULL,
            nm_id INTEGER NOT NULL,
            mute_until TIMESTAMP,
            PRIMARY KEY (user_id, nm_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS spp_subscriptions (
            user_id INTEGER NOT NULL,
            nm_id INTEGER NOT NULL,
            threshold REAL DEFAULT 5.0,
            PRIMARY KEY (user_id, nm_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS spp_global_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER DEFAULT 1,
            interval_minutes INTEGER DEFAULT 60,
            default_threshold REAL DEFAULT 5.0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('SELECT COUNT(*) FROM spp_global_settings')
    if cursor.fetchone()[0] == 0:
        cursor.execute('''
            INSERT INTO spp_global_settings (id, enabled, interval_minutes, default_threshold)
            VALUES (1, 1, 60, 5.0)
        ''')
    conn.commit()
    conn.close()
    logger.info("✅ БД инициализирована")
    
    upgrade_db_schema()

# ===== ИНИЦИАЛИЗАЦИЯ ТАБЛИЦ ДЛЯ СПП =====
def init_spp_tables():
    # уже созданы выше, оставляем для совместимости
    pass

def init_spp_global_settings():
    # уже созданы выше, оставляем для совместимости
    pass

# ===== ФУНКЦИИ ДЛЯ РАБОТЫ С СЕБЕСТОИМОСТЬЮ =====
# ... (оставляем всё, что было ранее, без изменений) ...

# ===== ФУНКЦИИ ДЛЯ РАБОТЫ С ОТЧЁТАМИ =====
# ... (оставляем всё, что было ранее, без изменений) ...

# ===== ФУНКЦИИ ДЛЯ НАСТРОЕК НОВОСТЕЙ =====
# ... (оставляем всё, что было ранее, без изменений) ...

# ===== ФУНКЦИИ ДЛЯ СПП =====
def get_spp_global_settings():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT enabled, interval_minutes, default_threshold FROM spp_global_settings WHERE id = 1')
    row = cursor.fetchone()
    conn.close()
    if row:
        return {'enabled': bool(row[0]), 'interval_minutes': row[1], 'default_threshold': row[2]}
    return {'enabled': True, 'interval_minutes': 60, 'default_threshold': 5.0}

def set_spp_global_settings(enabled=None, interval_minutes=None, default_threshold=None):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    updates = []
    params = []
    if enabled is not None:
        updates.append("enabled = ?")
        params.append(1 if enabled else 0)
    if interval_minutes is not None:
        updates.append("interval_minutes = ?")
        params.append(interval_minutes)
    if default_threshold is not None:
        updates.append("default_threshold = ?")
        params.append(default_threshold)
    if updates:
        params.append(1)
        cursor.execute(f"UPDATE spp_global_settings SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = 1", params)
        conn.commit()
    conn.close()

def get_user_subscriptions(user_id):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT nm_id, threshold FROM spp_subscriptions WHERE user_id = ?', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [{'nm_id': row[0], 'threshold': row[1]} for row in rows]

def subscribe_user(user_id, nm_id, threshold=5.0):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO spp_subscriptions (user_id, nm_id, threshold)
        VALUES (?, ?, ?)
    ''', (user_id, nm_id, threshold))
    conn.commit()
    conn.close()

def unsubscribe_user(user_id, nm_id):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('DELETE FROM spp_subscriptions WHERE user_id = ? AND nm_id = ?', (user_id, nm_id))
    conn.commit()
    conn.close()

def get_subscribed_users(nm_id):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM spp_subscriptions WHERE nm_id = ?', (nm_id,))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_all_tracked_articles():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT nm_id FROM spp_subscriptions')
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def is_muted(user_id, nm_id):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT mute_until FROM spp_mutes
        WHERE user_id = ? AND nm_id = ? AND mute_until > datetime('now')
    ''', (user_id, nm_id))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def mute_article(user_id, nm_id, hours=2):
    mute_until = (datetime.now() + timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO spp_mutes (user_id, nm_id, mute_until)
        VALUES (?, ?, ?)
    ''', (user_id, nm_id, mute_until))
    conn.commit()
    conn.close()

def get_last_spp(nm_id):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT current_price, old_price, spp_percent, checked_at
        FROM spp_history
        WHERE nm_id = ?
        ORDER BY checked_at DESC LIMIT 1
    ''', (nm_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'current_price': row[0],
            'old_price': row[1],
            'spp_percent': row[2],
            'checked_at': row[3]
        }
    return None

def get_spp_history(nm_id, limit=30):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT checked_at, spp_percent, current_price, old_price
        FROM spp_history
        WHERE nm_id = ?
        ORDER BY checked_at DESC LIMIT ?
    ''', (nm_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [{'checked_at': row[0], 'spp_percent': row[1], 'current_price': row[2], 'old_price': row[3]} for row in rows]

def save_spp_history(nm_id, article, current_price, old_price, spp_percent):
    """Сохраняет запись в историю СПП"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO spp_history (nm_id, article, current_price, old_price, spp_percent)
        VALUES (?, ?, ?, ?, ?)
    ''', (nm_id, article, current_price, old_price, spp_percent))
    conn.commit()
    conn.close()

# ===== ФУНКЦИИ ДЛЯ ПОИСКА NM_ID =====
def get_nm_id_by_article(article_name: str) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT nm_id FROM article_stats WHERE article = ? LIMIT 1', (article_name,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0

def get_article_by_nm_id(nm_id: int) -> str:
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT article FROM article_stats WHERE nm_id = ? LIMIT 1', (nm_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else f"Товар {nm_id}"
