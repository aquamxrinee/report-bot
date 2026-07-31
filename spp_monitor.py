import asyncio
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from pathlib import Path
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
from spp_parser import get_spp_for_article

async def send_brand_notification(bot_app, user_id: int, brand: str, old_avg: float, new_avg: float, diff: float):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    direction = "упала" if new_avg < old_avg else "выросла"
    text = (
        f"📊 *Изменение средней СПП по бренду {brand}!*\n\n"
        f"Средняя СПП: {old_avg:.1f}% → {new_avg:.1f}% ({direction} на {diff:.1f} п.п.)"
    )
    keyboard = [
        [InlineKeyboardButton("🔇 Глушить на 2ч", callback_data=f"spp_mute_brand_{brand}")],
        [InlineKeyboardButton("📈 График", callback_data=f"spp_graph_brand_{brand}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await bot_app.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode='Markdown',
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
        logger.info(f"✅ Уведомление о средней СПП по бренду {brand} отправлено пользователю {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления: {e}")

async def monitor_spp(bot_app):
    logger.info("🔄 Запуск мониторинга СПП...")
    nm_ids = get_all_tracked_articles()
    for nm_id in nm_ids:
        try:
            # Вызов синхронной функции без await
            data = get_spp_for_article(nm_id)
            if not data:
                logger.warning(f"⚠️ Не удалось получить данные для {nm_id}")
                await asyncio.sleep(10)
                continue
            article_name = data.get('title', f"Товар {nm_id}")
            save_spp_history(
                nm_id=nm_id,
                article=article_name,
                current_price=data['current_price'],
                old_price=data['old_price'],
                spp_percent=data['spp_percent']
            )
            last = get_last_spp(nm_id)
            if not last:
                logger.info(f"📝 Первая проверка для {nm_id}")
                await asyncio.sleep(5)
                continue
            old_spp = last['spp_percent']
            new_spp = data['spp_percent']
            diff = abs(new_spp - old_spp)
            if diff < 0.01:
                await asyncio.sleep(3)
                continue
            users = get_subscribed_users(nm_id)
            for user_id in users:
                if is_muted(user_id, nm_id):
                    logger.info(f"🔇 Уведомление для {nm_id} заглушено для пользователя {user_id}")
                    continue
                conn = sqlite3.connect(str(DB_PATH))
                cursor = conn.cursor()
                cursor.execute('SELECT threshold FROM spp_subscriptions WHERE user_id = ? AND nm_id = ?', (user_id, nm_id))
                row = cursor.fetchone()
                conn.close()
                threshold = row[0] if row else 5.0
                if diff >= threshold:
                    from handlers import send_spp_notification
                    await send_spp_notification(bot_app, user_id, nm_id, old_spp, new_spp, data, diff)
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"❌ Ошибка мониторинга для {nm_id}: {e}")
            await asyncio.sleep(10)

    brand_subs = {}
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT brand FROM spp_brand_subscriptions')
    brands = [row[0] for row in cursor.fetchall()]
    conn.close()

    for brand in brands:
        try:
            nm_ids_brand = get_articles_by_brand(brand)
            if not nm_ids_brand:
                logger.warning(f"⚠️ Нет артикулов с nm_id для бренда {brand}")
                continue
            spp_values = []
            for nm_id in nm_ids_brand:
                data = get_spp_for_article(nm_id)  # без await
                if data and data.get('spp_percent') is not None:
                    spp_values.append(data['spp_percent'])
                await asyncio.sleep(3)
            if not spp_values:
                logger.warning(f"⚠️ Не удалось получить СПП для артикулов бренда {brand}")
                continue
            avg_spp = sum(spp_values) / len(spp_values)
            save_brand_history(brand, avg_spp)
            last = get_last_brand_spp(brand)
            if not last:
                logger.info(f"📝 Первая проверка средней СПП для бренда {brand}")
                continue
            old_avg = last['avg_spp']
            diff = abs(avg_spp - old_avg)
            if diff < 0.01:
                continue
            subscribers = get_all_brand_subscribers(brand)
            for sub in subscribers:
                user_id = sub['user_id']
                threshold = sub['threshold']
                if diff >= threshold:
                    await send_brand_notification(bot_app, user_id, brand, old_avg, avg_spp, diff)
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"❌ Ошибка мониторинга бренда {brand}: {e}")

    logger.info("✅ Мониторинг СПП завершён")

def generate_spp_graph(nm_id: int, width: int = 800, height: int = 400) -> Optional[str]:
    history = get_spp_history(nm_id, limit=30)
    if not history:
        return None
    history = history[::-1]
    dates = [h['checked_at'][:16] for h in history]
    spp_values = [h['spp_percent'] for h in history]
    prices = [h['current_price'] for h in history]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(width/100, height/100), sharex=True)
    ax1.plot(dates, spp_values, marker='o', color='red', linewidth=2)
    ax1.set_ylabel('СПП, %')
    ax1.grid(True, alpha=0.3)
    ax1.set_title(f'Динамика СПП для артикула {nm_id}')
    ax2.plot(dates, prices, marker='s', color='blue', linewidth=2)
    ax2.set_ylabel('Цена, ₽')
    ax2.grid(True, alpha=0.3)
    ax2.set_xlabel('Дата')
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close()
    img_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    return img_base64
