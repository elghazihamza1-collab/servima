# Servima — "Airbnb for services" (Morocco) — v1

A trilingual (English / French / Arabic with RTL) marketplace where Moroccan
service workers post profiles and clients book them by the day. The platform
takes a 10–15% commission per job; payment is cash on service (no online
payments in v1).

**Sample data:** `seed.py` creates clearly-marked SAMPLE workers/listings so the
site looks alive on first run. Delete or replace them before production use.

## Project structure

```
services-platform/
├── app.py                  # Flask app: routes, SQLAlchemy models, sessions, i18n
├── translations.py         # ALL UI strings (en/fr/ar), categories, cities — no hardcoded UI text elsewhere
├── seed.py                 # creates admin user + SAMPLE Moroccan listings
├── requirements.txt        # pinned dependencies
├── templates/              # Jinja2 templates (base, home, browse, listing, auth,
│                           #   dashboard, listing_form, booking_*, admin, terms)
├── static/css/style.css    # vanilla CSS, RTL-aware (logical properties)
├── static/js/main.js       # tiny vanilla JS (delete confirmations)
├── uploads/                # worker listing photos (created at runtime)
└── instance/               # SQLite DB + server-side session files (created at runtime)
```

## Run locally

```bash
cd services-platform
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python seed.py     # first run only: admin + sample listings
python app.py      # serves on http://localhost:5000
```

Admin login: `admin@servima.ma` / `admin123` — **change immediately in production.**
Sample worker password: `worker123`.

### Configuration (environment variables)

| Variable          | Default                              | Purpose                              |
|-------------------|--------------------------------------|--------------------------------------|
| `SECRET_KEY`      | `dev-only-change-me-in-prod`         | Flask secret key — **must** be set in prod |
| `DATABASE_URL`    | `sqlite:///<project>/marketplace.db` | SQLAlchemy DB URL (e.g. Postgres on Render) |
| `COMMISSION_RATE` | `0.12`                               | Platform commission (0.10–0.15)      |
| `PORT`            | `5000`                               | Port to listen on                    |
| `FLASK_DEBUG`     | `0`                                  | Set `1` for debug mode (never in prod) |

## How it works (v1)

- **Workers** register → create listings (photo upload, DH/day price) → listings go to
  **pending** → admin approves → listing goes public.
- **Clients** browse/search by category + city (Casablanca, Rabat, Marrakech, Fès,
  Tanger, Agadir, other) → send a booking request (name/phone/date/message, no
  account needed) → get a **reference code** → check status anytime via
  "Check a booking status".
- **Workers** see incoming requests in their dashboard and Accept/Decline.
  Dashboard shows earnings estimate after the platform commission.
- **Admin** (`/admin`) approves/rejects pending listings.

## Deploy

### Option A — Render (easiest, free tier)

1. Push this folder to a GitHub repo.
2. On [render.com](https://render.com) → New → **Web Service** → connect the repo.
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
   - Add a **PostgreSQL** database (Render dashboard → New → PostgreSQL, free tier).
   - Environment variables:
     - `DATABASE_URL` = the Postgres "Internal Database URL"
     - `SECRET_KEY` = a long random string (`python -c "import secrets; print(secrets.token_hex(32))"`)
     - `COMMISSION_RATE` = `0.12`
3. After deploy, open a Render **Shell** and run `python seed.py` once
   (creates the admin user; skip if you don't want sample data — create the
   admin manually via `flask shell` instead).
4. **Uploads note:** Render's free disk is ephemeral — uploaded photos disappear
   on redeploy. For production, either use Render Disks (paid) or move uploads to
   object storage (S3/Cloudinary) in v2.

### Option B — VPS (Ubuntu) with gunicorn + nginx

```bash
# on the server
sudo apt update && sudo apt install -y python3-venv python3-pip nginx
git clone <your-repo> servima && cd servima/services-platform
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python seed.py   # first time only

# systemd service /etc/systemd/system/servima.service:
# [Unit] Description=Servima
# [Service] WorkingDirectory=/home/<user>/servima/services-platform
#   Environment="SECRET_KEY=<long-random>" "DATABASE_URL=sqlite:////home/<user>/servima/services-platform/marketplace.db"
#   ExecStart=/home/<user>/servima/services-platform/.venv/bin/gunicorn -w 3 -b 127.0.0.1:8000 app:app
#   Restart=always
# [Install] WantedBy=multi-user.target

sudo systemctl enable --now servima

# nginx site /etc/nginx/sites-available/servima (reverse proxy):
# server { listen 80; server_name servima.ma www.servima.ma;
#   client_max_body_size 3m;
#   location / { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host;
#                proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; } }
sudo ln -s /etc/nginx/sites-available/servima /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d servima.ma -d www.servima.ma   # free HTTPS
```

### Custom domain (servima.ma or similar)

1. **Buy the domain.** For `.ma`, use a Moroccan registrar — as a Moroccan
   individual you only need your CIN (national ID card); no company or
   trademark required. Prices vary a lot: ~150 MAD/yr at hostino.ma vs
   ~999 MAD HT/yr at genious.ma (checked 2026-10-05). Full comparison, the
   ANRT document rules, and the exact steps are in
   `../goals/services-marketplace-website/files/servima-ma-purchase-guide.md`.
   ⚠️ `.ma` is non-refundable and can't be edited after payment — double-check
   the spelling before paying.
2. **Point DNS to your hosting:**
   - Render: add the domain under Service → Settings → Custom Domains, then
     create the `CNAME` (or `A`) record Render shows you at your registrar.
   - VPS: create an `A` record for `@` and `www` pointing at your server's IP.
3. Wait for DNS propagation (minutes to a few hours), then enable HTTPS
   (Render does it automatically; on VPS use certbot as above).
4. Update any hardcoded links — this app uses `url_for` everywhere, so no code
   changes are needed.

## Security notes (v1)

- Passwords hashed with Werkzeug (scrypt). Never stored in plain text.
- Server-side sessions (Flask-Session, filesystem) — session cookies hold no data.
- ORM-only queries (SQLAlchemy) — no string-built SQL.
- Uploads: extension allowlist + magic-byte check + 2 MB limit + random filenames.
- No secrets hardcoded — everything via env vars with dev-only defaults.
- Known v1 gaps (see roadmap): no CSRF tokens, no rate limiting, no email
  verification, SQLite default (use Postgres in production).

## v2 roadmap

- Online payments via **CMI** (Morocco) / PayPal / Stripe — replace cash-on-service.
- Reviews & ratings for workers.
- Real-time chat between client and worker.
- SMS notifications (Twilio / local Moroccan SMS gateway) for booking updates.
- Object storage for uploads (S3/Cloudinary) + Postgres everywhere.
- CSRF protection, rate limiting, email verification, worker ID verification.
- Mobile apps / PWA.
