# svc-chat

Stdlib-only polling chat API + keyword bot. No WebSocket, no deps.

## Run

```bash
python app.py            # :8005
# or
python -m backend.svc-chat.app
```

DB: `data/chat.db` → `messages(id, channel, user, text, created)`.

## API

- `GET /api/health` → `{ok, svc, time}`
- `GET /api/channels` → `{channels: [...]}`
- `GET /api/chat/:channel?limit=50&after=0` → `{channel, messages[]}`
  - `limit` 1..200, default 50; `after` = last seen id (polling)
  - messages ascending by id
- `POST /api/chat/:channel` `{user, text}` → `{message, bot|null}` (201)
  - `user` ≤32 chars (default `anon`), `text` 1..2000 chars
  - channel: `[A-Za-z0-9_-]{1,64}`

## Bot

Auto-reply inserted as `user:"bot"` in same channel when text contains:

| kalit so'z | javob |
|---|---|
| narx/price/cost/pul/summa | smeta tariflari |
| stack/texnologiya/fastapi/react/python/flutter | stack tavsifi |
| muddat/timeline/deadline/vaqt/qachon/necha kun | muddatlar |
| salom/hello/assalom | salomlashuv |

## Polling (WS yo'q)

```js
let after = 0;
setInterval(async () => {
  const r = await fetch(`/api/chat/general?after=${after}`);
  const {messages} = await r.json();
  for (const m of messages) { render(m); after = Math.max(after, m.id); }
}, 2000);
```

## cURL

```bash
curl -X POST localhost:8005/api/chat/general -H "Content-Type: application/json" -d "{\"user\":\"ali\",\"text\":\"narx qancha?\"}"
curl "localhost:8005/api/chat/general?limit=50"
```
