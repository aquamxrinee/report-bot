# ===== ДЛЯ РАБОТЫ С АРТИКУЛАМИ И nmId =====

def get_article_nm_id(article_name: str) -> Optional[int]:
    """
    Получает nmId по названию артикула (из article_stats)
    """
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT nm_id FROM article_stats
        WHERE article = ? LIMIT 1
    ''', (article_name,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def get_all_articles_with_nm_id() -> List[Dict[str, Any]]:
    """
    Возвращает список всех артикулов с их nmId
    """
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT article, nm_id
        FROM article_stats
        WHERE nm_id IS NOT NULL
    ''')
    rows = cursor.fetchall()
    conn.close()
    return [{'article': row[0], 'nm_id': row[1]} for row in rows]
