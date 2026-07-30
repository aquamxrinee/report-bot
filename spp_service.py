import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import logger
from models import (
    get_user_subscriptions, subscribe_user, unsubscribe_user,
    get_all_articles_with_costs, get_active_cost
)
from spp_parser import get_spp_for_article
from spp_monitor import save_spp_history, get_last_spp, is_muted


def get_articles_for_user(user_id: int) -> List[Dict]:
    """
    Возвращает список артикулов с их nm_id и названиями
    для отображения в меню подписки
    """
    # Получаем все артикулы из отчётов
    articles = get_all_articles_with_costs()
    
    # Здесь нужно получить nm_id для каждого артикула
    # Если в БД нет nm_id, нужно добавить отдельную таблицу или поле
    # Пока возвращаем список с заглушками
    result = []
    for item in articles:
        result.append({
            'article': item['article'],
            'nm_id': item.get('nm_id', 0),  # нужно будет заполнять
            'brand': item.get('brand', ''),
            'cost': item.get('cost')
        })
    return result


async def check_spp_for_subscriptions(bot_app):
    """
    Проверяет СПП для всех подписанных артикулов
    и отправляет уведомления при изменении
    """
    from models import get_subscribed_users, get_user_subscriptions
    
    # Получаем все уникальные nm_id, на которые есть подписки
    # Пока упрощённо: проходим по всем пользователям
    # В реальности нужно хранить nm_id в подписках
    
    logger.info("🔄 Проверка СПП для подписанных артикулов...")
    
    # Здесь будет логика проверки
    # Для каждого подписанного артикула:
    # 1. Получить текущую СПП через парсер
    # 2. Сравнить с последней сохранённой
    # 3. Если изменилась > порога — отправить уведомление


def format_spp_notification(nm_id: int, old_spp: float, new_spp: float, title: str) -> str:
    """Форматирует уведомление об изменении СПП"""
    direction = "упала" if new_spp < old_spp else "выросла"
    diff = abs(new_spp - old_spp)
    
    text = (
        f"📊 *Изменение СПП!*\n\n"
        f"Артикул: {nm_id}\n"
        f"Название: {title}\n"
        f"СПП: {old_spp}% → {new_spp}% ({direction} на {diff:.1f} п.п.)\n"
        f"[Открыть карточку](https://www.wildberries.ru/catalog/{nm_id}/detail.aspx)"
    )
    return text