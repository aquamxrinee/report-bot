import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import List, Dict
import time
from config import logger

_news_cache = {
    "messages": [],
    "timestamp": None
}
CACHE_TTL = timedelta(minutes=30)


def fetch_channel_messages(channel: str = "news4sellers", limit: int = 10) -> List[Dict]:
    global _news_cache
    if _news_cache["timestamp"] and (datetime.now() - _news_cache["timestamp"]) < CACHE_TTL:
        logger.info("📦 Используем кешированные новости")
        return _news_cache["messages"][:limit]
    
    url = f"https://t.me/s/{channel}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        messages = []
        posts = soup.select('.tgme_widget_message')[:limit]
        for post in posts:
            text_elem = post.select_one('.tgme_widget_message_text')
            if not text_elem:
                continue
            text = text_elem.text.strip()
            if len(text) > 500:
                text = text[:500] + "..."
            date_elem = post.select_one('.tgme_widget_message_date')
            date_str = date_elem['datetime'][:16].replace('T', ' ') if date_elem else datetime.now().strftime("%Y-%m-%d %H:%M")
            messages.append({'text': text, 'date': date_str})
        
        _news_cache["messages"] = messages
        _news_cache["timestamp"] = datetime.now()
        logger.info(f"✅ Загружено {len(messages)} сообщений из @{channel}")
        return messages
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга новостей: {e}")
        return []


def format_news_digest(messages: List[Dict], prefix: str = "📰 *Новости маркетплейсов*") -> str:
    if not messages:
        return "Новостей пока нет."
    digest = f"{prefix}\n\n"
    for i, msg in enumerate(messages, 1):
        digest += f"{i}. {msg['text']}\n   🕐 {msg['date']}\n\n"
    return digest
