import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import time
from config import logger

# Кеш для новостей
_news_cache = {
    "messages": [],
    "timestamp": None
}
CACHE_TTL = timedelta(minutes=30)  # обновляем раз в 30 минут


def fetch_channel_messages(channel: str = "news4sellers", limit: int = 10) -> List[Dict]:
    """
    Парсит последние сообщения из публичного Telegram-канала через веб-версию.
    Не требует API-ключей, авторизации и телефона.
    
    Args:
        channel: имя канала (без @)
        limit: количество сообщений
    
    Returns:
        Список словарей с полями: text, date, views
    """
    global _news_cache
    
    # Проверяем кеш
    if _news_cache["timestamp"] and (datetime.now() - _news_cache["timestamp"]) < CACHE_TTL:
        logger.info("📦 Используем кешированные новости")
        return _news_cache["messages"][:limit]
    
    url = f"https://t.me/s/{channel}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }
    
    try:
        logger.info(f"🔄 Парсим новости из канала @{channel}")
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        messages = []
        posts = soup.select('.tgme_widget_message')[:limit]
        
        if not posts:
            logger.warning("⚠️ Не найдено сообщений. Возможно, канал приватный или изменилась структура страницы.")
            return []
        
        for post in posts:
            try:
                # Текст сообщения
                text_elem = post.select_one('.tgme_widget_message_text')
                if not text_elem:
                    continue
                
                text = text_elem.text.strip()
                # Обрезаем слишком длинные
                if len(text) > 800:
                    text = text[:800] + "..."
                
                # Дата/время
                date_elem = post.select_one('.tgme_widget_message_date')
                date_str = None
                if date_elem and date_elem.get('datetime'):
                    try:
                        dt = datetime.fromisoformat(date_elem['datetime'])
                        date_str = dt.strftime("%d.%m.%Y %H:%M")
                    except:
                        date_str = date_elem.text.strip()
                else:
                    date_str = datetime.now().strftime("%d.%m.%Y %H:%M")
                
                # Просмотры (если есть)
                views_elem = post.select_one('.tgme_widget_message_views')
                views = views_elem.text.strip() if views_elem else "0"
                
                messages.append({
                    'text': text,
                    'date': date_str,
                    'views': views,
                    'url': f"https://t.me/{channel}/{post.get('data-post', '')}"
                })
                
            except Exception as e:
                logger.warning(f"⚠️ Ошибка парсинга отдельного сообщения: {e}")
                continue
        
        # Сохраняем в кеш
        _news_cache["messages"] = messages
        _news_cache["timestamp"] = datetime.now()
        
        logger.info(f"✅ Загружено {len(messages)} сообщений из @{channel}")
        return messages
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка запроса к t.me: {e}")
        return []
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга новостей: {e}")
        return []


def format_news_digest(messages: List[Dict], prefix: str = "📰 *Новости маркетплейсов*") -> str:
    """
    Форматирует новости для отправки в Telegram
    
    Args:
        messages: список сообщений от fetch_channel_messages
        prefix: заголовок дайджеста
    
    Returns:
        Отформатированный текст для отправки
    """
    if not messages:
        return "📭 Новостей пока нет. Попробуйте позже."
    
    digest = f"{prefix}\n\n"
    for i, msg in enumerate(messages, 1):
        text = msg['text']
        # Убираем эмодзи и ссылки для краткости, но оставляем суть
        text = text.replace('\n', ' ').strip()
        if len(text) > 400:
            text = text[:400] + "..."
        
        digest += f"{i}. {text}\n"
        digest += f"   🕐 {msg['date']}"
        if msg.get('views') and msg['views'] != '0':
            digest += f"  👁 {msg['views']}"
        digest += "\n\n"
    
    return digest


def get_news_digest(limit: int = 10) -> str:
    """Утилита для быстрого получения дайджеста новостей"""
    messages = fetch_channel_messages(limit=limit)
    return format_news_digest(messages)


# Для тестирования (запустите этот файл отдельно, чтобы проверить работу)
if __name__ == "__main__":
    print("🔄 Тестируем парсинг новостей...")
    messages = fetch_channel_messages(limit=5)
    print(f"Загружено сообщений: {len(messages)}")
    for i, msg in enumerate(messages, 1):
        print(f"\n--- Сообщение {i} ---")
        print(f"Дата: {msg['date']}")
        print(f"Текст: {msg['text'][:200]}...")
