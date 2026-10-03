# svc-auth (port 8001, stdlib only)

JWT HS256 qo'lda (hmac), SQLite `data/auth.db`.

```bash
python app.py  # :8001, JWT_SECRET=... PORT=... optional
```

- `POST /api/auth/register {email,pass}` → 201 `{token,user}`
- `POST /api/auth/login {email,pass}` → 200 `{token,user}`
- `GET /api/auth/me` (Bearer) → `{id,email,created}`
- Parol: `sha256(salt+pass)`, salt `secrets.token_hex(8)`.
```
