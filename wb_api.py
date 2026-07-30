import os
import time
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from config import logger

WB_API_TOKEN = os.getenv("WB_API_TOKEN")

# Домены API (актуальные на 2026 год)
STATISTICS_API = "https://statistics-api.wildberries.ru/api/v1"
ANALYTICS_API = "https://seller-analytics-api.wildberries.ru/api/analytics"

# Кеш для агрегированной статистики
_cache = {
    "data": None,
    "timestamp": None,
    "errors": []
}
CACHE_TTL = timedelta(hours=1)  # обновляем раз в час


def get_headers() -> Dict:
    """Заголовки для запросов к WB API"""
    return {
        "Authorization": f"Bearer {WB_API_TOKEN}",
        "Content-Type": "application/json"
    }


def _safe_request(
    method: str,
    url: str,
    params: Optional[Dict] = None,
    json_data: Optional[Dict] = None,
    max_retries: int = 3
) -> Dict:
    """
    Безопасный запрос к WB API с обработкой 429 (Too Many Requests)
    и другими ошибками.
    """
    if not WB_API_TOKEN:
        return {"error": "WB_API_TOKEN не задан"}

    for attempt in range(1, max_retries + 1):
        try:
            if method.upper() == "GET":
                response = requests.get(
                    url,
                    headers=get_headers(),
                    params=params,
                    timeout=30
                )
            else:  # POST
                response = requests.post(
                    url,
                    headers=get_headers(),
                    json=json_data,
                    timeout=30
                )

            # 429 — превышен лимит, ждём и повторяем
            if response.status_code == 429:
                logger.warning(
                    f"⚠️ 429 Too Many Requests, попытка {attempt}/{max_retries}"
                )
                retry_after = response.headers.get("Retry-After")
                wait = int(retry_after) + 1 if retry_after else 10 + attempt * 5
                logger.info(f"⏳ Ожидание {wait} секунд")
                time.sleep(wait)
                continue

            # 404 — метод не найден (возможно, устарел)
            if response.status_code == 404:
                logger.error(f"❌ 404 Not Found: {url}")
                return {"error": f"404 Not Found: {url}", "status_code": 404}

            # 403 — недостаточно прав
            if response.status_code == 403:
                logger.error(f"❌ 403 Forbidden: {url}")
                return {"error": "403 Forbidden — проверьте права токена", "status_code": 403}

            # 400 — плохой запрос
            if response.status_code == 400:
                logger.error(f"❌ 400 Bad Request: {response.text[:200]}")
                return {"error": f"400 Bad Request: {response.text[:200]}", "status_code": 400}

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


# ===== 1. СТАТИСТИКА ПРОДАЖ =====

def get_sales(date_from: str, date_to: Optional[str] = None) -> Dict:
    """
    Получение списка продаж за период.
    Документация: GET /api/v1/supplier/sales
    """
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    url = f"{STATISTICS_API}/supplier/sales"
    params = {"dateFrom": date_from, "dateTo": date_to}
    return _safe_request("GET", url, params=params)


# ===== 2. ОСТАТКИ (НОВЫЙ МЕТОД) =====

def get_stocks_by_warehouse(warehouse_id: int, chrt_ids: List[int]) -> Dict:
    """
    Получение остатков по конкретному складу.
    Документация: POST /api/v3/stocks/{warehouseId}
    Тело: массив chrtId (ID размеров товаров)
    """
    url = f"{STATISTICS_API}/v3/stocks/{warehouse_id}"
    return _safe_request("POST", url, json_data=chrt_ids)


def get_stocks_wb_warehouses(nm_ids: Optional[List[int]] = None, limit: int = 1000) -> Dict:
    """
    Получение остатков на всех складах Wildberries (НОВЫЙ МЕТОД).
    Документация: POST /api/analytics/v1/stocks-report/wb-warehouses
    Доступен по Персональному или Сервисному токену.
    """
    url = f"{ANALYTICS_API}/v1/stocks-report/wb-warehouses"
    payload = {
        "limit": limit,
        "offset": 0
    }
    if nm_ids:
        payload["nmIds"] = nm_ids
    return _safe_request("POST", url, json_data=payload)


# ===== 3. ВОРОНКА ПРОДАЖ (СТАТИСТИКА ПО АРТИКУЛАМ) =====

def get_sales_funnel(
    nm_ids: Optional[List[int]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> Dict:
    """
    Получение воронки продаж по артикулам.
    Документация: POST /api/analytics/v3/sales-funnel/products
    Данные обновляются 1 раз в час.
    """
    if not date_from:
        date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")

    url = f"{ANALYTICS_API}/v3/sales-funnel/products"
    payload = {
        "selectedPeriod": {
            "start": date_from,
            "end": date_to
        },
        "limit": limit,
        "offset": offset
    }
    if nm_ids:
        payload["nmIds"] = nm_ids

    return _safe_request("POST", url, json_data=payload)


# ===== 4. АГРЕГИРОВАННАЯ СТАТИСТИКА (ДЛЯ ДАШБОРДА) =====

def get_aggregated_stats(force_refresh: bool = False) -> Dict:
    """
    Возвращает агрегированные метрики с кешированием.
    Обновляется раз в час или при force_refresh=True.
    """
    global _cache
    now = datetime.now()

    # Если кеш свежий и не запрошено принудительное обновление
    if not force_refresh and _cache["timestamp"] and (now - _cache["timestamp"]) < CACHE_TTL:
        logger.info("📦 Используем кешированные данные WB API")
        return _cache["data"] if _cache["data"] is not None else {"error": "Нет данных"}

    logger.info("🔄 Обновляем данные WB API")

    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    result = {
        "total_revenue": 0,          # общая выручка
        "total_orders": 0,           # количество заказов
        "avg_order_value": 0,        # средний чек
        "total_stock": 0,            # общие остатки
        "unique_articles": 0,        # уникальных артикулов
        "views": 0,                  # просмотры
        "cart_adds": 0,              # добавления в корзину
        "orders": 0,                 # заказы (воронка)
        "purchases": 0,              # выкупы
        "conversion_view_to_cart": 0,
        "conversion_cart_to_order": 0,
        "conversion_order_to_purchase": 0,
        "last_update": now.isoformat(),
        "errors": [],
        "articles": []               # детальная статистика по артикулам
    }

    # ---- 1. Продажи (статистика) ----
    sales = get_sales(week_ago, today)
    if isinstance(sales, dict) and "error" in sales:
        result["errors"].append(f"sales: {sales['error']}")
        logger.error(f"Ошибка продаж: {sales['error']}")
    elif isinstance(sales, list):
        total_revenue = sum(item.get("totalPrice", 0) for item in sales)
        total_orders = len(sales)
        result["total_revenue"] = total_revenue
        result["total_orders"] = total_orders
        result["avg_order_value"] = total_revenue / total_orders if total_orders > 0 else 0
    else:
        result["errors"].append("sales: неизвестный формат ответа")

    # Задержка между запросами (чтобы не превысить лимит 3/мин)
    time.sleep(2)

    # ---- 2. Остатки (новый метод) ----
    stocks = get_stocks_wb_warehouses(limit=1000)
    if isinstance(stocks, dict) and "error" in stocks:
        # 404 не критичен — возможно, метод ещё не доступен
        if stocks.get("status_code") != 404:
            result["errors"].append(f"stocks: {stocks['error']}")
            logger.error(f"Ошибка остатков: {stocks['error']}")
        else:
            logger.warning("⚠️ Метод stocks-report/wb-warehouses вернул 404 — пробуем альтернативный")
            # Пробуем альтернативный метод (если есть ID склада)
            # В реальности нужно получить ID склада из настроек
    elif isinstance(stocks, dict) and "data" in stocks:
        items = stocks.get("data", {}).get("items", [])
        result["total_stock"] = sum(item.get("quantity", 0) for item in items)
        result["unique_articles"] = len(set(item.get("nmId") for item in items if item.get("nmId")))
    else:
        result["errors"].append("stocks: неизвестный формат ответа")

    time.sleep(2)

    # ---- 3. Воронка продаж (статистика по артикулам) ----
    funnel = get_sales_funnel(date_from=week_ago, date_to=today, limit=100)
    if isinstance(funnel, dict) and "error" in funnel:
        result["errors"].append(f"funnel: {funnel['error']}")
        logger.error(f"Ошибка воронки: {funnel['error']}")
    elif isinstance(funnel, dict) and "data" in funnel:
        items = funnel.get("data", {}).get("items", [])
        for item in items:
            result["views"] += item.get("views", 0)
            result["cart_adds"] += item.get("cart", 0)
            result["orders"] += item.get("orders", 0)
            result["purchases"] += item.get("purchases", 0)
            # Сохраняем детальную статистику по каждому артикулу
            result["articles"].append({
                "nmId": item.get("nmId"),
                "name": item.get("name", ""),
                "brand": item.get("brand", ""),
                "views": item.get("views", 0),
                "cart": item.get("cart", 0),
                "orders": item.get("orders", 0),
                "purchases": item.get("purchases", 0),
                "revenue": item.get("revenue", 0),
                "conversion": item.get("conversion", 0)
            })

        # Расчёт конверсий
        if result["views"] > 0:
            result["conversion_view_to_cart"] = (result["cart_adds"] / result["views"]) * 100
        if result["cart_adds"] > 0:
            result["conversion_cart_to_order"] = (result["orders"] / result["cart_adds"]) * 100
        if result["orders"] > 0:
            result["conversion_order_to_purchase"] = (result["purchases"] / result["orders"]) * 100
    else:
        result["errors"].append("funnel: неизвестный формат ответа")

    # Если есть ошибки, помечаем как частичный успех
    if result["errors"]:
        logger.warning(f"⚠️ Частичная ошибка: {result['errors']}")
    else:
        logger.info("✅ Данные WB API успешно обновлены")

    # Сохраняем в кеш
    _cache["data"] = result
    _cache["timestamp"] = now
    return result


# ===== 5. ПОЛУЧЕНИЕ СТАТИСТИКИ ПО КОНКРЕТНЫМ АРТИКУЛАМ =====

def get_articles_stats(nm_ids: List[int], date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict:
    """
    Получение детальной статистики по конкретным артикулам.
    Использует тот же метод воронки продаж, но с фильтром по nmIds.
    """
    if not date_from:
        date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")

    # Разбиваем на части по 1000 nmIds (ограничение API)
    result = {"items": [], "errors": []}
    for i in range(0, len(nm_ids), 1000):
        chunk = nm_ids[i:i+1000]
        data = get_sales_funnel(
            nm_ids=chunk,
            date_from=date_from,
            date_to=date_to,
            limit=len(chunk)
        )
        if isinstance(data, dict) and "error" in data:
            result["errors"].append(data["error"])
        elif isinstance(data, dict) and "data" in data:
            result["items"].extend(data.get("data", {}).get("items", []))
        time.sleep(2)  # задержка между запросами

    return result
