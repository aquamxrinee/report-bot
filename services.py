import re
import shutil
import logging
import requests
import pandas as pd
import openpyxl
from datetime import datetime, timedelta
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler

from config import TEMP_DIR, NEWS_API_KEY, logger
from models import get_active_cost

# ===== ПЛАНИРОВЩИК =====
scheduler = BackgroundScheduler()

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

async def send_news_digest(context, user_id, time_of_day):
    from models import get_news_settings
    settings = get_news_settings(user_id)
    if not settings['enabled']:
        return
    articles = fetch_news(settings['query'], limit=10)
    prefix = "🌅 **Утренняя сводка**" if time_of_day == 'morning' else "🌇 **Вечерняя сводка**"
    text = format_news_digest(articles, prefix)
    try:
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')
        logger.info(f"Новостная сводка ({time_of_day}) отправлена пользователю {user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки новостей пользователю {user_id}: {e}")

async def scheduled_morning_digest(context):
    from models import get_news_settings
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
    from models import get_news_settings
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