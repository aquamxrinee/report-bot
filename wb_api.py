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
    """Получение продаж за период. date_from и date_to в формате YYYY-MM-DD"""
    if not WB_API_TOKEN:
        return {"error": "WB_API_TOKEN не задан"}
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    url = f"{STATISTICS_API}/supplier/sales"
    params = {"dateFrom": date_from, "dateTo": date_to}
    try:
        response = requests.get(url, headers=get_headers(), params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Ошибка получения продаж: {e}")
        return {"error": str(e)}

def get_stocks():
    """Получение текущих остатков на складах"""
    if not WB_API_TOKEN:
        return {"error": "WB_API_TOKEN не задан"}
    url = f"{STATISTICS_API}/supplier/stocks"
    try:
        response = requests.get(url, headers=get_headers(), timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Ошибка получения остатков: {e}")
        return {"error": str(e)}

def get_sales_funnel(nm_ids: list = None, date_from: str = None, date_to: str = None):
    """Получение воронки продаж. nm_ids — список артикулов WB (не ваш артикул поставщика)"""
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
        response = requests.post(url, headers=get_headers(), json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Ошибка получения воронки продаж: {e}")
        return {"error": str(e)}

def get_aggregated_stats():
    """Агрегированные метрики для дашборда"""
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
    
    # Обработка продаж
    if isinstance(sales, list):
        total_revenue = sum(item.get("totalPrice", 0) for item in sales)
        total_orders = len(sales)
        result["total_revenue"] = total_revenue
        result["total_orders"] = total_orders
        result["avg_order_value"] = total_revenue / total_orders if total_orders > 0 else 0
    
    # Обработка остатков
    if isinstance(stocks, list):
        result["total_stock"] = sum(item.get("quantity", 0) for item in stocks)
        result["unique_articles"] = len(set(item.get("nmId") for item in stocks if item.get("nmId")))
    
    # Обработка воронки
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