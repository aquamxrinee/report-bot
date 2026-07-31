import asyncio
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
import matplotlib.pyplot as plt
import io
import base64
from config import logger, DB_PATH
from models import (
    get_user_subscriptions, get_subscribed_users,
    get_last_spp, save_spp_history, is_muted,
    get_article_by_nm_id, get_nm_id_by_article,
    get_all_tracked_articles,
    get_user_brand_subscriptions, get_all_brand_subscribers,
    get_articles_by_brand, save_brand_history, get_last_brand_spp
)
from wb_api import get_supplier_prices
from spp_parser import parse_buyer_price

async def monitor_spp(bot_app):
    logger.info("🔄 Запуск мониторинга СПП...")
    nm_ids = get_all_tracked_articles()
    if not nm_ids:
        logger.info("📭 Нет артикулов для отслеживания")
        return

    # 1. Получаем цены продавца через API
    prices_data = get_supplier_prices(nm_ids)
    supplier_prices = {}
    if prices_data and isinstance(prices_data, list):
        for item in prices_data:
            nm_id = item.get('nmId')
            price = item.get('price')
            if nm_id and price:
                supplier_prices[nm_id] = price
    else:
        logger.warning("⚠️ Не удалось получить цены продавца через API")

    for nm_id in nm_ids:
        try:
            supplier_price = supplier_prices.get(nm_id)
            if not supplier_price:
                logger.warning(f"⚠️ Нет цены продавца для {nm_id}, пропускаем")
                continue

            # 2. Парсим цену покупателя
            buyer_price = parse_buyer_price(nm_id)
            if not buyer_price:
                logger.warning(f"⚠️ Не удалось получить цену покупателя для {nm_id}")
                continue

            # 3. Рассчитываем СПП
            spp_percent = round((supplier_price - buyer_price) / supplier_price * 100, 2)
            if spp_percent < 0:
                spp_percent = 0

            # 4. Сохраняем историю
            article_name = get_article_by_nm_id(nm_id) or f"Товар {nm_id}"
            save_spp_history(
                nm_id=nm_id,
                article=article_name,
                current_price=buyer_price,
                old_price=supplier_price,
                spp_percent=spp_percent
            )

            # 5. Сравниваем с последним значением
            last = get_last_spp(nm_id)
            if not last:
                logger.info(f"📝 Первая проверка для {nm_id}")
                continue

            old_spp = last['spp_percent']
            diff = abs(spp_percent - old_spp)
            if diff < 0.01:
                continue

            users = get_subscribed_users(nm_id)
            for user_id in users:
                if is_muted(user_id, nm_id):
                    continue
                conn = sqlite3.connect(str(DB_PATH))
                cursor = conn.cursor()
                cursor.execute('SELECT threshold FROM spp_subscriptions WHERE user_id = ? AND nm_id = ?', (user_id, nm_id))
                row = cursor.fetchone()
                conn.close()
                threshold = row[0] if row else 5.0
                if diff >= threshold:
                    from handlers import send_spp_notification
                    data = {
                        'current_price': buyer_price,
                        'old_price': supplier_price,
                        'url': f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
                        'title': article_name
                    }
                    await send_spp_notification(bot_app, user_id, nm_id, old_spp, spp_percent, data, diff)

            await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"❌ Ошибка обработки {nm_id}: {e}")

    logger.info("✅ Мониторинг СПП завершён")

# ... остальные функции (generate_spp_graph, send_brand_notification) остаются без изменений
