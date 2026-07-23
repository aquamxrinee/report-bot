#!/usr/bin/env python3
"""
ФИНАЛЬНЫЙ Telegram бот для обработки еженедельных отчетов
Деплой на Railway (бесплатно, 24/7)
"""

import os
import re
import shutil
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import openpyxl
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ===== НАСТРОЙКИ =====
# Токен берем из переменных окружения Railway
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ Токен не найден! Установите переменную TELEGRAM_BOT_TOKEN в Railway")

# Папка для временных файлов (в Railway доступна для записи)
TEMP_DIR = Path("/tmp/telegram_bot_temp")
TEMP_DIR.mkdir(exist_ok=True)

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

print(f"📁 Используется папка: {TEMP_DIR}")

# ===== FLASK ДЛЯ ПИНГОВ (чтобы бот не засыпал) =====
flask_app = Flask(__name__)

@flask_app.route("/")
def health_check():
    """Эндпоинт для проверки здоровья бота"""
    return "🤖 Бот работает!", 200

@flask_app.route("/ping")
def ping():
    """Эндпоинт для пингов от UptimeRobot/Kuma"""
    return "pong", 200

def run_flask():
    """Запускает Flask в отдельном потоке"""
    from threading import Thread
    def _run():
        flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
    Thread(target=_run, daemon=True).start()

# ===== КЛАСС ДЛЯ ОБРАБОТКИ ОТЧЕТОВ =====
class ReportProcessor:
    """Обрабатывает отчеты и заполняет шаблон"""
    
    def process_files(self, osn_path, vyk_path, template_path):
        """Основной метод обработки"""
        try:
            df_osn = pd.read_excel(osn_path)
            df_vyk = pd.read_excel(vyk_path)
            
            # Парсим дату из названия файла осн
            filename = Path(osn_path).name
            match = re.search(r'(\d{1,2})_(\d{2})-(\d{1,2})_(\d{2})', filename)
            if match:
                date_range = f"{match.group(1)}.{match.group(2)}-{match.group(3)}.{match.group(4)}"
            else:
                date_range = datetime.now().strftime("%d.%m")
            
            # Вычисляем все значения
            values = self._calculate_all_values(df_osn, df_vyk, date_range)
            
            # Заполняем шаблон
            self._fill_template(template_path, values)
            
            return True, values
        except Exception as e:
            logger.error(f"Ошибка обработки: {e}")
            return False, str(e)
    
    def _calculate_all_values(self, df_osn, df_vyk, date_range):
        """Вычисляет все 29 значений"""
        values = {'B1': date_range, 'F1': date_range}
        
        # ===== ОСНОВНОЙ ОТЧЕТ - ЦАП ЦАРАПКИН =====
        filter_carp_sale = ((df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа'] == 'Продажа')
        values['B4'] = df_osn[filter_carp_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_return = df_osn['Тип документа'] == 'Возврат'
        values['B5'] = df_osn[filter_return]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_carp_all = (df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())
        values['B7'] = df_osn[filter_carp_all]['Услуги по доставке товара покупателю'].sum()
        values['B9'] = df_osn[filter_carp_all]['Операции на приемке'].sum()
        values['B10'] = df_osn['Общая сумма штрафов'].sum()
        values['B11'] = df_osn[filter_carp_all]['Удержания'].sum()
        values['B26'] = df_osn[filter_carp_all]['Хранение'].sum()
        values['B29'] = df_osn[filter_carp_all]['Разовое изменение срока перечисления денежных средств'].sum()
        values['B44'] = df_osn['Цена розничная'].sum()
        
        # ===== ОСНОВНОЙ ОТЧЕТ - HARAKIRI =====
        filter_hara_sale = (df_osn['Бренд'] == 'Harakiri') & (df_osn['Тип документа'] == 'Продажа')
        values['F4'] = df_osn[filter_hara_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        values['F5'] = df_osn[filter_return]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_hara_all = df_osn['Бренд'] == 'Harakiri'
        values['F7'] = df_osn[filter_hara_all]['Услуги по доставке товара покупателю'].sum()
        values['F9'] = df_osn[filter_hara_all]['Операции на приемке'].sum()
        values['F10'] = df_osn[filter_hara_all]['Общая сумма штрафов'].sum()
        values['F11'] = df_osn[filter_hara_all]['Удержания'].sum()
        
        # ===== НОВАЯ СТРОКА для B41 =====
        values['B41'] = df_osn[filter_hara_all]['Цена розничная'].sum()  # Цена розничная для Harakiri
        
        # ===== ПО ВЫКУПАМ - ЦАП ЦАРАПКИН =====
        filter_carp_vyk_sale = ((df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())) & (df_vyk['Тип документа'] == 'Продажа')
        values['M4'] = df_vyk[filter_carp_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_return_vyk = df_vyk['Тип документа'] == 'Возврат'
        values['M5'] = df_vyk[filter_return_vyk]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_carp_vyk_all = (df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())
        values['M7'] = df_vyk[filter_carp_vyk_all]['Услуги по доставке товара покупателю'].sum()
        values['M8'] = df_vyk[filter_carp_vyk_all]['Операции на приемке'].sum()
        values['M9'] = df_vyk['Общая сумма штрафов'].sum()
        values['B47'] = df_vyk['Цена розничная'].sum()
        
        # ===== ПО ВЫКУПАМ - HARAKIRI =====
        filter_hara_vyk_sale = (df_vyk['Бренд'] == 'Harakiri') & (df_vyk['Тип документа'] == 'Продажа')
        values['Q4'] = df_vyk[filter_hara_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
        
        values['Q5'] = df_vyk[filter_return_vyk]['К перечислению Продавцу за реализованный Товар'].sum()
        
        filter_hara_vyk_all = df_vyk['Бренд'] == 'Harakiri'
        values['Q7'] = df_vyk[filter_hara_vyk_all]['Услуги по доставке товара покупателю'].sum()
        values['Q8'] = df_vyk[filter_hara_vyk_all]['Операции на приемке'].sum()
        values['Q9'] = df_vyk[filter_hara_vyk_all]['Общая сумма штрафов'].sum()
        
        return values
    
    def _fill_template(self, template_path, values):
        """Заполняет шаблон значениями, сохраняя формулы"""
        # Проверка: нельзя сохранять в /app/
        if str(template_path).startswith("/app/"):
            raise ValueError("❌ НЕЛЬЗЯ сохранять в /app/! Это read-only папка!")
        
        # Загружаем шаблон, НЕ вычисляя формулы
        wb = openpyxl.load_workbook(
            template_path,
            data_only=False,      # Не заменять формулы на значения
            keep_links=False,     # Не сохранять ссылки на внешние файлы
            keep_vba=False        # Не сохранять макросы
        )
        ws = wb.active
        
        # Заполняем значения
        for cell, value in values.items():
            ws[cell] = value
            if isinstance(value, float) and value != int(value):
                ws[cell].number_format = '0.00'
        
        # Отключаем авто-пересчёт при открытии
        ws.sheet_view.calcMode = 'manual'
        
        # Сохраняем
        wb.save(template_path)
        logger.info(f"Шаблон сохранен: {template_path}")


# ===== ОБРАБОТЧИКИ КОМАНД =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text(
        "👋 Привет! Я бот для обработки еженедельных отчетов.\n\n"
        "📤 Как пользоваться:\n"
        "1️⃣ Отправь мне первый файл отчета\n"
        "2️⃣ Я спрошу: это основной или по выкупам?\n"
        "3️⃣ Напиши /osn или /vyk\n"
        "4️⃣ Отправь второй файл\n"
        "5️⃣ Напиши тип второго файла\n"
        "6️⃣ Готово! Получишь заполненный шаблон! ✅\n\n"
        "⚠️ Файлы можно называть как угодно - просто указываешь тип!"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    await update.message.reply_text(
        "📋 Команды:\n"
        "/start - начать\n"
        "/help - помощь\n"
        "/osn - текущий файл это основной отчет\n"
        "/vyk - текущий файл это отчет по выкупам\n\n"
        "Просто отправляй файлы и указывай тип! 📁"
    )


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик загруженных файлов"""
    try:
        document = update.message.document
        
        if not document.file_name.endswith(('.xlsx', '.xls')):
            await update.message.reply_text("❌ Нужен Excel файл (.xlsx или .xls)")
            return
        
        # Скачиваем файл
        file = await context.bot.get_file(document.file_id)
        file_path = TEMP_DIR / document.file_name
        await file.download_to_drive(file_path)
        
        # Инициализируем контекст пользователя
        if 'files' not in context.user_data:
            context.user_data['files'] = {}
        
        # Сохраняем текущий файл
        context.user_data['current_file'] = str(file_path)
        
        await update.message.reply_text(
            f"📄 Файл получен: {document.file_name}\n\n"
            "Какой это отчет?\n"
            "/osn - Основной отчет\n"
            "/vyk - Отчет по выкупам"
        )
    except Exception as e:
        logger.error(f"Ошибка при загрузке файла: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")


async def handle_osn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /osn - это основной отчет"""
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправь файл!")
        return
    
    context.user_data['files']['osn'] = context.user_data['current_file']
    await update.message.reply_text("✅ Основной отчет сохранен!\nТеперь отправь отчет по выкупам...")
    
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)


async def handle_vyk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /vyk - это отчет по выкупам"""
    if 'current_file' not in context.user_data:
        await update.message.reply_text("❌ Сначала отправь файл!")
        return
    
    context.user_data['files']['vyk'] = context.user_data['current_file']
    await update.message.reply_text("✅ Отчет по выкупам сохранен!\nТеперь отправь основной отчет...")
    
    if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
        await process_and_send(update, context)


async def process_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает оба отчета и отправляет результат"""
    try:
        await update.message.reply_text("⏳ Обрабатываю отчеты...")
        
        osn_file = context.user_data['files']['osn']
        vyk_file = context.user_data['files']['vyk']
        
        # ВАЖНО: Используем оригинальный шаблон из /app/
        original_template = Path("/app/шаблон.xlsx")
        
        # Если шаблона нет, ищем в других местах
        if not original_template.exists():
            possible_paths = [
                Path("шаблон.xlsx"),
                TEMP_DIR / "template.xlsx",
            ]
            for path in possible_paths:
                if path.exists():
                    original_template = path
                    break
        
        # Создаем уникальное имя для временного файла
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        template_file = TEMP_DIR / f"шаблон_{timestamp}.xlsx"
        
        if original_template.exists():
            shutil.copy(original_template, template_file)
            logger.info(f"Шаблон скопирован из {original_template}")
        else:
            await update.message.reply_text("⚠️ Шаблон не найден. Создаю новый...")
            wb = openpyxl.Workbook()
            wb.save(template_file)
        
        # Обрабатываем
        processor = ReportProcessor()
        success, result = processor.process_files(osn_file, vyk_file, str(template_file))
        
        if success:
            with open(template_file, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    caption="✅ Готово! Шаблон заполнен и готов к скачиванию."
                )
            
            await update.message.reply_text(
                "📊 Статистика обработки:\n"
                "• Основной отчет: ЦАП + HARAKIRI ✅\n"
                "• По выкупам: ЦАП + HARAKIRI ✅\n"
                "• Ячеек заполнено: 30 ✅\n\n"  # Обновлено с 29 на 30
                "Спасибо за использование! 🚀"
            )
            
            # Удаляем временный файл
            try:
                os.remove(template_file)
            except:
                pass
            
            # Очищаем контекст пользователя
            context.user_data['files'] = {}
        else:
            await update.message.reply_text(f"❌ Ошибка обработки: {result}")
    
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await update.message.reply_text(f"❌ Критическая ошибка: {str(e)}")


# ===== ЗАПУСК БОТА =====
def main():
    """Запуск бота"""
    print("🤖 Запускаю Telegram бот...")
    
    # Запускаем Flask для пингов
    run_flask()
    print("✅ Flask сервер запущен для пингов")
    
    # Создаем приложение Telegram
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("osn", handle_osn))
    app.add_handler(CommandHandler("vyk", handle_vyk))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    
    print("✅ Бот запущен и ждет сообщений...")
    
    # Запускаем бота
    app.run_polling(allowed_updates=[])


if __name__ == '__main__':
    main()
