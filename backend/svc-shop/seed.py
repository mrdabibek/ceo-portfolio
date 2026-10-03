import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data", "shop.db")

PRODUCTS = [
    ("Non (tandir)", 3000, 200),
    ("Sut 1L", 12000, 100),
    ("Tuxum 10 dona", 15000, 80),
    ("Guruch 1kg", 25000, 150),
    ("Un 1kg", 15000, 120),
    ("Shakar 1kg", 18000, 200),
    ("Kungaboqar yog'i 1L", 35000, 90),
    ("Qora choy 200g", 30000, 110),
]


def main():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL, stock INTEGER NOT NULL DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, items TEXT NOT NULL, total REAL NOT NULL, created TEXT NOT NULL)")
    n = c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if n == 0:
        c.executemany("INSERT INTO products(name,price,stock) VALUES(?,?,?)", PRODUCTS)
        c.commit()
        print(f"seeded {len(PRODUCTS)} products")
    else:
        print(f"skip: {n} products exist")
    c.close()


if __name__ == "__main__":
    main()
