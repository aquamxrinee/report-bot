import requests
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup
from config import logger


def get_spp_for_article(nm_id: int) -> Optional[Dict[str, Any]]:
    """
    Получает данные о СПП через парсинг страницы товара.
    Использует requests + BeautifulSoup (без браузера).
    """
    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Ищем JSON-LD с данными о товаре
        scripts = soup.find_all('script', type='application/ld+json')
        product_data = None
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
        
        # Если JSON-LD не дал цену, ищем по селекторам
        if not current_price:
            price_el = soup.find('span', class_='price-block__final-price')
            if not price_el:
                price_el = soup.find('span', {'data-wba': 'price-final'})
            if price_el:
                price_text = price_el.text.replace('\u2009', '').replace('\xa0', '').replace('₽', '').strip()
                price_text = re.sub(r'[^\d.,]', '', price_text).replace(',', '.')
                current_price = float(price_text)
            else:
                return None
        
        # Ищем старую цену
        old_price_el = soup.find('span', class_='price-block__old-price')
        if not old_price_el:
            old_price_el = soup.find('span', {'data-wba': 'price-old'})
        if old_price_el:
            price_text = old_price_el.text.replace('\u2009', '').replace('\xa0', '').replace('₽', '').strip()
            price_text = re.sub(r'[^\d.,]', '', price_text).replace(',', '.')
            old_price = float(price_text)
        else:
            old_price = current_price
        
        # Рассчитываем СПП
        if old_price and old_price > 0 and old_price != current_price:
            spp_percent = round((1 - current_price / old_price) * 100, 2)
        else:
            spp_percent = 0.0
        
        # Название товара
        title_el = soup.find('h1', {'data-wba': 'product-name'})
        if not title_el:
            title_el = soup.find('h1', class_='product-page__title')
        title = title_el.text.strip() if title_el else f"Товар {nm_id}"
        
        return {
            'nm_id': nm_id,
            'current_price': current_price,
            'old_price': old_price,
            'spp_percent': spp_percent,
            'title': title,
            'url': url,
            'checked_at': datetime.now().isoformat()
        }
        
    except requests.exceptions.Timeout:
        logger.error(f"⏰ Таймаут при парсинге {nm_id}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка запроса для {nm_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга {nm_id}: {e}")
        return None
