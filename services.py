import re
import shutil
import logging
import requests
import pandas as pd
import openpyxl
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
import asyncio
import os

from config import TEMP_DIR, logger, DB_PATH, ALLOWED_USERS
from models import save_report_to_db, get_report_id_by_period, calculate_file_hash

# ===== ПЛАНИРОВЩИК =====
scheduler = BackgroundScheduler()

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
    # ... все методы без изменений ...
    # (process_files, _get_articles_stats, _calculate_all_values, _fill_template)
    # Они остаются точно такими же, как в предыдущей полной версии.
    # Вставьте их сюда.

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
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


def prepare_api_dataframe(detail_list):
    """Преобразует список строк детализации API в DataFrame."""
    df = pd.DataFrame(detail_list)
    # Приводим числовые колонки
    numeric_cols = ['retailAmount', 'forPay', 'quantity', 'penalty', 'deliveryAmount',
                    'paidAcceptance', 'paidStorage', 'deduction', 'acquiringPercent',
                    'additionalPayment']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        else:
            df[col] = 0

    # Маппинг названий колонок
    df['Бренд'] = df.get('brandName', '')
    df['Тип документа'] = df.get('docTypeName', '')
    df['Цена розничная'] = df.get('retailAmount', 0)
    df['К перечислению Продавцу за реализованный Товар'] = df.get('forPay', 0)
    df['Общая сумма штрафов'] = df.get('penalty', 0)
    df['Услуги по доставке товара покупателю'] = df.get('deliveryAmount', 0)
    df['Операции на приемке'] = df.get('paidAcceptance', 0)
    df['Хранение'] = df.get('paidStorage', 0)
    df['Удержания'] = df.get('deduction', 0)
    df['Разовое изменение срока перечисления денежных средств'] = df.get('additionalPayment', 0)
    df['Количество'] = df.get('quantity', 0).astype(int)

    # Эквайринг
    df['acquiring_percent'] = df.get('acquiringPercent', 0)
    df['Размер компенсации платёжных услуг/Комиссии за интеграцию платёжных сервисов, %'] = df['acquiring_percent']
    return df


async def process_auto_report(app, osn_detail, vyk_detail, period_str, date_from, date_to):
    """Обработка автоматического отчёта: объединяем оба набора детализации."""
    all_detail = osn_detail + vyk_detail
    df = prepare_api_dataframe(all_detail)

    processor = ReportProcessor()
    # Передаём один DataFrame в оба параметра, т.к. внутри _calculate_all_values будет разделение по Тип документа
    values = processor._calculate_all_values(df, df, period_str)

    template_path = Path("шаблон.xlsx")
    processor._fill_template(template_path, values)

    file_hash = hashlib.md5(f"{date_from}_{date_to}".encode()).hexdigest()

    # Запрашиваем выкуп
    from wb_api import get_buyout_by_brands
    buyouts = {}
    try:
        buyouts = get_buyout_by_brands(date_from, date_to)
    except Exception as e:
        logger.error(f"Ошибка получения выкупов в автоотчёте: {e}")

    metrics = extract_metrics_from_values(values)
    metrics['buyout_carp'] = buyouts.get('Цап царапкин')
    metrics['buyout_hara'] = buyouts.get('Harakiri')

    success, report_id = save_report_to_db(
        file_name=f"auto_{period_str}.xlsx",
        file_hash=file_hash,
        date_period=period_str,
        start_date=date_from,
        end_date=date_to,
        values=values,
        metrics=metrics,
        articles={}
    )
    if not success:
        logger.error("Ошибка сохранения автоотчёта")
        return

    report_dir = Path("/data/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_path, report_dir / f"отчёт_{report_id}.xlsx")

    def format_number(num):
        if num is None: return "0"
        if isinstance(num, float) and num.is_integer():
            return f"{int(num):,}".replace(",", " ")
        return f"{num:,.2f}".replace(",", " ")

    buyout_carp_str = f"{metrics['buyout_carp']:.1f}%" if metrics['buyout_carp'] is not None else "Н/Д"
    buyout_hara_str = f"{metrics['buyout_hara']:.1f}%" if metrics['buyout_hara'] is not None else "Н/Д"

    summary = (
        f"📊 Автоматический отчёт за {period_str}\n"
        f"💰 Оборот: {format_number(metrics.get('wb_total', 0))} ₽\n"
        f"🟢 ЦАП: {format_number(metrics.get('wb_carp', 0))} ₽\n"
        f"🔴 Harakiri: {format_number(metrics.get('wb_hara', 0))} ₽\n"
        f"💳 Эквайринг: {metrics.get('avg_acquiring', 0):.2f}%\n"
        f"💵 К выводу ЦАП: {format_number(metrics.get('k_vyvodu_carp', 0))} ₽\n"
        f"💵 К выводу Harakiri: {format_number(metrics.get('k_vyvodu_hara', 0))} ₽\n"
        f"📦 Выкуп ЦАП: {buyout_carp_str}\n"
        f"📦 Выкуп Harakiri: {buyout_hara_str}\n"
    )

    for uid in ALLOWED_USERS:
        try:
            with open(template_path, 'rb') as f:
                await app.bot.send_document(uid, document=f, filename=f"отчёт_{period_str}.xlsx",
                                           caption=f"✅ Автоматический отчёт за {period_str}")
            await app.bot.send_message(uid, summary, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Не удалось отправить автоотчёт пользователю {uid}: {e}")


async def fetch_weekly_reports_job(app):
    """Проверяет и загружает отчёты за прошлую неделю через API детализации."""
    from wb_api import get_weekly_reports, get_report_detail

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

    osn_report = next((r for r in reports_meta if r['report_type'] == 1), None)
    vyk_report = next((r for r in reports_meta if r['report_type'] == 2), None)
    if not osn_report or not vyk_report:
        msg = f"⚠️ Найдены не все типы отчётов за {period_str}."
        logger.warning(msg)
        for uid in ALLOWED_USERS:
            try:
                await app.bot.send_message(uid, msg)
            except:
                pass
        return

    detail_osn = get_report_detail(osn_report['report_id'])
    detail_vyk = get_report_detail(vyk_report['report_id'])
    if not detail_osn or not detail_vyk:
        msg = f"❌ Не удалось получить детализацию отчётов за {period_str}"
        logger.error(msg)
        for uid in ALLOWED_USERS:
            try:
                await app.bot.send_message(uid, msg)
            except:
                pass
        return

    try:
        await process_auto_report(app, detail_osn, detail_vyk, period_str, date_from, date_to)
    except Exception as e:
        logger.error(f"❌ Ошибка обработки автоматического отчёта: {e}")
        for uid in ALLOWED_USERS:
            try:
                await app.bot.send_message(uid, f"❌ Ошибка при обработке автоотчёта за {period_str}: {e}")
            except:
                pass
