async def articles_full_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    report_id = context.user_data.get('current_report_id')
    if not report_id:
        await update.message.reply_text("❌ Нет данных. Сначала загрузите отчет.")
        return

    current_articles = get_article_stats_for_report(report_id)
    if not current_articles:
        await update.message.reply_text("❌ Нет данных по артикулам.")
        return

    # Находим предыдущий отчёт
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT start_date FROM reports WHERE id = ?", (report_id,))
    row = cursor.fetchone()
    prev_start_date = row[0] if row else None
    conn.close()

    previous_articles = {}
    if prev_start_date:
        prev_reports = get_previous_reports(prev_start_date, limit=1)
        if prev_reports:
            prev_id = prev_reports[0][0]
            previous_articles = get_article_stats_for_report(prev_id)

    # Формируем список
    all_items = []
    for art, data in current_articles.items():
        cur_q = data['quantity']
        cur_r = data['revenue']
        prev_q = previous_articles.get(art, {}).get('quantity', 0)
        prev_r = previous_articles.get(art, {}).get('revenue', 0)
        change_q = cur_q - prev_q
        change_r_percent = ((cur_r - prev_r) / prev_r * 100) if prev_r else 0 if cur_r == 0 else float('inf')
        all_items.append((art, cur_q, cur_r, change_q, change_r_percent, prev_q, prev_r))

    all_items.sort(key=lambda x: x[2], reverse=True)
    period = context.user_data.get('current_period', '')

    msg = f"📦 **Все артикулы** ({period})\n\n"
    for art, cur_q, cur_r, change_q, change_r_percent, prev_q, prev_r in all_items:
        if prev_q == 0 and cur_q == 0:
            delta_str = "нет данных"
        elif prev_q == 0:
            delta_str = f"🆕 +{cur_q} шт."
        else:
            arrow = "📈" if change_q > 0 else "📉" if change_q < 0 else "➖"
            delta_str = f"{arrow} {change_q:+.0f} шт. ({change_r_percent:+.1f}%)"
        msg += f"**{art}**\n   Продажи: {cur_q} шт. | {cur_r:,.2f} ₽\n   Изм.: {delta_str}\n\n"
        if len(msg) > 4000:
            msg += "\n… (сообщение обрезано)"
            break

    keyboard = [[InlineKeyboardButton("📊 Детальное сравнение", callback_data="compare_articles")]]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
