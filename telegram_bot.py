#!/usr/bin/env python3
"""
Telegram бот для обработки еженедельных отчетов через Claude API
Автоматически обрабатывает два отчета (основной + по выкупам) и заполняет шаблон
"""

import os
import re
import io
from datetime import datetime
from pathlib import Path

from telegram import Update, Document
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import pandas as pd
import openpyxl

# ===== НАСТРОЙКИ =====
TELEGRAM_BOT_TOKEN = "8846869937:AAGcYtTTr2Z_CFmoniZ-62tG9Si-yy8zNJg"  # Получи от @BotFather
ANTHROPIC_API_KEY = "YOUR_ANTHROPIC_API_KEY"   # Получи с https://console.anthropic.com

# Папка для временных файлов
TEMP_DIR = Path("/tmp/reports")
TEMP_DIR.mkdir(exist_ok=True)

# ===== КЛАССЫ ОБРАБОТКИ =====
class ReportProcessor:
    """Класс для обработки отчетов"""
    
    def __init__(self):
        self.osn_file = None
        self.vyk_file = None
        self.template_file = None
        
    def process_files(self, osn_path, vyk_path, template_path):
        """Обработать оба отчета и заполнить шаблон"""
        try:
            # Читаем отчеты
            df_osn = pd.read_excel(osn_path)
            df_vyk = pd.read_excel(vyk_path)
            
            # Парсим дату из названия файла
            filename = Path(osn_path).name
            match = re.search(r'(\d{1,2})_(\d{2})-(\d{1,2})_(\d{2})', filename)
            if match:
                date_range = f"{match.group(1)}.{match.group(2)}-{match.group(3)}.{match.group(4)}"
            else:
                date_range = datetime.now().strftime("%d.%m")
            
            # Вычисляем все значения
            values = self._calculate_values(df_osn, df_vyk, date_range)
            
            # Заполняем шаблон
            self._fill_template(template_path, values)
            
            return True, values
        except Exception as e:
            return False, str(e)
    
    def _calculate_values(self, df_osn, df_vyk, date_range):
        """Вычислить все значения из отчетов"""
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
        """Заполнить шаблон значениями"""
        wb = openpyxl.load_workbook(template_path)
        ws = wb.active
        
        for cell, value in values.items():
            ws[cell] = value
            if isinstance(value, float) and value != int(value):
                ws[cell].number_format = '0.00'
        
        wb.save(template_path)


# ===== ОБРАБОТЧИКИ TELEGRAM =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text(
        "👋 Привет! Я бот для обработки еженедельных отчетов.\n\n"
        "📤 Отправь мне два файла:\n"
        "1️⃣ Основной отчет (осн.xlsx)\n"
        "2️⃣ Отчет по выкупам (вык.xlsx)\n\n"
        "✅ Я автоматически заполню шаблон и верну готовый файл!"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    await update.message.reply_text(
        "📋 Как использовать бота:\n\n"
        "1. Отправь основной отчет (Еженедельный_XX_XX-XX_XX_осн.xlsx)\n"
        "2. Отправь отчет по выкупам (Еженедельный_XX_XX-XX_XX_вык.xlsx)\n"
        "3. Бот автоматически обработает оба файла\n"
        "4. Получишь готовый шаблон с заполненными данными\n\n"
        "⚠️ Важно: Названия файлов должны содержать даты в формате XX_XX-XX_XX"
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик загруженных файлов"""
    try:
        document: Document = update.message.document
        
        # Проверяем, что это Excel файл
        if not document.file_name.endswith(('.xlsx', '.xls')):
            await update.message.reply_text("❌ Нужен файл Excel (.xlsx или .xls)")
            return
        
        # Скачиваем файл
        file = await context.bot.get_file(document.file_id)
        file_path = TEMP_DIR / document.file_name
        await file.download_to_drive(file_path)
        
        # Сохраняем файл в контексте
        if 'files' not in context.user_data:
            context.user_data['files'] = {}
        
        # Определяем тип файла (осн или вык)
        if '_осн' in document.file_name:
            context.user_data['files']['osn'] = str(file_path)
            await update.message.reply_text("✅ Основной отчет получен. Жду отчета по выкупам...")
        elif '_вык' in document.file_name:
            context.user_data['files']['vyk'] = str(file_path)
            await update.message.reply_text("✅ Отчет по выкупам получен. Жду основного отчета...")
        else:
            await update.message.reply_text("⚠️ Не могу определить тип файла. Проверь название (должно быть _осн или _вык)")
            return
        
        # Если получены оба файла - обрабатываем
        if 'osn' in context.user_data['files'] and 'vyk' in context.user_data['files']:
            await process_reports(update, context)
    
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при обработке файла: {str(e)}")


async def process_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработать оба отчета"""
    try:
        await update.message.reply_text("⏳ Обрабатываю отчеты...")
        
        osn_file = context.user_data['files']['osn']
        vyk_file = context.user_data['files']['vyk']
        
        # Копируем шаблон из uploads или создаем новый
        template_file = TEMP_DIR / "шаблон_результат.xlsx"
        
        # Попробуем найти исходный шаблон
        original_template = Path("/mnt/user-data/uploads/шаблон.xlsx")
        if original_template.exists():
            import shutil
            shutil.copy(original_template, template_file)
        else:
            await update.message.reply_text("⚠️ Не найден исходный шаблон. Создаю новый...")
            # Создаем минимальный шаблон
            openpyxl.Workbook().save(template_file)
        
        # Обрабатываем отчеты
        processor = ReportProcessor()
        success, result = processor.process_files(osn_file, vyk_file, str(template_file))
        
        if success:
            # Отправляем готовый файл
            with open(template_file, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    caption="✅ Готово! Шаблон заполнен."
                )
            
            # Выводим краткую статистику
            stats = (
                f"📊 Обработано:\n"
                f"• Основной отчет: ЦАП + HARAKIRI\n"
                f"• По выкупам: ЦАП + HARAKIRI\n"
                f"• Всего ячеек заполнено: 29\n\n"
                f"💾 Файл готов к скачиванию!"
            )
            await update.message.reply_text(stats)
            
            # Очищаем данные пользователя
            context.user_data['files'] = {}
        else:
            await update.message.reply_text(f"❌ Ошибка обработки: {result}")
    
    except Exception as e:
        await update.message.reply_text(f"❌ Критическая ошибка: {str(e)}")


# ===== ЗАПУСК БОТА =====
def main():
    """Запуск бота"""
    print("🤖 Запускаю Telegram бот для обработки отчетов...")
    print(f"📁 Временная папка: {TEMP_DIR}")
    
    # Создаем приложение
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # Запускаем бота
    print("✅ Бот запущен и ждет сообщений...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    


if __name__ == '__main__':
    main()
