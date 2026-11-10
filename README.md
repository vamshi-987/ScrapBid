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
| `scheduler` | Runs timed jobs (Django management commands) every few minutes. No Celery or Redis. |

The app and the API share one origin, so the browser uses session cookies with CSRF protection, with no CORS setup needed.

**CI:** GitHub Actions runs lint and tests on every push and pull request, using the same Docker Compose services and pinned images as local development.

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

Run `docker compose exec backend python manage.py seed_demo`, then log in at http://localhost with one of these numbers. The code is `DEV_FIXED_OTP` (`123456` by default).

| Phone | Account |
|---|---|
| `9000000001` | Admin. Also opens Django admin at http://localhost/admin. |
| `9000000002` | Seller 1 |
| `9000000003` | Seller 2 |
| `9000000004` | Approved dealer who also sells (Ameerpet). Switches between the Selling and Buying views. |
| `9000000005` | Dealer whose application is waiting for review (Kukatpally), with sample documents to approve in the admin |
| `9000000006` | Approved dealer, authorised for e-waste (Begumpet) |
| `9000000007` | Approved dealer (Secunderabad) |
| `9000000008` | Approved dealer out of range of the Hyderabad sellers (Warangal) |

The seed also adds scrap categories: paper, cardboard, iron, copper, aluminium, plastic, e-waste and batteries (the last two restricted), and appliance (priced per item). More demo data, such as dealers and listings, is added as features land. Any other valid Indian mobile number also works and gets a new seller account on first login.

## Key decisions

- **Same code locally and in production.** Only the adapters differ, chosen by env vars: storage (MinIO or S3), OTP delivery (console or SMS), and the database host. No feature is skipped locally.
- **Phone OTP login with a Django session.** One account per phone number.
  - Codes are 6 digits and expire after 5 minutes. A code locks after 5 wrong attempts, and a new request replaces any earlier code.
  - Requests are limited per phone and per IP, and the limits are counted in the database, so no Redis is needed.
  - The login records store only keyed hashes of the phone, IP and code, and are deleted after a day.
  - Responses are the same whether or not an account exists.
- **One account, several roles.** A user's roles (`seller`, `dealer`) are stored as a set, so a dealer who also sells uses the same account and switches views. Admin access is Django's staff flag.
- **Admins log in by phone OTP too.** Django admin's password login redirects to the web app's login, and the session then opens the admin.
- **Suspending an account** (Django admin, "Suspend selected accounts") blocks login and ends existing sessions straight away.
- **Categories are data, not code.**
  - Admins add and edit them in Django admin. Each is priced per kg or per item, and can be marked restricted (e-waste, batteries).
  - Categories are deactivated, never deleted, because listings refer to them.
  - The web app reads active categories from `GET /api/categories`.
- **Dealer onboarding.**
  - Any user can apply to be a dealer from their existing account. The application starts as pending and adds the dealer role.
  - A dealer sets a service area: shop location from the device plus a radius of 1–100 km. Locality and pincode are what others see.
  - Required documents are a shop or trade licence, ID proof, and a Legal Metrology scale certificate (with its expiry date). GST and e-waste authorisation are optional.
  - Documents are checked by content (PDF, JPG, PNG or WebP), stored under random names in the private bucket, and never returned as URLs.
  - Only the last 4 characters of the ID number are kept, shown as `XXXX-XXXX-1234`.
- **Dealer verification.**
  - Admins approve, reject, suspend or reinstate dealers in Django admin.
  - Approval needs all required documents and an unexpired scale certificate. Rejecting or suspending needs a reason, which the dealer sees. A rejected dealer can fix things and resubmit.
  - Only approved dealers pass the dealer check that every marketplace endpoint uses.
  - Admins open documents through an admin-only page that streams the file from private storage. No document link is ever created.
  - E-waste authorisation is a flag an admin can set only after an authorisation document has been uploaded.
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
