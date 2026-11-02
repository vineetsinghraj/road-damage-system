# Smart Road Damage Detection & Complaint System — Prototype

A working end-to-end prototype of the workflow:
**upload photo → detect damage → estimate severity → tag GPS location → check for
duplicates → auto-generate a complaint → route to an authority → track status → view on a map/dashboard.**

## ⚠️ What's real vs. what's mocked (read this first)

This is scoped as a runnable prototype, not the full production system the
original spec described (that would need a trained CV model, a live
government API integration, weeks of infra, etc.). Specifically:

| Piece | Status |
|---|---|
| Auth, roles, JWT, RBAC | Real |
| Database schema, complaint workflow, status history | Real |
| Duplicate detection (geo-radius + time window) | Real |
| Severity scoring, complaint ref generation | Real |
| Authority routing by city | Real (rule-based) |
| **AI damage detection** | **Mocked** — deterministic pseudo-detection derived from the image's own bytes, not a trained model. Every response is honest about this via `is_demo`-style framing. See `backend/ai_detector.py` for exactly how to swap in a real YOLOv8 model (full instructions in that file's docstring). |
| **Reverse geocoding** | **Mocked** — snaps coordinates to the nearest of 4 demo cities, no external API call (sandbox had no network egress). Swap point documented in `ai_detector.py`. |
| **Government complaint submission** | **Simulated** — complaints live in this app's own database only; nothing is sent to a real government system. That's the honest state for a prototype without real credentials. |
| Notifications | Written to a `notifications` table + printed server-side; no real email/push provider wired in (interface documented in `backend/utils.py`). |

## Stack

- **Backend:** Flask + Python stdlib `sqlite3` (no Postgres/Docker required to run today; the schema in `database.py` maps directly onto Postgres if you want to migrate — see "Extending" below)
- **Auth:** JWT (PyJWT) + Werkzeug password hashing
- **Frontend:** Single-page vanilla HTML/CSS/JS (no build step) + Leaflet (maps) + Chart.js (analytics), both loaded from CDN
- **AI:** Mocked detector with a documented swap-in point for a real YOLO model

This keeps the whole thing to **one process, one command, zero npm/docker
required** — good for actually running it right now. See "Extending" for how
to grow toward the original multi-service architecture.

## Project structure

```
road-damage-system/
├── backend/
│   ├── app.py            # Flask app: all API routes
│   ├── database.py       # SQLite schema + connection helper
│   ├── auth.py            # password hashing, JWT, role-based decorator
│   ├── ai_detector.py     # mock detection + severity + reverse-geocode (swap points documented)
│   ├── utils.py           # haversine distance, complaint ref generator, notification interface
│   ├── seed.py             # demo accounts + demo authorities
│   ├── test_api.py         # automated tests (Flask test client)
│   └── requirements.txt
├── frontend/
│   ├── index.html         # citizen / authority / admin views
│   ├── app.js
│   └── style.css
└── data/                    # created at runtime: sqlite db + uploaded images
```

## Running it

```bash
cd road-damage-system/backend
pip install -r requirements.txt
python3 app.py
```

Then open **http://localhost:5001** in your browser. The server also serves
the frontend directly, so there's nothing else to start.

On first run it seeds demo accounts and 4 demo city authorities automatically.

### Demo accounts (shown on the login screen too)

| Role | Email | Password |
|---|---|---|
| Citizen | citizen@demo.local | citizen123 |
| Authority | authority@demo.local | authority123 |
| Admin | admin@demo.local | admin123 |

### Try the flow

1. Log in as **Citizen** → "Report Road Damage" → pick any photo → (optionally) "Use my location" → Analyze & Report. You'll get a generated complaint ID and see it on your list and the map.
2. Log in as **Authority** → see the complaint, filter by severity/status, open "Update Status" to move it through the workflow, check the analytics charts.
3. Log in as **Admin** → add a new authority (e.g. for another city), see all users.

### Running tests

```bash
cd backend
python3 test_api.py -v
# or, if you have pytest: python3 -m pytest test_api.py -v
```

## What I'd extend first

1. **Swap in a real detector.** This is the single highest-leverage change — everything else (severity, complaints, dashboards) already consumes whatever `detect_road_damage()` returns, so plugging in a trained YOLOv8 model (see the docstring at the top of `ai_detector.py`) upgrades the whole app without touching other files.
2. **Real reverse geocoding + authority mapping.** Replace the 4-city mock with a Nominatim/Google Geocoding API call, and let admins map (city, ward) → authority instead of just city — this is what makes "route to the correct government office" actually correct.
3. **Move off SQLite once multiple people will hit it concurrently.** The schema was written to translate almost directly to PostgreSQL; swap `database.py` for SQLAlchemy models and point `DATABASE_URL` at Postgres. Do this before any real deployment — SQLite's fine for one dev, not for concurrent authority + citizen traffic.
4. **Video support.** The spec calls for frame extraction + duplicate-detection-across-frames; right now only single images are handled. This is a self-contained addition to `ai_detector.py` + a new `/api/reports/video` route.
5. **Real notification delivery.** `notify_user()` in `utils.py` is a single documented interface point — wire in an email provider (e.g. SMTP or SendGrid) or push (FCM) behind it.
6. **Harden auth for production.** Move `JWT_SECRET` out of the default fallback (set `RDM_JWT_SECRET` env var, required, no default), add refresh tokens, rate-limit `/api/auth/login`, and add file-type sniffing (not just extension checking) on uploads.
