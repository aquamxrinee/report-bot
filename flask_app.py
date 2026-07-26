from flask import Flask, render_template, jsonify, request
from config import logger, DB_PATH
from models import get_aggregated_metrics
import sqlite3
import traceback

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
    try:
        return render_template('dashboard.html')
    except Exception as e:
        logger.error(f"Ошибка в /mini: {e}\n{traceback.format_exc()}")
        return f"Ошибка: {e}", 500

@flask_app.route('/api/stats')
def api_stats():
    logger.info(f"Запрос /api/stats от {request.remote_addr}")
    try:
        data = get_aggregated_metrics()
        for key in ['total_reports', 'wb_total', 'wb_carp', 'wb_hara', 
                    'avg_acquiring', 'median_acquiring', 'min_acquiring', 
                    'max_acquiring', 'total_profit', 'avg_margin']:
            if key not in data or not isinstance(data[key], (int, float)):
                data[key] = 0
        logger.info(f"API stats OK: {data}")
        return jsonify(data)
    except Exception as e:
        logger.error(f"Ошибка в /api/stats: {e}\n{traceback.format_exc()}")
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
    try:
        flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
    except Exception as e:
        logger.error(f"Flask упал: {e}")
