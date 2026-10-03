# svc-shop (port 8004, stdlib only)

SQLite `data/shop.db`: products(id,name,price,stock), orders(id,items,total,created). 8 mahsulot seed.

```bash
python seed.py   # 8 mahsulot
python app.py    # :8004, PORT=... optional
```

- `GET /api/products` → `[{id,name,price,stock}]`
- `POST /api/products {name,price,stock?}` → 201
- `PATCH /api/products/:id/stock {stock} | {delta}` → 200
- `POST /api/orders {items:[{id,qty}]}` → 201 `{id,items,total,created}` (total server hisoblaydi, stock kamayadi)
- `GET /api/orders` → oxirgilari birinchi
```
