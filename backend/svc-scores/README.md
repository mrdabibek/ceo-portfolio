# svc-scores

Stdlib-only game-score API. SQLite `data/scores.db`, table `scores(id,game,name,value,created)`.
Games: `2048`, `platformer`, `racer`, `reflex`.

## Run

```bash
python app.py            # :8011
python app.py 8011
```

## Routes

| Method | Path | Notes |
|---|---|---|
| POST | `/api/scores` | Body `{game,name,value}` → `201 {id,game,name,value,created}`. 422 if game unknown, name empty/>32 chars, or value not int 1–10000 (anti-spam cap). |
| GET | `/api/scores/:game?limit=10` | Top scores sorted `value DESC`, limit 1–100 default 10. Also accepts `/api/scores?game=:game`. |
| GET | `/api/health` | `{ok:true}` |

CORS `*`, JSON only. No deps.
