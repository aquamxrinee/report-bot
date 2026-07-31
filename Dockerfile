FROM python:3.13-slim

WORKDIR /app

# Установка минимальных системных зависимостей для Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libnss3 \
    libx11-6 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxi6 \
    libxtst6 \
    libxrandr2 \
    libasound2 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libgbm1 \
    libxkbcommon0 \
    libpango-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libgl1-mesa-dri \
    && rm -rf /var/lib/apt/lists/*

# Копируем и устанавливаем Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Устанавливаем браузер Playwright (Chromium) и его системные зависимости
RUN playwright install chromium && playwright install-deps

# Копируем остальной проект
COPY . .

# Делаем start.sh исполняемым
RUN chmod +x start.sh

CMD ["bash", "start.sh"]
