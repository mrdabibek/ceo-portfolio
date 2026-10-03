# Backend Infra

13 servis, har biri `python:3.12-slim` + `python -m http.server`. Servis kodi yo'q — faqat infra skelet. Real handlerlar keyin yoziladi.

## Arxitektura

```
client -> gateway:8000 -> auth:8001, users:8002, portfolio:8003, orders:8004,
  payments:8005, notifications:8006, search:8007, files:8008, analytics:8009,
  chat:8010, admin:8011, webhook:8012 (telegram)
```

Shared volume: `./data:/data`. Env: `.env` (`.env.example` dan nusxala, optional).

## Port jadvali

| Servis | Port |
|---|---|
| gateway | 8000 |
| auth | 8001 |
| users | 8002 |
| portfolio | 8003 |
| orders | 8004 |
| payments | 8005 |
| notifications | 8006 |
| search | 8007 |
| files | 8008 |
| analytics | 8009 |
| chat | 8010 |
| admin | 8011 |
| webhook | 8012 |

## Ishga tushirish

```bash
cp .env.example .env
# Windows:
start.bat
# Linux:
sh start.sh
# yoki:
docker compose up -d && python smoke.py
```

`smoke.py`: urllib bilan 13 endpointni tekshiradi, hammasi 200 bo'lsa exit 0.

## Deploy

1. `.env` ni to'ldir (`SECRET_KEY`, `ADMIN_EMAIL`, `BOT_TOKEN`).
2. `docker compose up -d`.
3. `python smoke.py` — 13/13 up.
4. To'xtatish: `docker compose down`.

XAVFSIZLIK: `bot_config.json` dagi real tokenni hech qayerga ko'chirma.
