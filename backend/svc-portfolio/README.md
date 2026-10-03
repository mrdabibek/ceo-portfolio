# svc-portfolio

Stdlib-only Python catalog service. Port **8003**. SQLite at `data/portfolio.db`, table `projects(slug,title,cat,price,demo)`.

## Run

```bat
cd backend\svc-portfolio
python seed.py
set ADMIN_TOKEN=secret123 & python app.py
rem or:  python app.py   (default admin token "changeme")
```

## Routes

| Method | Route | Auth | Notes |
|---|---|---|---|
| GET | `/health` | no | `{"ok":true}` |
| GET | `/api/projects` | no | list all |
| GET | `/api/projects?cat=ai` | no | filter by category |
| GET | `/api/projects/:slug` | no | one project, 404 if missing |
| POST | `/api/projects` | admin (`X-Admin-Token` or `Authorization: Bearer <ADMIN_TOKEN>`) | body `{"slug","title","cat","price?","demo?"}` upsert, 201 |

## Examples

```bat
curl localhost:8003/api/projects
curl "localhost:8003/api/projects?cat=ai"
curl localhost:8003/api/projects/ai-chatbot
curl -X POST localhost:8003/api/projects -H "Content-Type: application/json" -H "X-Admin-Token: changeme" -d "{\"slug\":\"x\",\"title\":\"X\",\"cat\":\"ai\"}"
```
