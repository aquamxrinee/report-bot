import os
import time
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from config import logger, WB_API_TOKEN

STATISTICS_API = "https://statistics-api.wildberries.ru/api/v1"
ANALYTICS_API = "https://seller-analytics-api.wildberries.ru/api/analytics"

_cache = {
    "data": None,
    "timestamp": None,
    "error": None
}
CACHE_TTL = timedelta(hours=3)


def get_headers() -> Dict:
    return {"Authorization": f"Bearer {WB_API_TOKEN}"}


def _safe_request(method, url, params=None, json_data=None, max_retries=3):
    if not WB_API_TOKEN:
        return {"error": "WB_API_TOKEN не задан"}

    for attempt in range(1, max_retries + 1):
        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=get_headers(), params=params, timeout=30)
            else:
                response = requests.post(url, headers=get_headers(), json=json_data, timeout=30)

            if response.status_code == 429:
                logger.warning(f"⚠️ 429 Too Many Requests, попытка {attempt}/{max_retries}")
                retry_after = response.headers.get("Retry-After")
                wait = int(retry_after) + 1 if retry_after else 20 + attempt * 10
                time.sleep(wait)
                continue
            if response.status_code == 404:
                return {"error": f"404 Not Found: {url}", "status_code": 404}
            if response.status_code == 403:
                return {"error": "403 Forbidden", "status_code": 403}
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Ошибка запроса: {e}")
            if attempt == max_retries:
                return {"error": str(e)}
            time.sleep(5 * attempt)
    return {"error": "Превышено количество попыток"}


def get_sales(date_from: str, date_to: str = None) -> Dict:
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    url = f"{STATISTICS_API}/supplier/sales"
    params = {"dateFrom": date_from, "dateTo": date_to}
    return _safe_request("GET", url, params=params)


def get_stocks() -> Dict:
    url = f"{STATISTICS_API}/supplier/stocks"
    return _safe_request("GET", url)


def get_orders(date_from: str, date_to: str = None) -> Dict:
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    url = f"{STATISTICS_API}/supplier/orders"
    params = {"dateFrom": date_from, "dateTo": date_to}
    return _safe_request("GET", url, params=params)


def get_sales_funnel(nm_ids: list = None, date_from: str = None, date_to: str = None, limit=1000) -> Dict:
    if not date_from:
        date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    url = f"{ANALYTICS_API}/v3/sales-funnel/products"
    payload = {"selectedPeriod": {"start": date_from, "end": date_to}, "limit": limit}
    if nm_ids:
        payload["nmIds"] = nm_ids
    return _safe_request("POST", url, json_data=payload)


def get_all_nm_ids_from_api(days_back: int = 90) -> List[int]:
    nm_ids_set = set()
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    sales_data = get_sales(date_from)
    if isinstance(sales_data, list):
        for item in sales_data:
            nm_id = item.get('nmId')
            if nm_id:
                nm_ids_set.add(nm_id)
        logger.info(f"✅ Из продаж получено {len(sales_data)} записей, уникальных nmId: {len(nm_ids_set)}")
    else:
        logger.warning(f"⚠️ Не удалось получить продажи: {sales_data.get('error', 'неизвестная ошибка')}")

    stocks_data = get_stocks()
    if isinstance(stocks_data, list):
        for item in stocks_data:
            nm_id = item.get('nmId')
            if nm_id:
                nm_ids_set.add(nm_id)
        logger.info(f"✅ Из остатков добавлено уникальных nmId: {len(nm_ids_set)}")
    else:
        logger.warning(f"⚠️ Не удалось получить остатки: {stocks_data.get('error', 'неизвестная ошибка')}")

    orders_data = get_orders(date_from)
    if isinstance(orders_data, list):
        for item in orders_data:
            nm_id = item.get('nmId')
            if nm_id:
                nm_ids_set.add(nm_id)
        logger.info(f"✅ Из заказов добавлено уникальных nmId: {len(nm_ids_set)}")
    else:
        logger.warning(f"⚠️ Не удалось получить заказы: {orders_data.get('error', 'неизвестная ошибка')}")

    logger.info(f"📊 Всего уникальных nmId получено: {len(nm_ids_set)}")
    return list(nm_ids_set)


def get_aggregated_stats(force_refresh=False) -> Dict:
    global _cache
    now = datetime.now()
    if not force_refresh and _cache["timestamp"] and (now - _cache["timestamp"]) < CACHE_TTL:
        return _cache["data"] if _cache["data"] else {"error": "Нет данных"}

    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    sales = get_sales(week_ago, today)
    time.sleep(2)
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
        "errors": []
    }

    if isinstance(sales, dict) and "error" in sales:
        result["errors"].append(f"sales: {sales['error']}")
    elif isinstance(sales, list):
        total_revenue = sum(item.get("totalPrice", 0) for item in sales)
        total_orders = len(sales)
        result["total_revenue"] = total_revenue
        result["total_orders"] = total_orders
        result["avg_order_value"] = total_revenue / total_orders if total_orders else 0

    if isinstance(stocks, dict) and "error" in stocks:
        if stocks.get("status_code") != 404:
            result["errors"].append(f"stocks: {stocks['error']}")
    elif isinstance(stocks, list):
        result["total_stock"] = sum(item.get("quantity", 0) for item in stocks)
        result["unique_articles"] = len(set(item.get("nmId") for item in stocks if item.get("nmId")))

    if isinstance(funnel, dict) and "error" in funnel:
        result["errors"].append(f"funnel: {funnel['error']}")
    elif isinstance(funnel, dict) and "data" in funnel:
        products = funnel.get("data", {}).get("products", [])
        for product in products:
            stats = product.get("statistic", {}).get("selected", {})
            result["views"] += stats.get("openCount", 0)
            result["cart_adds"] += stats.get("cartCount", 0)
            result["orders"] += stats.get("orderCount", 0)
            result["purchases"] += stats.get("buyoutCount", 0)
        if result["views"] > 0:
            result["conversion_view_to_cart"] = (result["cart_adds"] / result["views"]) * 100
        if result["cart_adds"] > 0:
            result["conversion_cart_to_order"] = (result["orders"] / result["cart_adds"]) * 100
        if result["orders"] > 0:
            result["conversion_order_to_purchase"] = (result["purchases"] / result["orders"]) * 100

    _cache["data"] = result
    _cache["timestamp"] = now
    return result


def get_articles_stats(nm_ids: List[int], date_from: str = None, date_to: str = None) -> Dict:
    if not date_from:
        date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    result = {"items": [], "errors": []}
    for i in range(0, len(nm_ids), 1000):
        chunk = nm_ids[i:i+1000]
        data = get_sales_funnel(nm_ids=chunk, date_from=date_from, date_to=date_to, limit=len(chunk))
        if isinstance(data, dict) and "error" in data:
            result["errors"].append(data["error"])
        elif isinstance(data, dict) and "data" in data:
            products = data.get("data", {}).get("products", [])
            for product in products:
                product_info = product.get("product", {})
                stats = product.get("statistic", {}).get("selected", {})
                result["items"].append({
                    "nmId": product_info.get("nmId"),
                    "name": product_info.get("title", ""),
                    "brand": product_info.get("brandName", ""),
                    "views": stats.get("openCount", 0),
                    "cart": stats.get("cartCount", 0),
                    "orders": stats.get("orderCount", 0),
                    "purchases": stats.get("buyoutCount", 0),
                    "revenue": stats.get("orderSum", 0)
                })
        time.sleep(2)
    return result


def get_weekly_reports(date_from: str, date_to: str) -> List[Dict]:
    """Получение еженедельных финансовых отчётов."""
    url = f"https://statistics-api.wildberries.ru/api/v1/supplier/reportDetailByPeriod"
    payload = {
        "dateFrom": date_from,
        "dateTo": date_to,
        "limit": 10,
        "rrdid": 0
    }
    data = _safe_request("POST", url, json_data=payload)
    if isinstance(data, dict) and "error" in data:
        logger.error(f"Ошибка получения списка отчётов: {data['error']}")
        return []

    reports = []
    if isinstance(data, list):
        for item in data:
            file_url = item.get("url")
            rtype = item.get("reportType")
            fname = item.get("fileName", "")
            if file_url and rtype in [1, 2]:
                reports.append({
                    "url": file_url,
                    "report_type": rtype,
                    "file_name": fname
                })
    return reports


def get_buyout_by_brands(date_from: str, date_to: str, brand_names: List[str] = None) -> Dict[str, Optional[float]]:
    """
    Возвращает процент выкупа (purchases/orders * 100) по указанным брендам за период.
    Если бренды не указаны, возвращает по всем.
    Результат: {'Цап царапкин': 85.5, 'Harakiri': 72.3} и т.д.
    """
    if brand_names is None:
        brand_names = ['Цап царапкин', 'Harakiri']

    # Получаем все товары за период с большим лимитом
    funnel_data = get_sales_funnel(date_from=date_from, date_to=date_to, limit=2000)
    if isinstance(funnel_data, dict) and "error" in funnel_data:
        logger.error(f"Ошибка получения воронки для выкупов: {funnel_data['error']}")
        return {b: None for b in brand_names}

    products = funnel_data.get("data", {}).get("products", [])
    # Агрегируем по брендам
    brand_orders = {b: 0 for b in brand_names}
    brand_purchases = {b: 0 for b in brand_names}
    for product in products:
        brand = product.get("product", {}).get("brandName", "")
        if brand in brand_names:
            stats = product.get("statistic", {}).get("selected", {})
            orders = stats.get("orderCount", 0)
            purchases = stats.get("buyoutCount", 0)
            brand_orders[brand] += orders
            brand_purchases[brand] += purchases

    result = {}
    for brand in brand_names:
        if brand_orders[brand] > 0:
            result[brand] = round(brand_purchases[brand] / brand_orders[brand] * 100, 1)
        else:
            result[brand] = None
    return result
