import requests
import json
import re
import time
import random
from datetime import datetime
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from config import logger, PROXY_URL

ua = UserAgent()
_failed_cache = {}

def parse_buyer_price(nm_id: int, retries: int = 3) -> Optional[float]:
    """
    Парсит цену для покупателя с клиентской страницы товара.
    """
    if nm_id in _failed_cache and (datetime.now() - _failed_cache[nm_id]).seconds < 900:
        logger.warning(f"⏳ Артикул {nm_id} временно заблокирован (ждём 15 мин)")
        return None

    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    for attempt in range(1, retries + 1):
        try:
            time.sleep(random.uniform(8.0, 15.0) * attempt)
            headers = {
                "User-Agent": ua.random,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Referer": "https://www.wildberries.ru/"
            }
            proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
            response = requests.get(url, headers=headers, proxies=proxies, timeout=30, verify=False)
            if response.status_code == 498:
                logger.warning(f"⚠️ 498 Rate limit для {nm_id}, попытка {attempt}/{retries}")
                _failed_cache[nm_id] = datetime.now()
                time.sleep(30 * attempt)
                continue
            if response.status_code != 200:
                logger.error(f"❌ Статус {response.status_code} для {nm_id}")
                if attempt == retries:
                    _failed_cache[nm_id] = datetime.now()
                continue

            soup = BeautifulSoup(response.text, 'html.parser')
            # Ищем цену покупателя
            price_el = soup.find('span', class_='price-block__final-price')
            if not price_el:
                price_el = soup.find('span', {'data-wba': 'price-final'})
            if price_el:
                price_text = price_el.text.replace('\u2009', '').replace('\xa0', '').replace('₽', '').strip()
                price_text = re.sub(r'[^\d.,]', '', price_text).replace(',', '.')
                buyer_price = float(price_text)
                _failed_cache.pop(nm_id, None)
                return buyer_price
            else:
                logger.warning(f"⚠️ Цена покупателя не найдена для {nm_id}")
                return None
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга {nm_id}: {e}")
            if attempt == retries:
                _failed_cache[nm_id] = datetime.now()
            time.sleep(15 * attempt)
    return None
