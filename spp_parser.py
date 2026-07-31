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

PROXIES = {
    "http": PROXY_URL,
    "https": PROXY_URL,
}

_failed_cache = {}

def get_spp_for_article(nm_id: int, retries: int = 3) -> Optional[Dict[str, Any]]:
    if nm_id in _failed_cache and (datetime.now() - _failed_cache[nm_id]).seconds < 600:
        logger.warning(f"⏳ Артикул {nm_id} временно заблокирован (ждём 10 мин)")
        return None

    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    for attempt in range(1, retries + 1):
        try:
            time.sleep(random.uniform(2.0, 5.0) * attempt)
            session = requests.Session()
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
            response = session.get(url, headers=headers, proxies=PROXIES, timeout=20)
            if response.status_code == 498:
                logger.warning(f"⚠️ 498 Rate limit для {nm_id}, попытка {attempt}/{retries}")
                _failed_cache[nm_id] = datetime.now()
                wait = 15 * attempt
                time.sleep(wait)
                continue
            if response.status_code != 200:
                logger.error(f"❌ Статус {response.status_code} для {nm_id}")
                if attempt == retries:
                    _failed_cache[nm_id] = datetime.now()
                continue
            soup = BeautifulSoup(response.text, 'html.parser')
            product_data = None
            scripts = soup.find_all('script', type='application/ld+json')
            for script in scripts:
                try:
                    data = json.loads(script.string)
                    if data.get('@type') == 'Product' and data.get('offers'):
                        product_data = data
                        break
                except:
                    continue
            current_price = None
            old_price = None
            if product_data:
                offers = product_data.get('offers', {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                current_price = float(offers.get('price', 0)) if offers.get('price') else None
            if not current_price:
                price_el = soup.find('span', class_='price-block__final-price')
                if not price_el:
                    price_el = soup.find('span', {'data-wba': 'price-final'})
                if price_el:
                    price_text = price_el.text.replace('\u2009', '').replace('\xa0', '').replace('₽', '').strip()
                    price_text = re.sub(r'[^\d.,]', '', price_text).replace(',', '.')
                    current_price = float(price_text)
                else:
                    price_pattern = re.compile(r'"price":\s*([\d.]+)')
                    match = price_pattern.search(response.text)
                    if match:
                        current_price = float(match.group(1))
            if current_price is None:
                logger.warning(f"⚠️ Цена не найдена для {nm_id}")
                return None
            old_price_el = soup.find('span', class_='price-block__old-price')
            if not old_price_el:
                old_price_el = soup.find('span', {'data-wba': 'price-old'})
            if old_price_el:
                price_text = old_price_el.text.replace('\u2009', '').replace('\xa0', '').replace('₽', '').strip()
                price_text = re.sub(r'[^\d.,]', '', price_text).replace(',', '.')
                old_price = float(price_text)
            else:
                old_price = current_price
            if old_price and old_price > 0 and old_price != current_price:
                spp_percent = round((1 - current_price / old_price) * 100, 2)
            else:
                spp_percent = 0.0
            title_el = soup.find('h1', {'data-wba': 'product-name'})
            if not title_el:
                title_el = soup.find('h1', class_='product-page__title')
            title = title_el.text.strip() if title_el else f"Товар {nm_id}"
            _failed_cache.pop(nm_id, None)
            return {
                'nm_id': nm_id,
                'current_price': current_price,
                'old_price': old_price,
                'spp_percent': spp_percent,
                'title': title,
                'url': url,
                'checked_at': datetime.now().isoformat()
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Ошибка запроса для {nm_id}: {e}")
            if attempt == retries:
                _failed_cache[nm_id] = datetime.now()
            time.sleep(10 * attempt)
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга {nm_id}: {e}")
            if attempt == retries:
                _failed_cache[nm_id] = datetime.now()
            time.sleep(5 * attempt)
    return None
