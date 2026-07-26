import os
import time
import requests
from datetime import datetime, timedelta
from config import logger

WB_API_TOKEN = os.getenv("WB_API_TOKEN")
STATISTICS_API = "https://statistics-api.wildberries.ru/api/v1"
ANALYTICS_API = "https://seller-analytics-api.wildberries.ru/api/analytics"

# Кеш для агрегированной статистики
_cache = {
    "data": None,
    "timestamp": None,
    "error": None
}
CACHE_TTL = timedelta(minutes=30)  # обновляем раз в 30 минут, чтобы снизить нагрузку

def get_headers():
    return {"Authorization": f"Bearer {WB_API_TOKEN}"}

def _safe_request(method, url, params=None, json=None, max_retries=3):
    """Выполняет запрос с повторными попытками при 429."""
    if not WB_API_TOKEN:
        return {"error": "WB_API_TOKEN не задан"}
    
    for attempt in range(1, max_retries + 1):
        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=get_headers(), params=params, timeout=30)
            else:
                response = requests.post(url, headers=get_headers(), json=json, timeout=30)
            
            if response.status_code == 429:
                logger.warning(f"⚠️ Превышен лимит запросов к WB API (429), попытка {attempt}/{max_retries}")
                retry_after = response.headers.get("Retry-After")
                wait = int(retry_after) + 1 if retry_after else 5 + attempt * 3
                logger.info(f"⏳ Ожидание {wait} секунд")
                time.sleep(wait)
                continue
            elif response.status_code == 404:
                # Для 404 не повторяем, возвращаем ошибку
                logger.error(f"❌ 404 Not Found: {url}")
                return {"error": f"404 Not Found: {url}"}
            elif response.status_code == 400:
                logger.error(f"❌ 400 Bad Request: {url}, response: {response.text[:200]}")
                return {"error": f"400 Bad Request: {response.text[:200]}"}
            else:
                response.raise_for_status()
                return response.json()
        except requests.exceptions.HTTPError as e:
            if response.status_code == 429:
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

def get_stocks():
    """Остатки — без параметров, просто GET."""
    url = f"{STATISTICS_API}/supplier/stocks"
    return _safe_request("GET", url)

def get_sales_funnel(nm_ids: list = None, date_from: str = None, date_to: str = None):
    if not date_from:
        date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    url = f"{ANALYTICS_API}/v3/sales-funnel/products"
    payload = {"dateFrom": date_from, "dateTo": date_to, "limit": 100}
    if nm_ids:
        payload["nmIds"] = nm_ids
    return _safe_request("POST", url, json=payload)

def get_aggregated_stats(force_refresh=False):
    """Возвращает агрегированные метрики с кешированием."""
    global _cache
    now = datetime.now()
    
    # Если кеш свежий и не запрошено принудительное обновление
    if not force_refresh and _cache["timestamp"] and (now - _cache["timestamp"]) < CACHE_TTL:
        logger.info("📦 Используем кешированные данные WB API")
        return _cache["data"] if _cache["data"] is not None else {"error": "Нет данных"}
    
    logger.info("🔄 Обновляем данные WB API")
    
    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    
    # Выполняем запросы с задержкой между ними, чтобы избежать 429
    sales = get_sales(week_ago, today)
    time.sleep(2)  # задержка между запросами
    stocks = get_stocks()
    time.sleep(2)
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
        "last_update": now.isoformat(),
        "error": None
    }
    
    # Обработка продаж
    if isinstance(sales, dict) and "error" in sales:
        result["error"] = sales["error"]
        logger.error(f"Ошибка продаж: {sales['error']}")
    elif isinstance(sales, list):
        total_revenue = sum(item.get("totalPrice", 0) for item in sales)
        total_orders = len(sales)
        result["total_revenue"] = total_revenue
        result["total_orders"] = total_orders
        result["avg_order_value"] = total_revenue / total_orders if total_orders > 0 else 0
    else:
        result["error"] = "Неизвестный формат ответа sales"
    
    # Обработка остатков
    if isinstance(stocks, dict) and "error" in stocks:
        result["error"] = result["error"] or stocks["error"]
        logger.error(f"Ошибка остатков: {stocks['error']}")
    elif isinstance(stocks, list):
        result["total_stock"] = sum(item.get("quantity", 0) for item in stocks)
        result["unique_articles"] = len(set(item.get("nmId") for item in stocks if item.get("nmId")))
    else:
        result["error"] = result["error"] or "Неизвестный формат ответа stocks"
    
    # Обработка воронки
    if isinstance(funnel, dict) and "error" in funnel:
        result["error"] = result["error"] or funnel["error"]
        logger.error(f"Ошибка воронки: {funnel['error']}")
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
    else:
        result["error"] = result["error"] or "Неизвестный формат ответа funnel"
    
    # Если все запросы вернули ошибку, помечаем
    if result["error"]:
        logger.warning(f"⚠️ Часть данных не загрузилась: {result['error']}")
    else:
        logger.info("✅ Данные WB API успешно обновлены")
    
    # Сохраняем в кеш даже в случае ошибки, чтобы не долбить API
    _cache["data"] = result
    _cache["timestamp"] = now
    return result
