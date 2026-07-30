import asyncio
import re
from datetime import datetime
from typing import Optional, Dict, Any
from config import logger

try:
    from playwright.async_api import async_playwright
except ImportError:
    logger.error("❌ Playwright не установлен. Установите: pip install playwright && playwright install")
    async_playwright = None


class SPPParser:
    """Парсер для получения данных о цене и СПП с карточки товара Wildberries"""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright = None
        self._browser = None

    async def __aenter__(self):
        if async_playwright is None:
            raise RuntimeError("Playwright не установлен")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def get_product_data(self, nm_id: int) -> Optional[Dict[str, Any]]:
        """
        Получает данные о товаре: текущая цена, старая цена, СПП (в процентах)
        """
        url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
        logger.info(f"🔍 Парсим товар {nm_id}")

        try:
            context = await self._browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            # Переходим на страницу
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)

            # Ждём загрузки цены (селектор может меняться)
            try:
                await page.wait_for_selector(".price-block__final-price", timeout=10000)
            except:
                # Попробуем альтернативный селектор
                await page.wait_for_selector('[data-wba="price-final"]', timeout=10000)

            # Получаем HTML и парсим через BeautifulSoup или直接用 JS
            # Используем JavaScript для извлечения данных
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
                    return { currentPrice, oldPrice };
                }
            """)

            current_price = data.get('currentPrice')
            old_price = data.get('oldPrice')

            if current_price is None or old_price is None:
                # Пробуем альтернативный метод: парсим JSON из скрипта
                scripts = await page.evaluate("""
                    () => {
                        const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                        for (let s of scripts) {
                            try {
                                const json = JSON.parse(s.textContent);
                                if (json.offers) {
                                    return json.offers;
                                }
                            } catch(e) {}
                        }
                        return null;
                    }
                """)
                if scripts:
                    if isinstance(scripts, dict):
                        current_price = scripts.get('price')
                        old_price = scripts.get('price', current_price)  # не всегда есть
                    elif isinstance(scripts, list) and len(scripts) > 0:
                        current_price = scripts[0].get('price')
                        old_price = scripts[0].get('price', current_price)

            if current_price is None:
                logger.warning(f"⚠️ Не удалось найти цену для {nm_id}")
                return None

            # Если старая цена не найдена, считаем, что скидки нет
            if old_price is None or old_price == current_price:
                spp_percent = 0.0
            else:
                spp_percent = round((1 - current_price / old_price) * 100, 2)

            # Название товара (для отображения)
            title = await page.evaluate("""
                () => {
                    const h1 = document.querySelector('h1[data-wba="product-name"]');
                    return h1 ? h1.textContent.trim() : null;
                }
            """)

            await context.close()

            return {
                'nm_id': nm_id,
                'current_price': current_price,
                'old_price': old_price,
                'spp_percent': spp_percent,
                'title': title or f"Товар {nm_id}",
                'url': url,
                'checked_at': datetime.now().isoformat()
            }

        except Exception as e:
            logger.error(f"❌ Ошибка парсинга {nm_id}: {e}")
            return None


async def get_spp_for_article(nm_id: int) -> Optional[Dict[str, Any]]:
    """Утилита для быстрого получения данных по одному артикулу"""
    async with SPPParser(headless=True) as parser:
        return await parser.get_product_data(nm_id)