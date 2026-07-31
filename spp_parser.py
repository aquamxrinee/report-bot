import asyncio
import aiohttp
import random
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from config import logger, WB_API_TOKEN
from wb_api import get_supplier_prices, get_sales

STATISTICS_API = "https://statistics-api.wildberries.ru/api/v1"

_failed_cache = {}


async def get_price_from_api(nm_id: int) -> Optional[float]:
    """Получает цену без скидки продавца через WB API (sales)"""
    if not WB_API_TOKEN:
        logger.error("❌ WB_API_TOKEN не задан")
        return None

    # Используем синхронную функцию из wb_api, но вызываем в отдельном потоке, чтобы не блокировать asyncio
    loop = asyncio.get_event_loop()
    sales = await loop.run_in_executor(None, get_sales, (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"))
    
    if isinstance(sales, dict) and "error" in sales:
        logger.warning(f"⚠️ Ошибка получения sales: {sales['error']}")
        return None
    
    if not isinstance(sales, list):
        logger.warning("⚠️ Ответ sales не является списком")
        return None
    
    for item in sales:
        if item.get('nmId') == nm_id:
            return float(item.get('totalPrice', 0))
    
    logger.warning(f"⚠️ Артикул {nm_id} не найден в sales за последние 7 дней")
    return None


async def get_discounted_price_from_api(nm_id: int) -> Optional[float]:
    """Получает текущую цену со скидкой через метод prices"""
    if not WB_API_TOKEN:
        logger.error("❌ WB_API_TOKEN не задан")
        return None
    
    loop = asyncio.get_event_loop()
    prices = await loop.run_in_executor(None, get_supplier_prices, 1000, 0)
    
    if isinstance(prices, dict) and "error" in prices:
        logger.warning(f"⚠️ Ошибка получения prices: {prices['error']}")
        return None
    
    if not isinstance(prices, list):
        logger.warning("⚠️ Ответ prices не является списком")
        return None
    
    for item in prices:
        if item.get('nmId') == nm_id:
            # Цена со скидкой (возможно, уже учтена)
            return float(item.get('price', 0))
    
    logger.warning(f"⚠️ Артикул {nm_id} не найден в ценах")
    return None


async def get_spp_for_article_async(nm_id: int) -> Optional[Dict[str, Any]]:
    if nm_id in _failed_cache and (datetime.now() - _failed_cache[nm_id]).seconds < 900:
        logger.warning(f"⏳ Артикул {nm_id} заблокирован (ждём 15 мин)")
        return None
    
    # Получаем полную цену (без скидки продавца)
    full_price = await get_price_from_api(nm_id)
    if not full_price:
        logger.warning(f"⚠️ Не удалось получить полную цену для {nm_id}")
        return None
    
    # Получаем цену со скидкой (текущую)
    discounted_price = await get_discounted_price_from_api(nm_id)
    if not discounted_price:
        logger.warning(f"⚠️ Не удалось получить цену со скидкой для {nm_id}")
        return None
    
    if full_price > 0 and discounted_price > 0 and full_price != discounted_price:
        spp_percent = round((1 - discounted_price / full_price) * 100, 2)
    else:
        spp_percent = 0.0
    
    _failed_cache.pop(nm_id, None)
    
    return {
        'nm_id': nm_id,
        'api_price': full_price,
        'site_price': discounted_price,
        'spp_percent': spp_percent,
        'title': f"Товар {nm_id}",
        'url': f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
        'checked_at': datetime.now().isoformat()
    }
