def _calculate_all_values(self, df_osn, df_vyk, date_range):
    """Вычисляет все значения"""
    values = {'B1': date_range, 'F1': date_range}
    
    # ===== ОСНОВНОЙ ОТЧЕТ - ЦАП ЦАРАПКИН =====
    # Продажи ЦАП
    filter_carp_sale = ((df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа'] == 'Продажа')
    values['B4'] = df_osn[filter_carp_sale]['К перечислению Продавцу за реализованный Товар'].sum()
    
    # ===== ИСПРАВЛЕНО: Возвраты ЦАП =====
    filter_carp_return = ((df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())) & (df_osn['Тип документа'] == 'Возврат')
    values['B5'] = df_osn[filter_carp_return]['К перечислению Продавцу за реализованный Товар'].sum()
    
    filter_carp_all = (df_osn['Бренд'] == 'Цап царапкин') | (df_osn['Бренд'].isna())
    values['B7'] = df_osn[filter_carp_all]['Услуги по доставке товара покупателю'].sum()
    values['B9'] = df_osn[filter_carp_all]['Операции на приемке'].sum()
    values['B10'] = df_osn['Общая сумма штрафов'].sum()  # Штрафы общие
    values['B11'] = df_osn[filter_carp_all]['Удержания'].sum()
    values['B26'] = df_osn[filter_carp_all]['Хранение'].sum()
    values['B29'] = df_osn[filter_carp_all]['Разовое изменение срока перечисления денежных средств'].sum()
    values['B44'] = df_osn['Цена розничная'].sum()  # Общая цена
    values['B32'] = df_osn[filter_carp_all]['Цена розничная'].sum()  # ЦАП цена
    
    # ===== ОСНОВНОЙ ОТЧЕТ - HARAKIRI =====
    # Продажи Harakiri
    filter_hara_sale = (df_osn['Бренд'] == 'Harakiri') & (df_osn['Тип документа'] == 'Продажа')
    values['F4'] = df_osn[filter_hara_sale]['К перечислению Продавцу за реализованный Товар'].sum()
    
    # ===== ИСПРАВЛЕНО: Возвраты Harakiri =====
    filter_hara_return = (df_osn['Бренд'] == 'Harakiri') & (df_osn['Тип документа'] == 'Возврат')
    values['F5'] = df_osn[filter_hara_return]['К перечислению Продавцу за реализованный Товар'].sum()
    
    filter_hara_all = df_osn['Бренд'] == 'Harakiri'
    values['F7'] = df_osn[filter_hara_all]['Услуги по доставке товара покупателю'].sum()
    values['F9'] = df_osn[filter_hara_all]['Операции на приемке'].sum()
    values['F10'] = df_osn[filter_hara_all]['Общая сумма штрафов'].sum()
    values['F11'] = df_osn[filter_hara_all]['Удержания'].sum()
    
    # ===== ПО ВЫКУПАМ - ЦАП ЦАРАПКИН =====
    # Продажи ЦАП (выкупы)
    filter_carp_vyk_sale = ((df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())) & (df_vyk['Тип документа'] == 'Продажа')
    values['M4'] = df_vyk[filter_carp_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
    
    # ===== ИСПРАВЛЕНО: Возвраты ЦАП (выкупы) =====
    filter_carp_vyk_return = ((df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())) & (df_vyk['Тип документа'] == 'Возврат')
    values['M5'] = df_vyk[filter_carp_vyk_return]['К перечислению Продавцу за реализованный Товар'].sum()
    
    filter_carp_vyk_all = (df_vyk['Бренд'] == 'Цап царапкин') | (df_vyk['Бренд'].isna())
    values['M7'] = df_vyk[filter_carp_vyk_all]['Услуги по доставке товара покупателю'].sum()
    values['M8'] = df_vyk[filter_carp_vyk_all]['Операции на приемке'].sum()
    values['M9'] = df_vyk['Общая сумма штрафов'].sum()  # Штрафы общие
    values['B47'] = df_vyk['Цена розничная'].sum()  # Общая цена
    
    # ===== ПО ВЫКУПАМ - HARAKIRI =====
    # Продажи Harakiri (выкупы)
    filter_hara_vyk_sale = (df_vyk['Бренд'] == 'Harakiri') & (df_vyk['Тип документа'] == 'Продажа')
    values['Q4'] = df_vyk[filter_hara_vyk_sale]['К перечислению Продавцу за реализованный Товар'].sum()
    
    # ===== ИСПРАВЛЕНО: Возвраты Harakiri (выкупы) =====
    filter_hara_vyk_return = (df_vyk['Бренд'] == 'Harakiri') & (df_vyk['Тип документа'] == 'Возврат')
    values['Q5'] = df_vyk[filter_hara_vyk_return]['К перечислению Продавцу за реализованный Товар'].sum()
    
    filter_hara_vyk_all = df_vyk['Бренд'] == 'Harakiri'
    values['Q7'] = df_vyk[filter_hara_vyk_all]['Услуги по доставке товара покупателю'].sum()
    values['Q8'] = df_vyk[filter_hara_vyk_all]['Операции на приемке'].sum()
    values['Q9'] = df_vyk[filter_hara_vyk_all]['Общая сумма штрафов'].sum()
    values['B41'] = df_vyk[filter_hara_vyk_all]['Цена розничная'].sum()  # Harakiri цена
    
    return values
