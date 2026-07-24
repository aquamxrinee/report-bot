async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['history_page'] = 0
    await show_history_page(update, context, page=0)

async def show_history_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    """Показывает страницу истории с общей информацией и кнопками."""
    reports, total = get_all_reports(page=page, per_page=10)
    if not reports:
        await update.message.reply_text("📭 История пуста.")
        return

    total_pages = (total + 9) // 10 if total > 0 else 1
    current_page = page

    # Получаем общий диапазон дат
    min_date, max_date = get_report_date_range()
    date_range_str = f"{min_date} — {max_date}" if min_date and max_date else "данные отсутствуют"

    msg = f"📊 **Всего отчетов: {total}**\n"
    if min_date and max_date:
        msg += f"📅 Данные доступны с **{min_date}** по **{max_date}**\n"
    msg += f"\n*Страница {current_page+1} из {total_pages}*\n"

    # Кнопки для отчётов на текущей странице
    keyboard = []
    for r in reports:
        report_id, file_name, date_period, start_date, end_date, processed_at = r
        # Укорачиваем имя файла, если длинное
        short_name = file_name if len(file_name) <= 25 else file_name[:22] + "..."
        button_text = f"📄 {short_name} ({date_period})"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"history_report_{report_id}")])

    # Навигация
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"history_page_{current_page-1}"))
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"history_page_{current_page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')

async def show_history_page_from_query(query, context, page):
    """Аналогично, но для callback query."""
    reports, total = get_all_reports(page=page, per_page=10)
    if not reports:
        await query.edit_message_text("📭 История пуста.")
        return

    total_pages = (total + 9) // 10 if total > 0 else 1
    current_page = page

    min_date, max_date = get_report_date_range()
    date_range_str = f"{min_date} — {max_date}" if min_date and max_date else "данные отсутствуют"

    msg = f"📊 **Всего отчетов: {total}**\n"
    if min_date and max_date:
        msg += f"📅 Данные доступны с **{min_date}** по **{max_date}**\n"
    msg += f"\n*Страница {current_page+1} из {total_pages}*\n"

    keyboard = []
    for r in reports:
        report_id, file_name, date_period, start_date, end_date, processed_at = r
        short_name = file_name if len(file_name) <= 25 else file_name[:22] + "..."
        button_text = f"📄 {short_name} ({date_period})"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"history_report_{report_id}")])

    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"history_page_{current_page-1}"))
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"history_page_{current_page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
