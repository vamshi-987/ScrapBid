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

To compare offers, open an open listing. The **Offers** section lists them, highest estimate first. Seller 1's paper listing has 3 seeded offers. Choose **Accept this offer** and confirm. The listing moves to "Offer accepted", the other offers are marked not chosen, and the dealer gets a notification. Run `seed_demo` again to put the demo listing back to open.

After an offer is accepted, open **Pickup details** on the listing (the dealer finds it under **My pickups** in the Buying view):
1. The dealer picks a date and a start time (IST) and proposes it.
2. The seller confirms it, or proposes another time for the dealer to confirm.
3. Once confirmed, either side can **Ask for a new time** up to 2 hours before. The other side accepts it or keeps the confirmed time.

On the pickup page either side can **Cancel this pickup**, with a reason. During the confirmed slot the dealer taps **I've arrived (check in)**: the phone's location must be within 200 m of the address. Use DevTools Sensors to fake being there; see "Faking a location".

To record the weighing, log in as the dealer `9000000006` and open the scheduled cardboard pickup (40 kg declared) under **My pickups**. Set the location to `17.4370`, `78.4475` in DevTools Sensors, then choose **Record the weighing**:
1. Enter the verified weight. Within 5% needs no choice. Beyond 5%, choose to take or decline the excess, or to accept or deny the short weight.
2. Take the scale photo with the camera (allow camera access; on a laptop the webcam works).
3. Choose **Record weighing**. The listing becomes "Picked up", and the seller `9000000002` sees the verified amounts, the final amount and the photo.

Run `seed_demo` again to put the demo pickup back.

To try the handover, use the seeded paper pickup that's already weighed (26 kg at ₹15/kg, ₹390):
1. Log in as the seller `9000000003`, open it under **My pickups**, check the weighing result and choose **Confirm the weighing and show my code**.
2. In another browser profile (or a private window), log in as the dealer `9000000007`, open the same pickup, enter the 6-digit code, choose cash or UPI, then **Complete the pickup**.
3. Both sides see "Pickup complete" with the amount paid, and contact details are no longer shown. If the code expired or was mistyped 5 times, the seller chooses **Show a new code**.

Run `seed_demo` again to put it back.

To try a dispute, use the same weighed paper pickup: as the seller, choose **The weighing looks wrong: dispute it** and give a reason. To resolve one, log in as the admin `9000000001`, open http://localhost/admin/orders/order/, filter by listing status "disputed", open the order and choose **Resolve dispute**. Seller 1's iron pickup (`9000000002`, dealer `9000000006`) is already in dispute. Pick an outcome, say whether it's upheld, add a note and submit. Run `seed_demo` again to put both back.

Flags and suspensions are under **Flags** and **Suspensions** in the admin. The seed has an open rate flag on dealer `9000000007` and a suspension on dealer `9000000008`. Both are demo records created directly, because the seed has too few dealers in one city to raise a real flag and suspending an active demo dealer would block the other flows. Close a flag with the **Close selected flags** action and a note; lift a suspension with **Lift selected suspensions**. Log in as `9000000008` to see the suspension notice on the home page.

To try paying, log in as `9000000008`, choose **Pay now** (or open http://localhost/dues) and **Pay ₹500**. Locally this opens the fake gateway's test page: **Pay** completes the payment and returns you to the app, where the suspension is lifted; **Fail** returns without paying. Run `seed_demo` again for a fresh demo suspension. Dealer `9000000009` has an unpaid ₹100 no-show penalty to pay the same way.

**Razorpay in production:** create API keys in the Razorpay Dashboard (Account & Settings → API Keys; use test-mode keys first), set `PAYMENT_PROVIDER=razorpay`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` and `APP_BASE_URL` (your `https://` address) in the server's `.env`, and restart. No webhook is needed: the app confirms each payment with Razorpay when the user returns or taps **I've paid: check again**. A real suspension happens automatically on a dealer's 3rd upheld dispute (see the dispute steps above).

To see the misuse stats, open a user in the admin (http://localhost/admin/accounts/user/, search by the exact number, e.g. `+919000000006`). The seed gives dealer `9000000006` two weighed pickups (iron 30 → 29 kg, disputed; cardboard 20 → 15 kg, denied): 1 denial (50.0%), an average variance of −14.2% and 1 dispute.

Missed pickups are recorded by the scheduler 30 minutes after a confirmed slot ends. If the dealer checked in, the seller missed it; otherwise the dealer did. That side gets a strike and a ₹100 penalty, and is blocked until it's paid on **Penalties and fees** (`/dues`). They can challenge the strike on **Missed pickups** (`/strikes`) within 48 hours. Admins review strikes under **No show strikes** in the admin and choose **Uphold** or **Remove** (removing cancels an unpaid penalty; paid penalties aren't refunded).

The seed has one already, recorded by the real job: dealer `9000000009` missed Seller 2's (`9000000003`) iron pickup. The dealer has a strike to challenge and a penalty to pay, and can't make offers until it's paid. The listing is open again, with dealer `9000000006`'s offer selectable. Once the strike has been challenged, paid or removed, or is past 48 hours, `seed_demo` replaces it with a fresh one.

To try a no-show without waiting, move the seeded pickup's slot into the past and run the job:

```bash
docker compose exec backend python manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from orders.models import Order; o = Order.objects.get(listing__address_line__startswith='Demo listing 7:', status='active'); o.slot_start = timezone.now() - timedelta(hours=3); o.slot_end = timezone.now() - timedelta(hours=1); o.save()"
docker compose exec backend python manage.py record_no_shows
```

Run `seed_demo` afterwards to put the demo pickup back; it removes that strike and penalty too.

The pickup page also shows the other side's contact details: the dealer sees the exact address and the seller's phone, and the seller sees the dealer's phone. They disappear once the pickup is completed or cancelled.

To try it, the dealer `9000000007` has an accepted aluminium pickup with no time yet (under **My pickups**); the seller is `9000000003`.

The seeded draft iron listing for `9000000002` is ready to have a photo added and be published. `9000000003` has an expired listing: open it and choose **Relist**.

### Browsing listings as a dealer

Log in as an approved dealer and open the Buying view. Dual-role users switch views at the top of the page.

| Dealer | Sees |
|---|---|
| `9000000006` Begumpet, e-waste authorised | 4 listings, including the e-waste one |
| `9000000007` Secunderabad | 3 listings (e-waste hidden) |
| `9000000009` Secunderabad | 3 listings (e-waste hidden); can't offer until the penalty is paid |
| `9000000004` dual-role, Ameerpet | 2 listings (its own and the e-waste one hidden) |
| `9000000008` Warangal | none (out of range) |
| `9000000005` pending | a "not approved yet" message |

To make an offer, open a listing from the Buying view and enter a rate in rupees for each item. The estimated total updates as you type. Send it, then update or withdraw it from the same page. **My offers** in the Buying view lists them all. The seeded dealers already have offers, so withdraw one first to try making a new offer on the same listing.

Sellers get a notification for every new offer. The header shows the unread count, and **Notifications** lists them. On a fresh setup, the seeded offers leave Seller 1 (`9000000002`) with 3 unread.

To see how the service area works, change a dealer's location with DevTools Sensors, or edit it while pending. Then compare what each one sees.

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

### Deleting an account

Log in, open **Account** in the header and press **Delete my account**, then confirm.
- `9000000004` (dual role) and `9000000005` (pending dealer) can be deleted. Their listings are cancelled, offers withdrawn, and dealer details and documents removed.
- `9000000002` is refused (open pickups), and `9000000008` is refused (unpaid suspension fee).
- Logging in again with a deleted number creates a new, empty account. Run `seed_demo` to give the demo number its sample data back; the deleted account stays as an anonymised record.

In the admin, a deleted user shows `deleted-<id>` as the phone and a **Deleted at** time.

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

**Adding an API endpoint:** add it to the table in `backend/config/tests/test_security_sweep.py`, with who may call it (public, any user, seller, approved dealer) and whose object is in the URL. Until then, `test_every_endpoint_is_in_the_table` fails. The sweep then checks the endpoint for anonymous users, the wrong role, the wrong owner and bad input.

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
| `purge_otp_challenges` | Deletes login code records older than one day |
| `expire_listings` | Moves open listings with no accepted offer to expired, `LISTING_EXPIRY_DAYS` after publishing |
| `record_no_shows` | Records a no-show 30 minutes after a confirmed slot ends with no pickup: a strike and a ₹100 penalty for the side that missed it, who is blocked until it's paid |
| `expire_offers` | Marks active offers past their `OFFER_EXPIRY_HOURS` validity as expired. An offer past its time can't be accepted even before this runs. |
| `flag_rate_outliers` | Flags dealers whose average rate for a category over the last `RATE_FLAG_WINDOW_DAYS` is more than `RATE_FLAG_THRESHOLD_PERCENT` above the other dealers' average (needs `RATE_FLAG_MIN_PEERS` other dealers); at most one flag per dealer and category per window |

To expire listings straight away instead of waiting for the next run: `docker compose exec backend python manage.py expire_listings` A failing job is logged, and the other jobs and later runs still happen.

```bash
docker compose logs -f scheduler        # one "Scheduler run finished" line per run
```

The scheduler restarts by itself when a file under `backend/` changes (an edit or a `git pull`), so jobs always run the current code.

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
| `LISTING_EXPIRY_DAYS` | `7` | Days after publishing before an open listing with no accepted offer expires |
| `OFFER_EXPIRY_HOURS` | `24` | Hours an offer stays valid after it's made or last edited |
| `PICKUP_SLOT_HOURS` | `2` | Length of a pickup window |
| `PICKUP_DAY_START_HOUR`, `PICKUP_DAY_END_HOUR` | `8`, `20` | Pickup windows must fit between these hours (IST) |
| `PICKUP_MIN_NOTICE_HOURS` | `2` | A pickup must start at least this many hours from now |
| `PICKUP_MAX_DAYS_AHEAD` | `7` | A pickup can be booked at most this many days ahead |
| `PICKUP_RESCHEDULE_DEADLINE_HOURS` | `2` | Requests to move a confirmed pickup close this many hours before it |
| `PICKUP_MAX_DISTANCE_METERS` | `200` | How close the dealer must be to the pickup point to check in and to record the weighing |
| `NO_SHOW_GRACE_MINUTES` | `30` | A missed pickup is recorded this long after the slot ends |
| `NO_SHOW_PENALTY_PAISE` | `10000` | Penalty for a no-show (₹100); the user is blocked from publishing (sellers) or offering (dealers) until it's paid |
| `STRIKE_CHALLENGE_HOURS` | `48` | How long a user has to challenge a strike |
| `WEIGHT_TOLERANCE_PERCENT` | `5` | At pickup, a verified weight within this % of the declared weight counts as equal |
| `HANDOVER_CODE_EXPIRY_MINUTES` | `15` | How long the seller's handover code is valid |
| `HANDOVER_CODE_MAX_ATTEMPTS` | `5` | Wrong handover codes allowed before the seller must show a fresh one |
| `RATE_FLAG_THRESHOLD_PERCENT` | `20` | A dealer's average rate for a category more than this % above the other dealers' average is flagged |
| `RATE_FLAG_WINDOW_DAYS` | `30` | Offers from this many days back are compared; a dealer and category get at most one flag in this window |
| `RATE_FLAG_MIN_PEERS` | `3` | Other dealers needed before a rate is compared at all |
| `AUTO_SUSPEND_UPHELD_DISPUTES` | `3` | Upheld disputes against a dealer (since their last suspension) that suspend them from selling and buying |
| `SUSPENSION_FEE_PAISE` | `50000` | Fee to lift a suspension (₹500) |
| `SUSPENSION_FEE_DOUBLES_FROM` | `3` | From this suspension on, the fee doubles each time (₹500, ₹500, ₹1,000, ₹2,000…) |
| `PAYMENT_PROVIDER` | `fake` | `fake` simulates the gateway locally; production must use `razorpay` |
| `RAZORPAY_KEY_ID` | empty | Razorpay API key ID (production) |
| `RAZORPAY_KEY_SECRET` | empty | Razorpay API key secret (production); also checks callback signatures |
| `APP_BASE_URL` | `http://localhost` | Where the app is reached; the gateway returns users to `APP_BASE_URL/api/payments/callback`. Must be `https://` in production |
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
