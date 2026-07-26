import sqlite3
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

from config import DB_PATH, logger

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

# ===== ФУНКЦИИ ДЛЯ РАБОТЫ С ОТЧЁТАМИ =====
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
                try:
                    cursor.execute('''
                        INSERT INTO report_values (report_id, cell_name, cell_value)
                        VALUES (?, ?, ?)
                    ''', (report_id, cell, float(val)))
                except:
                    pass

        if metrics:
            for mname, mval in metrics.items():
                try:
                    cursor.execute('''
                        INSERT INTO report_metrics (report_id, metric_name, metric_value)
                        VALUES (?, ?, ?)
                    ''', (report_id, mname, float(mval)))
                except:
                    pass

        if articles:
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

        conn.commit()
        conn.close()
        return True, report_id
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
        logger.error(f"Ошибка агрегации метрик: {e}")
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

# ===== ФУНКЦИИ ДЛЯ НАСТРОЕК НОВОСТЕЙ =====
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