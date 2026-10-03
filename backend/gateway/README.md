# Gateway (`backend/gateway`)

Stdlib reverse-proxy on **:8000** → services **:8001–8012**. No dependencies.

## Routes

| Prefix | Upstream |
|---|---|
| `/api/auth` | :8001 |
| `/api/contact` | :8002 |
| `/api/projects` | :8003 |
| `/api/products`, `/api/orders` | :8004 |
| `/api/chat` | :8005 |
| `/api/checkout`, `/api/invoices` | :8006 |
| `/api/upload`, `/api/files` | :8007 |
| `/api/ai` | :8008 |
| `/api/estimate` | :8009 |
| `/api/news` | :8010 |
| `/api/scores` | :8011 |
| `/api/health`, `/api/stats` | :8012 |

`GET /api` → `{"gateway":"ok","routes":[...]}`. Unknown prefix → 404. Upstream down → 502.

## Run

```bat
python backend\gateway\app.py
```

CORS `*`, 5s upstream timeout, query strings and bodies forwarded.
