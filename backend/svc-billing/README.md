# svc-billing

Stdlib-only invoice API. SQLite `data/billing.db`, table `invoices(id,plan,amount,email,status)`.

Plans (USD): `sprint=1500`, `product=9800`, `partner=3000`.

## Run

```bash
python app.py            # :8006
python app.py 8006
```

## Routes

| Method | Path | Notes |
|---|---|---|
| POST | `/api/checkout` | Body `{plan,email}` → `201 {invoice,pay_url}`. 422 if plan/email invalid. `pay_url` mock (`https://pay.mock/inv_<id>`). |
| GET | `/api/invoices/:id` | → `200 {invoice}`. 404 if missing. |
| GET | `/api/health` | `{ok:true}` |

CORS `*`, JSON only. No deps.

Stripe-ready: real rejimda `STRIPE_SECRET_KEY` bilan `stripe.checkout.Session.create(...)` chaqirilib `pay_url=session.url` bo'ladi, webhook `status='paid'` qiladi — tafsilot `app.py` izohida.
