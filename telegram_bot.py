#!/usr/bin/env python3
import os,re,shutil,logging,sqlite3,hashlib,threading
from datetime import datetime,timedelta
from pathlib import Path
import pandas as pd,openpyxl,requests
from flask import Flask,render_template,jsonify,request
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup,BotCommand
from telegram.ext import Application,CommandHandler,MessageHandler,filters,ContextTypes,CallbackQueryHandler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN: raise ValueError("❌ Токен не найден!")
NEWS_API_KEY=os.getenv("NEWS_API_KEY")
MINI_APP_URL=os.getenv("MINI_APP_URL","worker-production-a75a.up.railway.app/mini")
if not MINI_APP_URL.startswith(("http://","https://")): MINI_APP_URL="https://"+MINI_APP_URL
print(f"🌐 Mini App URL: {MINI_APP_URL}")
ALLOWED_USER_IDS=os.getenv("ALLOWED_USER_IDS","")
ALLOWED_USERS=set(map(int,ALLOWED_USER_IDS.split(","))) if ALLOWED_USER_IDS else set()
print(f"🔒 Бот доступен только для ID: {ALLOWED_USERS}" if ALLOWED_USERS else "⚠️ ALLOWED_USER_IDS не задан. Бот доступен всем.")
USER_NAMES={1289447998:"Роман",5167366543:"Евгений"}
DATA_DIR=Path("/data");TEMP_DIR=DATA_DIR/"temp";DB_PATH=DATA_DIR/"reports.db"
if not DATA_DIR.exists(): DATA_DIR=Path("/tmp/telegram_data");TEMP_DIR=DATA_DIR/"temp";DB_PATH=DATA_DIR/"reports.db"
DATA_DIR.mkdir(exist_ok=True);TEMP_DIR.mkdir(exist_ok=True)
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",level=logging.INFO)
logger=logging.getLogger(__name__)
print(f"📁 Данные: {DATA_DIR}\n📊 БД: {DB_PATH}")

def init_db():
    conn=sqlite3.connect(str(DB_PATH));c=conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY AUTOINCREMENT,file_name TEXT NOT NULL,file_hash TEXT UNIQUE NOT NULL,date_period TEXT,start_date TEXT,end_date TEXT,processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS report_values (report_id INTEGER,cell_name TEXT,cell_value REAL,FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE,PRIMARY KEY (report_id, cell_name))''')
    c.execute('''CREATE TABLE IF NOT EXISTS report_metrics (report_id INTEGER,metric_name TEXT,metric_value REAL,FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE,PRIMARY KEY (report_id, metric_name))''')
    c.execute('''CREATE TABLE IF NOT EXISTS article_stats (id INTEGER PRIMARY KEY AUTOINCREMENT,report_id INTEGER,brand TEXT,article TEXT,quantity INTEGER,revenue REAL,FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE)''')
    c.execute('''CREATE TABLE IF NOT EXISTS news_settings (user_id INTEGER PRIMARY KEY,enabled INTEGER DEFAULT 1,query TEXT DEFAULT 'Wildberries OR ВБ OR Вайлдбериз OR Wildberries.ru',morning_time TEXT DEFAULT '08:30',evening_time TEXT DEFAULT '20:40')''')
    c.execute('''CREATE TABLE IF NOT EXISTS product_costs_history (id INTEGER PRIMARY KEY AUTOINCREMENT,article TEXT NOT NULL,brand TEXT NOT NULL,cost_price REAL NOT NULL,date_from TEXT NOT NULL,date_to TEXT,set_by INTEGER,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit();conn.close();logger.info("✅ БД инициализирована")
init_db()

def get_earliest_report_date():
    conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute("SELECT MIN(start_date) FROM reports WHERE start_date IS NOT NULL");r=c.fetchone();conn.close();return r[0] if r and r[0] else None
def get_active_cost(article,report_date):
    conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT cost_price FROM product_costs_history WHERE article=? AND date_from<=? AND (date_to IS NULL OR date_to>?) ORDER BY date_from DESC LIMIT 1',(article,report_date,report_date));r=c.fetchone();conn.close();return r[0] if r else None
def get_current_cost(article):
    conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT cost_price,date_from,id FROM product_costs_history WHERE article=? AND date_to IS NULL ORDER BY date_from DESC LIMIT 1',(article,));r=c.fetchone();conn.close();return {'cost':r[0],'date_from':r[1],'id':r[2]} if r else None
def get_all_articles_with_costs():
    conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT DISTINCT article FROM article_stats');a1=[r[0] for r in c.fetchall()]
    c.execute('SELECT DISTINCT article FROM product_costs_history');a2=[r[0] for r in c.fetchall()]
    all_articles=sorted(set(a1+a2));res=[]
    for art in all_articles:
        ci=get_current_cost(art)
        if ci: res.append({'article':art,'cost':ci['cost'],'date_from':ci['date_from'],'history_id':ci['id']})
        else: res.append({'article':art,'cost':None,'date_from':None,'history_id':None})
    conn.close();return res
def get_cost_history(article):
    conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT id,cost_price,date_from,date_to,set_by,created_at FROM product_costs_history WHERE article=? ORDER BY date_from DESC',(article,));r=c.fetchall();conn.close();return r
def set_product_cost(article,brand,cost,user_id):
    conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute("SELECT COUNT(*) FROM product_costs_history WHERE article=?",(article,));cnt=c.fetchone()[0]
    if cnt==0:
        earliest=get_earliest_report_date();date_from=earliest if earliest else datetime.now().strftime("%Y-%m-%d")
    else: date_from=datetime.now().strftime("%Y-%m-%d")
    if cnt>0:
        prev_date=(datetime.strptime(date_from,"%Y-%m-%d")-timedelta(days=1)).strftime("%Y-%m-%d")
        c.execute('UPDATE product_costs_history SET date_to=? WHERE article=? AND date_to IS NULL',(prev_date,article))
    c.execute('INSERT INTO product_costs_history (article,brand,cost_price,date_from,set_by) VALUES (?,?,?,?,?)',(article,brand,cost,date_from,user_id))
    conn.commit();conn.close();return True
def delete_cost_history(record_id):
    conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('DELETE FROM product_costs_history WHERE id=?',(record_id,));deleted=c.rowcount>0;conn.commit();conn.close();return deleted
def delete_all_costs_for_article(article):
    conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('DELETE FROM product_costs_history WHERE article=?',(article,));deleted=c.rowcount;conn.commit();conn.close();return deleted

def calculate_file_hash(file_path):
    md5=hashlib.md5()
    with open(file_path,"rb") as f:
        for chunk in iter(lambda: f.read(4096),b""): md5.update(chunk)
    return md5.hexdigest()
def get_report_id_by_period(start_date,end_date):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT id FROM reports WHERE start_date=? AND end_date=?',(start_date,end_date));r=c.fetchone();conn.close();return r[0] if r else None
    except: return None
def save_report_to_db(file_name,file_hash,date_period,start_date,end_date,values,metrics,articles):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor()
        c.execute('INSERT INTO reports (file_name,file_hash,date_period,start_date,end_date) VALUES (?,?,?,?,?)',(file_name,file_hash,date_period,start_date,end_date));report_id=c.lastrowid
        logger.info(f"✅ Отчет вставлен, ID: {report_id}")
        if values:
            for cell,val in values.items():
                try: c.execute('INSERT INTO report_values (report_id,cell_name,cell_value) VALUES (?,?,?)',(report_id,cell,float(val)))
                except: pass
        if metrics:
            for mname,mval in metrics.items():
                logger.info(f"💾 Сохраняем метрику: {mname} = {mval}")
                try: c.execute('INSERT INTO report_metrics (report_id,metric_name,metric_value) VALUES (?,?,?)',(report_id,mname,float(mval)))
                except Exception as e: logger.error(f"❌ Ошибка вставки метрики {mname}: {e}")
        if articles:
            inserted=0
            for brand,data in articles.items():
                all_arts={}
                for art,stats in data.get('sales',{}).items():
                    if art not in all_arts: all_arts[art]={'quantity':0,'revenue':0}
                    all_arts[art]['quantity']+=stats.get('quantity',0); all_arts[art]['revenue']+=stats.get('revenue',0)
                for art,stats in data.get('vyk',{}).items():
                    if art not in all_arts: all_arts[art]={'quantity':0,'revenue':0}
                    all_arts[art]['quantity']+=stats.get('quantity',0); all_arts[art]['revenue']+=stats.get('revenue',0)
                for art,stats in all_arts.items():
                    c.execute('INSERT INTO article_stats (report_id,brand,article,quantity,revenue) VALUES (?,?,?,?,?)',(report_id,brand,art,stats['quantity'],stats['revenue'])); inserted+=1
            logger.info(f"📦 Вставлено {inserted} записей артикулов")
        conn.commit();conn.close();return True,report_id
    except Exception as e: logger.error(f"❌ Ошибка сохранения: {e}");return False,None
def delete_report(report_id):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor()
        c.execute('DELETE FROM article_stats WHERE report_id=?',(report_id,))
        c.execute('DELETE FROM report_values WHERE report_id=?',(report_id,))
        c.execute('DELETE FROM report_metrics WHERE report_id=?',(report_id,))
        c.execute('DELETE FROM reports WHERE id=?',(report_id,));conn.commit();deleted=c.rowcount>0;conn.close();return deleted
    except: return False
def delete_reports(report_ids):
    if not report_ids: return 0
    deleted=0
    for rid in report_ids:
        if delete_report(rid): deleted+=1
    return deleted
def get_all_reports(page=0,per_page=10):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT COUNT(*) FROM reports');total=c.fetchone()[0]
        offset=page*per_page
        c.execute('SELECT id,file_name,date_period,start_date,end_date,processed_at FROM reports ORDER BY start_date DESC LIMIT ? OFFSET ?',(per_page,offset))
        results=c.fetchall();conn.close();return results,total
    except: return [],0
def get_all_report_ids():
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT id FROM reports ORDER BY start_date DESC');rows=c.fetchall();conn.close();return [r[0] for r in rows]
    except: return []
def get_report_values(report_id):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT cell_name,cell_value FROM report_values WHERE report_id=?',(report_id,));rows=c.fetchall();conn.close();return {r[0]:r[1] for r in rows}
    except: return {}
def get_report_metrics(report_id):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT metric_name,metric_value FROM report_metrics WHERE report_id=?',(report_id,));rows=c.fetchall();conn.close();return {r[0]:r[1] for r in rows}
    except: return {}
def get_previous_report_id(report_id):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT id FROM reports WHERE start_date < (SELECT start_date FROM reports WHERE id=?) ORDER BY start_date DESC LIMIT 1',(report_id,));r=c.fetchone();conn.close();return r[0] if r else None
    except: return None
def get_previous_reports(current_start_date,limit=12):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT id,start_date,end_date FROM reports WHERE start_date < ? ORDER BY start_date DESC LIMIT ?',(current_start_date,limit));results=c.fetchall();conn.close();return results
    except: return []
def get_article_stats_for_report(report_id,brand=None):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor()
        if brand: c.execute('SELECT article,SUM(quantity) as q,SUM(revenue) as r FROM article_stats WHERE report_id=? AND brand=? GROUP BY article',(report_id,brand))
        else: c.execute('SELECT article,SUM(quantity) as q,SUM(revenue) as r FROM article_stats WHERE report_id=? GROUP BY article',(report_id,))
        rows=c.fetchall();conn.close();return {r[0]:{'quantity':r[1],'revenue':r[2]} for r in rows}
    except: return {}
def get_report_date_range():
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute("SELECT MIN(start_date), MAX(end_date) FROM reports WHERE start_date IS NOT NULL AND end_date IS NOT NULL");r=c.fetchone();conn.close();return r[0],r[1]
    except: return None,None
def get_aggregated_metrics():
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor()
        c.execute('''SELECT COUNT(DISTINCT report_id) as total_reports,
        SUM(CASE WHEN metric_name='wb_total' THEN metric_value ELSE 0 END) as wb_total,
        SUM(CASE WHEN metric_name='wb_carp' THEN metric_value ELSE 0 END) as wb_carp,
        SUM(CASE WHEN metric_name='wb_hara' THEN metric_value ELSE 0 END) as wb_hara,
        AVG(CASE WHEN metric_name='avg_acquiring' THEN metric_value ELSE NULL END) as avg_acquiring,
        SUM(CASE WHEN metric_name='total_profit' THEN metric_value ELSE 0 END) as total_profit,
        AVG(CASE WHEN metric_name='margin' THEN metric_value ELSE NULL END) as avg_margin FROM report_metrics''')
        r=c.fetchone();conn.close()
        if r and r[0] is not None:
            return {'total_reports':r[0] or 0,'wb_total':r[1] or 0,'wb_carp':r[2] or 0,'wb_hara':r[3] or 0,'avg_acquiring':r[4] or 0,'total_profit':r[5] or 0,'avg_margin':r[6] or 0}
        else: return {'total_reports':0,'wb_total':0,'wb_carp':0,'wb_hara':0,'avg_acquiring':0,'total_profit':0,'avg_margin':0}
    except Exception as e: logger.error(f"Ошибка агрегации метрик: {e}");return {'total_reports':0,'wb_total':0,'wb_carp':0,'wb_hara':0,'avg_acquiring':0,'total_profit':0,'avg_margin':0}

def get_news_settings(user_id):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT enabled,query,morning_time,evening_time FROM news_settings WHERE user_id=?',(user_id,));r=c.fetchone();conn.close()
        if r: return {'enabled':bool(r[0]),'query':r[1],'morning_time':r[2],'evening_time':r[3]}
        else: return {'enabled':True,'query':'Wildberries OR ВБ OR Вайлдбериз OR Wildberries.ru','morning_time':'08:30','evening_time':'20:40'}
    except: return {'enabled':True,'query':'Wildberries OR ВБ OR Вайлдбериз OR Wildberries.ru','morning_time':'08:30','evening_time':'20:40'}
def set_news_settings(user_id,enabled=None,query=None,morning_time=None,evening_time=None):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT user_id FROM news_settings WHERE user_id=?',(user_id,))
        if c.fetchone():
            updates=[];params=[]
            if enabled is not None: updates.append("enabled=?");params.append(1 if enabled else 0)
            if query is not None: updates.append("query=?");params.append(query)
            if morning_time is not None: updates.append("morning_time=?");params.append(morning_time)
            if evening_time is not None: updates.append("evening_time=?");params.append(evening_time)
            if updates: params.append(user_id);c.execute(f"UPDATE news_settings SET {', '.join(updates)} WHERE user_id=?",params)
        else:
            c.execute('INSERT INTO news_settings (user_id,enabled,query,morning_time,evening_time) VALUES (?,?,?,?,?)',(user_id,1 if enabled is None else (1 if enabled else 0),query or 'Wildberries OR ВБ OR Вайлдбериз OR Wildberries.ru',morning_time or '08:30',evening_time or '20:40'))
        conn.commit();conn.close();return True
    except Exception as e: logger.error(f"Ошибка сохранения настроек новостей: {e}");return False

NEWS_CACHE={};CACHE_EXPIRY=timedelta(hours=2)
def fetch_news(query,limit=10):
    if not NEWS_API_KEY: return []
    cache_key=query;now=datetime.now()
    if cache_key in NEWS_CACHE and now-NEWS_CACHE[cache_key]['timestamp']<CACHE_EXPIRY:
        return NEWS_CACHE[cache_key]['articles'][:limit]
    url='https://newsapi.org/v2/everything';params={'q':query,'apiKey':NEWS_API_KEY,'pageSize':limit,'language':'ru','sortBy':'publishedAt'}
    try:
        resp=requests.get(url,params=params,timeout=10);data=resp.json()
        if data.get('status')=='ok':
            articles=data.get('articles',[]);articles=[a for a in articles if a.get('title')]
            NEWS_CACHE[cache_key]={'articles':articles,'timestamp':now};return articles[:limit]
        else: logger.error(f"NewsAPI error: {data.get('message')}");return []
    except Exception as e: logger.error(f"Ошибка запроса к NewsAPI: {e}");return []
def format_news_digest(articles,prefix="📰 **Новости**"):
    if not articles: return "Нет свежих новостей по вашей теме."
    digest=f"{prefix}\n\n"
    for i,article in enumerate(articles,1):
        title=article.get('title','');source=article.get('source',{}).get('name','');url=article.get('url','');published=article.get('publishedAt','')
        if published:
            try: dt=datetime.fromisoformat(published.replace('Z','+00:00'));published=dt.strftime('%H:%M')
            except: published=''
        digest+=f"{i}. [{title}]({url}) — *{source}*"
        if published: digest+=f" ({published})"
        digest+="\n"
    return digest

scheduler=BackgroundScheduler()
async def send_news_digest(context,user_id,time_of_day):
    settings=get_news_settings(user_id)
    if not settings['enabled']: return
    articles=fetch_news(settings['query'],limit=10)
    prefix="🌅 **Утренняя сводка**" if time_of_day=='morning' else "🌇 **Вечерняя сводка**"
    text=format_news_digest(articles,prefix)
    try: await context.bot.send_message(chat_id=user_id,text=text,parse_mode='Markdown');logger.info(f"Новостная сводка ({time_of_day}) отправлена пользователю {user_id}")
    except Exception as e: logger.error(f"Ошибка отправки новостей пользователю {user_id}: {e}")
async def scheduled_morning_digest(context):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT user_id FROM news_settings WHERE enabled=1');users=c.fetchall();conn.close()
    except: users=[]
    for (user_id,) in users: await send_news_digest(context,user_id,'morning')
async def scheduled_evening_digest(context):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT user_id FROM news_settings WHERE enabled=1');users=c.fetchall();conn.close()
    except: users=[]
    for (user_id,) in users: await send_news_digest(context,user_id,'evening')

def detect_report_type(filename):
    name=filename.lower()
    if 'осн' in name or 'osn' in name: return 'osn'
    elif 'вык' in name or 'vyk' in name: return 'vyk'
    return None
def parse_date_from_period(date_period):
    try:
        parts=date_period.split('-');start=parts[0].strip();end=parts[1].strip();year=datetime.now().year
        start_dt=datetime.strptime(start+f".{year}","%d.%m.%Y");end_dt=datetime.strptime(end+f".{year}","%d.%m.%Y")
        return start_dt.strftime("%Y-%m-%d"),end_dt.strftime("%Y-%m-%d")
    except: return None,None

flask_app=Flask(__name__,template_folder='templates')
@flask_app.before_request
def log_request_info(): logger.info(f"📥 Запрос: {request.method} {request.path} от {request.remote_addr}")
@flask_app.after_request
def after_request(response):
    response.headers['X-Content-Type-Options']='nosniff';response.headers['X-Frame-Options']='DENY';response.headers['X-XSS-Protection']='1; mode=block';response.headers['Cache-Control']='no-cache, no-store, must-revalidate';response.headers['Pragma']='no-cache';response.headers['Expires']='0';return response
@flask_app.route("/")
def health_check(): return "🤖 Бот работает!",200
@flask_app.route("/ping")
def ping(): return "pong",200
@flask_app.route('/mini')
def mini_app(): logger.info(f"Запрос /mini от {request.remote_addr}");return render_template('dashboard.html')
@flask_app.route('/api/stats')
def api_stats():
    logger.info(f"Запрос /api/stats от {request.remote_addr}")
    try:
        data=get_aggregated_metrics()
        for key in ['total_reports','wb_total','wb_carp','wb_hara','avg_acquiring','total_profit','avg_margin']:
            if key not in data or not isinstance(data[key],(int,float)): data[key]=0
        logger.info(f"API stats OK: {data}");return jsonify(data)
    except Exception as e: logger.error(f"Ошибка в /api/stats: {e}");return jsonify({'error':str(e)}),500
@flask_app.route('/api/debug')
def debug_db():
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor()
        c.execute("SELECT COUNT(*) FROM reports");reports_count=c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM report_metrics");metrics_count=c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM article_stats");articles_count=c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM product_costs_history");costs_count=c.fetchone()[0]
        conn.close()
        return jsonify({'reports_count':reports_count,'metrics_count':metrics_count,'articles_count':articles_count,'costs_count':costs_count})
    except Exception as e: return jsonify({'error':str(e)}),500
def run_flask(): flask_app.run(host="0.0.0.0",port=int(os.getenv("PORT",8080)))

async def check_access(update:Update)->bool:
    if not ALLOWED_USERS: return True
    user_id=update.effective_user.id
    if user_id not in ALLOWED_USERS:
        if update.message: await update.message.reply_text("⛔ Доступ запрещён. Вы не авторизованы.")
        elif update.callback_query: await update.callback_query.answer("⛔ Доступ запрещён.",show_alert=True)
        return False
    return True

class ReportProcessor:
    def process_files(self,osn_path,vyk_path,template_path):
        df_osn=pd.read_excel(osn_path);df_vyk=pd.read_excel(vyk_path)
        logger.info(f"Колонки основного: {df_osn.columns.tolist()}")
        logger.info(f"Колонки выкупов: {df_vyk.columns.tolist()}")
        filename=Path(osn_path).name
        match=re.search(r'(\d{1,2})\.(\d{2})-(\d{1,2})\.(\d{2})',filename)
        date_range=f"{match.group(1)}.{match.group(2)}-{match.group(3)}.{match.group(4)}" if match else datetime.now().strftime("%d.%m")
        values=self._calculate_all_values(df_osn,df_vyk,date_range)
        self._fill_template(template_path,values)
        articles=self._get_articles_stats(df_osn,df_vyk)
        return values,articles,date_range
    def _get_articles_stats(self,df_osn,df_vyk):
        result={}
        def normalize_cols(df): return {str(col).strip().lower():col for col in df.columns}
        cols_osn=normalize_cols(df_osn);cols_vyk=normalize_cols(df_vyk);all_cols={**cols_vyk,**cols_osn}
        qty_variants=['количество','кол-во','количество товара','кол-во (шт.)','кол-во шт','quantity','количество,шт']
        art_variants=['артикул поставщика','артикул','артикул товара','номенклатура','sku','артикул(поставщика)']
        qty_col=art_col=None
        for v in qty_variants:
            if v in all_cols: qty_col=all_cols[v];break
        for v in art_variants:
            if v in all_cols: art_col=all_cols[v];break
        if qty_col is None: logger.warning(f"❌ Колонка количества не найдена. Доступные нормализованные: {list(all_cols.keys())}");return result
        if art_col is None: logger.warning(f"❌ Колонка артикула не найдена. Доступные нормализованные: {list(all_cols.keys())}");return result
        logger.info(f"✅ Найдены колонки: количество='{qty_col}', артикул='{art_col}'")
        for df,key in [(df_osn,'sales'),(df_vyk,'vyk')]:
            for bren,mask_func in [('Цап царапкин',lambda d: (d['Бренд']=='Цап царапкин') | (d['Бренд'].isna())),('Harakiri',lambda d: d['Бренд']=='Harakiri')]:
                mask=mask_func(df);df_bren=df[mask]
                if df_bren.empty: continue
                sales=df_bren[(df_bren['Тип документа']=='Продажа') & (df_bren[qty_col]>0)]
                agg_sales=sales.groupby(art_col).agg(quantity=(qty_col,'sum'),revenue=('Цена розничная','sum')).to_dict('index') if not sales.empty else {}
                articles={}
                for art,vals in agg_sales.items(): articles[art]={'quantity':vals['quantity'],'revenue':vals['revenue']}
                if bren not in result: result[bren]={}
                result[bren][key]=articles
        logger.info(f"📦 Собрано артикулов: {sum(len(v.get('sales',{})) for v in result.values())}")
        return result
    def _calculate_all_values(self,df_osn,df_vyk,date_range):
        values={'B1':date_range,'F1':date_range}
        mask_carp_sale=((df_osn['Бренд']=='Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа']=='Продажа')
        values['B4']=df_osn[mask_carp_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        mask_carp_return=((df_osn['Бренд']=='Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа']=='Возврат')
        values['B5']=df_osn[mask_carp_return]['К перечислению Продавцу за реализованный Товар'].sum()
        mask_carp_all=(df_osn['Бренд']=='Цап царапкин') | (df_osn['Бренд'].isna())
        values['B7']=df_osn[mask_carp_all]['Услуги по доставке товара покупателю'].sum()
        values['B9']=df_osn[mask_carp_all]['Операции на приемке'].sum()
        values['B10']=df_osn['Общая сумма штрафов'].sum()
        values['B11']=df_osn[mask_carp_all]['Удержания'].sum()
        values['B26']=df_osn[mask_carp_all]['Хранение'].sum()
        values['B29']=df_osn[mask_carp_all]['Разовое изменение срока перечисления денежных средств'].sum()
        values['B44']=df_osn[mask_carp_sale]['Цена розничная'].sum()
        mask_hara_sale=(df_osn['Бренд']=='Harakiri') & (df_osn['Тип документа']=='Продажа')
        values['F4']=df_osn[mask_hara_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        mask_hara_return=(df_osn['Бренд']=='Harakiri') & (df_osn['Тип документа']=='Возврат')
        values['F5']=df_osn[mask_hara_return]['К перечислению Продавцу за реализованный Товар'].sum()
        mask_hara_all=df_osn['Бренд']=='Harakiri'
        values['F7']=df_osn[mask_hara_all]['Услуги по доставке товара покупателю'].sum()
        values['F9']=df_osn[mask_hara_all]['Операции на приемке'].sum()
        values['F10']=df_osn[mask_hara_all]['Общая сумма штрафов'].sum()
        values['F11']=df_osn[mask_hara_all]['Удержания'].sum()
        values['B32']=df_osn[mask_hara_sale]['Цена розничная'].sum()
        mask_carp_vyk_sale=((df_vyk['Бренд']=='Цап царапкин') | (df_vyk['Бренд'].isna())) & (df_vyk['Тип документа']=='Продажа')
        values['M4']=df_vyk[mask_carp_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        mask_carp_vyk_return=((df_vyk['Бренд']=='Цап царапкин') | (df_vyk['Бренд'].isna())) & (df_vyk['Тип документа']=='Возврат')
        values['M5']=df_vyk[mask_carp_vyk_return]['К перечислению Продавцу за реализованный Товар'].sum()
        mask_carp_vyk_all=(df_vyk['Бренд']=='Цап царапкин') | (df_vyk['Бренд'].isna())
        values['M7']=df_vyk[mask_carp_vyk_all]['Услуги по доставке товара покупателю'].sum()
        values['M8']=df_vyk[mask_carp_vyk_all]['Операции на приемке'].sum()
        values['M9']=df_vyk['Общая сумма штрафов'].sum()
        values['B47']=df_vyk[mask_carp_vyk_sale]['Цена розничная'].sum()
        mask_hara_vyk_sale=(df_vyk['Бренд']=='Harakiri') & (df_vyk['Тип документа']=='Продажа')
        values['Q4']=df_vyk[mask_hara_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        mask_hara_vyk_return=(df_vyk['Бренд']=='Harakiri') & (df_vyk['Тип документа']=='Возврат')
        values['Q5']=df_vyk[mask_hara_vyk_return]['К перечислению Продавцу за реализованный Товар'].sum()
        mask_hara_vyk_all=df_vyk['Бренд']=='Harakiri'
        values['Q7']=df_vyk[mask_hara_vyk_all]['Услуги по доставке товара покупателю'].sum()
        values['Q8']=df_vyk[mask_hara_vyk_all]['Операции на приемке'].sum()
        values['Q9']=df_vyk[mask_hara_vyk_all]['Общая сумма штрафов'].sum()
        values['B41']=df_vyk[mask_hara_vyk_sale]['Цена розничная'].sum()
        col="Размер компенсации платёжных услуг/Комиссии за интеграцию платёжных сервисов, %"
        if col in df_osn.columns:
            filtered=df_osn[col][df_osn[col].notna() & (df_osn[col]>0)]
            if not filtered.empty:
                values['B56']=filtered.mean();values['B59']=filtered.median();values['B62']=filtered.min();values['B65']=filtered.max()
            else: values['B56']=values['B59']=values['B62']=values['B65']=0
        else: values['B56']=values['B59']=values['B62']=values['B65']=0
        return values
    def _fill_template(self,template_path,values):
        wb=openpyxl.load_workbook(template_path,data_only=False,keep_links=False,keep_vba=False);ws=wb.active
        for cell,value in values.items():
            ws[cell]=value
            if isinstance(value,float) and value!=int(value): ws[cell].number_format='0.00'
        ws.sheet_view.calcMode='manual';wb.save(template_path)

def get_main_menu():
    keyboard=[[InlineKeyboardButton("📱 Открыть приложение",web_app={"url":MINI_APP_URL})],
              [InlineKeyboardButton("📊 Аналитика",callback_data="menu_analytics_main")],
              [InlineKeyboardButton("📂 Архив отчетов",callback_data="menu_history")],
              [InlineKeyboardButton("⚙️ Настройки",callback_data="menu_settings")]]
    return InlineKeyboardMarkup(keyboard)

async def menu_analytics_main_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    q=update.callback_query;await q.answer()
    keyboard=[[InlineKeyboardButton("📈 Аналитика по артикулам",callback_data="menu_analytics")],
              [InlineKeyboardButton("◀️ Назад",callback_data="back_to_menu")]]
    await q.edit_message_text("📊 **Раздел аналитики**\n\nВыберите нужный подраздел:",reply_markup=InlineKeyboardMarkup(keyboard),parse_mode='Markdown')

async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    await update.message.reply_text("👋 Привет! Я бот для аналитики кабинета WB по брендам Цап царапкин & Harakiri.\n\n📊 Используй меню ниже для быстрого доступа к функциям.",reply_markup=get_main_menu())
async def help_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    await update.message.reply_text("📋 **Доступные команды:**\n/start — начать\n/help — помощь\n/osn — отметить файл как основной (вручную)\n/vyk — отметить файл как выкупы (вручную)\n/articles — детали по артикулам (текущий отчет)\n/news_now — получить новости прямо сейчас\n/set_news — настроить новостные сводки\n/set_news_query — изменить поисковый запрос\n\nТакже можно использовать кнопки меню.",parse_mode='Markdown',reply_markup=get_main_menu())

async def menu_history_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    q=update.callback_query;await q.answer();context.user_data['history_page']=0;context.user_data['history_delete_mode']=False;context.user_data['history_selected_for_delete']=[];await show_history_page(q,context,page=0)
async def menu_analytics_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    q=update.callback_query;await q.answer();context.user_data['analytics_selected']=[];context.user_data['analytics_page']=0;await show_analytics_selection(q,context,page=0)

async def menu_settings_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    q=update.callback_query;await q.answer()
    keyboard=[[InlineKeyboardButton("📰 Новости",callback_data="news_settings")],
              [InlineKeyboardButton("💰 Себестоимость",callback_data="menu_costs")],
              [InlineKeyboardButton("◀️ Назад в меню",callback_data="back_to_menu")]]
    await q.edit_message_text("⚙️ **Настройки**\n\nВыберите раздел:",reply_markup=InlineKeyboardMarkup(keyboard),parse_mode='Markdown')

# --- Новости ---
async def news_settings_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    q=update.callback_query;await q.answer();user_id=update.effective_user.id;settings=get_news_settings(user_id)
    status="✅ Включены" if settings['enabled'] else "❌ Отключены"
    text=f"⚙️ **Настройки новостей**\n\nСтатус: {status}\nПоисковый запрос: `{settings['query']}`\nУтреннее время: {settings['morning_time']}\nВечернее время: {settings['evening_time']}\n\nВыберите действие:"
    keyboard=[[InlineKeyboardButton("📰 Получить новости сейчас",callback_data="news_now")],
              [InlineKeyboardButton("🔄 Вкл/Выкл",callback_data="news_toggle")],
              [InlineKeyboardButton("📝 Изменить запрос",callback_data="news_query")],
              [InlineKeyboardButton("🕐 Изменить время",callback_data="news_time")],
              [InlineKeyboardButton("◀️ Назад",callback_data="menu_settings")]]
    await q.edit_message_text(text,reply_markup=InlineKeyboardMarkup(keyboard),parse_mode='Markdown')
async def news_now_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    q=update.callback_query;await q.answer();user_id=update.effective_user.id;settings=get_news_settings(user_id);articles=fetch_news(settings['query'],limit=10);text=format_news_digest(articles,"📰 **Свежие новости по теме Wildberries**")
    await q.edit_message_text(text,parse_mode='Markdown',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад к настройкам",callback_data="menu_settings")],[InlineKeyboardButton("◀️ Назад в меню",callback_data="back_to_menu")]]))
async def news_toggle_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    q=update.callback_query;await q.answer();user_id=update.effective_user.id;settings=get_news_settings(user_id);new_enabled=not settings['enabled'];set_news_settings(user_id,enabled=new_enabled);await news_settings_callback(update,context)
async def news_query_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    q=update.callback_query;await q.answer();await q.edit_message_text("📝 Введите новый поисковый запрос для новостей.\nНапример: `Wildberries OR ВБ`\nИспользуйте команду /set_news_query <запрос>",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад",callback_data="news_settings")]]),parse_mode='Markdown')
async def news_time_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    q=update.callback_query;await q.answer()
    keyboard=[[InlineKeyboardButton("🌅 Утро (08:30)",callback_data="news_time_morning_08:30")],[InlineKeyboardButton("🌅 Утро (09:00)",callback_data="news_time_morning_09:00")],[InlineKeyboardButton("🌅 Утро (07:00)",callback_data="news_time_morning_07:00")],[InlineKeyboardButton("🌇 Вечер (20:40)",callback_data="news_time_evening_20:40")],[InlineKeyboardButton("🌇 Вечер (21:00)",callback_data="news_time_evening_21:00")],[InlineKeyboardButton("🌇 Вечер (19:00)",callback_data="news_time_evening_19:00")],[InlineKeyboardButton("◀️ Назад",callback_data="news_settings")]]
    await q.edit_message_text("Выберите время для утренней/вечерней сводки:",reply_markup=InlineKeyboardMarkup(keyboard))
async def news_time_set_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    q=update.callback_query;await q.answer();data=q.data;parts=data.split("_");time_of_day=parts[2];time_str=parts[3];user_id=update.effective_user.id
    if time_of_day=='morning': set_news_settings(user_id,morning_time=time_str)
    else: set_news_settings(user_id,evening_time=time_str)
    await news_settings_callback(update,context)
async def news_now_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    user_id=update.effective_user.id;settings=get_news_settings(user_id);articles=fetch_news(settings['query'],limit=10);text=format_news_digest(articles,"📰 **Свежие новости по теме Wildberries**")
    await update.message.reply_text(text,parse_mode='Markdown',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад в меню",callback_data="back_to_menu")]]))
async def set_news_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    user_id=update.effective_user.id;settings=get_news_settings(user_id);status="включены" if settings['enabled'] else "отключены"
    text=f"Текущие настройки новостей:\nРассылка: {status}\nЗапрос: `{settings['query']}`\nУтро: {settings['morning_time']}\nВечер: {settings['evening_time']}\n\nИспользуйте меню для настройки (кнопка '⚙️ Настройки' в главном меню)."
    await update.message.reply_text(text,parse_mode='Markdown')
async def set_news_query_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    args=context.args
    if not args: await update.message.reply_text("❌ Укажите запрос. Пример: /set_news_query Wildberries OR ВБ");return
    new_query=' '.join(args);set_news_settings(update.effective_user.id,query=new_query);await update.message.reply_text(f"✅ Поисковый запрос обновлён: `{new_query}`",parse_mode='Markdown')
    def get_news_settings(user_id):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT enabled,query,morning_time,evening_time FROM news_settings WHERE user_id=?',(user_id,));r=c.fetchone();conn.close()
        if r: return {'enabled':bool(r[0]),'query':r[1],'morning_time':r[2],'evening_time':r[3]}
        else: return {'enabled':True,'query':'Wildberries OR ВБ OR Вайлдбериз OR Wildberries.ru','morning_time':'08:30','evening_time':'20:40'}
    except: return {'enabled':True,'query':'Wildberries OR ВБ OR Вайлдбериз OR Wildberries.ru','morning_time':'08:30','evening_time':'20:40'}
def set_news_settings(user_id,enabled=None,query=None,morning_time=None,evening_time=None):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT user_id FROM news_settings WHERE user_id=?',(user_id,))
        if c.fetchone():
            updates=[];params=[]
            if enabled is not None: updates.append("enabled=?");params.append(1 if enabled else 0)
            if query is not None: updates.append("query=?");params.append(query)
            if morning_time is not None: updates.append("morning_time=?");params.append(morning_time)
            if evening_time is not None: updates.append("evening_time=?");params.append(evening_time)
            if updates: params.append(user_id);c.execute(f"UPDATE news_settings SET {', '.join(updates)} WHERE user_id=?",params)
        else:
            c.execute('INSERT INTO news_settings (user_id,enabled,query,morning_time,evening_time) VALUES (?,?,?,?,?)',(user_id,1 if enabled is None else (1 if enabled else 0),query or 'Wildberries OR ВБ OR Вайлдбериз OR Wildberries.ru',morning_time or '08:30',evening_time or '20:40'))
        conn.commit();conn.close();return True
    except Exception as e: logger.error(f"Ошибка сохранения настроек новостей: {e}");return False

NEWS_CACHE={};CACHE_EXPIRY=timedelta(hours=2)
def fetch_news(query,limit=10):
    if not NEWS_API_KEY: return []
    cache_key=query;now=datetime.now()
    if cache_key in NEWS_CACHE and now-NEWS_CACHE[cache_key]['timestamp']<CACHE_EXPIRY:
        return NEWS_CACHE[cache_key]['articles'][:limit]
    url='https://newsapi.org/v2/everything';params={'q':query,'apiKey':NEWS_API_KEY,'pageSize':limit,'language':'ru','sortBy':'publishedAt'}
    try:
        resp=requests.get(url,params=params,timeout=10);data=resp.json()
        if data.get('status')=='ok':
            articles=data.get('articles',[]);articles=[a for a in articles if a.get('title')]
            NEWS_CACHE[cache_key]={'articles':articles,'timestamp':now};return articles[:limit]
        else: logger.error(f"NewsAPI error: {data.get('message')}");return []
    except Exception as e: logger.error(f"Ошибка запроса к NewsAPI: {e}");return []
def format_news_digest(articles,prefix="📰 **Новости**"):
    if not articles: return "Нет свежих новостей по вашей теме."
    digest=f"{prefix}\n\n"
    for i,article in enumerate(articles,1):
        title=article.get('title','');source=article.get('source',{}).get('name','');url=article.get('url','');published=article.get('publishedAt','')
        if published:
            try: dt=datetime.fromisoformat(published.replace('Z','+00:00'));published=dt.strftime('%H:%M')
            except: published=''
        digest+=f"{i}. [{title}]({url}) — *{source}*"
        if published: digest+=f" ({published})"
        digest+="\n"
    return digest

scheduler=BackgroundScheduler()
async def send_news_digest(context,user_id,time_of_day):
    settings=get_news_settings(user_id)
    if not settings['enabled']: return
    articles=fetch_news(settings['query'],limit=10)
    prefix="🌅 **Утренняя сводка**" if time_of_day=='morning' else "🌇 **Вечерняя сводка**"
    text=format_news_digest(articles,prefix)
    try: await context.bot.send_message(chat_id=user_id,text=text,parse_mode='Markdown');logger.info(f"Новостная сводка ({time_of_day}) отправлена пользователю {user_id}")
    except Exception as e: logger.error(f"Ошибка отправки новостей пользователю {user_id}: {e}")
async def scheduled_morning_digest(context):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT user_id FROM news_settings WHERE enabled=1');users=c.fetchall();conn.close()
    except: users=[]
    for (user_id,) in users: await send_news_digest(context,user_id,'morning')
async def scheduled_evening_digest(context):
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor();c.execute('SELECT user_id FROM news_settings WHERE enabled=1');users=c.fetchall();conn.close()
    except: users=[]
    for (user_id,) in users: await send_news_digest(context,user_id,'evening')

def detect_report_type(filename):
    name=filename.lower()
    if 'осн' in name or 'osn' in name: return 'osn'
    elif 'вык' in name or 'vyk' in name: return 'vyk'
    return None
def parse_date_from_period(date_period):
    try:
        parts=date_period.split('-');start=parts[0].strip();end=parts[1].strip();year=datetime.now().year
        start_dt=datetime.strptime(start+f".{year}","%d.%m.%Y");end_dt=datetime.strptime(end+f".{year}","%d.%m.%Y")
        return start_dt.strftime("%Y-%m-%d"),end_dt.strftime("%Y-%m-%d")
    except: return None,None

flask_app=Flask(__name__,template_folder='templates')
@flask_app.before_request
def log_request_info(): logger.info(f"📥 Запрос: {request.method} {request.path} от {request.remote_addr}")
@flask_app.after_request
def after_request(response):
    response.headers['X-Content-Type-Options']='nosniff';response.headers['X-Frame-Options']='DENY';response.headers['X-XSS-Protection']='1; mode=block';response.headers['Cache-Control']='no-cache, no-store, must-revalidate';response.headers['Pragma']='no-cache';response.headers['Expires']='0';return response
@flask_app.route("/")
def health_check(): return "🤖 Бот работает!",200
@flask_app.route("/ping")
def ping(): return "pong",200
@flask_app.route('/mini')
def mini_app(): logger.info(f"Запрос /mini от {request.remote_addr}");return render_template('dashboard.html')
@flask_app.route('/api/stats')
def api_stats():
    logger.info(f"Запрос /api/stats от {request.remote_addr}")
    try:
        data=get_aggregated_metrics()
        for key in ['total_reports','wb_total','wb_carp','wb_hara','avg_acquiring','total_profit','avg_margin']:
            if key not in data or not isinstance(data[key],(int,float)): data[key]=0
        logger.info(f"API stats OK: {data}");return jsonify(data)
    except Exception as e: logger.error(f"Ошибка в /api/stats: {e}");return jsonify({'error':str(e)}),500
@flask_app.route('/api/debug')
def debug_db():
    try:
        conn=sqlite3.connect(str(DB_PATH));c=conn.cursor()
        c.execute("SELECT COUNT(*) FROM reports");reports_count=c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM report_metrics");metrics_count=c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM article_stats");articles_count=c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM product_costs_history");costs_count=c.fetchone()[0]
        conn.close()
        return jsonify({'reports_count':reports_count,'metrics_count':metrics_count,'articles_count':articles_count,'costs_count':costs_count})
    except Exception as e: return jsonify({'error':str(e)}),500
def run_flask(): flask_app.run(host="0.0.0.0",port=int(os.getenv("PORT",8080)))

async def check_access(update:Update)->bool:
    if not ALLOWED_USERS: return True
    user_id=update.effective_user.id
    if user_id not in ALLOWED_USERS:
        if update.message: await update.message.reply_text("⛔ Доступ запрещён. Вы не авторизованы.")
        elif update.callback_query: await update.callback_query.answer("⛔ Доступ запрещён.",show_alert=True)
        return False
    return True

class ReportProcessor:
def process_files(self,osn_path,vyk_path,template_path):
        df_osn=pd.read_excel(osn_path);df_vyk=pd.read_excel(vyk_path)
        logger.info(f"Колонки основного: {df_osn.columns.tolist()}")
        logger.info(f"Колонки выкупов: {df_vyk.columns.tolist()}")
        filename=Path(osn_path).name
        match=re.search(r'(\d{1,2})\.(\d{2})-(\d{1,2})\.(\d{2})',filename)
        date_range=f"{match.group(1)}.{match.group(2)}-{match.group(3)}.{match.group(4)}" if match else datetime.now().strftime("%d.%m")
        values=self._calculate_all_values(df_osn,df_vyk,date_range)
        self._fill_template(template_path,values)
        articles=self._get_articles_stats(df_osn,df_vyk)
        return values,articles,date_range
