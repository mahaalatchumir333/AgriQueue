# AgriQueue

AgriQueue is a Flask and SQLite web MVP for agricultural procurement slot
booking, digital queue tracking, and procurement-centre operations.

## Run locally on Windows

In PowerShell, from the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:APP_SECRET_KEY = & python -c "import secrets; print(secrets.token_urlsafe(48))"
python app.py
```

Open <http://127.0.0.1:5000>. Create an official account in a second terminal
using the same project directory and database:

```powershell
.\.venv\Scripts\Activate.ps1
python -m flask --app app create-official --username procurement-admin
```

The command prompts for a password; use at least 12 characters. Farmers can
register from the portal. Existing farmer records without a password can have
one set by an authorized operator after verifying the farmer's identity:

```powershell
python -m flask --app app reset-farmer-password --mobile 9876543210
```

The database is created at `instance\agriqueue.sqlite3`. Set `DATABASE_PATH`
to use a different path. Schema changes are migrated automatically while
preserving existing records.

## Current web MVP

* Farmer accounts register with a 10-digit mobile number and a password.
  Passwords are stored as hashes; authenticated farmers only see their own
  bookings.
* Farmers select one centre, date, and time slot per booking. The API checks
  centre capacity transactionally and returns a digital token.
* Queue position and estimated wait are shown for each waiting booking. The
  portal refreshes its data every 30 seconds while open, and has a manual
  refresh button.
* Officials sign in with accounts provisioned through the Flask CLI. They can
  review centre and slot workloads, advance procurement status, record the
  actual quantity received, manually mark a completed booking as paid, and
  update Kisan Farmer ID plus PM-Kisan/PMFBY tracking fields.
* Mutating browser requests use a session-bound CSRF token. Session cookies
  are HTTP-only and SameSite=Lax; production cookies are HTTPS-only. The
  application sets a restrictive Content Security Policy, anti-framing and
  content-type headers, and disables caching for API responses.
* Sample farmer records were removed. Farmer accounts and booking data are
  stored server-side in SQLite, not browser localStorage.

## API overview

JSON errors use `{"error":{"code":"...","message":"..."}}`.

| Method | Route | Access | Purpose |
|---|---|---|---|
| `GET` | `/api/auth/session` | Public | Obtain CSRF token and current session |
| `POST` | `/api/auth/login` | Public + CSRF | Farmer mobile or official username sign-in |
| `POST` | `/api/auth/logout` | Signed in + CSRF | Sign out |
| `POST` | `/api/farmers` | Public + CSRF | Register and sign in a farmer |
| `GET` | `/api/availability` | Public | Check capacity and slot booking counts for a centre/date |
| `POST` | `/api/bookings` | Farmer + CSRF | Book one centre/date/slot |
| `GET` | `/api/farmer/bookings` | Farmer | View own queue and booking status |
| `GET` | `/api/official/analytics` | Official | Centre load, peak slots, and completion metrics |
| `GET` | `/api/official/bookings` | Official | Review procurement bookings |
| `PATCH` | `/api/official/bookings/<id>` | Official + CSRF | Advance status, record actual quantity, mark paid |
| `DELETE` | `/api/official/bookings/<id>` | Official + CSRF | Cancel an active booking |
| `GET` | `/api/admin/farmers` | Official session or admin API token | List farmer records |
| `PATCH` | `/api/admin/farmers/<id>` | Official session or admin API token | Update Kisan ID and scheme tracking |

Send the CSRF token in `X-CSRF-Token` on state-changing browser/API requests
that use session cookies. The compatibility admin API also accepts
`Authorization: Bearer <ADMIN_API_TOKEN>`; do not put this token in browser
JavaScript.
Bookings use the authenticated farmer session; clients cannot book on behalf
of a different farmer by supplying another farmer ID.

`GET /api/availability?centre_id=1&service_date=YYYY-MM-DD` returns the
remaining daily capacity and booking counts for each configured slot. A
centre's daily capacity is shared across slots. The availability check is
informational; booking creation checks capacity again transactionally.

## Production deployment

This project includes a Gunicorn Docker image. Production requires stable,
secret `APP_SECRET_KEY` and `ADMIN_API_TOKEN` values, each at least 32
characters. Store them in a secret manager; do not commit `.env` files.
Mount persistent storage at `/data`:

```powershell
docker build -t agriqueue .
docker volume create agriqueue-data
docker run -d --name agriqueue `
  --env "APP_ENV=production" `
  --env "APP_SECRET_KEY=replace-with-a-long-random-session-secret" `
  --env "ADMIN_API_TOKEN=replace-with-a-long-random-admin-token" `
  --mount "source=agriqueue-data,target=/data" `
  -p 8000:8000 agriqueue
docker exec -it agriqueue flask --app app create-official --username procurement-admin
```

Use HTTPS via a trusted reverse proxy, restrict access to the SQLite volume,
back it up, and create official accounts only after verifying staff identity.
SQLite with a persistent volume is for a single application host; use
PostgreSQL before horizontally scaling. The included `vercel.json` uses an
ephemeral serverless filesystem and is not suitable for production SQLite
data.

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Not integrated yet

This MVP does not provide an Android application, OTP-based identity
verification, SMS notifications, a payment gateway or payment webhooks, or
government data integrations. “Paid” and PM-Kisan/PMFBY statuses are manually
entered by officials; they are not provider-verified. It does not link Aadhaar
to Kisan ID, land ownership, survey numbers, crop surveys, PM-Kisan, or PMFBY.
Do not collect Aadhaar in this app. Add authorized integrations only through
approved services, documented consent, provider credentials, and an
appropriate privacy/security review. Production launch also needs deployment
monitoring, rate limiting/abuse controls, tested backups, and an operational
account-recovery process.
