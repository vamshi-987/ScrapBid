# Runbook

How to run, test and operate ScrapBid. You only need **Docker** (with Compose) and **git**.

## Local setup

```bash
git clone https://github.com/vamshi-987/ScrapBid.git
cd ScrapBid
cp .env.example .env
docker compose up --build -d
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py seed_demo
```

`seed_demo` creates the demo accounts listed in the README. It's safe to run again: it restores the demo accounts without duplicating them. It refuses to run in production.

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

### Logging in locally

1. Open http://localhost and choose **Log in**.
2. Enter a demo number from the README, for example `9000000002`, or any valid Indian mobile number.
3. Enter the code `123456` (the `DEV_FIXED_OTP` value). With `DEV_FIXED_OTP` empty, codes are random. Find them in the log:

   ```bash
   docker compose logs backend | grep "Login code"
   ```

   The log shows the number masked, for example `Login code for +91XXXXXX3210: 123456`.

### Selling scrap

1. Log in as a seller, for example `9000000002`. The home page shows **My listings**.
2. Choose **Sell scrap**. Tick categories and enter a weight (or a count for appliances), fill in the address, choose **Use my location**, then **Save and add photos**.
3. On the listing page, use **Add photo** (it opens the camera on a phone), then **Publish listing**.

The seeded draft iron listing for `9000000002` is ready to have a photo added and be published.

### Applying as a dealer

1. Log in with any account and choose **Become a dealer** on the home page (or open http://localhost/dealer).
2. Fill in the form and choose **Use my location**. The browser asks for location access.
3. Upload the documents on the status page that follows.

The seeded accounts `9000000004` and `9000000005` already have applications with sample documents.

### Faking a location in the browser

Location works on `http://localhost` without HTTPS. To pretend to be somewhere else, for example a dealer in a different area:
1. Open Chrome DevTools, then **More tools** (⋮), then **Sensors**.
2. Under **Location**, pick a preset or choose **Other…** and enter a latitude and longitude, for example Ameerpet, Hyderabad at `17.4375`, `78.4482`.
3. Reload the page and use **Use my location** again.

Firefox: open `about:config` and set `geo.provider.network.url` to `data:application/json,{"location": {"lat": 17.4375, "lng": 78.4482}, "accuracy": 10}`. Restore the setting afterwards.

To test on a real phone on the same Wi-Fi, the app must be served over HTTPS, because phones only allow location and camera on secure pages. Use an HTTPS tunnel to `http://localhost`, for example `cloudflared tunnel --url http://localhost`. Open the `https://` address it prints on the phone.

### Django admin

Open http://localhost/admin. Its login page sends you to the web app's phone login, then brings you back. Only staff accounts get in, such as the seeded admin `9000000001`. Other logged-in users get "403: no admin access".

To make another account an admin, use the admin (Users, then tick **Is staff** and **Is superuser**) or the shell:

```bash
docker compose exec backend python manage.py shell -c "from accounts.models import User; User.objects.filter(phone='+91XXXXXXXXXX').update(is_staff=True, is_superuser=True)"
```

To review dealers, go to **Dealer profiles** in the admin.
- **Approve, reject, suspend or reinstate:** tick one or more dealers, choose the action, and type a reason when rejecting or suspending (the dealer sees it). Then press **Go**.
  - Approval is refused, with a message, if required documents are missing or the scale certificate has expired.
  - Status can't be edited directly.
- **Check documents:** open a dealer and use **Open** next to each document. The file is streamed from private storage to admins only.
- **Authorise for e-waste:** open the dealer and tick **Ewaste authorised**. This needs an uploaded e-waste authorisation document.

The seeded dealer `9000000005` is waiting for review, so you can try this.

To manage scrap categories, go to Categories in the admin.
- **Add a category:** set its pricing unit (per kg or per item), and tick **Is restricted** for regulated items such as e-waste.
- **Retire a category:** untick **Is active**. Categories can't be deleted.

To suspend an account, go to Users in the admin, tick the account and choose **Suspend selected accounts**. The user is logged out straight away and can't log in again until reactivated.

Editing files under `frontend/src` or `backend/` reloads automatically. Frontend dependencies live in a Docker volume, not in `frontend/node_modules` on your machine. They reinstall automatically when `package-lock.json` changes.

## Tests and lint

Tests run against the PostGIS database in Docker, never SQLite. Files that tests upload go to MinIO under a `test-runs/<id>/` prefix, which is deleted when the run ends.

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

### CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every push and pull request:
- **Backend job:** starts `db`, `minio` and `backend` with Docker Compose, then runs ruff and pytest.
- **Frontend job:** runs lint, tests, type check and build in the `frontend` container.

To see results: `gh run list` or the repository's Actions tab. If a job fails, its last step prints the service logs.

## Scheduler

The `scheduler` service runs Django management commands every `SCHEDULER_INTERVAL_SECONDS` (default 300). The job list is `JOBS` in `backend/scheduler.py`.

| Job | What it does |
|---|---|
| `purge_otp_challenges` | Deletes login code records older than one day | A failing job is logged, and the other jobs and later runs still happen.

```bash
docker compose logs -f scheduler        # one "Scheduler run finished" line per run
docker compose restart scheduler        # needed after changing job code
```

To run a job by hand: `docker compose exec backend python manage.py <command>`

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
docker compose logs -f scheduler
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
| `S3_PUBLIC_ENDPOINT_URL` | `http://localhost` | Address the browser uses to open presigned photo links. Locally, Caddy forwards `/<bucket>/...` to MinIO. Leave empty in production to use AWS S3. |
| `PRESIGNED_URL_TTL_SECONDS` | `600` | How long a presigned photo link works |
| `LISTING_PHOTO_MAX_BYTES` | `10485760` | Largest listing photo, in bytes (10 MB) |
| `LISTING_MAX_PHOTOS` | `6` | Most photos per listing |
| `LISTING_MAX_WEIGHT_KG` | `10000` | Largest declared weight for one category on a listing, in kg |
| `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | `scrapbid`, `scrapbid-secret` | Local only |
| `MINIO_CONSOLE_PORT` | `9001` | Host port for the MinIO console |
| `SCHEDULER_INTERVAL_SECONDS` | `300` | Seconds between scheduler runs. Must be positive. |
| `DEALER_DOCUMENT_MAX_BYTES` | `10485760` | Largest dealer verification document, in bytes (10 MB) |
| `OTP_PROVIDER` | `console` | `console` prints codes to the logs. Not allowed in production. |
| `DEV_FIXED_OTP` | `123456` | Local only, 6 digits, used only with the `console` provider. Must be empty in production. |
| `OTP_EXPIRY_SECONDS` | `300` | Login code lifetime |
| `OTP_MAX_ATTEMPTS` | `5` | Wrong attempts before a code stops working |
| `OTP_MAX_REQUESTS_PER_PHONE_10_MIN` | `3` | Code requests per phone in 10 minutes |
| `OTP_MAX_REQUESTS_PER_PHONE_DAY` | `10` | Code requests per phone in 24 hours |
| `OTP_MAX_REQUESTS_PER_IP_HOUR` | `20` | Code requests per IP address in an hour |

All the `OTP_*` limits must be whole numbers greater than 0, or the backend won't start.

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

**`Permission denied` inside containers on Linux.** The `backend`, `scheduler` and `frontend` containers run as uid 1000 and write to the mounted source, for example caches, migrations, `package-lock.json` and `dist`. On Linux, the checkout must be writable by uid 1000, which is usually the first user account. Check with `id -u`. If yours is different, run `sudo chown -R 1000:1000 .` in the checkout, or clone as a uid-1000 user. macOS and Windows with Docker Desktop aren't affected.

**"Too many codes requested" while testing.** The limits work locally too. To reset them, delete the stored login records:

```bash
docker compose exec backend python manage.py shell -c "from accounts.models import OtpChallenge; OtpChallenge.objects.all().delete()"
```

**Frontend errors about missing packages.** Reinstall the dependencies with `docker compose run --rm frontend npm ci`, or remove the volume with `docker compose down` and `docker volume rm scrapbid_frontend-node-modules`, then start again.

**Health check shows `"storage": "error"`.**
1. Check that `minio` is running and that `minio-init` exited with code 0: `docker compose ps -a`.
2. Check that `minio-init` logged `Bucket created successfully` or that the bucket already exists: `docker compose logs minio-init`.
3. If `S3_BUCKET` or the MinIO login in `.env` has changed, run `docker compose up -d` again.

**Photos don't load (broken images), or links return 403.**
- `S3_PUBLIC_ENDPOINT_URL` must match the address in the browser's address bar. That's `http://localhost` by default, because links are signed for that host.
- If you open the app on another address (for example an HTTPS tunnel), set `S3_PUBLIC_ENDPOINT_URL` to that address and run `docker compose up -d`.
- Links also stop working after `PRESIGNED_URL_TTL_SECONDS`. Reload the page to get fresh ones.

**Health check shows `"database": "error"`.** Check `docker compose ps` for `db` being `healthy`, and `docker compose logs db`.

**Start from a clean slate.** This deletes all local data:

```bash
docker compose down -v
docker compose up --build -d
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py seed_demo
```
