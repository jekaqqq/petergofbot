# database.py
import sqlite3

DB_NAME = "products.db"

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    # включаем проверку внешних ключей
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            option_type TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            option TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER DEFAULT 0,
            image_id TEXT,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        )
    """)

    # индексы для ускорения выборок
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_variants_product ON variants(product_id)")

    # Категории: теперь используем "strength" вместо "flavor"
    categories = [
        ("Подики", "color"),
        ("Жижа", "strength"),
        ("Одноразки", "strength"),
        ("Снюс", "strength"),
        ("Ватки", "strength")
    ]
    cursor.executemany("INSERT OR IGNORE INTO categories (name, option_type) VALUES (?, ?)", categories)

    # Тестовые данные (добавляются только если ещё нет)
    cursor.execute("SELECT id FROM categories WHERE name = ?", ("Подики",))
    row = cursor.fetchone()
    if row:
        pod_id = row[0]
        cursor.execute("INSERT OR IGNORE INTO products (brand, category_id) VALUES (?, ?)", ("Xiaomi", pod_id))
        # получим id продукта (если он был создан раньше, lastrowid может быть 0)
        cursor.execute("SELECT id FROM products WHERE brand=? AND category_id=?", ("Xiaomi", pod_id))
        prod = cursor.fetchone()
        if prod:
            xiaomi_id = prod[0]
            cursor.executemany("""
                INSERT OR IGNORE INTO variants (product_id, option, price, stock, image_id)
                VALUES (?, ?, ?, ?, ?)
            """, [
                (xiaomi_id, "Чёрный", 2500, 5, None),
                (xiaomi_id, "Белый", 2400, 3, None),
            ])

    cursor.execute("SELECT id FROM categories WHERE name = ?", ("Одноразки",))
    row = cursor.fetchone()
    if row:
        disp_id = row[0]
        cursor.execute("INSERT OR IGNORE INTO products (brand, category_id) VALUES (?, ?)", ("Elf Bar", disp_id))
        cursor.execute("SELECT id FROM products WHERE brand=? AND category_id=?", ("Elf Bar", disp_id))
        prod = cursor.fetchone()
        if prod:
            elf_id = prod[0]
            cursor.execute("""
                INSERT OR IGNORE INTO variants (product_id, option, price, stock, image_id)
                VALUES (?, ?, ?, ?, ?)
            """, (elf_id, "12 mg", 600, 12, None))

    conn.commit()
    conn.close()
