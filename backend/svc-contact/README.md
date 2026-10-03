# svc-contact

Stdlib-only lead API. SQLite `data/contact.db`, table `leads(id,name,email,budget,msg,created)`.

## Run

```bash
python app.py            # :8002
python app.py 8002
```

## Routes

| Method | Path | Notes |
|---|---|---|
| POST | `/api/contact` | Body `{name,email,msg,budget?}` → `201 {id}`. 422 if name/email/msg invalid. |
| GET | `/api/contact?limit=50` | Admin list, newest first, limit 1–200 default 50. |
| GET | `/api/health` | `{ok:true}` |

CORS `*`, JSON only. No deps.
