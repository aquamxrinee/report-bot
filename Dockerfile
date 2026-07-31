FROM python:3.13-slim

WORKDIR /app

# Устанавливаем компилятор и системные зависимости для Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    build-essential \
    g++ \
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

# Устанавливаем Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Устанавливаем браузер Playwright (Chromium)
RUN playwright install chromium && playwright install-deps

COPY . .
RUN chmod +x start.sh

CMD ["bash", "start.sh"]
