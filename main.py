import threading
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from apscheduler.triggers.cron import CronTrigger

from config import TELEGRAM_BOT_TOKEN, logger
from flask_app import run_flask
from handlers import *
from services import scheduler, scheduled_morning_digest, scheduled_evening_digest
from wb_api import get_aggregated_stats  # для команды /wb_stats

def main():
    print("🤖 Запуск бота...")
    
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Создаём приложение бота
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # === КОМАНДЫ ===
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("osn", handle_osn))
    app.add_handler(CommandHandler("vyk", handle_vyk))
    app.add_handler(CommandHandler("articles", articles_full_cmd))
    app.add_handler(CommandHandler("news_now", news_now_cmd))
    app.add_handler(CommandHandler("set_news", set_news_cmd))
    app.add_handler(CommandHandler("set_news_query", set_news_query_cmd))
    app.add_handler(CommandHandler("wb_stats", wb_stats_cmd))   # <-- новая команда

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

    # === АНАЛИТИКА ПО АРТИКУЛАМ ===
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

    # === АРТИКУЛЫ (из отчёта) ===
    app.add_handler(CallbackQueryHandler(articles_callback, pattern="^show_articles$"))
    app.add_handler(CallbackQueryHandler(growth_callback, pattern="^growth$"))
    app.add_handler(CallbackQueryHandler(decline_callback, pattern="^decline$"))
    app.add_handler(CallbackQueryHandler(compare_articles_callback, pattern="^compare_articles$"))

    # === ОБРАБОТЧИКИ ФАЙЛОВ И ТЕКСТА ===
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # === ПЛАНИРОВЩИК НОВОСТЕЙ ===
    scheduler.add_job(scheduled_morning_digest, CronTrigger(hour=8, minute=30), args=[app])
    scheduler.add_job(scheduled_evening_digest, CronTrigger(hour=20, minute=40), args=[app])
    scheduler.start()

    print("✅ Бот готов, запускаем polling...")
    app.run_polling(allowed_updates=[])

if __name__ == "__main__":
    main()
