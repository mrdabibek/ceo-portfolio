# svc-health (port 8012, stdlib only)

Read-only aggregator. Yozish yo'q — DB ga `mode=ro`, downstream ga faqat GET.

```bash
python app.py  # :8012, PORT=... / argv[1] optional
```

- `GET /api/health` → `{ok, services:[{name,port,status,code,ms}]}` — 8001-8011 ni parallel 1s timeout bilan tekshiradi (`/api/health`, fallback `/health`). `status` = `up` (javob oldi, kod farqi yo'q) / `down`. Hammasi up bo'lmasa 503.
- `GET /api/stats` → `{ok, databases:[{svc,file,tables,total}]}` — `backend/*/data/*.db` dagi har jadvaldan `COUNT(*)`. Fayl topilmasa `databases: []`.
- `GET /health`, `GET /` → alias (gateway/smoke mosligi).
