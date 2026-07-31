import asyncio
import aiohttp
import json
import re
import random
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from config import logger, PROXY_URL, WB_API_TOKEN

# Импортируем aiohttp_socks для поддержки SOCKS5
try:
    from aiohttp_socks import ProxyConnector
except ImportError:
    logger.warning("⚠️ aiohttp-socks не установлен, SOCKS5 прокси не будет работать. Установите: pip install aiohttp-socks")
    ProxyConnector = None

STATISTICS_API = "https://statistics-api.wildberries.ru/api/v1"

ua = UserAgent()
_failed_cache = {}


async def get_price_from_api(nm_id: int) -> Optional[float]:
    if not WB_API_TOKEN:
        logger.error("❌ WB_API_TOKEN не задан")
        return None

    url = f"{STATISTICS_API}/supplier/sales"
    headers = {"Authorization": f"Bearer {WB_API_TOKEN}"}
    params = {"dateFrom": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")}
    
    await asyncio.sleep(random.uniform(0.5, 1.5))
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params, timeout=15) as response:
                if response.status == 429:
                    logger.warning("⚠️ Превышен лимит запросов к WB API (429)")
                    return None
                if response.status != 200:
                    logger.warning(f"⚠️ API вернул статус {response.status}")
                    return None
                
                data = await response.json()
                if not data or not isinstance(data, list):
                    logger.warning("⚠️ API вернул пустой ответ")
                    return None
                
                for item in data:
                    if item.get('nmId') == nm_id:
                        return float(item.get('totalPrice', 0))
                
                logger.warning(f"⚠️ Артикул {nm_id} не найден в API за последние 7 дней")
                return None
                
    except asyncio.TimeoutError:
        logger.error(f"❌ Таймаут API для {nm_id}")
        return None
    except Exception as e:
        logger.error(f"❌ Ошибка API: {e}")
        return None


async def get_price_from_mobile_site(nm_id: int) -> Optional[Dict]:
    url = f"https://m.wildberries.ru/catalog/{nm_id}/detail.aspx"
    fallback_url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx?x=1"
    
    for attempt in range(1, 4):
        try:
            await asyncio.sleep(random.uniform(2, 5) * attempt)
            
            headers = {
                "User-Agent": ua.random,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Referer": "https://m.wildberries.ru/"
            }
            
            # Создаём коннектор с прокси, если он задан
            connector = None
            if PROXY_URL and ProxyConnector:
                connector = ProxyConnector.from_url(PROXY_URL)
            
            async with aiohttp.ClientSession(connector=connector) as session:
                # Пробуем мобильную версию
                response = await session.get(url, headers=headers, timeout=20)
                
                if response.status in (498, 429, 403):
                    logger.warning(f"⚠️ Мобильная версия {response.status} для {nm_id}, пробуем fallback")
                    response = await session.get(fallback_url, headers=headers, timeout=20)
                
                if response.status == 498:
                    logger.warning(f"⚠️ 498 Rate limit для {nm_id}, попытка {attempt}/3")
                    await asyncio.sleep(20 * attempt)
                    continue
                
                if response.status != 200:
                    logger.error(f"❌ Статус {response.status} для {nm_id}")
                    continue
                
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                # Поиск цены
                price_el = soup.find('span', class_='price-block__final-price')
                if not price_el:
                    price_el = soup.find('span', {'data-wba': 'price-final'})
                if not price_el:
                    price_el = soup.find('span', class_='final-price')
                if not price_el:
                    price_el = soup.find('div', class_='product-price__current')
                if not price_el:
                    price_el = soup.find('span', {'itemprop': 'price'})
                
                if price_el:
                    price_text = price_el.text.replace('\u2009', '').replace('\xa0', '').replace('₽', '').strip()
                    price_text = re.sub(r'[^\d.,]', '', price_text).replace(',', '.')
                    site_price = float(price_text)
                else:
                    # JSON-LD
                    scripts = soup.find_all('script', type='application/ld+json')
                    for script in scripts:
                        try:
                            data = json.loads(script.string)
                            if data.get('@type') == 'Product' and data.get('offers'):
                                offers = data.get('offers', {})
                                if isinstance(offers, list):
                                    offers = offers[0] if offers else {}
                                site_price = float(offers.get('price', 0))
                                break
                        except:
                            continue
                    else:
                        # regex
                        match = re.search(r'"price":\s*([\d.]+)', html)
                        if match:
                            site_price = float(match.group(1))
                        else:
                            logger.warning(f"⚠️ Цена не найдена для {nm_id}")
                            return None
                
                # Название
                title_el = soup.find('h1', {'data-wba': 'product-name'})
                if not title_el:
                    title_el = soup.find('h1', class_='product-page__title')
                if not title_el:
                    title_el = soup.find('h1', {'itemprop': 'name'})
                title = title_el.text.strip() if title_el else f"Товар {nm_id}"
                
                return {
                    'site_price': site_price,
                    'title': title,
                    'url': url
                }
                
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга {nm_id}: {e}")
            await asyncio.sleep(10 * attempt)
    
    return None


async def get_spp_for_article_async(nm_id: int) -> Optional[Dict[str, Any]]:
    if nm_id in _failed_cache and (datetime.now() - _failed_cache[nm_id]).seconds < 900:
        logger.warning(f"⏳ Артикул {nm_id} заблокирован (ждём 15 мин)")
        return None
    
    api_price = await get_price_from_api(nm_id)
    if not api_price:
        logger.warning(f"⚠️ Не удалось получить цену из API для {nm_id}")
        return None
    
    site_data = await get_price_from_mobile_site(nm_id)
    if not site_data:
        logger.warning(f"⚠️ Не удалось получить цену на сайте для {nm_id}")
        return None
    
    site_price = site_data['site_price']
    title = site_data['title']
    url = site_data['url']
    
    if api_price > 0 and site_price > 0 and api_price != site_price:
        spp_percent = round((1 - site_price / api_price) * 100, 2)
    else:
        spp_percent = 0.0
    
    _failed_cache.pop(nm_id, None)
    
    return {
        'nm_id': nm_id,
        'api_price': api_price,
        'site_price': site_price,
        'spp_percent': spp_percent,
        'title': title,
        'url': url,
        'checked_at': datetime.now().isoformat()
    }
