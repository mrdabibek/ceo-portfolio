# svc-ai (port 8008)

Stdlib-only keyword brain. No external API.

## Run

```sh
python app.py  # :8008
```

## API

- `POST /api/ai` `{q}` → `{a}` — keywords: price/stack/timeline/hi, else default. UZ+EN.
- `GET /api/ai/prompts` → `{prompts: [...]}`

```sh
curl -X POST localhost:8008/api/ai -H "Content-Type: application/json" -d '{"q":"narx qancha?"}'
curl localhost:8008/api/ai/prompts
```
