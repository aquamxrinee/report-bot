import os
import logging
from pathlib import Path

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ Токен не найден!")

NEWS_API_KEY = os.getenv("NEWS_API_KEY")

MINI_APP_URL = os.getenv("MINI_APP_URL", "worker-production-a75a.up.railway.app/mini")
if not MINI_APP_URL.startswith(("http://", "https://")):
    MINI_APP_URL = "https://" + MINI_APP_URL

ALLOWED_USER_IDS = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USERS = set(map(int, ALLOWED_USER_IDS.split(","))) if ALLOWED_USER_IDS else set()

USER_NAMES = {
    1289447998: "Роман",
    5167366543: "Евгений"
}

DATA_DIR = Path("/data")
TEMP_DIR = DATA_DIR / "temp"
DB_PATH = DATA_DIR / "reports.db"

if not DATA_DIR.exists():
    DATA_DIR = Path("/tmp/telegram_data")
    TEMP_DIR = DATA_DIR / "temp"
    DB_PATH = DATA_DIR / "reports.db"

DATA_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

# ===== ПРОКСИ ДЛЯ ПАРСИНГА =====
# Получаем прокси из переменной окружения, если нет — используем значение по умолчанию
proxy_raw = os.getenv("PROXY_URL", "dd3e124eaquamxrinee-c-ru:svsvs12e2d@gate.cyberyozh.net:11000")
# Если протокол не указан, добавляем http://
if not proxy_raw.startswith(("http://", "https://", "socks5://")):
    proxy_raw = "http://" + proxy_raw
PROXY_URL = proxy_raw

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

print(f"📁 Данные: {DATA_DIR}")
print(f"📊 БД: {DB_PATH}")
print(f"🌐 Mini App URL: {MINI_APP_URL}")
if ALLOWED_USERS:
    print(f"🔒 Бот доступен только для ID: {ALLOWED_USERS}")
else:
    print("⚠️ ALLOWED_USER_IDS не задан. Бот доступен всем.")
print(f"🔑 Прокси: {PROXY_URL[:50]}...")
