#!/bin/bash
echo "Установка зависимостей..."
pip install -r requirements.txt
echo "Установка браузеров для Playwright..."
echo "Запуск бота..."
python main.py
