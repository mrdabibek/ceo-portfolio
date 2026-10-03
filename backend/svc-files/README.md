# svc-files

Stdlib-only upload microservice. Port `8007`.

## Run

```bash
python app.py          # or: PORT=8007 python app.py
```

Files stored in `uploads/` (auto-created, gitignored).

## Routes

| Method | Path           | Description                              |
| ------ | -------------- | ---------------------------------------- |
| POST   | `/api/upload`  | multipart field `file` → `201 {url}`     |
| GET    | `/api/files`   | `200 {files: [{name, url, size}]}`       |
| GET    | `/files/:name` | static file                              |
| GET    | `/api/health`  | `{"status":"ok"}`                        |

## Limits

- Max **5MB** per file (`413` if exceeded).
- Only `png / jpg / jpeg / webp` (`415` otherwise).
- Stored names are `uuid + ext`; path traversal blocked (`..`, `/`, `\` rejected, normpath contained in `uploads/`).

## Example

```bash
curl -F "file=@photo.png" http://localhost:8007/api/upload
curl http://localhost:8007/api/files
```
