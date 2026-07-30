import asyncio
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path
import matplotlib.pyplot as plt
import io
import base64

from config import logger, DB_PATH
from models import get_all_articles_with_costs  # используем для получения списка артикулов
from spp_parser import get_spp_for_article


def init_spp_tables():
    """Создаёт таблицы для хранения истории СПП и заглушек"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # История изменений СПП
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS spp_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nm_id INTEGER NOT NULL,
            article TEXT,
            current_price REAL,
            old_price REAL,
            spp_percent REAL,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Заглушки (mutes)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS spp_mutes (
            user_id INTEGER NOT NULL,
            nm_id INTEGER NOT NULL,
            mute_until TIMESTAMP,
            PRIMARY KEY (user_id, nm_id)
        )
    ''')

    # Подписки пользователей на уведомления по артикулам
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS spp_subscriptions (
            user_id INTEGER NOT NULL,
            nm_id INTEGER NOT NULL,
            threshold REAL DEFAULT 5.0,  -- порог изменения в п.п.
            PRIMARY KEY (user_id, nm_id)
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("✅ Таблицы для СПП инициализированы")


def save_spp_history(nm_id: int, article: str, current_price: float, old_price: float, spp_percent: float):
    """Сохраняет запись в историю СПП"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO spp_history (nm_id, article, current_price, old_price, spp_percent)
        VALUES (?, ?, ?, ?, ?)
    ''', (nm_id, article, current_price, old_price, spp_percent))
    conn.commit()
    conn.close()


def get_last_spp(nm_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает последнюю запись СПП для артикула"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT current_price, old_price, spp_percent, checked_at
        FROM spp_history
        WHERE nm_id = ?
        ORDER BY checked_at DESC LIMIT 1
    ''', (nm_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'current_price': row[0],
            'old_price': row[1],
            'spp_percent': row[2],
            'checked_at': row[3]
        }
    return None


def get_spp_history(nm_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    """Возвращает историю СПП для построения графика"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT checked_at, spp_percent, current_price, old_price
        FROM spp_history
        WHERE nm_id = ?
        ORDER BY checked_at DESC LIMIT ?
    ''', (nm_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [{'checked_at': row[0], 'spp_percent': row[1], 'current_price': row[2], 'old_price': row[3]} for row in rows]


def is_muted(user_id: int, nm_id: int) -> bool:
    """Проверяет, заглушены ли уведомления для пользователя по артикулу"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT mute_until FROM spp_mutes
        WHERE user_id = ? AND nm_id = ? AND mute_until > datetime('now')
    ''', (user_id, nm_id))
    row = cursor.fetchone()
    conn.close()
    return row is not None


def mute_article(user_id: int, nm_id: int, hours: int = 2):
    """Заглушает уведомления на указанное количество часов"""
    mute_until = (datetime.now() + timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO spp_mutes (user_id, nm_id, mute_until)
        VALUES (?, ?, ?)
    ''', (user_id, nm_id, mute_until))
    conn.commit()
    conn.close()


def unmute_article(user_id: int, nm_id: int):
    """Снимает заглушку"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('DELETE FROM spp_mutes WHERE user_id = ? AND nm_id = ?', (user_id, nm_id))
    conn.commit()
    conn.close()


def get_subscribed_users(nm_id: int) -> List[int]:
    """Возвращает список пользователей, подписанных на уведомления по артикулу"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM spp_subscriptions WHERE nm_id = ?', (nm_id,))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]


def subscribe_user(user_id: int, nm_id: int, threshold: float = 5.0):
    """Подписывает пользователя на уведомления по артикулу"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO spp_subscriptions (user_id, nm_id, threshold)
        VALUES (?, ?, ?)
    ''', (user_id, nm_id, threshold))
    conn.commit()
    conn.close()


def unsubscribe_user(user_id: int, nm_id: int):
    """Отписывает пользователя"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('DELETE FROM spp_subscriptions WHERE user_id = ? AND nm_id = ?', (user_id, nm_id))
    conn.commit()
    conn.close()


def get_user_subscriptions(user_id: int) -> List[Dict[str, Any]]:
    """Возвращает все подписки пользователя"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT nm_id, threshold
        FROM spp_subscriptions
        WHERE user_id = ?
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [{'nm_id': row[0], 'threshold': row[1]} for row in rows]


def get_all_tracked_articles() -> List[int]:
    """Возвращает все nm_id, на которые есть хотя бы одна подписка"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT nm_id FROM spp_subscriptions')
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]


async def check_and_notify(bot_app, user_id: int, nm_id: int, new_data: Dict[str, Any]):
    """Проверяет изменение СПП и отправляет уведомление, если порог превышен"""
    last = get_last_spp(nm_id)
    if not last:
        # Первая проверка — просто сохраняем, не уведомляем
        return

    old_spp = last['spp_percent']
    new_spp = new_data['spp_percent']
    diff = abs(new_spp - old_spp)

    # Получаем порог для пользователя
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT threshold FROM spp_subscriptions WHERE user_id = ? AND nm_id = ?', (user_id, nm_id))
    row = cursor.fetchone()
    conn.close()
    threshold = row[0] if row else 5.0

    if diff >= threshold:
        # Проверяем заглушку
        if is_muted(user_id, nm_id):
            logger.info(f"🔇 Уведомление для {nm_id} заглушено для пользователя {user_id}")
            return

        # Формируем сообщение
        title = new_data.get('title', f'Товар {nm_id}')
        direction = "упала" if new_spp < old_spp else "выросла"
        text = (
            f"📊 *СПП изменилась!*\n\n"
            f"Артикул: {nm_id}\n"
            f"Название: {title}\n"
            f"СПП: {old_spp}% → {new_spp}% ({direction} на {diff:.1f} п.п.)\n"
            f"Цена: {last['current_price']} ₽ → {new_data['current_price']} ₽\n"
            f"[Открыть карточку]({new_data['url']})"
        )

        # Кнопки: Глушить, График, Открыть
        keyboard = [
            [
                InlineKeyboardButton("🔇 Глушить на 2ч", callback_data=f"spp_mute_{nm_id}"),
                InlineKeyboardButton("📈 График", callback_data=f"spp_graph_{nm_id}"),
            ],
            [InlineKeyboardButton("🔗 Открыть", url=new_data['url'])]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await bot_app.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            logger.info(f"✅ Уведомление о СПП отправлено пользователю {user_id} для {nm_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления: {e}")


async def monitor_spp(bot_app):
    """Фоновая задача: проверяет все отслеживаемые артикулы"""
    logger.info("🔄 Запуск мониторинга СПП...")
    nm_ids = get_all_tracked_articles()
    if not nm_ids:
        logger.info("📭 Нет артикулов для отслеживания СПП")
        return

    for nm_id in nm_ids:
        try:
            data = await get_spp_for_article(nm_id)
            if not data:
                continue

            # Сохраняем историю
            save_spp_history(
                nm_id=nm_id,
                article=data.get('title', ''),
                current_price=data['current_price'],
                old_price=data['old_price'],
                spp_percent=data['spp_percent']
            )

            # Проверяем для всех подписанных пользователей
            users = get_subscribed_users(nm_id)
            for user_id in users:
                await check_and_notify(bot_app, user_id, nm_id, data)

            # Задержка между артикулами, чтобы не перегружать
            await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"❌ Ошибка мониторинга для {nm_id}: {e}")


def generate_spp_graph(nm_id: int, width: int = 800, height: int = 400) -> str:
    """
    Генерирует график изменения СПП и возвращает base64-строку для вставки в HTML
    """
    history = get_spp_history(nm_id, limit=30)
    if not history:
        return None

    # Разворачиваем для хронологического порядка
    history = history[::-1]

    dates = [h['checked_at'][:16] for h in history]
    spp_values = [h['spp_percent'] for h in history]
    prices = [h['current_price'] for h in history]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(width/100, height/100), sharex=True)

    # График СПП
    ax1.plot(dates, spp_values, marker='o', color='red', linewidth=2)
    ax1.set_ylabel('СПП, %')
    ax1.grid(True, alpha=0.3)
    ax1.set_title(f'Динамика СПП для артикула {nm_id}')

    # График цены
    ax2.plot(dates, prices, marker='s', color='blue', linewidth=2)
    ax2.set_ylabel('Цена, ₽')
    ax2.grid(True, alpha=0.3)
    ax2.set_xlabel('Дата')

    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()

    # Сохраняем в буфер
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close()

    # Кодируем в base64
    img_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    return img_base64