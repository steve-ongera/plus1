# Plus1 — Never Go Out Alone

> Working name: **Plus1**. Easy to rename later (see [Name ideas](#name-ideas)).

**Plus1** is a real-time, location-based social app for people who want to go out for
a soda, a coffee, wine or cocktails, but have nobody to go with. Users set a status
("Out now", "Going out soon"), check in at a **venue** (never a live GPS dot), see who
else is there with the same intent, and **link up** — only after both sides agree.

No dating pressure. No unsolicited DMs. No continuous tracking.

This README covers:
1. [MVP Scope](#1-mvp-scope)
2. [Tech Stack](#2-tech-stack)
3. [Project Structure](#3-project-structure)
4. [Data Model](#4-data-model-apimodelspy)
5. [API Endpoints](#5-api-endpoints)
6. [Real-Time Notifications](#6-real-time-notifications)
7. [Frontend Architecture](#7-frontend-architecture)
8. [Backend Setup](#8-backend-setup)
9. [Frontend Setup](#9-frontend-setup)
10. [Build Order](#10-build-order)
11. [Roadmap](#11-roadmap-after-the-mvp)
12. [Name ideas](#name-ideas)

---

## 1. MVP Scope

Goal: **a small, working app**, not a massive one. We scale after it works.

| In the MVP | Not yet (see Roadmap) |
|---|---|
| Register / login (JWT) | Mobile apps (React Native / Capacitor) |
| Profile with avatar and slug URL | PostGIS geospatial queries |
| Venues with cover image + gallery (images/video) | Google Places import |
| Status: Out now / Going out soon / Staying in | Venue owner dashboard + payments |
| Venue-level check-in (auto-expires) | Push notifications (FCM / OneSignal) |
| Moments: short photo/video posted at a venue (24h) | Group meetups |
| Link Up request → accept / decline | Supporter tier, sponsorships |
| Chat unlocked only after a link-up is accepted | AI recommendations |
| Real-time in-app notifications (WebSocket) | Multi-language |
| Block, report, Ghost Mode | |

---

## 2. Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, Django 5, Django REST Framework |
| Auth | JWT (`djangorestframework-simplejwt`) |
| Real-time | Django Channels + Daphne (ASGI). Redis in production; in-memory layer in dev |
| Database | PostgreSQL (SQLite is fine for local dev). Venue distance uses lat/lng + Haversine in `services.py` for the MVP |
| Media | Pillow for images; video stored as files (local disk in dev, S3-compatible bucket in production) |
| Frontend | React 18 + Vite, React Router, Axios |
| Maps | Mapbox GL JS (venue markers with headcounts, not people) |
| Styling | Plain CSS with theme variables (mobile-first) |

---

## 3. Project Structure

One Django project, **one app** (`api`), **one `models.py`**.

```
plus1/
│
├── backend/
│   ├── manage.py
│   ├── requirements.txt
│   ├── .env
│   ├── .gitignore
│   │
│   ├── config/                        # Django project (settings only)
│   │   ├── __init__.py
│   │   ├── settings.py
│   │   ├── urls.py                    # MAIN urls -> includes api.urls, serves media in dev
│   │   ├── asgi.py                    # HTTP + WebSocket routing (ProtocolTypeRouter)
│   │   └── wsgi.py
│   │
│   ├── api/                           # the one and only application
│   │   ├── __init__.py
│   │   ├── apps.py                    # imports signals on ready()
│   │   ├── admin.py
│   │   ├── models.py                  # ALL models (see section 4)
│   │   ├── serializers.py             # ALL serializers
│   │   ├── views.py                   # ALL API views / viewsets
│   │   ├── services.py                # business logic (rules live here, not in views)
│   │   ├── urls.py                    # app-level urls (/api/...)
│   │   ├── permissions.py             # IsOwner, NotBlocked, ...
│   │   ├── signals.py                 # creates Notification when LinkUp/Message is created
│   │   ├── consumers.py               # WebSocket consumer (notifications + live chat)
│   │   ├── routing.py                 # ws/ URL patterns
│   │   ├── middleware.py              # JWT auth for WebSocket (token in query string)
│   │   ├── tests.py
│   │   │
│   │   ├── management/
│   │   │   └── commands/
│   │   │       ├── seed_data.py       # demo venues, users, statuses
│   │   │       └── expire_data.py     # expire statuses, check-ins, moments (run via cron)
│   │   │
│   │   └── migrations/
│   │       └── __init__.py
│   │
│   └── media/                         # uploads in dev (git-ignored)
│       ├── avatars/
│       ├── venues/
│       └── moments/
│
├── frontend/
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   ├── .env
│   ├── .gitignore
│   │
│   └── src/
│       ├── main.jsx                   # React root
│       ├── App.jsx                    # lazy routes + guards (the app shell)
│       │
│       ├── services/
│       │   ├── api.js                 # axios instance, JWT refresh, all endpoint calls
│       │   └── socket.js              # WebSocket client with auto-reconnect
│       │
│       ├── context/
│       │   ├── AuthContext.jsx        # user, login, logout, register
│       │   └── NotificationContext.jsx# live notifications + unread count
│       │
│       ├── components/
│       │   ├── AppLayout.jsx          # navbar + bottom nav + <Outlet/>
│       │   ├── Navbar.jsx
│       │   ├── BottomNav.jsx          # mobile tab bar
│       │   ├── ProtectedRoute.jsx
│       │   ├── PageLoader.jsx         # circular loader (full-screen or inline)
│       │   ├── StatusToggle.jsx       # Out now / Going out soon / Staying in
│       │   ├── VenueCard.jsx
│       │   ├── PersonCard.jsx         # person at a venue + "Link Up" button
│       │   ├── MomentCard.jsx         # photo/video moment
│       │   ├── MediaUploader.jsx      # image/video picker with preview
│       │   └── SafetyMenu.jsx         # block / report
│       │
│       ├── pages/
│       │   ├── Login.jsx
│       │   ├── Register.jsx
│       │   ├── Home.jsx               # set status + check in
│       │   ├── Venues.jsx             # list + map of nearby venues
│       │   ├── VenueDetail.jsx        # /venues/:slug -> gallery, people, moments
│       │   ├── Moments.jsx            # feed + post a moment
│       │   ├── LinkUps.jsx            # incoming / outgoing / accepted
│       │   ├── Chat.jsx               # /chat/:linkupId (only after acceptance)
│       │   ├── Notifications.jsx
│       │   ├── Profile.jsx            # own profile + Ghost Mode
│       │   ├── PublicProfile.jsx      # /u/:slug
│       │   ├── Safety.jsx             # blocked users, how reporting works
│       │   └── NotFound.jsx
│       │
│       └── styles/
│           └── main.css               # theme variables, responsive layout
│
└── README.md
```

---

## 4. Data Model (`api/models.py`)

All models live in one file. Every public-facing model has a **`slug`** (auto-generated,
unique) so URLs are readable (`/venues/the-soda-bar`, `/u/amina-k3x9`).

| Model | Purpose | Key fields |
|---|---|---|
| **User** (extends `AbstractUser`) | Account + profile | `slug`, `display_name`, `bio`, `avatar` (image), `birth_date`, `is_ghost` (hidden from everyone), `last_seen` |
| **Venue** | Bar, café, lounge | `name`, `slug`, `description`, `address`, `city`, `country`, `latitude`, `longitude`, `cover_image`, `is_sponsored`, `is_active` |
| **VenueMedia** | Venue gallery | `venue`, `kind` (image / video), `file`, `caption`, `created_at` |
| **Status** | Current intent of a user (one per user) | `user` (1-to-1), `intent` (OUT / SOON / IN), `vibe` (SODA / WINE / COCKTAILS / COFFEE / ANY), `venue` (nullable), `note`, `expires_at` |
| **Moment** | Short photo/video posted at a venue | `author`, `venue`, `slug`, `kind` (image / video), `file`, `caption`, `expires_at` (24h) |
| **LinkUp** | Mutual opt-in request | `sender`, `receiver`, `venue`, `message`, `status` (PENDING / ACCEPTED / DECLINED / EXPIRED), `created_at`, `responded_at` |
| **Message** | Chat message (only inside an ACCEPTED LinkUp) | `linkup`, `sender`, `body`, `created_at`, `read_at` |
| **Notification** | Real-time + stored notification | `user`, `kind`, `title`, `body`, `data` (JSON), `is_read`, `created_at` |
| **Block** | User blocks another user | `blocker`, `blocked` |
| **Report** | Safety report for moderators | `reporter`, `reported_user`, `reason`, `details`, `status` |

**Rules enforced in `services.py`** (never trusted to the frontend):
- A user can only be linked with someone **checked in at the same venue**.
- No LinkUp if either side has blocked the other, or either is in Ghost Mode.
- Chat only opens when the LinkUp is `ACCEPTED`.
- Statuses expire after 6 hours, Moments after 24 hours (`expire_data` command).
- Upload validation: image types/size and video types/size/duration limits.

---

## 5. API Endpoints

Base path: `/api/` · Auth: `Authorization: Bearer <access_token>`

| Area | Method & Path | Description |
|---|---|---|
| **Auth** | `POST /auth/register/` | Create account |
| | `POST /auth/login/` | Returns access + refresh tokens |
| | `POST /auth/refresh/` | New access token |
| | `GET, PATCH /me/` | Own profile (avatar upload, Ghost Mode) |
| | `GET /users/<slug>/` | Public profile |
| **Venues** | `GET /venues/?lat=&lng=&radius=&q=` | Nearby venues with live headcount |
| | `GET /venues/<slug>/` | Venue detail + gallery |
| | `GET /venues/<slug>/people/` | People currently checked in (respects blocks + Ghost Mode) |
| | `POST /venues/<slug>/check-in/` | Check in (sets status venue) |
| | `POST /venues/<slug>/check-out/` | Check out |
| **Status** | `GET, PUT /status/` | Read / set intent, vibe, note |
| | `DELETE /status/` | Clear status |
| **Moments** | `GET /moments/?venue=<slug>` | Active moments feed |
| | `POST /moments/` | Upload photo/video (multipart) |
| | `DELETE /moments/<slug>/` | Delete own moment |
| **Link Ups** | `GET /linkups/?box=incoming\|outgoing\|accepted` | List |
| | `POST /linkups/` | Send request (`receiver`, `venue`, `message`) |
| | `POST /linkups/<id>/accept/` | Accept |
| | `POST /linkups/<id>/decline/` | Decline |
| **Chat** | `GET, POST /linkups/<id>/messages/` | History / send (accepted link-ups only) |
| **Notifications** | `GET /notifications/` | List |
| | `POST /notifications/read/` | Mark all (or given ids) as read |
| **Safety** | `GET, POST /blocks/` · `DELETE /blocks/<id>/` | Manage blocks |
| | `POST /reports/` | Report a user |

---

## 6. Real-Time Notifications

```
 Action (e.g. someone sends a Link Up)
        │
        ▼
 services.send_linkup()  ──creates──▶  Notification row (DB)
        │
        ▼
 signals.py / services.notify()
        │   channel_layer.group_send("user_<id>", {...})
        ▼
 consumers.NotificationConsumer  ──WebSocket──▶  Browser (NotificationContext)
```

- WebSocket URL: `ws://localhost:8000/ws/notifications/?token=<access_token>`
- `middleware.py` validates the JWT and attaches the user to the socket.
- Each connected user joins the group `user_<id>`.
- Events pushed: `linkup.received`, `linkup.accepted`, `message.new`, `status.nearby`.
- If the socket is down, notifications are still saved; the frontend loads them from
  `GET /notifications/` on reconnect.
- Dev uses `InMemoryChannelLayer`. Production switches to `channels_redis`.

---

## 7. Frontend Architecture

`App.jsx` follows the same professional pattern as a large multi-role app:

- **Every page is `lazy()`-loaded** — code downloads only when a page opens.
- A **`<Suspense>` boundary** shows `PageLoader` (full-screen for public pages, inline
  inside the layout so the navbar stays visible).
- **`Guarded`** wrapper = `ProtectedRoute` (signed in? allowed role?) → `Suspense` → page.
- **Providers**: `AuthProvider` (outermost) → `NotificationProvider` (inside the app
  shell, needs the signed-in user to open the WebSocket).
- Public routes (`/login`, `/register`, `/u/:slug`) sit outside the shell.
- Unknown paths show `NotFound`, inside the shell when signed in, full-screen otherwise.
- Roles are kept simple for now: `USER`, `VENUE_OWNER` (later), `ADMIN`.

`services/api.js`:
- One Axios instance with `VITE_API_BASE_URL`.
- Request interceptor adds the JWT; response interceptor refreshes it on `401`.
- Grouped exports: `authApi`, `venuesApi`, `statusApi`, `momentsApi`, `linkupsApi`,
  `notificationsApi`, `safetyApi`.
- Uploads use `FormData` for images/video.

---

## 8. Backend Setup

### 8.1 Requirements
- Python 3.11+
- PostgreSQL 14+ (optional locally — SQLite works)
- Redis (production only)

### 8.2 `requirements.txt`

```
Django>=5.0,<5.3
djangorestframework>=3.15,<4.0
djangorestframework-simplejwt>=5.3,<6.0
django-cors-headers>=4.3,<5.0
django-filter>=24.1
channels>=4.1,<5.0
channels-redis>=4.2,<5.0
daphne>=4.1,<5.0
Pillow>=10.3
python-dotenv>=1.0,<2.0
dj-database-url>=2.1,<3.0
psycopg2-binary>=2.9,<3.0
```

### 8.3 Install & run

```bash
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create `backend/.env`:

```env
SECRET_KEY=change-me
DEBUG=True
DATABASE_URL=sqlite:///db.sqlite3
# DATABASE_URL=postgres://user:pass@localhost:5432/plus1

CORS_ALLOWED_ORIGINS=http://localhost:5173
FRONTEND_URL=http://localhost:5173

# Leave empty in dev to use the in-memory channel layer
REDIS_URL=

# Upload limits
MAX_IMAGE_MB=5
MAX_VIDEO_MB=30
STATUS_TTL_HOURS=6
MOMENT_TTL_HOURS=24
```

```bash
python manage.py makemigrations api
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_data            # demo venues and users
python manage.py runserver            # Daphne serves HTTP + WebSocket
```

Run `python manage.py expire_data` every 10–15 minutes (cron / scheduler) to expire
old statuses, check-ins and moments.

---

## 9. Frontend Setup

```bash
cd frontend
npm create vite@latest . -- --template react
npm install axios react-router-dom mapbox-gl
```

Create `frontend/.env`:

```env
VITE_API_BASE_URL=http://localhost:8000/api
VITE_WS_URL=ws://localhost:8000/ws
VITE_MAPBOX_TOKEN=
```

```bash
npm run dev          # http://localhost:5173
npm run build        # production build -> /dist
```

---

## 10. Build Order

We build in small steps, and each step ends with something that runs.

1. **README.md** ← this file
2. **Backend core:** `config/settings.py`, `api/models.py`, migrations, admin
3. **Backend logic:** `services.py` → `serializers.py` → `views.py` → `urls.py`
4. **Backend real-time:** `consumers.py`, `routing.py`, `middleware.py`, `signals.py`, `asgi.py`
5. **Frontend shell:** `App.jsx`, `services/api.js`, `AuthContext`, `Login`/`Register`
6. **Frontend core flow:** `Home` (status + check-in) → `Venues` → `VenueDetail`
7. **Link Up flow:** `LinkUps`, `Chat`, `NotificationContext`, `Notifications`
8. **Media:** `Moments`, `MediaUploader`, venue gallery
9. **Safety:** block, report, Ghost Mode
10. **Polish & deploy**

---

## 11. Roadmap (after the MVP)

- PostGIS for fast geospatial queries
- Google Places import for venues
- Push notifications (FCM / OneSignal) and mobile apps (Capacitor / React Native)
- Venue owner dashboard (check-in analytics, "Hotspot" boosts, vouchers)
- Supporter tier (advanced filters, no ads) and non-alcoholic brand sponsorships
- Group meetups ("3 of us, looking for 2 more")
- Moderation tools and 24/7 report queue
- Multi-language support (English, Swahili, French, Spanish…)

**Privacy by design:** venue-level check-ins only, no background location tracking,
data auto-expires, Ghost Mode and blocking are always free.

---

## Name ideas

| Name | Why |
|---|---|
| **Plus1** | "Plus one" — you're never going out alone. Short, global, easy to say |
| **Tuko** | Swahili/Sheng for "we're here" — strong East African identity |
| **Nightcap** | Friendly, classic, fits drinks + evenings |
| **Sundowner** | Relaxed "drink at sunset" feel, great for coastal cities like Mombasa |
| **Pullup** | Casual, youthful: "pull up, we're at the bar" |
| **Cheers Circle** | Warm and social, signals groups rather than dating |

> Before committing: check domain, app-store, and trademark availability.

---

## License

Proprietary — all rights reserved.