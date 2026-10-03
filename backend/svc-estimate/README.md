# svc-estimate (port 8009)

Stdlib-only project estimator. No deps.

Formula: `price = 1500 + scope*700 + design*500 + integrations*450`,
`weeks = ceil(2 + scope*0.5 + design*0.4 + integrations*0.7)`.

## Run

```sh
python app.py            # :8009
python app.py 8009
PORT=8009 python app.py
```

## API

- `POST /api/estimate` `{scope(1-10), design(1-10), integrations(0-8)}` -> `{price, weeks, scope, design, integrations}`. 400 bad JSON, 422 out of range.
- `GET /api/estimate?scope=5&design=7&integrations=3` -> same shape. 422 if missing/out of range.
- `GET /api/health` -> `{ok:true}`

```sh
curl -X POST localhost:8009/api/estimate -H "Content-Type: application/json" -d "{\"scope\":5,\"design\":7,\"integrations\":3}"
curl "localhost:8009/api/estimate?scope=5&design=7&integrations=3"
```

Example: scope=5, design=7, integrations=3 -> price=9850, weeks=10.
