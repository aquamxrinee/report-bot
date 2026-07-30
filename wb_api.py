# ... (предыдущие функции и импорты остаются без изменений) ...

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
        "errors": [],
        "articles": []
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

    time.sleep(2)

    # ---- 2. Остатки (новый метод) ----
    stocks = get_stocks_wb_warehouses(limit=1000)
    if isinstance(stocks, dict) and "error" in stocks:
        if stocks.get("status_code") != 404:
            result["errors"].append(f"stocks: {stocks['error']}")
            logger.error(f"Ошибка остатков: {stocks['error']}")
        else:
            logger.warning("⚠️ Метод stocks-report/wb-warehouses вернул 404")
    elif isinstance(stocks, dict) and "data" in stocks:
        # Исправлено: данные находятся в data.items
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
        # 🔥 ГЛАВНОЕ ИСПРАВЛЕНИЕ: данные находятся в data.products, а не data.items
        products = funnel.get("data", {}).get("products", [])
        for product in products:
            # Статистика за выбранный период находится в product.statistic.selected
            stats = product.get("statistic", {}).get("selected", {})
            result["views"] += stats.get("openCount", 0)
            result["cart_adds"] += stats.get("cartCount", 0)
            result["orders"] += stats.get("orderCount", 0)
            result["purchases"] += stats.get("buyoutCount", 0)

            # Сохраняем детальную статистику по каждому артикулу
            product_info = product.get("product", {})
            result["articles"].append({
                "nmId": product_info.get("nmId"),
                "name": product_info.get("title", ""),
                "brand": product_info.get("brandName", ""),
                "views": stats.get("openCount", 0),
                "cart": stats.get("cartCount", 0),
                "orders": stats.get("orderCount", 0),
                "purchases": stats.get("buyoutCount", 0),
                "revenue": stats.get("orderSum", 0),
                "conversion": stats.get("conversions", {}).get("buyoutPercent", 0)
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

    if result["errors"]:
        logger.warning(f"⚠️ Частичная ошибка: {result['errors']}")
    else:
        logger.info("✅ Данные WB API успешно обновлены")

    _cache["data"] = result
    _cache["timestamp"] = now
    return result

# ... (остальные функции без изменений) ...
