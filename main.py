import threading
import asyncio
from datetime import datetime, timedelta
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import TELEGRAM_BOT_TOKEN, logger
from flask_app import run_flask
from handlers import *
from services import scheduler, scheduled_morning_digest, scheduled_evening_digest
from models import init_spp_tables, init_spp_global_settings
from spp_monitor import monitor_spp
from wb_api import get_aggregated_stats


def refresh_wb_cache():
    """Фоновая задача: обновляет кеш WB API каждый час"""
    logger.info("🔄 Фоновое обновление кеша WB API")
    try:
        get_aggregated_stats(force_refresh=True)
    except Exception as e:
        logger.error(f"❌ Ошибка фонового обновления WB: {e}")


def main():
    print("🤖 Запуск бота...")

    # Инициализация таблиц БД (включая СПП и глобальные настройки)
    init_spp_tables()
    init_spp_global_settings()

    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Создаём приложение бота
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # === КОМАНДЫ ===
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("news_now", news_now_cmd))
    app.add_handler(CommandHandler("set_news", set_news_cmd))
    app.add_handler(CommandHandler("set_news_query", set_news_query_cmd))
    # Команды СПП (оставляем для совместимости)
    app.add_handler(CommandHandler("spp_check", spp_check_cmd))
    app.add_handler(CommandHandler("spp_list", spp_list_cmd))

    # === CALLBACK'и для меню ===
    app.add_handler(CallbackQueryHandler(menu_history_callback, pattern="^menu_history$"))
    app.add_handler(CallbackQueryHandler(menu_analytics_callback, pattern="^menu_analytics$"))
    app.add_handler(CallbackQueryHandler(menu_analytics_main_callback, pattern="^menu_analytics_main$"))
    app.add_handler(CallbackQueryHandler(menu_settings_callback, pattern="^menu_settings$"))
    app.add_handler(CallbackQueryHandler(back_to_menu_callback, pattern="^back_to_menu$"))

    # === НОВОСТИ ===
    app.add_handler(CallbackQueryHandler(news_settings_callback, pattern="^news_settings$"))
    app.add_handler(CallbackQueryHandler(news_now_callback, pattern="^news_now$"))
    app.add_handler(CallbackQueryHandler(news_toggle_callback, pattern="^news_toggle$"))
    app.add_handler(CallbackQueryHandler(news_query_callback, pattern="^news_query$"))
    app.add_handler(CallbackQueryHandler(news_time_callback, pattern="^news_time$"))
    app.add_handler(CallbackQueryHandler(news_time_set_callback, pattern="^news_time_"))

    # === СЕБЕСТОИМОСТЬ ===
    app.add_handler(CallbackQueryHandler(menu_costs_callback, pattern="^menu_costs$"))
    app.add_handler(CallbackQueryHandler(cost_edit_callback, pattern="^cost_edit_"))
    app.add_handler(CallbackQueryHandler(cost_set_callback, pattern="^cost_set_"))
    app.add_handler(CallbackQueryHandler(cost_history_callback, pattern="^cost_history_"))
    app.add_handler(CallbackQueryHandler(cost_delete_callback, pattern="^cost_delete_"))
    app.add_handler(CallbackQueryHandler(cost_delete_all_callback, pattern="^cost_delete_all_"))
    app.add_handler(CallbackQueryHandler(cost_confirm_delete_all_callback, pattern="^cost_confirm_delete_all_"))

    # === АНАЛИТИКА ===
    app.add_handler(CallbackQueryHandler(analytics_toggle_callback, pattern="^analytics_toggle_"))
    app.add_handler(CallbackQueryHandler(analytics_page_callback, pattern="^analytics_page_"))
    app.add_handler(CallbackQueryHandler(analytics_select_all_callback, pattern="^analytics_select_all$"))
    app.add_handler(CallbackQueryHandler(analytics_deselect_all_callback, pattern="^analytics_deselect_all$"))
    app.add_handler(CallbackQueryHandler(analytics_quick_callback, pattern="^analytics_quick_"))
    app.add_handler(CallbackQueryHandler(analytics_show_callback, pattern="^analytics_show$"))

    # === ИСТОРИЯ ===
    app.add_handler(CallbackQueryHandler(history_page_callback, pattern="^history_page_"))
    app.add_handler(CallbackQueryHandler(history_report_callback, pattern="^history_report_"))
    app.add_handler(CallbackQueryHandler(history_toggle_delete_callback, pattern="^history_toggle_delete_"))
    app.add_handler(CallbackQueryHandler(history_enable_delete_callback, pattern="^history_enable_delete$"))
    app.add_handler(CallbackQueryHandler(history_cancel_delete_callback, pattern="^history_cancel_delete$"))
    app.add_handler(CallbackQueryHandler(history_confirm_delete_callback, pattern="^history_confirm_delete$"))

    # === СПП (упрощённое управление через кнопки) ===
    app.add_handler(CallbackQueryHandler(menu_spp_callback, pattern="^menu_spp$"))
    app.add_handler(CallbackQueryHandler(spp_show_articles_callback, pattern="^spp_show_articles$"))
    app.add_handler(CallbackQueryHandler(spp_subscribe_article_callback, pattern="^spp_subscribe_article_"))
    app.add_handler(CallbackQueryHandler(spp_my_subscriptions_callback, pattern="^spp_my_subscriptions$"))
    app.add_handler(CallbackQueryHandler(spp_unsubscribe_button_callback, pattern="^spp_unsubscribe_"))
    app.add_handler(CallbackQueryHandler(spp_toggle_global_callback, pattern="^spp_toggle_global$"))
    app.add_handler(CallbackQueryHandler(spp_threshold_callback, pattern="^spp_threshold$"))
    app.add_handler(CallbackQueryHandler(spp_set_threshold_callback, pattern="^spp_set_threshold_"))
    app.add_handler(CallbackQueryHandler(spp_mute_callback, pattern="^spp_mute_"))
    app.add_handler(CallbackQueryHandler(spp_graph_callback, pattern="^spp_graph_"))

    # === ОБРАБОТЧИКИ ФАЙЛОВ И ТЕКСТА ===
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # === ПЛАНИРОВЩИКИ ===
    # Новости
    scheduler.add_job(scheduled_morning_digest, CronTrigger(hour=8, minute=30), args=[app])
    scheduler.add_job(scheduled_evening_digest, CronTrigger(hour=20, minute=40), args=[app])
    # Обновление WB API каждый час
    scheduler.add_job(refresh_wb_cache, IntervalTrigger(hours=1))
    # Мониторинг СПП — запускаем сразу и затем с интервалом 1 час
    scheduler.add_job(
        lambda: asyncio.run(monitor_spp(app)),
        IntervalTrigger(hours=1),
        next_run_time=datetime.now() + timedelta(minutes=1)
    )

    scheduler.start()

    print("✅ Бот готов, запускаем polling...")
    app.run_polling(allowed_updates=[])


if __name__ == "__main__":
    main()
