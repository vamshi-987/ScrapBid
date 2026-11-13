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
| `caddy` | Serves everything on one origin. `/api`, `/admin` and `/static` go to Django. Locally, the storage bucket path goes to MinIO, for presigned photo links. Everything else goes to the web app. |
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
| `9000000001` | Admin. Also opens Django admin at http://localhost/admin, with a dealer application to review, a dispute to resolve, a rate flag, a suspension and a missed-pickup strike. |
| `9000000002` | Seller 1 (Ameerpet): an open listing (paper and cardboard), a draft iron listing to add a photo to and publish, a cardboard pickup confirmed for tomorrow at 10:00, a plastic listing whose dealer cancelled before the seller withdrew it, an iron pickup in dispute, and a cardboard pickup the dealer refused because the weight was short |
| `9000000003` | Seller 2 (Secunderabad): an open listing with restricted e-waste and an appliance, an expired plastic listing to relist, an aluminium listing with an accepted offer, a paper pickup already weighed (waiting for the seller to confirm), and an iron listing reopened after its dealer missed the pickup, with the other dealer's offer selectable again |
| `9000000004` | Approved dealer who also sells (Ameerpet), with an open 350.5 kg bulk copper listing. Switches between the Selling and Buying views. |
| `9000000005` | Dealer whose application is waiting for review (Kukatpally), with sample documents to approve in the admin |
| `9000000006` | Approved dealer, authorised for e-waste (Begumpet) |
| `9000000007` | Approved dealer (Secunderabad), flagged for a rate far above other dealers' |
| `9000000008` | Approved dealer out of range of the Hyderabad sellers (Warangal), suspended until the ₹500 fee is paid on **Penalties and fees** |
| `9000000009` | Approved dealer (Secunderabad) who missed a pickup: a strike to challenge on **Missed pickups** within 48 hours, and a ₹100 penalty to pay before making new offers |

The seed also adds scrap categories: paper, cardboard, iron, copper, aluminium, plastic, e-waste and batteries (the last two restricted), and appliance (priced per item). It also adds the listings above, with photos, and offers on the open ones: three dealers on Seller 1's paper listing, two on the bulk copper, and only the e-waste-authorised dealer on the e-waste listing. Any other valid Indian mobile number also works and gets a new seller account on first login.

Seeded pickup times are relative to the day you seed ("tomorrow at 10:00"). After a slot passes, the scheduler records it as missed. Run `seed_demo` again to reset every demo account and pickup; it's safe to run at any time.

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
- **Listing status follows one state machine, in one place** (`backend/listings/transitions.py`):

  ```
  DRAFT → OPEN → OFFER_ACCEPTED → PICKUP_SCHEDULED → PICKED_UP → COMPLETED
  Before PICKED_UP, a listing can be CANCELLED (with a reason).
  If the dealer cancels after acceptance, it goes back to OPEN (with a reason).
  PICKED_UP → DISPUTED → COMPLETED or CANCELLED (admin only)
  OPEN → EXPIRED (scheduled job only)
  ```

  - Every change locks the listing row, so two changes can't both succeed.
  - Every change writes one row to an append-only audit log in the same transaction (who, when, from, to, reason). Audit rows can't be edited or deleted, and the admin shows them read-only.
  - A test fails if any other code sets a listing's status directly.
- **Selling scrap.**
  - A seller picks one or more categories, with an approximate weight in kg (up to `LISTING_MAX_WEIGHT_KG` per category, so bulk dealer loads fit) or a count for per-item goods. They add the pickup address and location, and save a draft.
  - They then add 1–6 photos (the phone camera opens directly) and publish. Publishing needs at least one photo.
  - Listings can be edited until an offer is accepted. After that, the declared weights are locked.
  - An open listing with no accepted offer expires `LISTING_EXPIRY_DAYS` (7) after publishing. It leaves the dealer feed at once, and the scheduler marks it expired.
  - The seller can relist an expired listing as a new draft with the same address and items. Fresh photos are needed to publish it.
  - Sellers only ever see their own listings. The exact address appears only on the owner's listing page, never in lists.
- **What dealers see.**
  - An approved dealer's Buying view lists open listings inside their service area, nearest first, 20 at a time. PostGIS does the filtering in the database.
  - Dealers see the locality, pincode, items and photos. The exact address, coordinates, distance and anything about the seller stay hidden until an offer is accepted.
  - Dealers never see their own listings. A listing with any restricted category (e-waste, batteries) appears only to dealers authorised for e-waste.
  - Dealers whose scale certificate has expired lose access until they're re-approved with a valid certificate.
- **Offers.**
  - A dealer offers a rate per category: ₹ per kg, or ₹ per item for appliances. The server works out the estimate: each line is weight × rate (or count × rate), rounded half up to the paisa, and the lines are added up. Money is always integer paise, and any total sent by the app is ignored.
  - Offers are labelled as estimates everywhere: the final amount depends on the weight measured at pickup.
  - A dealer can have one active offer per listing, enforced by the database. They can edit it (which restarts its validity) or withdraw it while the listing is open.
  - The seller compares offers on their listing page, highest estimate first. They see each dealer's business name and area, but no contact details until they accept an offer. Only offers from dealers who are still approved, hold a valid scale certificate and still cover the pickup address are shown. Offers past their time show as expired.
  - **Accepting** an offer is atomic. The listing row is locked, so two accepts can never both succeed; the second gets "already accepted". A retried accept returns the same order.
    - Accepting creates an order with a snapshot of the declared amounts and agreed rates. Later rate edits can't change it.
    - Every other offer is marked rejected ("another offer was accepted"), so it can come back if that dealer cancels. The accepted dealer is notified.
    - Only live offers from dealers who are still eligible can be accepted.
  - Offers expire after `OFFER_EXPIRY_HOURS` (24), because rates change daily. An offer past its time can't be accepted and stops locking the listing straight away; the scheduler then marks it expired. Once a listing has an active offer, the seller can't edit it. Offers close when the listing expires.
- **Pickup times are agreed by both sides.**
  - After an offer is accepted, the dealer proposes a 2-hour window: between 8 am and 8 pm IST, at least 2 hours from now and at most 7 days ahead.
  - The seller confirms it or proposes another time for the dealer to confirm. The pickup is scheduled only once confirmed.
  - Either side can then ask to move it, until 2 hours before the slot. The new time applies only if the other side accepts before the confirmed slot starts; otherwise the confirmed time stands.
  - Every confirmation and accepted reschedule is in the audit log (with the old and new times), and the other side is notified. All the limits are config values.
- **The final amount at pickup** follows fixed rules, computed on the server only. The rate is always the accepted offer's rate; nobody can change it.
  - **Within 5%** of the declared weight (`WEIGHT_TOLERANCE_PERCENT`), either way, counts as equal: the dealer pays the verified weight × rate and can't refuse.
  - **More than 5% over:** the dealer either takes the excess (pays the verified weight) or declines it (pays the declared weight, and the seller keeps the rest).
  - **More than 5% under:** the dealer either accepts (pays the verified weight) or denies the pickup, which cancels it as `WEIGHT_SHORT`.
  - Counts of per-item goods are exact, with no tolerance. The dealer never pays for more than was verified. Each line rounds half up to the paisa.
- **Weighing at pickup.**
  - The dealer enters the verified weight (or count) for each item and takes a photo of the scale reading with the app's camera. The app has no file picker, so the photo can't come from the gallery.
  - The phone's GPS must be within 200 m of the pickup address (`PICKUP_MAX_DISTANCE_METERS`), or nothing is recorded.
  - The server applies the rules above and records, for each item, the declared, verified and taken amounts, the dealer's choice and the line amount, plus the final total. It also stores the photo (cleaned like listing photos), the server time and the distance from the address.
  - The listing moves to picked up and the seller is notified. If the dealer denies a short weight, the pickup is cancelled as `WEIGHT_SHORT`, with no strike for either side.
  - Both sides see the result and the scale photo on the pickup page. A retried weighing returns the recorded result unchanged.
  - Known limit: the camera-only photo and the GPS position are enforced by the app, so a modified client could fake either one. The seller's confirmation and the handover code are the real safeguards.
- **Handover: the dealer can never complete an order alone.**
  - After the weighing, the seller checks the scale photo and the amounts, then confirms. The app shows them a 6-digit code, valid for 15 minutes (`HANDOVER_CODE_EXPIRY_MINUTES`). Only a hash of it is stored, and it's never shown again.
  - The seller can show a fresh code at any time; the old one stops working. No code exists before the seller confirms.
  - The dealer pays the seller directly (cash or UPI), then enters the code and the payment method. After 5 wrong codes (`HANDOVER_CODE_MAX_ATTEMPTS`) the code is locked until the seller shows a fresh one.
  - The listing then becomes completed. The order records the method and the amount paid, which is always the server's final amount; any amount sent by the app is ignored. Contact details stop being shared, and the seller is notified. A retried completion changes nothing.
  - No payment gateway for pickups: the app only records the payment. (Penalties and fees are paid online; see below.)
- **Disputes.**
  - Instead of confirming, the seller can dispute the weighing, with a reason. The listing moves to disputed, any code already shown stops working, and nobody can complete the pickup. The dealer is notified.
  - Admins open the order in Django admin (filter by listing status "disputed") and choose **Resolve dispute**. The page shows the seller's reason, the scale photo, and the declared, verified and taken amounts.
  - The admin completes the order at the measured amounts, completes it at amounts they set (each line is that amount × the agreed rate, rounded half up; the rate never changes), or cancels it. A note is required and is shown to both sides.
  - The admin also records whether the dispute is upheld (the seller was right), which counts against the dealer. The dealer's original reading stays on the order, and the audit log keeps the old and new totals.
  - The decision ends the order without the handover code, closes contact details and notifies both sides. An admin cancellation doesn't count as a cancellation by either side. When an admin completes the order, the final amount is what the dealer owes; no payment method is recorded.
- **Cancellations and no-shows.**
  - Either side can cancel before pickup, with a reason. If the dealer cancels, the listing reopens with a fresh 7-day window, and the other offers come back if they're still within their time. If the seller cancels, the listing is cancelled. A seller can also cancel a draft or open listing.
  - During the confirmed slot, the dealer checks in from the pickup point. This uses the phone's GPS and must be within 200 m (`PICKUP_MAX_DISTANCE_METERS`).
  - 30 minutes after a confirmed slot ends with no pickup, the scheduler records a no-show. If the dealer checked in, the seller missed it; otherwise the dealer did.
  - The side that missed it gets a strike and a ₹100 penalty (`NO_SHOW_PENALTY_PAISE`). Until it's paid, a seller can't publish and a dealer can't make offers.
  - A strike can be challenged within 48 hours. Admins see every case with the check-in evidence, and can uphold the strike or remove it. Removing it cancels an unpaid penalty; a paid penalty isn't refunded. Cancellation and no-show counts appear on each user in the admin.
- **Misuse stats for admins.** Each user's page in Django admin shows, as a dealer: pickups weighed, short-weight denials and their rate, the average weight variance and disputes (with how many were upheld); as a seller: the average weight variance; and for everyone, cancellations and no-shows.
  - Variance is (verified − declared) ÷ declared for each weighed per-kg line, averaged, so a negative value means less was measured than declared. Counts of per-item goods are left out.
  - A short-weight denial isn't counted as a cancellation, because it carries no penalty.
  - The numbers are computed in the database and appear only in the admin, never in the app's API.
- **Rate flags and suspensions.**
  - A scheduled job flags a dealer whose average rate for a category over the last 30 days is more than 20% above the average of the other dealers. Each dealer counts once, by their own average, and at least 3 other dealers are needed. Withdrawn offers are left out. Admins review flags in the admin and close them with a note; a dealer and category get at most one flag in 30 days.
  - A dealer's 3rd upheld dispute (since their last suspension) suspends them. A suspended user can still log in and finish open pickups, but can't publish listings, accept offers, or make or edit offers, and sellers no longer see their offers. The user is notified.
  - The fee to lift a suspension is ₹500, doubling from the 3rd suspension (₹500, ₹500, ₹1,000, ₹2,000…). Paying the fee in the app lifts the suspension straight away. An admin can also lift it, which cancels the unpaid fee.
  - All thresholds and amounts are config values.
- **Penalties and fees are paid online through Razorpay.**
  - The **Penalties and fees** page (`/dues`) lists what the user owes. **Pay** opens a Razorpay payment page for the amount the server set; nothing the app sends changes it.
  - When Razorpay sends the user back, the server checks the callback's signature (HMAC-SHA256 with the key secret) and then asks Razorpay whether the link is fully paid. Only then is the due marked paid and the block or suspension lifted. A forged or replayed callback changes nothing.
  - If the redirect back is missed, **I've paid: check again** asks Razorpay directly.
  - The backend talks to Razorpay with the standard library only (no SDK). Locally, `PAYMENT_PROVIDER=fake` serves a test pay page with **Pay** and **Fail** buttons and sends the same signed callback, so the same code runs without an account or internet. Production refuses to start with the fake gateway.
- **Contact details are shared only within an open order.**
  - Once an offer is accepted, the dealer sees the exact pickup address (with an "Open in maps" link) and the seller's phone. The seller sees the dealer's phone.
  - Nobody else ever sees them: not other dealers, other sellers, or admins using the app.
  - They're never in lists, offers, notifications or logs, and they stop being shared as soon as the order is completed or cancelled.
- **In-app notifications** (no SMS, WhatsApp or push in the MVP).
  - Sellers are notified of each new offer. The header shows an unread count, refreshed every 30 seconds while the tab is open.
  - Notifications store only the kind and record IDs. The message text comes from the app's strings (ready for other languages), and no personal data is copied into them.
  - A notification is created in the same transaction as the change, so a failed change leaves none behind.
- **Users can delete their account** (**Account** in the header, `POST /api/auth/delete-account`).
  - Personal data is removed: the phone number, listing addresses and coordinates, listing photos, the dealer's business name, area, ID details and verification documents (files too), pickup GPS points, notifications and login records.
  - Transaction records stay without those details: orders with their weights and amounts, penalties and fees, strikes, disputes and the audit log.
  - Deletion is refused while a pickup is still open or a penalty or fee is unpaid. Otherwise draft and open listings are cancelled and live offers withdrawn.
  - Every session of the account stops working. The same number can sign up again later, as a new, empty account.
- **Photos are cleaned and private.**
  - Listing photos (JPG, PNG or WebP, up to 10 MB, up to 6 per listing) are decoded to check they're real images.
  - They're rotated upright, shrunk to at most 2048 px, and re-encoded from pixels only. All EXIF, GPS and other metadata is removed. Dealer document photos go through the same cleaning.
  - Files live in a private bucket. The app hands out presigned links that expire after `PRESIGNED_URL_TTL_SECONDS`.
- **Weights are exact decimals** (`NUMERIC(10,3)` kg). Each category on a listing has either a weight (priced per kg) or a quantity (priced per item), never both.
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
