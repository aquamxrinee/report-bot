#!/usr/bin/env python3
"""
Telegram бот для обработки еженедельных отчетов Wildberries
Деплой на Railway (бесплатно, 24/7)
Полная версия: эквайринг, обороты, вывод, реклама, налоги, количество заказов,
детализация по артикулам, история, статистика, удаление.
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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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
    logger.info("✅ БД инициализирована")

init_db()

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
    except:
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
            float(values.get('B4', 0)), float(values.get('B5', 0)),
            float(values.get('B7', 0)), float(values.get('B9', 0)),
            float(values.get('B10', 0)), float(values.get('B11', 0)),
            float(values.get('B26', 0)), float(values.get('B29', 0)),
            float(values.get('B32', 0)),
            float(values.get('F4', 0)), float(values.get('F5', 0)),
            float(values.get('F7', 0)), float(values.get('F9', 0)),
            float(values.get('F10', 0)), float(values.get('F11', 0)),
            float(values.get('M4', 0)), float(values.get('M5', 0)),
            float(values.get('M7', 0)), float(values.get('M8', 0)),
            float(values.get('M9', 0)), float(values.get('B47', 0)),
            float(values.get('Q4', 0)), float(values.get('Q5', 0)),
            float(values.get('Q7', 0)), float(values.get('Q8', 0)),
            float(values.get('Q9', 0)), float(values.get('B41', 0))
        ))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
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
    except:
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
    except:
        return []

# ===== ОПРЕДЕЛЕНИЕ ТИПА ФАЙЛА =====
def detect_report_type(filename):
    name = filename.lower()
    if 'осн' in name or 'osn' in name:
        return 'osn'
    elif 'вык' in name or 'vyk' in name:
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
        df_osn = pd.read_excel(osn_path)
        df_vyk = pd.read_excel(vyk_path)

        filename = Path(osn_path).name
        match = re.search(r'(\d{1,2})\.(\d{2})-(\d{1,2})\.(\d{2})', filename)
        date_range = f"{match.group(1)}.{match.group(2)}-{match.group(3)}.{match.group(4)}" if match else datetime.now().strftime("%d.%m")

        values = self._calculate_all_values(df_osn, df_vyk, date_range)
        self._fill_template(template_path, values)

        # Сбор артикулов (если есть колонки)
        articles = self._get_articles_stats(df_osn, df_vyk)
        return values, articles

    def _get_articles_stats(self, df_osn, df_vyk):
        result = {}
        qty_col = next((c for c in ['Количество', 'Количество товара', 'Кол-во'] if c in df_osn.columns), None)
        art_col = next((c for c in ['Артикул', 'Артикул товара', 'Номенклатура'] if c in df_osn.columns), None)
        if qty_col is None or art_col is None:
            return result

        for bren, mask_func in [('Цап царапкин', lambda df: (df['Бренд'] == 'Цап царапкин') | (df['Бренд'].isna())),
                                ('Harakiri', lambda df: df['Бренд'] == 'Harakiri')]:
            for df, key in [(df_osn, 'sales'), (df_vyk, 'vyk')]:
                mask = mask_func(df)
                df_bren = df[mask]
                if df_bren.empty:
                    continue
                sales = df_bren[df_bren['Тип документа'] == 'Продажа']
                returns = df_bren[df_bren['Тип документа'] == 'Возврат']
                agg_sales = sales.groupby(art_col).agg(
                    quantity=(qty_col, 'sum'),
                    revenue=('К перечислению Продавцу за реализованный Товар', 'sum')
                ).to_dict('index') if not sales.empty else {}
                agg_returns = returns.groupby(art_col).agg(
                    return_quantity=(qty_col, 'sum'),
                    return_revenue=('К перечислению Продавцу за реализованный Товар', 'sum')
                ).to_dict('index') if not returns.empty else {}
                articles = {}
                for art in set(agg_sales.keys()) | set(agg_returns.keys()):
                    articles[art] = {
                        'quantity': agg_sales.get(art, {}).get('quantity', 0),
                        'revenue': agg_sales.get(art, {}).get('revenue', 0),
                        'return_quantity': agg_returns.get(art, {}).get('return_quantity', 0),
                        'return_revenue': agg_returns.get(art, {}).get('return_revenue', 0)
                    }
                if bren not in result:
                    result[bren] = {}
                result[bren][key] = articles
        return result

    def _calculate_all_values(self, df_osn, df_vyk, date_range):
        values = {'B1': date_range, 'F1': date_range}

        # ЦАП основной
        mask_carp = (df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())
        values['B4'] = df_osn[mask_carp & (df_osn['Тип документа'] == 'Продажа')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['B5'] = df_osn[mask_carp & (df_osn['Тип документа'] == 'Возврат')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['B7'] = df_osn[mask_carp]['Услуги по доставке товара покупателю'].sum()
        values['B9'] = df_osn[mask_carp]['Операции на приемке'].sum()
        values['B10'] = df_osn['Общая сумма штрафов'].sum()
        values['B11'] = df_osn[mask_carp]['Удержания'].sum()
        values['B26'] = df_osn[mask_carp]['Хранение'].sum()
        values['B29'] = df_osn[mask_carp]['Разовое изменение срока перечисления денежных средств'].sum()
        values['B44'] = df_osn['Цена розничная'].sum()
        values['B32'] = df_osn[mask_carp]['Цена розничная'].sum()

        # Harakiri основной
        mask_hara = (df_osn['Бренд'] == 'Harakiri')
        values['F4'] = df_osn[mask_hara & (df_osn['Тип документа'] == 'Продажа')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['F5'] = df_osn[mask_hara & (df_osn['Тип документа'] == 'Возврат')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['F7'] = df_osn[mask_hara]['Услуги по доставке товара покупателю'].sum()
        values['F9'] = df_osn[mask_hara]['Операции на приемке'].sum()
        values['F10'] = df_osn[mask_hara]['Общая сумма штрафов'].sum()
        values['F11'] = df_osn[mask_hara]['Удержания'].sum()

        # Выкупы ЦАП
        mask_carp_vyk = (df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())
        values['M4'] = df_vyk[mask_carp_vyk & (df_vyk['Тип документа'] == 'Продажа')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['M5'] = df_vyk[mask_carp_vyk & (df_vyk['Тип документа'] == 'Возврат')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['M7'] = df_vyk[mask_carp_vyk]['Услуги по доставке товара покупателю'].sum()
        values['M8'] = df_vyk[mask_carp_vyk]['Операции на приемке'].sum()
        values['M9'] = df_vyk['Общая сумма штрафов'].sum()
        values['B47'] = df_vyk[mask_carp_vyk]['Цена розничная'].sum()

        # Выкупы Harakiri
        mask_hara_vyk = (df_vyk['Бренд'] == 'Harakiri')
        values['Q4'] = df_vyk[mask_hara_vyk & (df_vyk['Тип документа'] == 'Продажа')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['Q5'] = df_vyk[mask_hara_vyk & (df_vyk['Тип документа'] == 'Возврат')]['К перечислению Продавцу за реализованный Товар'].sum()
        values['Q7'] = df_vyk[mask_hara_vyk]['Услуги по доставке товара покупателю'].sum()
        values['Q8'] = df_vyk[mask_hara_vyk]['Операции на приемке'].sum()
        values['Q9'] = df_vyk[mask_hara_vyk]['Общая сумма штрафов'].sum()
        values['B41'] = df_vyk[mask_hara_vyk]['Цена розничная'].sum()

        # Эквайринг
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

# ===== КОМАНДЫ БОТА =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для обработки еженедельных отчетов WB.\n\n"
        "📤 Отправь файлы с 'осн' и 'вык' в названии — я автоматически их обработаю.\n"
        "📊 Команды: /history, /stats, /delete, /articles"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — начать\n"
        "/help — помощь\n"
        "/osn — отметить файл как основной (вручную)\n"
        "/vyk — отметить файл как выкупы (вручную)\n"
        "/history — история отчетов\n"
        "/stats — общая статистика\n"
        "/delete — удалить отчет\n"
        "/articles — детали по артикулам"
    )

# === ОБРАБОТКА ФАЙЛОВ ===
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        doc = update.message.document
        if not doc.file_name.endswith(('.xlsx', '.xls')):
            await update.message.reply_text("❌ Нужен Excel файл")
            return

        file = await context.bot.get_file(doc.file_id)
        file_path = TEMP_DIR / doc.file_name
        await file.download_to_drive(file_path)

        file_hash = calculate_file_hash(file_path)
        dup = is_file_duplicate(file_hash)
        if dup:
            await update.message.reply_text(f"⚠️ Отчет уже загружен: {dup[1]}")
            os.remove(file_path)
            return

        report_type = detect_report_type(doc.file_name)
        if not report_type:
            context.user_data['current_file'] = str(file_path)
            context.user_data['current_file_hash'] = file_hash
            await update.message.reply_text("❓ Тип не определен. Используйте /osn или /vyk")
            return

        if 'files' not in context.user_data:
            context.user_data['files'] = {}
        context.user_data['files'][report_type] = str(file_path)
        if report_type == 'osn':
            context.user_data['osn_hash'] = file_hash
            await update.message.reply_text("✅ Основной отчет получен. Теперь отправьте выкупы ('вык')")
        else:
            context.user_data['vyk_hash'] = file_hash
            await update.message.reply_text("✅ Отчет по выкупам получен. Теперь отправьте основной ('осн')")

        if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
            await process_and_send(update, context)
    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def handle_osn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправьте файл!")
        return
    context.user_data['files']['osn'] = context.user_data['current_file']
    context.user_data['osn_hash'] = context.user_data['current_file_hash']
    await update.message.reply_text("✅ Основной отчет сохранен")
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)

async def handle_vyk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправьте файл!")
        return
    context.user_data['files']['vyk'] = context.user_data['current_file']
    context.user_data['vyk_hash'] = context.user_data['current_file_hash']
    await update.message.reply_text("✅ Отчет по выкупам сохранен")
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)

async def process_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("⏳ Обработка...")

        osn_file = context.user_data['files']['osn']
        vyk_file = context.user_data['files']['vyk']
        osn_hash = context.user_data.get('osn_hash')

        # Шаблон
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

        # Читаем коэффициенты хранения
        wb_coeff = openpyxl.load_workbook(template_path, data_only=True)
        ws_coeff = wb_coeff.active
        b23 = float(ws_coeff['B23'].value) if ws_coeff['B23'].value is not None else 0.0
        c23 = float(ws_coeff['C23'].value) if ws_coeff['C23'].value is not None else 0.0
        wb_coeff.close()

        # Копируем шаблон
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out_file = TEMP_DIR / f"шаблон_{timestamp}.xlsx"
        shutil.copy(template_path, out_file)

        processor = ReportProcessor()
        values, articles = processor.process_files(osn_file, vyk_file, str(out_file))

        # Приводим всё к float
        for k in values:
            try:
                values[k] = float(values[k])
            except:
                values[k] = 0.0

        # Сохраняем в БД
        if osn_hash is None:
            osn_hash = calculate_file_hash(Path(osn_file))
        saved = save_report_to_db(Path(osn_file).name, osn_hash, values.get('B1', ''), values)

        # Отправляем файл
        with open(out_file, 'rb') as f:
            await update.message.reply_document(f, caption="✅ Готово!")

        # === ВЫЧИСЛЯЕМ МЕТРИКИ ===
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
        b38 = f13 - b35

        wb_total = b44 + b47 + b32 + b41
        wb_carp = b44 + b47
        wb_hara = b32 + b41
        k_carp = b13 + m10
        k_hara = f13 + q10
        reklama_carp = b11
        reklama_hara = f11
        shtrafy = b10 + f10
        nalog = b35 + b50

        # Количество заказов
        carp_orders = sum(a.get('quantity', 0) for a in articles.get('Цап царапкин', {}).get('sales', {}).values())
        hara_orders = sum(a.get('quantity', 0) for a in articles.get('Harakiri', {}).get('sales', {}).values())
        carp_vyk_orders = sum(a.get('quantity', 0) for a in articles.get('Цап царапкин', {}).get('vyk', {}).values())
        hara_vyk_orders = sum(a.get('quantity', 0) for a in articles.get('Harakiri', {}).get('vyk', {}).values())

        # Сохраняем в контекст для /articles
        context.user_data['articles_data'] = articles
        context.user_data['current_period'] = values.get('B1', '')

        # === СООБЩЕНИЕ ===
        msg = (
            "📊 **Статистика обработки:**\n\n"
            "• Основной отчет: ЦАП + HARAKIRI ✅\n"
            "• По выкупам: ЦАП + HARAKIRI ✅\n\n"
            f"💳 **Средний эквайринг:** {values.get('B56', 0):,.2f} %\n"
            f"📊 **Медианный эквайринг:** {values.get('B59', 0):,.2f} %\n\n"
            f"💰 **ВБшный оборот общий:** {wb_total:,.2f} ₽\n"
            f"   🐱 ЦАП: {wb_carp:,.2f} ₽\n"
            f"   ⚔️ Харакири: {wb_hara:,.2f} ₽\n\n"
            f"📦 **Заказы (осн):** ЦАП {carp_orders} шт., Харакири {hara_orders} шт.\n"
            f"📦 **Заказы (вык):** ЦАП {carp_vyk_orders} шт., Харакири {hara_vyk_orders} шт.\n\n"
            f"💸 **К выводу ЦАП:** {k_carp:,.2f} ₽\n"
            f"💸 **К выводу Харакири:** {k_hara:,.2f} ₽\n"
            f"💸 **Итого к выводу:** {k_carp + k_hara:,.2f} ₽\n"
            f"💸 **Харакири (с налогом):** {b38:,.2f} ₽\n\n"
            f"📢 **Реклама:** ЦАП {reklama_carp:,.2f} ₽, Харакири {reklama_hara:,.2f} ₽\n"
            f"⚠️ **Штрафы:** {shtrafy:,.2f} ₽\n"
            f"🧾 **Налог общий:** {nalog:,.2f} ₽\n\n"
            "✅ Отчет сохранен"
        )

        await update.message.reply_text(msg, parse_mode='Markdown')

        # Кнопка "Детали по артикулам"
        keyboard = [[InlineKeyboardButton("📦 Детали по артикулам", callback_data="show_articles")]]
        await update.message.reply_text("Нажмите кнопку для деталей:", reply_markup=InlineKeyboardMarkup(keyboard))

        # Очистка
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

# === ИСТОРИЯ, СТАТИСТИКА, УДАЛЕНИЕ ===
async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reports = get_all_reports()
    if not reports:
        await update.message.reply_text("📭 История пуста.")
        return
    msg = "📊 **История отчетов:**\n\n"
    for r in reports[:10]:
        msg += f"📄 {r[1]} ({r[2]})\n   🐱 {r[4]:,.2f} ₽ | ⚔️ {r[5]:,.2f} ₽\n"
    if len(reports) > 10:
        msg += f"… и еще {len(reports)-10}"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reports = get_all_reports()
    if not reports:
        await update.message.reply_text("📭 Нет данных.")
        return
    total = len(reports)
    avg_carp = sum(r[4] for r in reports) / total
    avg_hara = sum(r[5] for r in reports) / total
    avg_carp_vyk = sum(r[6] for r in reports) / total
    avg_hara_vyk = sum(r[7] for r in reports) / total
    msg = f"📊 **Общая статистика** ({total} отч.)\n\n"
    msg += f"🐱 ЦАП осн: {avg_carp:,.2f} ₽\n"
    msg += f"⚔️ Харакири осн: {avg_hara:,.2f} ₽\n"
    msg += f"🐱 ЦАП вык: {avg_carp_vyk:,.2f} ₽\n"
    msg += f"⚔️ Харакири вык: {avg_hara_vyk:,.2f} ₽"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reports = get_all_reports()
    if not reports:
        await update.message.reply_text("📭 История пуста.")
        return
    keyboard = []
    for r in reports[:10]:
        keyboard.append([InlineKeyboardButton(f"❌ {r[1]} ({r[2]})", callback_data=f"del_{r[0]}")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="del_cancel")])
    await update.message.reply_text("🗑️ Выберите отчет для удаления:", reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "del_cancel":
        await query.edit_message_text("✅ Отменено.")
        return
    if data.startswith("del_"):
        rid = int(data.split("_")[1])
        if delete_report(rid):
            await query.edit_message_text(f"✅ Отчет #{rid} удален.")
        else:
            await query.edit_message_text("❌ Ошибка удаления.")

# === АРТИКУЛЫ ===
async def articles_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    articles = context.user_data.get('articles_data', {})
    period = context.user_data.get('current_period', '')
    if not articles:
        await query.edit_message_text("❌ Нет данных по артикулам.")
        return

    all_items = []
    for brand, data in articles.items():
        for art, st in data.get('sales', {}).items():
            all_items.append((brand, art, st.get('quantity',0), st.get('revenue',0), st.get('return_quantity',0), st.get('return_revenue',0)))
    all_items.sort(key=lambda x: x[3], reverse=True)
    top = all_items[:10]
    msg = f"📦 **Топ-10 артикулов** ({period})\n\n"
    for brand, art, qty, rev, rqty, rrev in top:
        msg += f"**{brand}** — {art}\n   Продажи: {qty} шт. | {rev:,.2f} ₽\n   Возвраты: {rqty} шт. | {rrev:,.2f} ₽\n\n"
    if len(all_items) > 10:
        msg += f"… и еще {len(all_items)-10}. Используйте /articles для полного списка."
    await query.edit_message_text(msg, parse_mode='Markdown')

async def articles_full_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    articles = context.user_data.get('articles_data', {})
    period = context.user_data.get('current_period', '')
    if not articles:
        await update.message.reply_text("❌ Нет данных. Сначала загрузите отчет.")
        return
    msg = f"📦 **Все артикулы** ({period})\n\n"
    all_items = []
    for brand, data in articles.items():
        for art, st in data.get('sales', {}).items():
            all_items.append((brand, art, st.get('quantity',0), st.get('revenue',0), st.get('return_quantity',0), st.get('return_revenue',0)))
    all_items.sort(key=lambda x: x[3], reverse=True)
    for brand, art, qty, rev, rqty, rrev in all_items:
        msg += f"**{brand}** — {art}\n   Продажи: {qty} шт. | {rev:,.2f} ₽\n   Возвраты: {rqty} шт. | {rrev:,.2f} ₽\n\n"
        if len(msg) > 4000:
            msg += "\n… (сообщение обрезано)"
            break
    await update.message.reply_text(msg, parse_mode='Markdown')

# ===== ЗАПУСК =====
def main():
    print("🤖 Запуск бота...")
    run_flask()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Меню команд
    async def set_commands(app_instance):
        await app_instance.bot.set_my_commands([
            BotCommand("start", "Начать"),
            BotCommand("help", "Помощь"),
            BotCommand("osn", "Отметить как основной"),
            BotCommand("vyk", "Отметить как выкупы"),
            BotCommand("history", "История"),
            BotCommand("stats", "Статистика"),
            BotCommand("delete", "Удалить отчет"),
            BotCommand("articles", "Все артикулы"),
        ])
    app.post_init = set_commands

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("osn", handle_osn))
    app.add_handler(CommandHandler("vyk", handle_vyk))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("articles", articles_full_cmd))
    app.add_handler(CallbackQueryHandler(articles_callback, pattern="^show_articles$"))
    app.add_handler(CallbackQueryHandler(delete_callback, pattern="^del_"))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    print("✅ Бот готов")
    app.run_polling(allowed_updates=[])

if __name__ == "__main__":
    main()
