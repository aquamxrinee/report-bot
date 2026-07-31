import asyncio
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright
from fake_useragent import UserAgent
from config import logger, PROXY_URL

ua = UserAgent()
_failed_cache = {}

async def get_spp_for_article(nm_id: int, retries: 3) -> Optional[Dict[str, Any]]:
    if nm_id in _failed_cache and (datetime.now() - _failed_cache[nm_id]).seconds < 600:
        logger.warning(f"⏳ Артикул {nm_id} временно заблокирован (ждём 10 мин)")
        return None

    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    for attempt in range(1, retries + 1):
        try:
            logger.info(f"🔍 Парсинг {nm_id}, попытка {attempt}/{retries}")
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, proxy={
                    "server": PROXY_URL
                })
                context = await browser.new_context(
                    user_agent=ua.random,
                    viewport={"width": 1920, "height": 1080},
                    locale="ru-RU"
                )
                page = await context.new_page()
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                # Ждём появления цены
                try:
                    await page.wait_for_selector('.price-block__final-price', timeout=10000)
                except:
                    await page.wait_for_selector('[data-wba="price-final"]', timeout=10000)

                # Получаем данные через JavaScript
                data = await page.evaluate("""
                    () => {
                        function getPrice(selector) {
                            const el = document.querySelector(selector);
                            if (!el) return null;
                            const text = el.textContent.replace(/\\s/g, '').replace(/₽/g, '').trim();
                            return parseFloat(text) || null;
                        }
                        const currentPrice = getPrice('.price-block__final-price') || getPrice('[data-wba="price-final"]');
                        const oldPrice = getPrice('.price-block__old-price') || getPrice('[data-wba="price-old"]');
                        const title = document.querySelector('h1[data-wba="product-name"]')?.textContent?.trim() || null;
                        return { currentPrice, oldPrice, title };
                    }
                """)
                await browser.close()

                current_price = data.get('currentPrice')
                old_price = data.get('oldPrice')
                title = data.get('title', f"Товар {nm_id}")

                if current_price is None:
                    logger.warning(f"⚠️ Цена не найдена для {nm_id}")
                    if attempt == retries:
                        _failed_cache[nm_id] = datetime.now()
                    await asyncio.sleep(10 * attempt)
                    continue

                if old_price is None or old_price == current_price:
                    spp_percent = 0.0
                else:
                    spp_percent = round((1 - current_price / old_price) * 100, 2)

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
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга {nm_id}: {e}")
            if attempt == retries:
                _failed_cache[nm_id] = datetime.now()
            await asyncio.sleep(15 * attempt)
    return None
