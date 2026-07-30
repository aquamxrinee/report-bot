#!/bin/bash
echo "Установка зависимостей..."
pip install -r requirements.txt
echo "Установка браузеров для Playwright..."
playwright install chromium
playwright install-deps
echo "Запуск бота..."
python main.py
