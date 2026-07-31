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

try:
    from aiohttp_socks import ProxyConnector
except ImportError:
    logger.warning("⚠️ aiohttp-socks не установлен, SOCKS5 прокси не будет работать")
    ProxyConnector = None

STATISTICS_API = "https://statistics-api.wildberries.ru/api/v1"

ua = UserAgent()
_failed_cache = {}


async def get_price_from_api(nm_id: int) -> Optional[float]:
    """Получает цену без скидки продавца через WB API (sales)"""
    if not WB_API_TOKEN:
        logger.error("❌ WB_API_TOKEN не задан")
        return None

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
            # totalPrice — это цена после всех скидок, но для нас это цена до скидки продавца (обычно она же)
            return float(item.get('totalPrice', 0))
    
    logger.warning(f"⚠️ Артикул {nm_id} не найден в sales за последние 7 дней")
    return None


async def get_price_from_site(nm_id: int, proxy_url: Optional[str] = None) -> Optional[Dict]:
    """Парсит сайт для получения текущей цены и старой цены (если есть)"""
    # Пробуем мобильную версию и обычную
    urls = [
        f"https://m.wildberries.ru/catalog/{nm_id}/detail.aspx",
        f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx?x=1",
        f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    ]
    
    for attempt in range(1, 4):
        for url in urls:
            try:
                await asyncio.sleep(random.uniform(2, 5) * attempt)
                
                headers = {
                    "User-Agent": ua.random,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "Referer": "https://www.wildberries.ru/"
                }
                
                connector = None
                if proxy_url and ProxyConnector:
                    try:
                        connector = ProxyConnector.from_url(proxy_url)
                    except Exception as e:
                        logger.warning(f"⚠️ Ошибка прокси: {e}, пробуем без прокси")
                        connector = None
                
                async with aiohttp.ClientSession(connector=connector) as session:
                    response = await session.get(url, headers=headers, timeout=20)
                    
                    if response.status == 498:
                        logger.warning(f"⚠️ 498 Rate limit для {nm_id}, попытка {attempt}/3")
                        await asyncio.sleep(20 * attempt)
                        break  # переходим к следующей попытке
                    
                    if response.status != 200:
                        logger.error(f"❌ Статус {response.status} для {nm_id}, URL: {url}")
                        continue
                    
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Ищем текущую цену
                    current_price = None
                    old_price = None
                    
                    # Селекторы для текущей цены
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
                        current_price = float(price_text)
                    else:
                        # Ищем через JSON-LD
                        scripts = soup.find_all('script', type='application/ld+json')
                        for script in scripts:
                            try:
                                data = json.loads(script.string)
                                if data.get('@type') == 'Product' and data.get('offers'):
                                    offers = data.get('offers', {})
                                    if isinstance(offers, list):
                                        offers = offers[0] if offers else {}
                                    current_price = float(offers.get('price', 0))
                                    break
                            except:
                                continue
                    
                    if current_price is None:
                        logger.warning(f"⚠️ Текущая цена не найдена для {nm_id}")
                        continue
                    
                    # Ищем старую цену (до скидки)
                    old_price_el = soup.find('span', class_='price-block__old-price')
                    if not old_price_el:
                        old_price_el = soup.find('span', {'data-wba': 'price-old'})
                    if old_price_el:
                        price_text = old_price_el.text.replace('\u2009', '').replace('\xa0', '').replace('₽', '').strip()
                        price_text = re.sub(r'[^\d.,]', '', price_text).replace(',', '.')
                        old_price = float(price_text)
                    
                    # Название
                    title_el = soup.find('h1', {'data-wba': 'product-name'})
                    if not title_el:
                        title_el = soup.find('h1', class_='product-page__title')
                    if not title_el:
                        title_el = soup.find('h1', {'itemprop': 'name'})
                    title = title_el.text.strip() if title_el else f"Товар {nm_id}"
                    
                    return {
                        'current_price': current_price,
                        'old_price': old_price if old_price else current_price,
                        'title': title,
                        'url': url
                    }
                    
            except Exception as e:
                logger.error(f"❌ Ошибка парсинга {nm_id} (URL: {url}): {e}")
                if 'proxy' in str(e).lower() or 'socks' in str(e).lower():
                    logger.warning(f"⚠️ Ошибка прокси для {nm_id}, пробуем без прокси")
                    proxy_url = None
                    await asyncio.sleep(5)
                else:
                    await asyncio.sleep(10 * attempt)
    
    return None


async def get_spp_for_article_async(nm_id: int) -> Optional[Dict[str, Any]]:
    if nm_id in _failed_cache and (datetime.now() - _failed_cache[nm_id]).seconds < 900:
        logger.warning(f"⏳ Артикул {nm_id} заблокирован (ждём 15 мин)")
        return None
    
    # 1. Получаем полную цену (до скидки продавца) из API (sales)
    full_price = await get_price_from_api(nm_id)
    
    # 2. Парсим сайт для текущей цены и старой цены
    site_data = await get_price_from_site(nm_id, proxy_url=PROXY_URL)
    if not site_data:
        logger.warning(f"⚠️ Не удалось получить данные с сайта para {nm_id}")
        return None
    
    current_price = site_data['current_price']
    old_price = site_data['old_price']  # цена до скидки на сайте (может не быть)
    
    # Если полная цена из API не найдена, используем старую цену с сайта (если есть)
    if not full_price and old_price and old_price != current_price:
        full_price = old_price
        logger.info(f"📝 Полная цена взята со страницы (старая цена) para {nm_id}: {full_price}")
    
    if not full_price:
        logger.warning(f"⚠️ Не удалось определить полную цену para {nm_id}")
        return None
    
    if full_price > 0 and current_price > 0 and full_price != current_price:
        spp_percent = round((1 - current_price / full_price) * 100, 2)
    else:
        spp_percent = 0.0
    
    _failed_cache.pop(nm_id, None)
    
    return {
        'nm_id': nm_id,
        'api_price': full_price,
        'site_price': current_price,
        'spp_percent': spp_percent,
        'title': site_data['title'],
        'url': site_data['url'],
        'checked_at': datetime.now().isoformat()
    }


def get_sales(date_from: str):
    """Синхронная обёртка для получения sales (используется в get_price_from_api)"""
    import requests
    from config import WB_API_TOKEN
    url = f"{STATISTICS_API}/supplier/sales"
    headers = {"Authorization": f"Bearer {WB_API_TOKEN}"}
    params = {"dateFrom": date_from}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code == 200:
            return response.json()
        else:
            return {"error": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"error": str(e)}
