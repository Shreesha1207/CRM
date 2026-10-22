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
cp .env.example .env    # then set DATABASE_URL to your PostgreSQL database
docker compose up --build
docker compose exec backend python -m app.seed      # optional demo data
open http://localhost:8080
```

The stack has no database container. The API connects to the PostgreSQL database in `DATABASE_URL` (a hosted one such as Railway's works; plain `postgresql://` URLs are fine) and applies the migrations when it starts.

### Option B: Local development

```bash
# 1. PostgreSQL: create a database and user named "booking" (password "booking"),
#    or set DATABASE_URL in backend/.env (see backend/.env.example).

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
| Super admin | `superadmin@example.com` | `superadmin123` |
| Admin | `admin@example.com` | `admin12345` |
| User  | `user@example.com`  | `user12345`  |

Roles follow a hierarchy. An admin can create and edit customers (role User). A super admin can also give any other role (admin, manager, staff, resource owner), change or delete admin accounts, and delete accounts at all; admins keep everything else. Nobody can make someone a super admin in the app, and a super admin's account can only be changed by its owner. Admins never choose anyone's password: a new account is e-mailed a link to set up its own (it stays *Invited*, and cannot sign in, until then), and for an existing one an admin can only send a password reset link. On a database seeded before the super admin existed, create one with `SUPER_ADMIN_PASSWORD=... python -m app.create_super_admin --email you@example.com` (it promotes the account if the e-mail is already registered).

The seed builds a small catalogue spanning several industries: a strength coach with a lunch break, a consultant who is off on Wednesdays, a meeting room, a tennis court at another location, and a 12-seat yoga class.

### E-mail

Out of the box, e-mails (booking confirmations, reminders, invitations) are only written to the backend's log. To really send them, put the mail server's details in `.env` next to `docker-compose.yml`, for example for Gmail (turn on 2-Step Verification, then create an *App Password*; a normal password is refused):

```
EMAIL_BACKEND=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=your-16-letter-app-password
SMTP_FROM=you@gmail.com
```

Restart with `docker compose up --build`, then check it sends:

```bash
docker compose exec backend python -m app.send_test_email --to you@gmail.com
```

It prints what is configured (never the password) and, if the server refuses, what usually fixes it. Port 587 (STARTTLS) and 465 (SSL) both work.

When an admin books for a customer:

- **who already has an account** is booked straight away and gets a booking confirmation e-mail (it says who booked it for them);
- **with a new e-mail address** is booked straight away too, as *pending*, so the time is held and shows on the calendar. The person is e-mailed a link to create their account and confirm it, which also sends their confirmation e-mail. If they decline, the booking is cancelled and comes off the calendar. If they never answer, it stays pending; the link works for `INVITATION_TTL_DAYS` days (default 7).
  Only the customer can confirm such a booking: the admin sees *Awaiting customer* with a **Resend invitation** button instead of Confirm. If the admin reschedules it, the old time is cancelled, the old link stops working and the customer is e-mailed a new link for the new time (to the admin it stays awaiting the customer). If an invitation expires, the booking stays pending: resend the invitation, reschedule it, or cancel it.

Booking e-mails (confirmed, moved, cancelled, reminders, and the invitation to a new customer) are HTML with a plain-text version. They say what was booked, when (with the time zone) and where, with an **Open in Google Maps** button, who booked it, and a **View booking** button. Each one is a separate conversation in Gmail (its own `Message-ID` and `X-Entity-Ref-ID`, and its own subject), so Gmail does not fold one into another as quoted text.

Confirmed and moved bookings are also a calendar invitation (a `text/calendar` part plus `invite.ics`), shaped like the ones Google Calendar sends, so Gmail, Outlook and Apple Mail show them with Yes / No / Maybe:

- the customer answers **Yes** and the booking is in their calendar. Google Calendar adds invitations by itself only from senders the customer knows (its default since December 2025, under *Settings → Event settings → Add invitations to my calendar*); otherwise it shows them in Gmail to answer;
- if the booking is rescheduled or reassigned, the customer gets a "Booking moved" e-mail showing the new and the old time, and the same calendar entry moves, so the old time disappears;
- if it is cancelled, a cancellation is sent that takes the entry out of their calendar.

Answering the invitation does not change the booking (the answer is e-mailed to `SMTP_FROM`); customers change or cancel it with View booking. A moved booking is a new booking row internally; all rows of one reschedule chain share the calendar entry's id (the first booking's id) and each move raises its revision.

To try it with Gmail, book for an address other than `SMTP_FROM`: Gmail may show no invitation for an event you organise yourself.

### Booking assistant

Customers get an **Assistant** link: a chat that books from a sentence, behind a (demo) paywall.

1. The customer types *"Book personal training with John next Tuesday at 10 for two hours"*.
2. The assistant passes what it understood (service "personal training", person "John", the date, 10:00, 120 minutes) to one `book` tool. The server matches the names against the catalogue: exact, or with typos. It never guesses an unclear name; instead it asks, with the real names as buttons. When the time isn't free, the assistant says why and offers the nearest free times as buttons. The prompt always lists the real catalogue, so the assistant doesn't invent service names.
3. It **books it straight away as a hold**: a `PENDING` booking that keeps the slot, so nobody else can take it, and sends no e-mail yet. All the usual rules are checked, through the same booking engine as the website, as that customer.
4. The chat shows a card with the same details as the booking e-mails (date and time with the time zone, the service and who with, where with an **Open in Google Maps** button, the price, a countdown) and **Confirm** / **Delete**. Confirm sends the normal confirmation e-mail and calendar invitation; Delete frees the time without any e-mail. A hold nobody answers is released after `CHAT_HOLD_MINUTES` (default 10), and a customer can hold at most `CHAT_MAX_OPEN_HOLDS` (default 2) at once.
5. Moving or cancelling an existing booking is only *offered* (a card with Confirm / Decline); nothing changes until the customer confirms. "Change that to Priya" right after a hold swaps the hold. Moving a 1-hour booking "for 2 hours" holds a new 2-hour session and offers to cancel the old one, because a move keeps a booking's length.

It also answers questions about the business from text an admin writes under **Admin → Settings → Booking assistant FAQ**.

**Setup.** One key runs everything: put a Groq API key (free, from [console.groq.com](https://console.groq.com)) in `.env`. Without it the chat says it isn't set up yet, and the rest of the app is unaffected.

```
GROQ_API_KEY=gsk_...
```

| Role | Setting | Default | |
| --- | --- | --- | --- |
| The model that talks to customers and calls the tools | `CHAT_MODEL` | `openai/gpt-oss-120b` | Stronger at multi-step requests and invents names far less than `openai/gpt-oss-20b`, and still fast on Groq. |
| How hard it thinks | `CHAT_REASONING_EFFORT` | `low` | `medium` or `high` are more accurate and use more tokens; empty leaves it out (for models without the option). |
| The policy guard (ALLOW / BLOCK) | `GUARD_TOPIC_MODEL` | `openai/gpt-oss-20b` | |
| The jailbreak pre-filter | `GUARD_INJECTION_MODEL` | `meta-llama/llama-prompt-guard-2-86m` | Leave empty to switch it off. |

**Groq's free tier** (as of October 2026; your account's Limits page has the exact numbers) gives each model a few thousand tokens a minute and a daily token allowance, roughly 30 requests a minute. Limits apply per model and per organisation, so extra keys don't add capacity. The model (gpt-oss-120b) and the policy guard (gpt-oss-20b) are different models, so each has its own limits. A customer message takes about 2 model calls (one `book`, `move` or `cancel` call, then the reply) and 2 guard checks, so the free tier carries **roughly 30–100 customer messages a day for the whole site** and 1–2 a minute: enough to develop and demo. To keep within it, the prompt and tools come first in every request (Groq doesn't count cached prompt tokens against the limits), only the last `CHAT_HISTORY_TURNS` (8) customer turns are sent, only the latest two with their tool results (older ones as plain text), and tool results are kept small. A rate limit that clears within 20 seconds is waited out (up to twice per request) instead of failing the message. When a limit is hit, the customer is told the assistant is busy (or has reached today's limit) and nothing is held; it isn't counted against them. For real traffic, Groq's paid Developer tier (add a card) raises the limits about 10x; nothing else changes.

`GROQ_BASE_URL` can point at any OpenAI-compatible server instead, e.g. a self-hosted Ollama (`http://ollama:11434/v1`, `CHAT_MODEL=gpt-oss:20b`, `GUARD_TOPIC_MODEL=gpt-oss:20b`, `GUARD_INJECTION_MODEL=` empty), so nothing leaves your servers.

**How it finds the booking you mean.** Every booking has a unique id, but customers never need it. When someone says "move my Thursday 9 PM yoga to Tuesday at 8", the assistant calls `move` with Thursday, around 9 PM and "yoga". The server finds that customer's matching bookings (typos allowed, in each booking's own time zone). One match is used; several make it ask which one, with buttons; none make it say so and show the bookings they do have. The move is then a card showing that booking, with the old time struck through, and nothing changes until Confirm.

**How it is kept on topic and safe.** The model can write code or anything else, and no instruction in a prompt can take that away, so the protection sits outside it:

- *Every message* passes rule checks (length, anything shaped like code) and Prompt Guard. Anything they stop never reaches the main model. Then comes the policy guard, a model that reads a written policy ("only this business's bookings") and must answer exactly `ALLOW` or `BLOCK`. To save time it runs while the main model reads the message, but nothing the model answers is stored, shown or acted on until the guard allows the message. A blocked message gets a fixed reply.
- *Every reply* (and every button label) passes the rule checks and the policy guard before it is shown. A blocked reply is never shown: the whole turn is thrown away, anything it held is released, and the customer gets the fixed reply. Replies are therefore shown once checked (progress steps stream while it works), not word by word.
- A guard that errors, times out or answers anything else counts as a block. Three blocked messages in 10 minutes pause the assistant for that customer for an hour and flag the conversation.
- The model never sees SQL, tables or other customers: its only access is six fixed tools that take names and times, return small JSON and run as the signed-in customer. The browser sends only the new message; the server keeps and replays the history, so nobody can plant fake turns.
- Per-customer limits: `CHAT_MESSAGES_PER_MINUTE` (10), `CHAT_DAILY_MESSAGE_LIMIT` (60), `CHAT_DAILY_TOKEN_LIMIT` (500k), 40 messages per conversation, 8 tool rounds per message, one reply at a time per conversation.

**Contacting the admin.** When a customer has a real problem the assistant can't solve (a double charge, a complaint, something that went wrong), they can pass it to the admin. Only the customer can start it, and only for a real problem:
- *Through the assistant.* When they report such a problem or insist on a person, it shows a card: **"Tell the admin what happened"**, with a box filled with its summary for the customer to check or rewrite, the 6-hour promise, and **Send to admin** / **No thanks**. If they only said "contact admin", it first asks what happened; a summary that says nothing about the problem is refused. Nothing is sent before they press Send.
- *After a failure.* When the assistant couldn't finish or was unavailable, a **Contact admin** button lets them describe what went wrong (20–500 characters).
- *Limits:* one open request per customer, at most `CHAT_HELP_REQUESTS_PER_WEEK` (3) a week, none while paused for blocked messages, and the description goes through the same checks as messages. Failures, thumbs-down and blocked messages on their own notify no one.

Once sent:
- The customer sees **"Your issue has been passed to the admin. They will contact you by e-mail or phone within 6 hours (by Tue 20 Oct, 2:23 AM)"** (`CHAT_HELP_RESPONSE_HOURS`, default 6).
- Admins, super admins and managers (permission `chat:review`) get it in the app and by e-mail, with the description, the customer's e-mail and phone, the due time and the **whole chat**.
- The admin console's **Help requests** page lists open requests ("due in 3h", "overdue by 40m"). Each request shows the problem, the contact details, the customer's upcoming bookings, and the whole chat as they saw it (including anything said since). **Mark resolved** takes an optional note. The customer sees it at the end of the chat they asked from ('The admin has resolved your request (…). Their note: "…"'), on the card they sent, and by e-mail. A request still open past its due time reminds the admins once.

Staff can read only chats that a customer sent this way, and every opening is written to the audit log. The chat page tells customers so.

**The paywall is a demo.** **Pay** just activates a `chat_subscriptions` row; nothing is charged. To take real payments, send **Pay** to a payment provider's checkout (e.g. Stripe Checkout) and set the row from its webhook (`status=ACTIVE` on payment, `CANCELLED` when the subscription ends).

**Where the time goes.** The backend log has one line per message, for example `Chat turn 3120 ms: guard in 180 ms, model 1250 ms (2900 in, 60 out tokens), guard policy 640 ms, book 35 ms, model 820 ms (...), guard out 410 ms; waited 10 s for rate limits`, so a slow reply can be traced to the model, the guards, a tool or Groq's rate limits.

**Checking it with the real model.** The unit tests use stand-ins and never touch the network. To see how the real model and guards behave on realistic and hostile conversations ("reverse a linked list in Python", role-play, prompts hidden in base64, two people with the same name, another language...), run the eval. It uses part of the day's free Groq tokens (it pauses between cases to stay inside the per-minute limits; `--pause` sets how long), runs on a throwaway `booking_eval` database, and reports leaks (must be 0), wrongly blocked booking messages, how well it found existing bookings, timings and tokens used:

```bash
# a throwaway PostgreSQL for it (or `createdb booking_eval` on a local PostgreSQL)
docker run -d --name booking-eval-db -e POSTGRES_USER=booking -e POSTGRES_PASSWORD=booking -e POSTGRES_DB=booking_eval -p 5433:5432 postgres:16
# in backend/.env, next to GROQ_API_KEY:
#   EVAL_DATABASE_URL=postgresql+psycopg://booking:booking@localhost:5433/booking_eval
cd backend && python -m app.chat_eval
```

### Tests

```bash
cd backend && pytest            # 242 tests, against a real PostgreSQL database
cd frontend && npm test         # unit tests for prices, booking lengths and the chat's event stream
cd frontend && npm run build    # type-check + production build
```

The tests use the `booking_test` database by default; override it with `TEST_DATABASE_URL`. They ignore `DATABASE_URL` on purpose: they drop and rebuild the schema (by running the Alembic migrations, so the migration itself is tested too), which must never happen to a real database.

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
    chat/                the booking assistant: guards, model, tools, held bookings, one turn
  api/routes/  auth, catalog (public), bookings, notifications, chat, admin_*
  jobs/        background loop (single-runner via PostgreSQL advisory lock)
  seed.py      demo data
```

### Frontend layout

```
frontend/src/
  api/          fetch client (CSRF header, typed errors) + TypeScript API types
  auth/         session context and route guards
  components/   UI kit, layout, slot picker, booking widgets
  pages/        public, account (dashboard, bookings, profile), book (wizard), chat (assistant + demo paywall)
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

**Process settings** are environment variables; see `backend/.env.example`. The main ones are `DATABASE_URL`, `JWT_SECRET`, `COOKIE_SECURE`, `CORS_ORIGINS`, `FRONTEND_URL`, `EMAIL_BACKEND` and `SMTP_*`, and for the booking assistant `GROQ_API_KEY`, `CHAT_MODEL` and `GUARD_*`. `docker compose` takes `DATABASE_URL`, `JWT_SECRET`, `EMAIL_BACKEND`, the `SMTP_*` values and the assistant's settings (`GROQ_API_KEY`, `CHAT_MODEL`, `GUARD_*`) from `.env` in the repository root (see `.env.example`).

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
| Booking assistant | `GET/POST/DELETE /api/chat/subscription` (demo paywall), `GET/POST /api/chat/conversations`, `GET /api/chat/conversations/:id`, `POST /api/chat/conversations/:id/messages` (server-sent events: `status`, then the checked `reply`), `POST /api/chat/proposals/:id/confirm · decline`, `POST /api/chat/conversations/:id/help` (contact the admin), `POST /api/chat/messages/:id/feedback` |
| Admin: help requests | `GET /api/admin/help-requests?status=open\|resolved`, `GET /api/admin/help-requests/count`, `GET /api/admin/help-requests/:id` (audited), `POST /api/admin/help-requests/:id/resolve` |
| Booking invitations | `GET /api/invitations/:token`, `POST /api/invitations/:token/accept · decline` (public; the e-mailed token is the credential) |
| Admin: bookings | `GET/POST /api/admin/bookings`, `POST /api/admin/bookings/invite` (book for an e-mail with no account: the booking goes on the calendar as pending and the person is e-mailed a link to create an account and confirm it; declining cancels it), `GET/PUT /api/admin/bookings/:id`, `POST …/:id/cancel · reschedule · reassign · confirm · complete · no-show`, `GET …/:id/history` |
| Admin: conflicts | `GET /api/admin/conflicts[/:id]`, `POST /api/admin/conflicts/:id/resolve` |
| Admin: catalogue | `…/resources`, `…/resources/:id/services`, `…/resources/:id/availability`, `…/services`, `…/locations` |
| Admin: schedules | `GET/PUT /api/admin/operating-hours`, `GET/POST/DELETE /api/admin/exceptions` |
| Admin: system | `GET /api/admin/dashboard · calendar · reports/utilization · audit-logs`, `GET/POST/PUT /api/admin/users`, `DELETE /api/admin/users/:id` (super admin), `GET/PUT /api/admin/settings` |

Schedule-changing admin endpoints accept `?dry_run=true&conflict_action=keep|mark_conflicted|cancel`.

Naive datetimes (e.g. `"2026-09-28T10:00"`) are wall-clock time at the resource's location. Datetimes with an offset are taken as absolute instants.

Bookings, recurring bookings and alternatives take an optional `duration_minutes` for services with a flexible length; without it a booking gets the service's standard length.

---

## Scope

**Implemented (MVP and most of Phase 2):** authentication; users and admin with RBAC; resources with JSON metadata; services; resource-service overrides; operating hours; weekly availability; exceptions and special hours; dynamic slots; individual and capacity bookings; buffers; notice and advance windows; clash prevention (application and database); rescheduling; cancellation policy; waitlists; recurring bookings with per-occurrence validation; multi-resource bookings (atomic); schedule-change previews and conflict resolution; alternative suggestions; a customers' booking assistant (chat that books from a sentence, with guards outside the model and a demo paywall); multiple locations and timezones, each location linking to Google Maps (its own link, or a search for its address) on bookings, resource pages and booking e-mails; in-app and e-mail notifications with reminders; admin dashboard with utilization; day/week/month calendar; audit log.

**Not implemented (Phase 2 remainder):** payments and refunds (the assistant's paywall is a demo), reviews, SMS/push delivery providers, advanced reporting beyond utilization, and scoping `RESOURCE_OWNER` to their own resources (the role and permission hooks exist).
