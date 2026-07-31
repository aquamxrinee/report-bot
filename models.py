import sqlite3
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any

from config import DB_PATH, logger

def upgrade_db_schema():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(article_stats)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'nm_id' not in columns:
        cursor.execute("ALTER TABLE article_stats ADD COLUMN nm_id INTEGER")
        conn.commit()
    conn.close()

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
    upgrade_db_schema()

def init_spp_tables():
    pass

def init_spp_global_settings():
    pass

def init_spp_brand_tables():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS spp_brand_subscriptions (
            user_id INTEGER NOT NULL,
            brand TEXT NOT NULL,
            threshold REAL DEFAULT 5.0,
            PRIMARY KEY (user_id, brand)
        )
    ''')
    conn.commit()
    conn.close()

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

def delete_all_costs_for_article(article):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('DELETE FROM product_costs_history WHERE article = ?', (article,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

def calculate_file_hash(file_path):
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            md5.update(chunk)
    return md5.hexdigest()

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

        if values:
            for cell, val in values.items():
                if cell in ['B1', 'F1']:
                    continue
                try:
                    cursor.execute('''
                        INSERT INTO report_values (report_id, cell_name, cell_value)
                        VALUES (?, ?, ?)
                    ''', (report_id, cell, float(val)))
                except Exception as e:
                    logger.error(f"Ошибка вставки {cell}: {e}")

        if metrics:
            for mname, mval in metrics.items():
                try:
                    cursor.execute('''
                        INSERT INTO report_metrics (report_id, metric_name, metric_value)
                        VALUES (?, ?, ?)
                    ''', (report_id, mname, float(mval)))
                except Exception as e:
                    logger.error(f"Ошибка вставки метрики {mname}: {e}")

        if articles:
            for brand, data in articles.items():
                all_arts = {}
                for art, stats in data.get('sales', {}).items():
                    if art not in all_arts:
                        all_arts[art] = {'quantity': 0, 'revenue': 0, 'nm_id': None}
                    all_arts[art]['quantity'] += stats.get('quantity', 0)
                    all_arts[art]['revenue'] += stats.get('revenue', 0)
                    if stats.get('nm_id') and not all_arts[art]['nm_id']:
                        all_arts[art]['nm_id'] = stats['nm_id']
                for art, stats in data.get('vyk', {}).items():
                    if art not in all_arts:
                        all_arts[art] = {'quantity': 0, 'revenue': 0, 'nm_id': None}
                    all_arts[art]['quantity'] += stats.get('quantity', 0)
                    all_arts[art]['revenue'] += stats.get('revenue', 0)
                    if stats.get('nm_id') and not all_arts[art]['nm_id']:
                        all_arts[art]['nm_id'] = stats['nm_id']
                for art, stats in all_arts.items():
                    try:
                        nm_id_val = stats['nm_id']
                        if nm_id_val is not None:
                            nm_id_val = int(nm_id_val)
                        cursor.execute('''
                            INSERT INTO article_stats (report_id, brand, article, quantity, revenue, nm_id)
                            VALUES (?, ?, ?, ?, ?, ?)
                        ''', (report_id, brand, art, stats['quantity'], stats['revenue'], nm_id_val))
                    except Exception as e:
                        logger.error(f"Ошибка вставки артикула {art}: {e}")
                        try:
                            cursor.execute('''
                                INSERT INTO article_stats (report_id, brand, article, quantity, revenue)
                                VALUES (?, ?, ?, ?, ?)
                            ''', (report_id, brand, art, stats['quantity'], stats['revenue']))
                        except:
                            pass
        conn.commit()
        conn.close()
        return True, report_id
    except Exception as e:
        logger.error(f"Критическая ошибка сохранения: {e}")
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
                SELECT article, SUM(quantity) as q, SUM(revenue) as r, MAX(nm_id) as nm_id
                FROM article_stats
                WHERE report_id = ? AND brand = ?
                GROUP BY article
            ''', (report_id, brand))
        else:
            cursor.execute('''
                SELECT article, SUM(quantity) as q, SUM(revenue) as r, MAX(nm_id) as nm_id
                FROM article_stats
                WHERE report_id = ?
                GROUP BY article
            ''', (report_id,))
        results = cursor.fetchall()
        conn.close()
        return {row[0]: {'quantity': row[1], 'revenue': row[2], 'nm_id': row[3]} for row in results}
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
                AVG(CASE WHEN metric_name = 'median_acquiring' THEN metric_value ELSE NULL END) as median_acquiring,
                MIN(CASE WHEN metric_name = 'min_acquiring' THEN metric_value ELSE NULL END) as min_acquiring,
                MAX(CASE WHEN metric_name = 'max_acquiring' THEN metric_value ELSE NULL END) as max_acquiring,
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
                'median_acquiring': row[5] or 0,
                'min_acquiring': row[6] or 0,
                'max_acquiring': row[7] or 0,
                'total_profit': row[8] or 0,
                'avg_margin': row[9] or 0
            }
        else:
            return {
                'total_reports': 0,
                'wb_total': 0,
                'wb_carp': 0,
                'wb_hara': 0,
                'avg_acquiring': 0,
                'median_acquiring': 0,
                'min_acquiring': 0,
                'max_acquiring': 0,
                'total_profit': 0,
                'avg_margin': 0
            }
    except Exception as e:
        logger.error(f"Ошибка агрегации: {e}")
        return {
            'total_reports': 0,
            'wb_total': 0,
            'wb_carp': 0,
            'wb_hara': 0,
            'avg_acquiring': 0,
            'median_acquiring': 0,
            'min_acquiring': 0,
            'max_acquiring': 0,
            'total_profit': 0,
            'avg_margin': 0
        }

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
        cursor.execute(
            f"UPDATE spp_global_settings SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
            params
        )
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
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO spp_history (nm_id, article, current_price, old_price, spp_percent)
        VALUES (?, ?, ?, ?, ?)
    ''', (nm_id, article, current_price, old_price, spp_percent))
    conn.commit()
    conn.close()

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

def get_user_brand_subscriptions(user_id):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT brand, threshold FROM spp_brand_subscriptions WHERE user_id = ?', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [{'brand': row[0], 'threshold': row[1]} for row in rows]

def subscribe_brand(user_id, brand, threshold=5.0):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO spp_brand_subscriptions (user_id, brand, threshold)
        VALUES (?, ?, ?)
    ''', (user_id, brand, threshold))
    conn.commit()
    conn.close()

def unsubscribe_brand(user_id, brand):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('DELETE FROM spp_brand_subscriptions WHERE user_id = ? AND brand = ?', (user_id, brand))
    conn.commit()
    conn.close()

def get_all_brand_subscribers(brand):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, threshold FROM spp_brand_subscriptions WHERE brand = ?', (brand,))
    rows = cursor.fetchall()
    conn.close()
    return [{'user_id': row[0], 'threshold': row[1]} for row in rows]
