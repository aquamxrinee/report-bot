def _calculate_all_values(self, df_osn, df_vyk, date_range):
    """Вычисляет все значения с правильными фильтрами"""
    values = {'B1': date_range, 'F1': date_range}

    # ============================================================
    # ОСНОВНОЙ ОТЧЕТ (df_osn) — ЦАП ЦАРАПКИН
    # ============================================================
    
    # Продажи ЦАП (B4)
    filter_carp_sale = ((df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа'] == 'Продажа')
    values['B4'] = df_osn[filter_carp_sale]['К перечислению Продавцу за реализованный Товар'].sum()
    
    # Возвраты ЦАП (B5)
    filter_carp_return = ((df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа'] == 'Возврат')
    values['B5'] = df_osn[filter_carp_return]['К перечислению Продавцу за реализованный Товар'].sum()
    
    # Прочие показатели ЦАП
    filter_carp_all = (df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())
    values['B7'] = df_osn[filter_carp_all]['Услуги по доставке товара покупателю'].sum()
    values['B9'] = df_osn[filter_carp_all]['Операции на приемке'].sum()
    values['B10'] = df_osn['Общая сумма штрафов'].sum()
    values['B11'] = df_osn[filter_carp_all]['Удержания'].sum()
    values['B26'] = df_osn[filter_carp_all]['Хранение'].sum()
    values['B29'] = df_osn[filter_carp_all]['Разовое изменение срока перечисления денежных средств'].sum()
    values['B44'] = df_osn['Цена розничная'].sum()
    values['B32'] = df_osn[filter_carp_all]['Цена розничная'].sum()

    # ============================================================
    # ОСНОВНОЙ ОТЧЕТ (df_osn) — HARAKIRI
    # ============================================================
    
    # Продажи Harakiri (F4)
    filter_hara_sale = (df_osn['Бренд'] == 'Harakiri') & (df_osn['Тип документа'] == 'Продажа')
    values['F4'] = df_osn[filter_hara_sale]['К перечислению Продавцу за реализованный Товар'].sum()
    
    # Возвраты Harakiri (F5)
    filter_hara_return = (df_osn['Бренд'] == 'Harakiri') & (df_osn['Тип документа'] == 'Возврат')
    values['F5'] = df_osn[filter_hara_return]['К перечислению Продавцу за реализованный Товар'].sum()
    
    # Прочие показатели Harakiri
    filter_hara_all = df_osn['Бренд'] == 'Harakiri'
    values['F7'] = df_osn[filter_hara_all]['Услуги по доставке товара покупателю'].sum()
    values['F9'] = df_osn[filter_hara_all]['Операции на приемке'].sum()
    values['F10'] = df_osn[filter_hara_all]['Общая сумма штрафов'].sum()
    values['F11'] = df_osn[filter_hara_all]['Удержания'].sum()

    # ============================================================
    # ОТЧЕТ ПО ВЫКУПАМ (df_vyk) — ЦАП ЦАРАПКИН
    # ============================================================
    
    # M4: Продажи ЦАП (выкупы) — фильтр по бренду и типу "Продажа"
    filter_carp_vyk_sale = (df_vyk['Бренд'] == 'Цап царапкин') & (df_vyk['Тип документа'] == 'Продажа')
    values['M4'] = df_vyk[filter_carp_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
    
    # M5: Возвраты ЦАП (выкупы)
    filter_carp_vyk_return = (df_vyk['Бренд'] == 'Цап царапкин') & (df_vyk['Тип документа'] == 'Возврат')
    values['M5'] = df_vyk[filter_carp_vyk_return]['К перечислению Продавцу за реализованный Товар'].sum()
    
    # M7: Доставка ЦАП (выкупы) — фильтр только по бренду (все типы документов)
    filter_carp_vyk_all = (df_vyk['Бренд'] == 'Цап царапкин')
    values['M7'] = df_vyk[filter_carp_vyk_all]['Услуги по доставке товара покупателю'].sum()
    
    # M8: Приемка ЦАП (выкупы)
    values['M8'] = df_vyk[filter_carp_vyk_all]['Операции на приемке'].sum()
    
    # M9: Штрафы общие (по выкупам) — без фильтра по бренду
    values['M9'] = df_vyk['Общая сумма штрафов'].sum()
    
    # B47: Цена розничная ЦАП (выкупы)
    values['B47'] = df_vyk[filter_carp_vyk_all]['Цена розничная'].sum()

    # ============================================================
    # ОТЧЕТ ПО ВЫКУПАМ (df_vyk) — HARAKIRI
    # ============================================================
    
    # Q4: Продажи Harakiri (выкупы) — фильтр по бренду и типу "Продажа"
    filter_hara_vyk_sale = (df_vyk['Бренд'] == 'Harakiri') & (df_vyk['Тип документа'] == 'Продажа')
    values['Q4'] = df_vyk[filter_hara_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
    
    # Q5: Возвраты Harakiri (выкупы)
    filter_hara_vyk_return = (df_vyk['Бренд'] == 'Harakiri') & (df_vyk['Тип документа'] == 'Возврат')
    values['Q5'] = df_vyk[filter_hara_vyk_return]['К перечислению Продавцу за реализованный Товар'].sum()
    
    # Q7: Доставка Harakiri (выкупы) — фильтр только по бренду (все типы документов)
    filter_hara_vyk_all = (df_vyk['Бренд'] == 'Harakiri')
    values['Q7'] = df_vyk[filter_hara_vyk_all]['Услуги по доставке товара покупателю'].sum()
    
    # Q8: Приемка Harakiri (выкупы)
    values['Q8'] = df_vyk[filter_hara_vyk_all]['Операции на приемке'].sum()
    
    # Q9: Штрафы Harakiri (выкупы)
    values['Q9'] = df_vyk[filter_hara_vyk_all]['Общая сумма штрафов'].sum()
    
    # B41: Цена розничная Harakiri (выкупы)
    values['B41'] = df_vyk[filter_hara_vyk_all]['Цена розничная'].sum()

    return values
