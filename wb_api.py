import os
import time
import requests
from datetime import datetime, timedelta
from config import logger, WB_API_TOKEN

STATISTICS_API = "https://statistics-api.wildberries.ru/api/v1"
ANALYTICS_API = "https://seller-analytics-api.wildberries.ru/api/analytics"

def get_headers():
    return {"Authorization": f"Bearer {WB_API_TOKEN}"}

def _safe_request(method, url, params=None, json=None, max_retries=3):
    if not WB_API_TOKEN:
        return {"error": "WB_API_TOKEN не задан"}
    for attempt in range(1, max_retries + 1):
        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=get_headers(), params=params, timeout=30)
            else:
                response = requests.post(url, headers=get_headers(), json=json, timeout=30)
            if response.status_code == 429:
                logger.warning(f"⚠️ 429 Too Many Requests, попытка {attempt}/{max_retries}")
                wait = 10 + attempt * 5
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Ошибка запроса: {e}")
            return {"error": str(e)}
    return {"error": "Превышено количество попыток"}

def get_supplier_prices(nm_ids: list):
    """
    Получает текущие цены продавца (цена до скидки) для указанных nm_id.
    Документация: GET /api/v1/supplier/prices
    """
    url = f"{STATISTICS_API}/supplier/prices"
    params = {"nmId": ",".join(map(str, nm_ids))}
    return _safe_request("GET", url, params=params)

# ... остальные существующие функции (get_sales, get_stocks, etc.) остаются без изменений
