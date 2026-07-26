import os
import time
import random
import requests
from datetime import datetime, timedelta
from config import logger

WB_API_TOKEN = os.getenv("WB_API_TOKEN")
STATISTICS_API = "https://statistics-api.wildberries.ru/api/v1"
ANALYTICS_API = "https://seller-analytics-api.wildberries.ru/api/analytics"

_cache = {
    "data": None,
    "timestamp": None,
    "error": None
}
CACHE_TTL = timedelta(minutes=30)  # увеличил до 30 минут

def get_headers():
    return {"Authorization": f"Bearer {WB_API_TOKEN}"}

def _safe_request(method, url, params=None, json=None, retries=2):
    if not WB_API_TOKEN:
        return {"error": "WB_API_TOKEN не задан"}
    for attempt in range(retries + 1):
        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=get_headers(), params=params, timeout=30)
            else:
                response = requests.post(url, headers=get_headers(), json=json, timeout=30)
            
            if response.status_code == 429:
                logger.warning(f"⚠️ Превышен лимит запросов к WB API (429), попытка {attempt+1}/{retries+1}")
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    wait = int(retry_after) + 1
                else:
                    wait = 5 + random.randint(1, 5)  # случайная задержка
                logger.info(f"⏳ Ожидание {wait} секунд")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if response.status_code == 429 and attempt < retries:
                continue
            logger.error(f"HTTP ошибка: {e}")
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"Ошибка запроса: {e}")
            return {"error": str(e)}
    return {"error": "Превышено количество попыток"}

def get_sales(date_from: str, date_to: str = None):
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    url = f"{STATISTICS_API}/supplier/sales"
    params = {"dateFrom": date_from, "dateTo": date_to}
    return _safe_request("GET", url, params=params)

def get_stocks(date_from: str = None):
    if not date_from:
        date_from = datetime.now().strftime("%Y-%m-%d")
    # Правильный URL для остатков с обязательным параметром dateFrom
    url = f"{STATISTICS_API}/supplier/stocks"
    params = {"dateFrom": date_from}
    return _safe_request("GET", url, params=params)

def get_sales_funnel(nm_ids: list = None, date_from: str = None, date_to: str = None):
    if not date_from:
        date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    url = f"{ANALYTICS_API}/v3/sales-funnel/products"
    payload = {
        "dateFrom": date_from,
        "dateTo": date_to,
        "limit": 100
    }
    # В документации limit может быть до 100, но если не работает, убираем
    if nm_ids:
        payload["nmIds"] = nm_ids
    # Попробуем без limit, если ошибка 400
    result = _safe_request("POST", url, json=payload)
    if isinstance(result, dict) and "error" in result and "400" in result["error"]:
        logger.warning("Попытка без параметра limit")
        del payload["limit"]
        result = _safe_request("POST", url, json=payload)
    return result

def get_aggregated_stats(force_refresh=False):
    global _cache
    now = datetime.now()
    
    if not force_refresh and _cache["timestamp"] and (now - _cache["timestamp"]) < CACHE_TTL:
        logger.info("📦 Используем кешированные данные WB API")
        return _cache["data"] if _cache["data"] is not None else {"error": "Нет данных"}
    
    logger.info("🔄 Обновляем данные WB API")
    
    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    
    # Добавляем небольшие задержки между запросами, чтобы не бить лимит
    sales = get_sales(week_ago, today)
    time.sleep(1 + random.random())
    stocks = get_stocks(today)  # остатки на сегодня
    time.sleep(1 + random.random())
    funnel = get_sales_funnel(date_from=week_ago, date_to=today)
    
    result = {
        "total_revenue": 0,
        "total_orders": 0,
        "avg_order_value": 0,
        "total_stock": 0,
        "unique_articles": 0,
        "views": 0,
        "cart_adds": 0,
        "orders": 0,
        "purchases": 0,
        "conversion_view_to_cart": 0,
        "conversion_cart_to_order": 0,
        "conversion_order_to_purchase": 0,
        "last_update": now.isoformat()
    }
    
    # Обработка продаж
    if isinstance(sales, dict) and "error" in sales:
        result["error_sales"] = sales["error"]
    elif isinstance(sales, list):
        total_revenue = sum(item.get("totalPrice", 0) for item in sales)
        total_orders = len(sales)
        result["total_revenue"] = total_revenue
        result["total_orders"] = total_orders
        result["avg_order_value"] = total_revenue / total_orders if total_orders > 0 else 0
    
    # Обработка остатков
    if isinstance(stocks, dict) and "error" in stocks:
        result["error_stocks"] = stocks["error"]
    elif isinstance(stocks, list):
        result["total_stock"] = sum(item.get("quantity", 0) for item in stocks)
        result["unique_articles"] = len(set(item.get("nmId") for item in stocks if item.get("nmId")))
    
    # Обработка воронки
    if isinstance(funnel, dict) and "error" in funnel:
        result["error_funnel"] = funnel["error"]
    elif isinstance(funnel, dict) and "data" in funnel:
        for item in funnel.get("data", []):
            result["views"] += item.get("views", 0)
            result["cart_adds"] += item.get("cart", 0)
            result["orders"] += item.get("orders", 0)
            result["purchases"] += item.get("purchases", 0)
        
        if result["views"] > 0:
            result["conversion_view_to_cart"] = (result["cart_adds"] / result["views"]) * 100
        if result["cart_adds"] > 0:
            result["conversion_cart_to_order"] = (result["orders"] / result["cart_adds"]) * 100
        if result["orders"] > 0:
            result["conversion_order_to_purchase"] = (result["purchases"] / result["orders"]) * 100
    
    if any(k.startswith("error_") for k in result):
        result["error"] = "Часть данных не загрузилась"
    
    _cache["data"] = result
    _cache["timestamp"] = now
    return result
