# Runbook

How to run, test and operate ScrapBid. You only need **Docker** (with Compose) and **git**.

## Local setup

```bash
git clone https://github.com/vamshi-987/ScrapBid.git
cd ScrapBid
cp .env.example .env
docker compose up --build -d
docker compose exec backend python manage.py migrate
```

The first start takes a minute or two while images build and frontend dependencies install. Then:

| What | URL |
|---|---|
| Web app | http://localhost |
| API health check | http://localhost/api/health |
| Django admin | http://localhost/admin |
| MinIO console | http://localhost:9001 (log in with `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` from `.env`) |

Check that it's working:

```bash
curl http://localhost/api/health
```

Expected output: `{"status":"ok","checks":{"database":"ok","storage":"ok"}}`. The home page also shows this status.

Editing files under `frontend/src` or `backend/` reloads automatically. Frontend dependencies live in a Docker volume, not in `frontend/node_modules` on your machine. They reinstall automatically when `package-lock.json` changes.

## Tests and lint

Tests run against the PostGIS database in Docker, never SQLite.

```bash
docker compose exec backend pytest
docker compose exec backend ruff check .
docker compose exec backend ruff format --check .
```

To fix backend formatting: `docker compose exec backend ruff format .`

Frontend:

```bash
docker compose exec frontend npm run lint      # eslint + prettier check
docker compose exec frontend npm test          # Vitest
docker compose exec frontend npx tsc --noEmit  # type check
docker compose exec frontend npm run build     # production build
```

To fix frontend formatting: `docker compose exec frontend npm run format`

To add a frontend package, run npm inside the container so `package-lock.json` stays correct: `docker compose exec frontend npm install <package>@<exact-version>`.

## Migrations

```bash
docker compose exec backend python manage.py makemigrations
docker compose exec backend python manage.py migrate
```

A test fails if a model change has no migration. Never edit a migration that has already been applied. Add a new one instead.

## Logs

```bash
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs caddy
docker compose logs minio minio-init
```

## Health check

`GET /api/health` checks the database and the storage bucket.

| Result | Meaning |
|---|---|
| `200` with `"status": "ok"` | Both checks pass |
| `503` with `"status": "error"` | At least one failed. `checks` shows which one, and the backend log has the details. |

The response never includes error details.

## Environment variables

All variables are in `.env.example` with safe local defaults. Never commit `.env`.

| Variable | Local default | Notes |
|---|---|---|
| `DJANGO_ENV` | `local` | `local` or `production` |
| `DJANGO_DEBUG` | `True` | Must be `False` in production |
| `DJANGO_SECRET_KEY` | empty (built-in dev key) | Production needs a long random value |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | `scrapbid` | Used by both the `db` container and the backend |
| `POSTGRES_HOST`, `POSTGRES_PORT` | `db`, `5432` | |
| `S3_ENDPOINT_URL` | `http://minio:9000` | Leave empty in production to use AWS S3 |
| `S3_BUCKET` | `scrapbid-local` | Private bucket, created automatically locally |
| `S3_REGION` | `us-east-1` | |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | match the MinIO login | |
| `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | `scrapbid`, `scrapbid-secret` | Local only |
| `MINIO_CONSOLE_PORT` | `9001` | Host port for the MinIO console |
| `OTP_PROVIDER` | `console` | `console` prints codes to the logs. Not allowed in production. |
| `DEV_FIXED_OTP` | `123456` | Local only. Must be empty in production. |

### Production safety check

With `DJANGO_ENV=production`, the backend refuses to start if any of these is true:
- `DJANGO_DEBUG` is on.
- `DJANGO_SECRET_KEY` is empty or the dev default.
- `OTP_PROVIDER` is `console`.
- `DEV_FIXED_OTP` is set.

The error lists every problem it found.

## Troubleshooting

**`bind: address already in use` on start-up.** Another program is using a host port. To find out which: `lsof -nP -iTCP:80 -sTCP:LISTEN` (or `:9001`).
- **Port 80:** stop the other program. The app is meant to run on `http://localhost`.
- **Port 9001:** set `MINIO_CONSOLE_PORT` in `.env` to a free port, such as `9011`, then run `docker compose up -d` again.

**The web app shows "Can't reach the server" or Caddy returns 502.** The backend or frontend is still starting. Wait a few seconds and reload. If it persists, check `docker compose ps` and `docker compose logs backend frontend`.

**Frontend errors about missing packages.** Reinstall the dependencies with `docker compose run --rm frontend npm ci`, or remove the volume with `docker compose down` and `docker volume rm scrapbid_frontend-node-modules`, then start again.

**Health check shows `"storage": "error"`.**
1. Check that `minio` is running and that `minio-init` exited with code 0: `docker compose ps -a`.
2. Check that `minio-init` logged `Bucket created successfully` or that the bucket already exists: `docker compose logs minio-init`.
3. If `S3_BUCKET` or the MinIO login in `.env` has changed, run `docker compose up -d` again.

**Health check shows `"database": "error"`.** Check `docker compose ps` for `db` being `healthy`, and `docker compose logs db`.

**Start from a clean slate.** This deletes all local data:

```bash
docker compose down -v
docker compose up --build -d
docker compose exec backend python manage.py migrate
```
