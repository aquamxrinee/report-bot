import re
import shutil
import logging
import requests
import pandas as pd
import openpyxl
from datetime import datetime, timedelta
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
import asyncio
import os

from config import TEMP_DIR, logger, DB_PATH, ALLOWED_USERS
from models import get_active_cost, save_report_to_db, get_report_id_by_period, calculate_file_hash

# ===== ПЛАНИРОВЩИК =====
scheduler = BackgroundScheduler()

# ===== НОВОСТИ ИЗ TELEGRAM-КАНАЛА =====
from tg_news_parser import fetch_channel_messages, format_news_digest

async def send_news_digest(context, user_id, time_of_day):
    from models import get_news_settings
    settings = get_news_settings(user_id)
    if not settings['enabled']:
        return
    messages = fetch_channel_messages("news4sellers", limit=10)
    prefix = "🌅 **Утренняя сводка**" if time_of_day == 'morning' else "🌇 **Вечерняя сводка**"
    text = format_news_digest(messages, prefix)
    try:
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown', disable_web_page_preview=True)
        logger.info(f"✅ Новостная сводка ({time_of_day}) отправлена пользователю {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки новостей пользователю {user_id}: {e}")

async def scheduled_morning_digest(context):
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM news_settings WHERE enabled = 1')
    users = cursor.fetchall()
    conn.close()
    for (user_id,) in users:
        await send_news_digest(context, user_id, 'morning')

async def scheduled_evening_digest(context):
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM news_settings WHERE enabled = 1')
    users = cursor.fetchall()
    conn.close()
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
        art_variants = [
            'артикул поставщика', 'артикул', 'артикул товара', 'номенклатура',
            'sku', 'артикул(поставщика)', 'артикул поставщика (поставщика)',
            'код товара', 'id товара', 'vendor code', 'article'
        ]
        nm_id_variants = ['код номенклатуры', 'nmId', 'nm_id', 'артикул товара', 'номенклатура', 'артикул']

        qty_col = None
        art_col = None
        nm_id_col = None

        for v in qty_variants:
            if v in all_cols:
                qty_col = all_cols[v]
                break
        for v in art_variants:
            if v in all_cols:
                art_col = all_cols[v]
                break
        for v in nm_id_variants:
            if v in all_cols:
                nm_id_col = all_cols[v]
                break

        if art_col is None:
            original_cols = df_osn.columns.tolist()
            for col in original_cols:
                col_lower = col.strip().lower()
                if any(keyword in col_lower for keyword in ['артикул поставщика', 'vendor', 'article', 'артикул']):
                    if 'номенклатура' not in col_lower and 'код' not in col_lower:
                        art_col = col
                        logger.info(f"🔍 Нашли колонку артикула по точному совпадению: '{col}'")
                        break

        if nm_id_col is None:
            original_cols = df_osn.columns.tolist()
            for col in original_cols:
                if 'Код номенклатуры' in col or col.strip().lower() == 'код номенклатуры':
                    nm_id_col = col
                    logger.info(f"🔍 Нашли колонку nm_id по точному совпадению: '{col}'")
                    break

        if qty_col is None:
            logger.warning(f"❌ Колонка количества не найдена. Доступные: {list(all_cols.keys())}")
            return result
        if art_col is None:
            logger.warning(f"❌ Колонка артикула не найдена. Доступные: {list(all_cols.keys())}")
            possible = [col for col in all_cols.keys() if 'артикул' in col or 'article' in col]
            if possible:
                art_col = all_cols[possible[0]]
                logger.info(f"✅ Нашли возможную колонку артикула: '{art_col}'")

        logger.info(f"✅ Найдены колонки: количество='{qty_col}', артикул='{art_col}', nm_id='{nm_id_col}'")

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
                if sales.empty:
                    articles = {}
                else:
                    agg_dict = {
                        'quantity': (qty_col, 'sum'),
                        'revenue': ('Цена розничная', 'sum')
                    }
                    if nm_id_col:
                        agg_dict['nm_id'] = (nm_id_col, 'first')
                    agg_sales = sales.groupby(art_col).agg(**agg_dict).to_dict('index')
                    articles = {}
                    for art, vals in agg_sales.items():
                        nm_id_val = vals.get('nm_id') if nm_id_col else None
                        if nm_id_val is not None:
                            try:
                                nm_id_val = int(float(nm_id_val))
                            except:
                                nm_id_val = None
                        articles[art] = {
                            'quantity': vals['quantity'],
                            'revenue': vals['revenue'],
                            'nm_id': nm_id_val
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

# ===== АВТОМАТИЧЕСКАЯ ЗАГРУЗКА ЕЖЕНЕДЕЛЬНЫХ ОТЧЁТОВ =====
def extract_metrics_from_values(values):
    metrics = {
        'avg_acquiring': values.get('B56', 0),
        'wb_carp': values.get('B44', 0),
        'wb_hara': values.get('B32', 0),
        'wb_total': values.get('B44', 0) + values.get('B32', 0),
        'k_vyvodu_carp': values.get('B4', 0),
        'k_vyvodu_hara': values.get('F4', 0),
    }
    return metrics

async def process_auto_report(app, osn_path, vyk_path, period_str, date_from, date_to):
    """Обработка автоматически загруженных отчётов без участия пользователя."""
    processor = ReportProcessor()
    template_path = Path("шаблон.xlsx")
    values, articles, _ = processor.process_files(osn_path, vyk_path, template_path)

    file_hash = calculate_file_hash(osn_path) + calculate_file_hash(vyk_path)
    metrics = extract_metrics_from_values(values)

    k_vyvodu_hara = metrics['k_vyvodu_hara']
    wb_hara = metrics['wb_hara']
    metrics['k_vyvodu_hara_nalog'] = k_vyvodu_hara - wb_hara * 0.01

    success, report_id = save_report_to_db(
        file_name=f"auto_{period_str}.xlsx",
        file_hash=file_hash,
        date_period=period_str,
        start_date=date_from,
        end_date=date_to,
        values=values,
        metrics=metrics,
        articles=articles
    )
    if not success:
        logger.error("Ошибка сохранения автоотчёта")
        return

    report_dir = Path("/data/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"отчёт_{report_id}.xlsx"
    shutil.copy2(template_path, report_file)

    def format_number(num):
        if num is None: return "0"
        if isinstance(num, float) and num.is_integer():
            return f"{int(num):,}".replace(",", " ")
        return f"{num:,.2f}".replace(",", " ")

    summary = (
        f"📊 Автоматический отчёт за {period_str}\n"
        f"💰 Оборот: {format_number(metrics.get('wb_total', 0))} ₽\n"
        f"🟢 ЦАП: {format_number(metrics.get('wb_carp', 0))} ₽\n"
        f"🔴 Harakiri: {format_number(wb_hara)} ₽\n"
        f"💳 Эквайринг: {metrics.get('avg_acquiring', 0):.2f}%\n"
        f"💵 К выводу ЦАП: {format_number(metrics.get('k_vyvodu_carp', 0))} ₽\n"
        f"💵 К выводу Harakiri: {format_number(k_vyvodu_hara)} ₽\n"
        f"💵 К выводу Harakiri с вычетом налога: {format_number(metrics.get('k_vyvodu_hara_nalog', 0))} ₽\n"
    )

    for uid in ALLOWED_USERS:
        try:
            with open(template_path, 'rb') as f:
                await app.bot.send_document(
                    uid, document=f,
                    filename=f"отчёт_{period_str}.xlsx",
                    caption=f"✅ Автоматический отчёт за {period_str}"
                )
            await app.bot.send_message(uid, summary, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Не удалось отправить автоотчёт пользователю {uid}: {e}")

async def fetch_weekly_reports_job(app):
    """Проверяет и загружает отчёты за прошлую неделю."""
    from wb_api import get_weekly_reports

    today = datetime.now().date()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    date_from = last_monday.strftime("%Y-%m-%d")
    date_to = last_sunday.strftime("%Y-%m-%d")
    period_str = f"{last_monday.strftime('%d.%m')}-{last_sunday.strftime('%d.%m')}"

    logger.info(f"🔍 Автопроверка отчётов за {period_str}")

    if get_report_id_by_period(date_from, date_to):
        msg = f"ℹ️ Отчёт за {period_str} уже существует в базе."
        logger.info(msg)
        for uid in ALLOWED_USERS:
            try:
                await app.bot.send_message(uid, msg)
            except:
                pass
        return

    reports_meta = get_weekly_reports(date_from, date_to)
    if not reports_meta:
        msg = f"📭 Еженедельный отчёт за {period_str} ещё не готов."
        logger.info(msg)
        for uid in ALLOWED_USERS:
            try:
                await app.bot.send_message(uid, msg)
            except:
                pass
        return

    temp_files = []
    for meta in reports_meta:
        try:
            r = requests.get(meta["url"], timeout=60)
            r.raise_for_status()
            fname = meta.get("fileName", f"report_{meta['reportType']}.xlsx")
            temp_path = Path(TEMP_DIR) / f"auto_{datetime.now().timestamp()}_{fname}"
            with open(temp_path, 'wb') as f:
                f.write(r.content)
            temp_files.append((meta["reportType"], str(temp_path)))
            logger.info(f"Скачан файл {fname}")
        except Exception as e:
            logger.error(f"Ошибка скачивания {meta['url']}: {e}")

    if len(temp_files) < 2:
        msg = f"⚠️ Скачано {len(temp_files)} файлов вместо 2 за {period_str}."
        logger.warning(msg)
        for uid in ALLOWED_USERS:
            try:
                await app.bot.send_message(uid, msg)
            except:
                pass
        for _, p in temp_files:
            Path(p).unlink(missing_ok=True)
        return

    osn_path = None
    vyk_path = None
    for rtype, path in temp_files:
        if rtype == 1:
            osn_path = path
        elif rtype == 2:
            vyk_path = path
    if not osn_path:
        osn_path = temp_files[0][1]
    if not vyk_path:
        vyk_path = temp_files[1][1]

    try:
        await process_auto_report(app, osn_path, vyk_path, period_str, date_from, date_to)
    except Exception as e:
        logger.error(f"❌ Ошибка обработки автоматического отчёта: {e}")
        for uid in ALLOWED_USERS:
            try:
                await app.bot.send_message(uid, f"❌ Ошибка при обработке автоотчёта за {period_str}: {e}")
            except:
                pass
    finally:
        for _, p in temp_files:
            Path(p).unlink(missing_ok=True)
