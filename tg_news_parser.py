import os
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from telethon import TelegramClient, events
from telethon.tl.functions.messages import GetHistoryRequest
from config import logger

# Настройки для Telegram API (нужно получить на my.telegram.org)
API_ID = os.getenv("TELEGRAM_API_ID")  # int
API_HASH = os.getenv("TELEGRAM_API_HASH")
PHONE_NUMBER = os.getenv("TELEGRAM_PHONE_NUMBER")  # для авторизации

# Канал для парсинга
CHANNEL_USERNAME = "news4sellers"

# Кеш новостей
_news_cache = {
    "messages": [],
    "timestamp": None
}
CACHE_TTL = timedelta(minutes=30)


async def get_telegram_client():
    """Создаёт и возвращает клиент Telethon"""
    if not API_ID or not API_HASH:
        logger.error("❌ TELEGRAM_API_ID или TELEGRAM_API_HASH не заданы")
        return None
    
    client = TelegramClient(
        f"session_{PHONE_NUMBER}" if PHONE_NUMBER else "session",
        int(API_ID),
        API_HASH
    )
    return client


async def fetch_channel_messages(limit: int = 10) -> List[Dict]:
    """
    Получает последние сообщения из канала @news4sellers
    """
    global _news_cache
    
    # Проверяем кеш
    if _news_cache["timestamp"] and (datetime.now() - _news_cache["timestamp"]) < CACHE_TTL:
        return _news_cache["messages"][:limit]
    
    client = await get_telegram_client()
    if not client:
        return []
    
    try:
        await client.start(phone=PHONE_NUMBER)
        
        # Получаем канал
        channel = await client.get_entity(CHANNEL_USERNAME)
        
        # Получаем историю сообщений
        history = await client(GetHistoryRequest(
            peer=channel,
            limit=limit,
            offset_date=None,
            offset_id=0,
            max_id=0,
            min_id=0,
            add_offset=0,
            hash=0
        ))
        
        messages = []
        for msg in history.messages:
            if msg.message:
                messages.append({
                    "id": msg.id,
                    "text": msg.message,
                    "date": msg.date.strftime("%Y-%m-%d %H:%M"),
                    "views": getattr(msg, "views", 0)
                })
        
        # Сохраняем в кеш
        _news_cache["messages"] = messages
        _news_cache["timestamp"] = datetime.now()
        
        logger.info(f"✅ Загружено {len(messages)} сообщений из @{CHANNEL_USERNAME}")
        return messages[:limit]
        
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга Telegram: {e}")
        return []
    finally:
        await client.disconnect()


def format_telegram_news(messages: List[Dict], prefix: str = "📰 *Новости маркетплейсов*") -> str:
    """
    Форматирует новости для отправки в Telegram
    """
    if not messages:
        return "Нет свежих новостей."
    
    digest = f"{prefix}\n\n"
    for i, msg in enumerate(messages, 1):
        text = msg['text']
        # Обрезаем слишком длинные сообщения
        if len(text) > 500:
            text = text[:500] + "..."
        digest += f"{i}. {text}\n"
        digest += f"   🕐 {msg['date']}\n\n"
    
    return digest


async def get_news_digest(limit: int = 10) -> str:
    """Утилита для получения дайджеста новостей"""
    messages = await fetch_channel_messages(limit)
    return format_telegram_news(messages)