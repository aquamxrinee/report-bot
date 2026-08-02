import threading
import asyncio
import traceback
import sys
from datetime import datetime, timedelta
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import TELEGRAM_BOT_TOKEN, logger
from flask_app import run_flask
from handlers import *
from services import scheduler, fetch_weekly_reports_job
from models import init_spp_tables, init_spp_global_settings, init_db, init_spp_brand_tables
from spp_monitor import monitor_spp
from wb_api import get_aggregated_stats


def refresh_wb_cache():
    logger.info("🔄 Фоновое обновление кеша WB API")
    try:
        get_aggregated_stats(force_refresh=True)
    except Exception as e:
        logger.error(f"❌ Ошибка фонового обновления WB: {e}")


def main():
    print("🤖 Запуск бота...")

    init_db()
    init_spp_tables()
    init_spp_global_settings()
    init_spp_brand_tables()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("osn", lambda u,c: u.message.reply_text("Используйте отправку файлов")))
    app.add_handler(CommandHandler("vyk", lambda u,c: u.message.reply_text("Используйте отправку файлов")))
    app.add_handler(CommandHandler("articles", menu_analytics_callback))
    app.add_handler(CommandHandler("spp_subscribe", spp_subscribe_cmd))
    app.add_handler(CommandHandler("spp_unsubscribe", spp_unsubscribe_cmd))
    app.add_handler(CommandHandler("spp_list", spp_list_cmd))
    app.add_handler(CommandHandler("spp_check", spp_check_cmd))
    app.add_handler(CommandHandler("spp_status", spp_status_cmd))
    app.add_handler(CommandHandler("test_parser", test_parser_cmd))
    app.add_handler(CommandHandler("test_proxy", test_proxy_cmd))
    app.add_handler(CommandHandler("sync_articles", sync_articles_cmd))
    app.add_handler(CommandHandler("set_article", set_article_cmd))
    app.add_handler(CommandHandler("fetch_weekly", fetch_weekly_cmd))  # <-- новая команда

    # Колбэки
    app.add_handler(CallbackQueryHandler(menu_history_callback, pattern="^menu_history$"))
    app.add_handler(CallbackQueryHandler(menu_analytics_callback, pattern="^menu_analytics$"))
    app.add_handler(CallbackQueryHandler(menu_analytics_main_callback, pattern="^menu_analytics_main$"))
    app.add_handler(CallbackQueryHandler(menu_settings_callback, pattern="^menu_settings$"))
    app.add_handler(CallbackQueryHandler(back_to_menu_callback, pattern="^back_to_menu$"))
    app.add_handler(CallbackQueryHandler(dev_commands_callback, pattern="^dev_commands$"))

    app.add_handler(CallbackQueryHandler(menu_costs_callback, pattern="^menu_costs$"))
    app.add_handler(CallbackQueryHandler(cost_edit_callback, pattern="^cost_edit_"))
    app.add_handler(CallbackQueryHandler(cost_set_callback, pattern="^cost_set_"))
    app.add_handler(CallbackQueryHandler(cost_history_callback, pattern="^cost_history_"))
    app.add_handler(CallbackQueryHandler(cost_delete_callback, pattern="^cost_delete_"))
    app.add_handler(CallbackQueryHandler(cost_delete_all_callback, pattern="^cost_delete_all_"))
    app.add_handler(CallbackQueryHandler(cost_confirm_delete_all_callback, pattern="^cost_confirm_delete_all_"))

    app.add_handler(CallbackQueryHandler(analytics_toggle_callback, pattern="^analytics_toggle_"))
    app.add_handler(CallbackQueryHandler(analytics_page_callback, pattern="^analytics_page_"))
    app.add_handler(CallbackQueryHandler(analytics_select_all_callback, pattern="^analytics_select_all$"))
    app.add_handler(CallbackQueryHandler(analytics_deselect_all_callback, pattern="^analytics_deselect_all$"))
    app.add_handler(CallbackQueryHandler(analytics_quick_callback, pattern="^analytics_quick_"))
    app.add_handler(CallbackQueryHandler(analytics_show_callback, pattern="^analytics_show$"))

    app.add_handler(CallbackQueryHandler(history_page_callback, pattern="^history_page_"))
    app.add_handler(CallbackQueryHandler(history_report_callback, pattern="^history_report_"))
    app.add_handler(CallbackQueryHandler(history_toggle_delete_callback, pattern="^history_toggle_delete_"))
    app.add_handler(CallbackQueryHandler(history_enable_delete_callback, pattern="^history_enable_delete$"))
    app.add_handler(CallbackQueryHandler(history_cancel_delete_callback, pattern="^history_cancel_delete$"))
    app.add_handler(CallbackQueryHandler(history_confirm_delete_callback, pattern="^history_confirm_delete$"))

    app.add_handler(CallbackQueryHandler(menu_spp_callback, pattern="^menu_spp$"))
    app.add_handler(CallbackQueryHandler(spp_show_articles_callback, pattern="^spp_show_articles$"))
    app.add_handler(CallbackQueryHandler(spp_subscribe_article_callback, pattern="^spp_subscribe_art_"))
    app.add_handler(CallbackQueryHandler(spp_show_brands_callback, pattern="^spp_show_brands$"))
    app.add_handler(CallbackQueryHandler(spp_subscribe_brand_callback, pattern="^spp_subscribe_brand_"))
    app.add_handler(CallbackQueryHandler(spp_my_subscriptions_callback, pattern="^spp_my_subscriptions$"))
    app.add_handler(CallbackQueryHandler(spp_unsubscribe_button_callback, pattern="^spp_unsubscribe_"))
    app.add_handler(CallbackQueryHandler(spp_stats_callback, pattern="^spp_stats$"))
    app.add_handler(CallbackQueryHandler(spp_toggle_global_callback, pattern="^spp_toggle_global$"))
    app.add_handler(CallbackQueryHandler(spp_threshold_callback, pattern="^spp_threshold$"))
    app.add_handler(CallbackQueryHandler(spp_set_threshold_callback, pattern="^spp_set_threshold_"))
    app.add_handler(CallbackQueryHandler(spp_threshold_custom_callback, pattern="^spp_threshold_custom$"))
    app.add_handler(CallbackQueryHandler(spp_mute_callback, pattern="^spp_mute_"))
    app.add_handler(CallbackQueryHandler(spp_graph_callback, pattern="^spp_graph_"))

    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Планировщик
    scheduler.add_job(refresh_wb_cache, IntervalTrigger(hours=1))
    scheduler.add_job(
        lambda: asyncio.run(monitor_spp(app)),
        IntervalTrigger(hours=3),
        next_run_time=datetime.now() + timedelta(minutes=1)
    )
    # Еженедельный отчёт: понедельник, 12:00 МСК = UTC 9:00
    scheduler.add_job(
        lambda: asyncio.run(fetch_weekly_reports_job(app)),
        CronTrigger(day_of_week='mon', hour=9, minute=0),
        id='weekly_report'
    )
    scheduler.start()

    print("✅ Бот готов, запускаем polling...")
    app.run_polling(allowed_updates=[])


if __name__ == "__main__":
    try:
        main()
    except MemoryError:
        print("❌ КРИТИЧЕСКАЯ ОШИБКА: нехватка памяти!")
        logger.error("Бот упал из-за нехватки памяти (MemoryError). Railway может перезапустить процесс.")
        sys.exit(1)
    except Exception as e:
        print("❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПУСКЕ:")
        traceback.print_exc()
        logger.error(f"Бот упал с ошибкой: {e}")
        sys.exit(1)
