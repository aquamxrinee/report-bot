import os
import time
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from config import logger, WB_API_TOKEN

STATISTICS_API = "https://statistics-api.wildberries.ru/api/v1"
ANALYTICS_API = "https://seller-analytics-api.wildberries.ru/api/analytics"
FINANCE_API = "https://finance-api.wildberries.ru/api/finance/v1"

_cache = {
    "data": None,
    "timestamp": None,
    "error": None
}
CACHE_TTL = timedelta(hours=3)


def get_headers() -> Dict:
    return {"Authorization": f"Bearer {WB_API_TOKEN}"}


def _safe_request(method, url, params=None, json_data=None, max_retries=3, raw=False):
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
            if response.status_code >= 400:
                try:
                    error_body = response.text[:500]
                except:
                    error_body = "(не удалось прочитать тело)"
                logger.error(f"❌ API {response.status_code} для {url}. Ответ: {error_body}")
                response.raise_for_status()

            if raw:
                return response
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

    stocks_data = get_stocks()
    if isinstance(stocks_data, list):
        for item in stocks_data:
            nm_id = item.get('nmId')
            if nm_id:
                nm_ids_set.add(nm_id)

    orders_data = get_orders(date_from)
    if isinstance(orders_data, list):
        for item in orders_data:
            nm_id = item.get('nmId')
            if nm_id:
                nm_ids_set.add(nm_id)

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
        "total_revenue": 0, "total_orders": 0, "avg_order_value": 0,
        "total_stock": 0, "unique_articles": 0,
        "views": 0, "cart_adds": 0, "orders": 0, "purchases": 0,
        "conversion_view_to_cart": 0, "conversion_cart_to_order": 0,
        "conversion_order_to_purchase": 0,
        "last_update": now.isoformat(), "errors": []
    }

    if isinstance(sales, list):
        result["total_revenue"] = sum(item.get("totalPrice", 0) for item in sales)
        result["total_orders"] = len(sales)
        result["avg_order_value"] = result["total_revenue"] / result["total_orders"] if result["total_orders"] else 0
    elif isinstance(sales, dict) and "error" in sales:
        result["errors"].append(f"sales: {sales['error']}")

    if isinstance(stocks, list):
        result["total_stock"] = sum(item.get("quantity", 0) for item in stocks)
        result["unique_articles"] = len(set(item.get("nmId") for item in stocks if item.get("nmId")))
    elif isinstance(stocks, dict) and "error" in stocks:
        if stocks.get("status_code") != 404:
            result["errors"].append(f"stocks: {stocks['error']}")

    if isinstance(funnel, dict) and "data" in funnel:
        products = funnel["data"].get("products", [])
        for p in products:
            stats = p.get("statistic", {}).get("selected", {})
            result["views"] += stats.get("openCount", 0)
            result["cart_adds"] += stats.get("cartCount", 0)
            result["orders"] += stats.get("orderCount", 0)
            result["purchases"] += stats.get("buyoutCount", 0)
        if result["views"]: result["conversion_view_to_cart"] = result["cart_adds"] / result["views"] * 100
        if result["cart_adds"]: result["conversion_cart_to_order"] = result["orders"] / result["cart_adds"] * 100
        if result["orders"]: result["conversion_order_to_purchase"] = result["purchases"] / result["orders"] * 100
    elif isinstance(funnel, dict) and "error" in funnel:
        result["errors"].append(f"funnel: {funnel['error']}")

    _cache["data"] = result
    _cache["timestamp"] = now
    return result


def get_articles_stats(nm_ids: List[int], date_from: str = None, date_to: str = None) -> Dict:
    if not date_from: date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not date_to: date_to = datetime.now().strftime("%Y-%m-%d")
    result = {"items": [], "errors": []}
    for i in range(0, len(nm_ids), 1000):
        chunk = nm_ids[i:i+1000]
        data = get_sales_funnel(nm_ids=chunk, date_from=date_from, date_to=date_to, limit=len(chunk))
        if isinstance(data, dict) and "data" in data:
            for p in data["data"].get("products", []):
                info = p.get("product", {})
                stats = p.get("statistic", {}).get("selected", {})
                result["items"].append({
                    "nmId": info.get("nmId"),
                    "name": info.get("title", ""),
                    "brand": info.get("brandName", ""),
                    "views": stats.get("openCount", 0),
                    "cart": stats.get("cartCount", 0),
                    "orders": stats.get("orderCount", 0),
                    "purchases": stats.get("buyoutCount", 0),
                    "revenue": stats.get("orderSum", 0)
                })
        elif isinstance(data, dict) and "error" in data:
            result["errors"].append(data["error"])
        time.sleep(2)
    return result


def get_weekly_reports(date_from: str, date_to: str) -> List[Dict]:
    url = f"{FINANCE_API}/sales-reports/list"
    payload = {
        "dateFrom": date_from,
        "dateTo": date_to,
        "limit": 10,
        "period": "weekly"
    }
    logger.info(f"Запрос списка отчётов через POST {url}")
    data = _safe_request("POST", url, json_data=payload)

    if isinstance(data, dict) and "error" in data:
        logger.error(f"Ошибка получения списка отчётов: {data['error']}")
        return []

    if isinstance(data, list):
        reports = []
        for item in data:
            rtype = item.get("reportType")
            if rtype in [1, 2]:
                reports.append({
                    "report_id": item.get("reportId"),
                    "report_type": rtype,
                    "date_from": item.get("dateFrom"),
                    "date_to": item.get("dateTo"),
                    "create_date": item.get("createDate")
                })
        logger.info(f"✅ Найдено отчётов: {len(reports)}")
        return reports
    else:
        logger.warning(f"Неожиданный ответ от sales-reports/list: {str(data)[:500]}")
        return []


def get_report_detail(report_id: int) -> List[Dict]:
    url = f"{FINANCE_API}/sales-reports/detailed/{report_id}"
    all_rows = []
    rrd_id = 0
    limit = 100000
    while True:
        payload = {
            "limit": limit,
            "rrdId": rrd_id
        }
        logger.info(f"Запрос детализации отчёта {report_id}, rrdId={rrd_id}")
        data = _safe_request("POST", url, json_data=payload)

        if isinstance(data, dict):
            if "error" in data:
                logger.error(f"Ошибка получения детализации отчёта {report_id}: {data['error']}")
                return []
            # Если вернулся словарь, возможно, это конец данных (пустой ответ)
            break
        if isinstance(data, list):
            if not data:
                break
            all_rows.extend(data)
            rrd_id = data[-1].get("rrdId", 0)
            if rrd_id == 0 or len(data) < limit:
                break
            time.sleep(1)
        else:
            logger.warning(f"Неожиданный формат детализации отчёта {report_id}: {str(data)[:500]}")
            break

    logger.info(f"Всего строк детализации для отчёта {report_id}: {len(all_rows)}")
    return all_rows


def get_buyout_by_brands(date_from: str, date_to: str, brand_names: List[str] = None) -> Dict[str, Optional[float]]:
    if brand_names is None:
        brand_names = ['Цап царапкин', 'Harakiri']

    funnel_data = get_sales_funnel(date_from=date_from, date_to=date_to, limit=1000)
    if isinstance(funnel_data, dict) and "error" in funnel_data:
        logger.error(f"Ошибка получения воронки для выкупов: {funnel_data['error']}")
        try:
            nm_ids = get_all_nm_ids_from_api(days_back=90)
            if nm_ids:
                funnel_data = get_sales_funnel(nm_ids=nm_ids, date_from=date_from, date_to=date_to, limit=1000)
        except Exception as e:
            logger.error(f"Исключение при повторном запросе выкупов: {e}")
            return {b: None for b in brand_names}

    if isinstance(funnel_data, dict) and "error" in funnel_data:
        return {b: None for b in brand_names}

    products = funnel_data.get("data", {}).get("products", [])
    brand_orders = {b: 0 for b in brand_names}
    brand_purchases = {b: 0 for b in brand_names}
    for p in products:
        brand = p.get("product", {}).get("brandName", "")
        if brand in brand_names:
            stats = p.get("statistic", {}).get("selected", {})
            brand_orders[brand] += stats.get("orderCount", 0)
            brand_purchases[brand] += stats.get("buyoutCount", 0)

    result = {}
    for b in brand_names:
        result[b] = round(brand_purchases[b] / brand_orders[b] * 100, 1) if brand_orders[b] > 0 else None
    return result
