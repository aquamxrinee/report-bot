# === АНАЛИТИКА ПО АРТИКУЛАМ С ВЫБОРОМ ОТЧЁТОВ ===
async def menu_analytics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # Инициализируем состояние выбора
    context.user_data['analytics_selected'] = []
    context.user_data['analytics_page'] = 0
    await show_analytics_selection(query, context, page=0)

async def show_analytics_selection(query, context, page):
    """Отображает страницу выбора отчётов для аналитики."""
    reports, total = get_all_reports(page=page, per_page=10)
    if not reports:
        await query.edit_message_text("📭 Нет отчётов для анализа.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))
        return

    selected = context.user_data.get('analytics_selected', [])
    total_pages = (total + 9) // 10 if total > 0 else 1
    current_page = page

    msg = f"📊 **Выберите отчёты для анализа**\n"
    msg += f"Выбрано: {len(selected)} из {total}\n"
    msg += f"\n*Страница {current_page+1} из {total_pages}*\n\n"

    keyboard = []
    for r in reports:
        report_id, file_name, date_period, start_date, end_date, processed_at = r
        checked = "✅" if report_id in selected else "⬜"
        button_text = f"{checked} {file_name} ({date_period})"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"analytics_toggle_{report_id}")])

    # Кнопки быстрого выбора
    quick_buttons = [
        InlineKeyboardButton("✅ Выбрать все", callback_data="analytics_select_all"),
        InlineKeyboardButton("📅 Неделя (1)", callback_data="analytics_quick_1"),
        InlineKeyboardButton("📅 2 недели", callback_data="analytics_quick_2"),
        InlineKeyboardButton("📅 4 недели", callback_data="analytics_quick_4"),
        InlineKeyboardButton("📅 12 недель", callback_data="analytics_quick_12"),
    ]
    # Разбиваем по 2 в ряд
    quick_rows = [quick_buttons[i:i+2] for i in range(0, len(quick_buttons), 2)]
    keyboard.extend(quick_rows)

    # Навигация по страницам
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"analytics_page_{current_page-1}"))
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"analytics_page_{current_page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    # Кнопка "Показать аналитику" и "Назад в меню"
    keyboard.append([InlineKeyboardButton("📊 Показать аналитику", callback_data="analytics_show")])
    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")])

    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def analytics_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("analytics_toggle_"):
        report_id = int(data.split("_")[2])
        selected = context.user_data.get('analytics_selected', [])
        if report_id in selected:
            selected.remove(report_id)
        else:
            selected.append(report_id)
        context.user_data['analytics_selected'] = selected
        # Обновляем текущую страницу
        page = context.user_data.get('analytics_page', 0)
        await show_analytics_selection(query, context, page)

async def analytics_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("analytics_page_"):
        page = int(data.split("_")[2])
        context.user_data['analytics_page'] = page
        await show_analytics_selection(query, context, page)

async def analytics_select_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # Получаем все ID отчётов
    reports, total = get_all_reports(page=0, per_page=total)  # загружаем все
    all_ids = [r[0] for r in reports]
    context.user_data['analytics_selected'] = all_ids
    page = context.user_data.get('analytics_page', 0)
    await show_analytics_selection(query, context, page)

async def analytics_quick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    # Определяем количество недель
    if data == "analytics_quick_1":
        limit = 1
    elif data == "analytics_quick_2":
        limit = 2
    elif data == "analytics_quick_4":
        limit = 4
    elif data == "analytics_quick_12":
        limit = 12
    else:
        return
    # Получаем последние N отчётов
    reports, total = get_all_reports(page=0, per_page=limit)
    selected = [r[0] for r in reports]
    context.user_data['analytics_selected'] = selected
    page = context.user_data.get('analytics_page', 0)
    await show_analytics_selection(query, context, page)

async def analytics_show_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected = context.user_data.get('analytics_selected', [])
    if not selected:
        await query.edit_message_text("⚠️ Вы не выбрали ни одного отчёта.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад к выбору", callback_data="menu_analytics")],
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))
        return

    # Получаем выбранные отчёты (упорядочиваем по дате)
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    placeholders = ','.join('?' * len(selected))
    cursor.execute(f'''
        SELECT id, start_date, end_date, date_period
        FROM reports
        WHERE id IN ({placeholders})
        ORDER BY start_date ASC
    ''', selected)
    reports_data = cursor.fetchall()
    conn.close()

    if len(reports_data) < 1:
        await query.edit_message_text("❌ Не удалось загрузить выбранные отчёты.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))
        return

    # Агрегируем артикулы за выбранный период
    articles_agg = {}
    total_orders = 0
    total_revenue = 0
    for rid, start, end, period in reports_data:
        articles = get_article_stats_for_report(rid)
        for art, data in articles.items():
            if art not in articles_agg:
                articles_agg[art] = {'quantity': 0, 'revenue': 0}
            articles_agg[art]['quantity'] += data['quantity']
            articles_agg[art]['revenue'] += data['revenue']
            total_orders += data['quantity']
            total_revenue += data['revenue']

    if not articles_agg:
        await query.edit_message_text("❌ В выбранных отчётах нет данных по артикулам.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
        ]))
        return

    # Определяем предыдущий период (такой же длины, непосредственно перед первым выбранным отчётом)
    first_report_start = reports_data[0][1]  # start_date первого (самого раннего)
    # Находим все отчёты до first_report_start, упорядоченные по убыванию, берём столько же, сколько выбрано
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id
        FROM reports
        WHERE start_date < ?
        ORDER BY start_date DESC
        LIMIT ?
    ''', (first_report_start, len(reports_data)))
    prev_ids = [row[0] for row in cursor.fetchall()]
    conn.close()

    prev_articles_agg = {}
    if prev_ids:
        for pid in prev_ids:
            articles = get_article_stats_for_report(pid)
            for art, data in articles.items():
                if art not in prev_articles_agg:
                    prev_articles_agg[art] = {'quantity': 0, 'revenue': 0}
                prev_articles_agg[art]['quantity'] += data['quantity']
                prev_articles_agg[art]['revenue'] += data['revenue']

    # Формируем сообщение
    period_str = f"{reports_data[0][3]} — {reports_data[-1][3]}" if len(reports_data) > 1 else reports_data[0][3]
    msg = f"📊 **Аналитика по артикулам**\n"
    msg += f"📅 Период: {period_str}\n"
    msg += f"📦 Всего заказов: {total_orders}\n"
    msg += f"💰 Общая выручка: {total_revenue:,.2f} ₽\n\n"

    # Сортируем артикулы по выручке (убывание)
    sorted_articles = sorted(articles_agg.items(), key=lambda x: x[1]['revenue'], reverse=True)
    # Ограничим вывод 15 артикулами (можно увеличить)
    top_articles = sorted_articles[:15]

    msg += "**Топ-15 артикулов по выручке:**\n"
    for art, data in top_articles:
        qty = data['quantity']
        rev = data['revenue']
        # Изменение относительно прошлого периода
        if art in prev_articles_agg:
            prev_q = prev_articles_agg[art]['quantity']
            prev_rev = prev_articles_agg[art]['revenue']
            if prev_q > 0:
                change_q = ((qty - prev_q) / prev_q) * 100
            else:
                change_q = 0 if qty == 0 else float('inf')
            if prev_rev > 0:
                change_rev = ((rev - prev_rev) / prev_rev) * 100
            else:
                change_rev = 0 if rev == 0 else float('inf')
            change_str = f" (Δ {change_q:+.1f}% / {change_rev:+.1f}%)"
        else:
            change_str = " (новинка)"
        msg += f"• **{art}**: {qty} шт. | {rev:,.2f} ₽{change_str}\n"

    if len(sorted_articles) > 15:
        msg += f"\n… и еще {len(sorted_articles)-15} артикулов."

    keyboard = [
        [InlineKeyboardButton("◀️ Назад к выбору отчётов", callback_data="menu_analytics")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
