# ScrapBid

A marketplace where people in India sell scrap (paper, metal, plastic, e-waste, appliances) to verified local scrap dealers, with transparent pricing.

**How it works for a seller**
1. Upload photos of the scrap, pick categories and enter an approximate weight.
2. Compare offers from verified dealers nearby. Offers are estimates.
3. Accept one offer and schedule a pickup.
4. At pickup the dealer weighs the scrap. The final amount is verified weight × agreed rate. The seller confirms, and payment (cash or UPI) is recorded.

Dealers get more customers. Dealers can also sell, for example in bulk to a bigger dealer, using the same flow.

> **Status:** early development. This version has the foundation: database, file storage, API health check, and a mobile-first web app served on `http://localhost`. Features are added in small, tested steps.

## Architecture

Everything runs in Docker Compose, locally and in production.

| Service | What it does |
|---|---|
| `db` | PostgreSQL 16 with PostGIS, for data and location queries |
| `minio` | S3-compatible file storage for local development (production uses AWS S3) |
| `minio-init` | Creates the private storage bucket, then exits |
| `backend` | Django REST API and Django admin |
| `frontend` | React web app (Vite dev server locally) |
| `caddy` | Serves everything on one origin: `/api`, `/admin` and `/static` go to Django, everything else goes to the web app |

The app and the API share one origin, so the browser uses session cookies with CSRF protection, with no CORS setup needed. A scheduler for timed jobs will be added next.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, Django 5.2 LTS, Django REST Framework, GeoDjango |
| Database | PostgreSQL 16 + PostGIS |
| Auth | Phone OTP, then Django session auth with CSRF |
| File storage | django-storages over the S3 API: MinIO locally, AWS S3 in production |
| Frontend | React 19 + TypeScript + Vite, React Router, mobile-first web app |
| Reverse proxy | Caddy |
| Admin | Django admin |
| Tests and lint | pytest + pytest-django, ruff; Vitest, eslint, prettier |
| CI | GitHub Actions |

## Demo accounts

Demo accounts come with the seed data command, which isn't in this version yet. Once it is, the phone numbers will be listed here. Locally they log in with the fixed OTP from `DEV_FIXED_OTP` (`123456` by default).

## Key decisions

- **Same code locally and in production.** Only the adapters differ, chosen by env vars: storage (MinIO or S3), OTP delivery (console or SMS), and the database host. No feature is skipped locally.
- **Production refuses unsafe settings.** The backend won't start in production with debug on, the default secret key, the console OTP provider, or a fixed OTP.
- **Money is stored as integer paise and weight as a decimal.** Floats are never used for either, and totals are computed on the server only.
- **Times are stored in UTC and shown in IST.** Amounts are shown in Indian format, e.g. ₹1,23,456.
- **UI text lives in one strings module** (`frontend/src/strings`), so more languages can be added later.
- **TypeScript 6.0.** The ESLint TypeScript tooling doesn't support TypeScript 7 yet.
- **Config comes from env vars.** Every variable is listed with a safe local default in `.env.example`.
- **Django 5.2 LTS** is the supported 5.x line.
- **Debian trixie base image,** because GeoDjango needs GDAL 3.9 or later to block file and network access from spatial data.
- **MinIO image from Chainguard.** MinIO no longer publishes its own images. Chainguard's build of MinIO is used, pinned by digest.

## Running it

See [RUNBOOK.md](RUNBOOK.md) for setup, tests, migrations, logs, environment variables and troubleshooting.
