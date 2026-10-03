# svc-news

Stdlib-only news API. Port **8010**. SQLite at `data/news.db`, table `posts(id,title,body,created)`.

## Run

```bash
python seed.py
python app.py        # or: PORT=8010 python app.py
```

## Routes

| Method | Path       | Body               | Resp         |
|--------|------------|--------------------|--------------|
| GET    | `/api/news`| —                  | 200 `[{id,title,body,created}]` newest first |
| POST   | `/api/news`| `{title,body}`     | 201 created post |
| GET    | `/health`  | —                  | 200 `{ok:true}` |

Validation: `title` and `body` required, else 400. CORS `*`.
