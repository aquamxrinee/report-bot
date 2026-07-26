import os
import requests
from datetime import datetime, timedelta
from config import logger

WB_API_TOKEN = os.getenv("WB_API_TOKEN")
STATISTICS_API = "https://statistics-api.wildberries.ru/api/v1"
ANALYTICS_API = "https://seller-analytics-api.wildberries.ru/api/analytics"

def get_headers():
    return {"Authorization": f"Bearer {WB_API_TOKEN}"}

def get_sales(date_from: str, date_to: str = None):
    if not WB_API_TOKEN:
        return {"error": "WB_API_TOKEN не задан"}
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    url = f"{STATISTICS_API}/supplier/sales"
    params = {"dateFrom": date_from, "dateTo": date_to}
    try:
        logger.info(f"Запрос продаж: {url}, params={params}")
        response = requests.get(url, headers=get_headers(), params=params, timeout=30)
        logger.info(f"Статус продаж: {response.status_code}")
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Ошибка продаж: {response.status_code} {response.text}")
            return {"error": f"HTTP {response.status_code}: {response.text[:100]}"}
    except Exception as e:
        logger.error(f"Ошибка получения продаж: {e}")
        return {"error": str(e)}

def get_stocks():
    if not WB_API_TOKEN:
        return {"error": "WB_API_TOKEN не задан"}
    url = f"{STATISTICS_API}/supplier/stocks"
    try:
        logger.info(f"Запрос остатков: {url}")
        response = requests.get(url, headers=get_headers(), timeout=30)
        logger.info(f"Статус остатков: {response.status_code}")
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Ошибка остатков: {response.status_code} {response.text}")
            return {"error": f"HTTP {response.status_code}: {response.text[:100]}"}
    except Exception as e:
        logger.error(f"Ошибка получения остатков: {e}")
        return {"error": str(e)}

def get_sales_funnel(nm_ids: list = None, date_from: str = None, date_to: str = None):
    if not WB_API_TOKEN:
        return {"error": "WB_API_TOKEN не задан"}
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
    if nm_ids:
        payload["nmIds"] = nm_ids
    try:
        logger.info(f"Запрос воронки: {url}, payload={payload}")
        response = requests.post(url, headers=get_headers(), json=payload, timeout=30)
        logger.info(f"Статус воронки: {response.status_code}")
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Ошибка воронки: {response.status_code} {response.text}")
            return {"error": f"HTTP {response.status_code}: {response.text[:100]}"}
    except Exception as e:
        logger.error(f"Ошибка получения воронки продаж: {e}")
        return {"error": str(e)}

def get_aggregated_stats():
    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    
    sales = get_sales(week_ago, today)
    stocks = get_stocks()
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
        "conversion_order_to_purchase": 0
    }
    
    # Если sales — это ошибка (dict с 'error'), возвращаем её
    if isinstance(sales, dict) and 'error' in sales:
        return {"error": sales['error']}
    if isinstance(stocks, dict) and 'error' in stocks:
        return {"error": stocks['error']}
    if isinstance(funnel, dict) and 'error' in funnel:
        return {"error": funnel['error']}
    
    if isinstance(sales, list):
        total_revenue = sum(item.get("totalPrice", 0) for item in sales)
        total_orders = len(sales)
        result["total_revenue"] = total_revenue
        result["total_orders"] = total_orders
        result["avg_order_value"] = total_revenue / total_orders if total_orders > 0 else 0
    
    if isinstance(stocks, list):
        result["total_stock"] = sum(item.get("quantity", 0) for item in stocks)
        result["unique_articles"] = len(set(item.get("nmId") for item in stocks if item.get("nmId")))
    
    if isinstance(funnel, dict) and "data" in funnel:
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
    
    return result
