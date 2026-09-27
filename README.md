# Generic Booking & Appointment Management System

A reusable booking platform for anything people reserve: a trainer, a doctor, a meeting room, a tennis court, a class, a camera. The booking engine has no industry-specific logic. It only answers one question:

> **Is RESOURCE X available for SERVICE Y between START and END?**

Everything else is configuration: resources, services, schedules and booking rules.

| Layer    | Stack                                                                 |
| -------- | --------------------------------------------------------------------- |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS 4, React Router, TanStack Query |
| Backend  | Python 3.11, FastAPI, SQLAlchemy 2, Alembic                           |
| Database | PostgreSQL 16 (row locks + `btree_gist` exclusion constraint)         |

---

## Quick start

### Option A: Docker

```bash
docker compose up --build
docker compose exec backend python -m app.seed      # optional demo data
open http://localhost:8080
```

### Option B: Local development

```bash
# 1. PostgreSQL: create a database and user named "booking" (password "booking"),
#    or set DATABASE_URL (see backend/.env.example).

# 2. Backend (http://localhost:8000, API docs at /api/docs)
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
python -m app.seed          # demo data
uvicorn app.main:app --reload

# 3. Frontend (http://localhost:5173, proxies /api to :8000)
cd frontend
npm install
npm run dev
```

Demo accounts created by the seed:

| Role  | E-mail              | Password     |
| ----- | ------------------- | ------------ |
| Admin | `admin@example.com` | `admin12345` |
| User  | `user@example.com`  | `user12345`  |

The seed builds a small catalogue spanning several industries: a strength coach with a lunch break, a consultant who is off on Wednesdays, a meeting room, a tennis court at another location, and a 12-seat yoga class.

### Tests

```bash
cd backend && pytest            # 70 tests, against a real PostgreSQL database
cd frontend && npm run build    # type-check + production build
```

The tests use the `booking_test` database by default (override with `DATABASE_URL`). They rebuild the schema by running the Alembic migrations, so the migration itself is tested too.

---

## Architecture

```
                 React SPA (Vite)
                        │  same-origin /api (cookie + CSRF, or Bearer token)
                        ▼
                ┌──────────────┐
                │   REST API   │  FastAPI routers, pydantic validation, RBAC
                └──────┬───────┘
       ┌───────────────┼────────────────────┐
       ▼               ▼                    ▼
 Authentication   Booking Engine       Admin services
 (JWT + server    (lock → recheck      (catalogue, schedules,
  sessions)        → write)             conflicts, reports)
                       │
                       ▼
               Availability Engine  ◄── pure interval / schedule maths
                       │
                       ▼
                  PostgreSQL  ◄── exclusion constraint = last line of defence
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
      Background jobs      Notifications
      (reminders,          (domain events → in-app + e-mail outbox)
       delivery)
```

### Backend layout

```
backend/app/
  core/        config, security (argon2, JWT, rate limiter), permissions, timezone helpers
  models/      SQLAlchemy entities + enums
  services/
    intervals.py         half-open interval algebra (union / intersect / subtract)
    schedule.py          weekly rules + date overrides → concrete intervals (DST-safe)
    availability.py      the availability engine: snapshot, slot generation, slot checks
    booking_engine.py    create / cancel / reschedule / waitlist / recurring / conflicts
    notifications.py     event handlers, reminder scheduling, delivery
    settings_service.py  runtime-configurable booking rules
    reports.py, audit.py, events.py
  api/routes/  auth, catalog (public), bookings, notifications, admin_*
  jobs/        background loop (single-runner via PostgreSQL advisory lock)
  seed.py      demo data
```

### Frontend layout

```
frontend/src/
  api/          fetch client (CSRF header, typed errors) + TypeScript API types
  auth/         session context and route guards
  components/   UI kit, layout, slot picker, booking widgets
  pages/        public, account (dashboard, bookings, profile), book (wizard)
  pages/admin/  overview, calendar (day/week/month), bookings, conflicts,
                resources, services, schedules, locations, users, settings, audit
```

Colours are semantic tokens (`bg-surface`, `text-muted`, `border-line`, `bg-accent`…) defined once in `src/index.css` for a light and a dark theme. The theme follows the system setting until the user picks one in the header; `public/theme.js` applies it before the first paint (the CSP only allows same-origin scripts). Layouts work down to phone width: tables stack into labelled rows, dialogs open as bottom sheets and the header folds into a menu.

---

## How the core rules are enforced

### Available time

```
AVAILABLE = Operating hours ∩ Resource availability
            − Blocks / holidays / maintenance
            − Existing bookings (including buffers)
```

`availability.build_snapshot()` gathers every constraint for one resource over a time range. `ResourceSnapshot.check()` evaluates one candidate booking against it. **Slot listing and booking validation call the same function**, so the UI can never offer a slot that the engine would then reject for a different reason.

- **Operating hours** apply per location, falling back to a default set. With no hours configured, there is no restriction.
- **Resource availability** is a set of weekly rules with optional `valid_from`/`valid_until` dates. Multiple periods per day are supported, and `is_available = false` rows carve out breaks. A resource with no rules follows the operating hours.
- **Exceptions** can apply to one resource, one location, or everything. `BLOCKED`, `UNAVAILABLE`, `HOLIDAY` and `MAINTENANCE` remove time. `SPECIAL_HOURS` *replaces* the recurring schedule for the dates it covers (rule 9: date-specific availability overrides recurring availability).
- **Slots** are generated dynamically; nothing is pre-generated. They sit on a grid anchored at each availability window. Blocks and bookings mark grid slots unavailable rather than shifting the grid. Only slots that fit the whole booking length are returned. The step is the `slot_interval` setting, or the service duration when that is 0.
- **Booking length** is the service's duration (or the resource's custom duration). An individual service with a `max_duration_minutes` lets customers book longer, in multiples of that duration up to the maximum (a 30-minute consultation with a 120-minute maximum can be booked for 30, 60, 90 or 120 minutes); the slot grid keeps stepping by the base duration. Group sessions always run for their set length. A moved booking keeps its own length.
- **Prices are hourly rates.** A booking costs `rate × booked hours` (× places for group sessions), rounded half up to the cent and stored on the booking, so later rate changes don't touch existing bookings.

### Clash detection and buffers

Two bookings overlap when `newStart < existingEnd AND newEnd > existingStart`, using half-open intervals (touching is fine). Each booking occupies `[start − buffer_before, end + buffer_after]`. Buffers come from the resource-service link, else the service, else the settings default.

### Race conditions (rules 1–4)

Every write follows the same sequence:

```
validate (fail fast) → BEGIN → SELECT … FOR UPDATE on the resource rows (sorted by id)
→ RE-validate against committed data → write → COMMIT
```

Locking resource rows serializes competing requests for the same resource. Sorting the ids prevents deadlocks. Underneath that, PostgreSQL enforces:

```sql
EXCLUDE USING gist (
  resource_id WITH =,
  tstzrange(occupied_start, occupied_end, '[)') WITH &&,
  allocation_key WITH <>
) WHERE (active)
```

Each booking's hold on each resource is a `booking_resources` row. Exclusive bookings get a unique `allocation_key`. All seats of one capacity session share a key, so they may overlap each other but nothing else. Even a write that bypassed the application could not double-book. The test suite fires 10 simultaneous requests at one slot: exactly one succeeds. Twelve requests for a 3-seat class yield exactly 3 confirmed and 9 waitlisted.

### Capacity and waitlists (rules 5, 6, 14)

- `remaining = capacity − Σ quantity` over active seats in the session. Capacity is the service capacity, capped by the resource capacity.
- When a session is full, a booking made with `join_waitlist` becomes `WAITLISTED` and takes no allocation. A cancellation promotes waitlisted bookings strictly first-come-first-served, in the same transaction and under the resource lock, so one freed seat can never go to two people.
- Cancelled, rescheduled and waitlisted bookings never consume capacity. `PENDING` bookings consume it only if `pending_consumes_capacity` is on. When it is off, confirming a pending booking re-checks availability first.

### Rescheduling (rule 13)

**Chosen strategy:** a reschedule creates a *replacement booking* linked via `rescheduled_from_id`, and the original becomes `RESCHEDULED`. The new slot is validated, and the original's allocation is only released inside the same transaction that writes the replacement. Any failure rolls everything back, leaving the original intact and still holding its slot. A test covers this. "Reassign resource" is the same operation with a different resource.

### Schedule changes and conflicts (spec §35–36)

Every admin change that can shrink availability goes through `apply_schedule_change`: resource availability, operating hours, blocks, special hours, resource deactivation, and location timezone changes. It can run as a **dry run**: the change is applied inside a savepoint, affected bookings are collected with the same availability rules, and everything is rolled back. The admin sees *"This change affects N existing bookings"*, then chooses:

- **Keep**: leave the bookings as they are.
- **Mark as conflicts**: set the bookings to `CONFLICTED`.
- **Cancel**: cancel the bookings.

Conflicts are resolved from the Conflicts page by rescheduling, reassigning, cancelling, or overriding the block. Alternative slots are suggested.

### Booking state machine

```
PENDING ──► CONFIRMED ──► COMPLETED / NO_SHOW
   │            │
   ├────────────┴──► CANCELLED / RESCHEDULED / CONFLICTED
WAITLISTED ──► CONFIRMED (promotion) / CANCELLED
CONFLICTED ──► CONFIRMED (override) / CANCELLED / RESCHEDULED
```

Transitions are whitelisted in `booking_engine.ALLOWED_TRANSITIONS`. Bookings are never deleted.

### Timezones and DST

Instants are stored as `timestamptz` (UTC). Each location has an IANA timezone, and wall-clock values (weekly rules, a date the user picked, a naive `start` sent to the API) are interpreted in the resource's location timezone. The server's OS timezone is never used. Slot steps are absolute durations, so DST days produce the correct 23 or 25 hours, with no phantom or missing slots. Weekly recurrences step in local time, so a 10:00 class stays at 10:00 across a DST change. Periods whose end is before their start run past midnight.

### Security

- Passwords are hashed with **argon2id**, with constant-time handling of unknown e-mails.
- Each JWT references a server-side **session row**, so logout and password resets really revoke tokens.
- The browser uses an **httpOnly, SameSite=Lax cookie** plus a **double-submit CSRF token** on every state-changing request. API clients may use `Authorization: Bearer`, which needs no CSRF check.
- **RBAC with permissions** (`core/permissions.py`): routes check permissions, not role names. `USER` and `ADMIN` are active; `STAFF`, `MANAGER`, `RESOURCE_OWNER` and `SUPER_ADMIN` are pre-mapped. Self-registration always creates a `USER`, and only a `SUPER_ADMIN` can create another `SUPER_ADMIN`.
- Users can only see or modify their own bookings (others return 404), enforced in the engine, not the UI.
- Rate limiting on login, registration and password reset; pydantic validation on every input; ORM-only SQL.
- Secure headers on API responses (`nosniff`, `DENY`, CSP, HSTS when HTTPS) and a strict CSP on the SPA (see `frontend/nginx.conf`).
- Every booking change, schedule change and **admin override** is written to `audit_logs` in the same transaction.
- In production (`ENVIRONMENT=production`) the API refuses to start with the default JWT secret or without secure cookies.

### Notifications

The booking engine publishes domain events (`booking.created`, `.cancelled`, `.rescheduled`, `.waitlist_promoted`, `.conflicted` …). The notifications module subscribes to them and writes in-app messages and queued e-mails **in the same transaction** as the booking change (a transactional outbox). A background job delivers queued e-mails and schedules reminders (default 24 h and 1 h before). Reminders carry a unique `dedupe_key`, so they are never sent twice, even with several workers. The e-mail backend is `console` (logs) or `smtp`. SMS and push are recognized channels, but no delivery provider is implemented; they are skipped.

---

## Configuration

**Process settings** are environment variables; see `backend/.env.example`. The main ones are `DATABASE_URL`, `JWT_SECRET`, `COOKIE_SECURE`, `CORS_ORIGINS`, `FRONTEND_URL`, `EMAIL_BACKEND` and `SMTP_*`.

**Booking rules** live in the `settings` table. Admins edit them live under Admin → Settings:

| Key | Default | Meaning |
| --- | --- | --- |
| `slot_interval` | 0 | Minutes between slot starts (0 = service duration) |
| `minimum_booking_notice` | 0 | Minutes of notice required |
| `maximum_advance_booking_days` | 90 | Booking window |
| `cancellation_window` / `rescheduling_window` | 0 | Minutes before start after which users can no longer cancel / reschedule (admins can, audited) |
| `default_buffer_before` / `default_buffer_after` | 0 | Fallback buffers |
| `allow_waitlist`, `allow_recurring_bookings` | true | Feature switches |
| `require_admin_confirmation` | false | New user bookings start `PENDING` |
| `pending_consumes_capacity` | true | Whether `PENDING` holds the slot |
| `enforce_operating_hours` | true | Intersect with operating hours |
| `reminder_offsets` | [1440, 60] | Reminder times (minutes before start) |
| `default_timezone`, `business_name`, `max_recurring_occurrences` | | |

---

## API overview

Interactive docs: `http://localhost:8000/api/docs` (development only).

| Area | Endpoints |
| --- | --- |
| Auth | `POST /api/auth/register · login · logout · forgot-password · reset-password · change-password`, `GET/PUT /api/auth/me`, `GET /api/auth/session` |
| Catalogue | `GET /api/services[/:id]`, `GET /api/resources[/:id]`, `GET /api/resources/:id/availability`, `GET /api/locations`, `GET /api/config` |
| Availability | `GET /api/availability?service_id&date[&resource_id][&location_id][&quantity][&duration_minutes]`, `GET /api/availability/alternatives` |
| Bookings | `POST/GET /api/bookings`, `GET /api/bookings/:id`, `POST /api/bookings/:id/cancel · reschedule`, `POST /api/bookings/recurring[/preview]` |
| Notifications | `GET /api/notifications`, `GET /api/notifications/unread-count`, `POST /api/notifications/:id/read · read-all` |
| Admin: bookings | `GET/POST /api/admin/bookings`, `GET/PUT /api/admin/bookings/:id`, `POST …/:id/cancel · reschedule · reassign · confirm · complete · no-show`, `GET …/:id/history` |
| Admin: conflicts | `GET /api/admin/conflicts[/:id]`, `POST /api/admin/conflicts/:id/resolve` |
| Admin: catalogue | `…/resources`, `…/resources/:id/services`, `…/resources/:id/availability`, `…/services`, `…/locations` |
| Admin: schedules | `GET/PUT /api/admin/operating-hours`, `GET/POST/DELETE /api/admin/exceptions` |
| Admin: system | `GET /api/admin/dashboard · calendar · reports/utilization · audit-logs`, `GET/POST/PUT /api/admin/users`, `GET/PUT /api/admin/settings` |

Schedule-changing admin endpoints accept `?dry_run=true&conflict_action=keep|mark_conflicted|cancel`.

Naive datetimes (e.g. `"2026-09-28T10:00"`) are wall-clock time at the resource's location. Datetimes with an offset are taken as absolute instants.

Bookings, recurring bookings and alternatives take an optional `duration_minutes` for services with a flexible length; without it a booking gets the service's standard length.

---

## Scope

**Implemented (MVP and most of Phase 2):** authentication; users and admin with RBAC; resources with JSON metadata; services; resource-service overrides; operating hours; weekly availability; exceptions and special hours; dynamic slots; individual and capacity bookings; buffers; notice and advance windows; clash prevention (application and database); rescheduling; cancellation policy; waitlists; recurring bookings with per-occurrence validation; multi-resource bookings (atomic); schedule-change previews and conflict resolution; alternative suggestions; multiple locations and timezones, each location linking to Google Maps (its own link, or a search for its address) on bookings, resource pages and booking e-mails; in-app and e-mail notifications with reminders; admin dashboard with utilization; day/week/month calendar; audit log.

**Not implemented (Phase 2 remainder):** payments and refunds, reviews, SMS/push delivery providers, advanced reporting beyond utilization, and scoping `RESOURCE_OWNER` to their own resources (the role and permission hooks exist).
