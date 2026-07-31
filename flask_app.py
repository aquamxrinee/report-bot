import os
import traceback
import sqlite3
from flask import Flask, render_template, jsonify, request

from config import logger, DB_PATH
from models import get_aggregated_metrics
from wb_api import get_aggregated_stats, get_articles_stats
from spp_monitor import generate_spp_graph

flask_app = Flask(__name__, template_folder='templates')

@flask_app.before_request
def log_request():
    logger.info(f"📥 {request.method} {request.path}")

@flask_app.route("/")
def health():
    return "OK", 200

@flask_app.route("/ping")
def ping():
    return "pong", 200

@flask_app.route('/mini')
def mini():
    logger.info("➡️ /mini вызван")
    try:
        return render_template('dashboard.html')
    except Exception as e:
        logger.error(f"❌ Ошибка /mini: {e}\n{traceback.format_exc()}")
        return f"Ошибка: {e}", 500

@flask_app.route('/api/stats')
def stats():
    logger.info("➡️ /api/stats вызван")
    try:
        data = get_aggregated_metrics()
        return jsonify(data)
    except Exception as e:
        logger.error(f"❌ Ошибка /api/stats: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

@flask_app.route('/api/wb_stats')
def wb_stats():
    logger.info("➡️ /api/wb_stats вызван")
    try:
        force = request.args.get('force', 'false').lower() == 'true'
        data = get_aggregated_stats(force_refresh=force)
        return jsonify(data)
    except Exception as e:
        logger.error(f"❌ Ошибка /api/wb_stats: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

@flask_app.route('/api/wb_articles')
def wb_articles():
    logger.info("➡️ /api/wb_articles вызван")
    try:
        nm_ids = request.args.get('nm_ids', '')
        if not nm_ids:
            return jsonify({'error': 'Не указаны nmIds'}), 400
        ids = [int(x.strip()) for x in nm_ids.split(',') if x.strip()]
        if not ids:
            return jsonify({'error': 'Некорректные nmIds'}), 400
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        data = get_articles_stats(ids, date_from, date_to)
        return jsonify(data)
    except Exception as e:
        logger.error(f"❌ Ошибка /api/wb_articles: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

@flask_app.route('/api/spp_graph/<int:nm_id>')
def spp_graph_endpoint(nm_id):
    try:
        img_base64 = generate_spp_graph(nm_id)
        if not img_base64:
            return "<h3>Недостаточно данных для построения графика</h3>", 404
        return f"""
        <html>
            <head><title>График СПП {nm_id}</title></head>
            <body>
                <h2>Динамика СПП для артикула {nm_id}</h2>
                <img src="data:image/png;base64,{img_base64}" />
                <br><a href="https://www.wildberries.ru/catalog/{nm_id}/detail.aspx" target="_blank">Открыть на WB</a>
            </body>
        </html>
        """
    except Exception as e:
        logger.error(f"❌ Ошибка генерации графика: {e}")
        return f"Ошибка: {e}", 500

@flask_app.route('/api/debug_metrics')
def debug_metrics():
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM reports ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Нет отчётов'}), 404
        report_id = row[0]
        cursor.execute("SELECT metric_name, metric_value FROM report_metrics WHERE report_id = ?", (report_id,))
        metrics = cursor.fetchall()
        conn.close()
        return jsonify({'report_id': report_id, 'metrics': metrics})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def run_flask():
    port = int(os.getenv("PORT", 8080))
    logger.info(f"🚀 Запуск Flask на порту {port}")
    flask_app.run(host="0.0.0.0", port=port)
