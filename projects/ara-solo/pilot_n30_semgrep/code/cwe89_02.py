import sqlite3

def find_products_by_category(conn, category):
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, category, price FROM products WHERE category = ?", (category,))
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
