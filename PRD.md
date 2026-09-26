# Product Requirements Document — Generic Booking & Appointment Management System

| | |
| --- | --- |
| **Product** | Generic Booking & Appointment Management System |
| **Status** | MVP + Phase 2 largely built and passing end-to-end tests (see §12) |
| **Source spec** | *Generic Booking & Appointment Management System — Resource Document* (73 sections, provided by the product owner) |
| **Codebase** | `backend/` (FastAPI + PostgreSQL), `frontend/` (React + TypeScript) |
| **Related docs** | `docs/system-architecture.md`, `docs/database-model.md`, `docs/functional-overview.md`, `docs/user-stories.md`, `docs/e2e-test-results.md` |

## 1. Problem statement

Every business that takes reservations — a clinic, a gym, a salon, a meeting-room desk, a tennis club, a photography studio, a tutor — rebuilds the same scheduling logic: who can book what, when it's free, how to stop double-booking, how to handle cancellations and no-shows. That logic is usually hard-coded per industry, which makes it expensive to adapt and easy to get subtly wrong (timezones, daylight saving, concurrent bookings).

## 2. Vision and design principle

Build **one** booking engine that never asks an industry-specific question ("Is the doctor free?", "Is the room free?"). It only ever answers:

> **Is RESOURCE X available for SERVICE Y between START and END?**

Everything industry-specific — what a "resource" is, what attributes it has, what services it offers, its hours — is *configuration*, not code. A doctor, a trainer, a meeting room, a tennis court and a camera kit are all just `Resource` rows with a `type`; the booking engine treats them identically.

## 3. Goals

- G1 — One reusable booking engine that serves many industries with no code changes, only catalogue/config changes.
- G2 — Never double-book a resource, under any amount of concurrent load, with the database as the final backstop.
- G3 — Correct behavior across timezones and daylight-saving transitions, independent of the server's own clock.
- G4 — Admins can reconfigure booking rules (notice periods, buffers, waitlists, recurring limits…) at runtime, without a deploy.
- G5 — Every rule the backend enforces is also enforced if the frontend is bypassed (curl, DevTools, a modified request).
- G6 — Every administrative action that bends a rule is audited.

## 4. Non-goals (out of scope for this product)

- Payments, invoicing, refunds.
- Reviews/ratings.
- Real SMS or push notification delivery (the channels are modelled and skipped, not sent — see §11).
- Advanced BI-style analytics beyond the utilization report described in §8.9.

## 5. Target users / personas

| Persona | Role in system | Primary needs |
| --- | --- | --- |
| **Customer** | `USER` | Find a service/resource, see real availability, book, change their mind (reschedule/cancel) within the rules, get notified. |
| **Front-desk / staff** | `STAFF` (reserved role, permission-mapped) | See and manage all bookings, confirm pending requests, mark no-shows, without full admin access. |
| **Business admin** | `ADMIN` | Configure the catalogue (locations, resources, services), set hours and rules, resolve schedule conflicts, manage users, watch utilization. |
| **Platform owner** | `SUPER_ADMIN` | Everything an admin can, plus create other `SUPER_ADMIN` accounts. |
| **Reserved, pre-mapped roles** | `RESOURCE_OWNER`, `MANAGER` | Not exposed in the UI yet, but the permission system already has a slot for them (see §6), so they can be turned on later with no schema change. |

## 6. Roles and permissions

Routes check **permissions**, not role names (`backend/app/core/permissions.py`), so a new role only needs an entry in one map.

| Permission | USER | STAFF | MANAGER | ADMIN / SUPER_ADMIN | RESOURCE_OWNER |
| --- | --- | --- | --- | --- | --- |
| `bookings:view_all` | – | ✓ | ✓ | ✓ | ✓ |
| `bookings:manage` | – | ✓ | ✓ | ✓ | – |
| `bookings:override_rules` | – | – | ✓ | ✓ | – |
| `conflicts:manage` | – | – | ✓ | ✓ | – |
| `resources:manage` / `services:manage` / `schedules:manage` / `locations:manage` | – | – | ✓ | ✓ | – |
| `users:view` | – | – | ✓ | ✓ | – |
| `users:manage` | – | – | – | ✓ | – |
| `settings:manage` | – | – | – | ✓ | – |
| `reports:view` | – | ✓ | ✓ | ✓ | – |
| `audit:view` | – | – | ✓ | ✓ | – |

Rules that hold regardless of role:
- Self-registration always creates a `USER` — a client can never grant itself `ADMIN` in the request body.
- Only a `SUPER_ADMIN` may create another `SUPER_ADMIN`.
- An admin can never change their own role or suspend their own account.
- A user may only see or modify **their own** bookings; another user's booking returns `404`, not `403`, so ids can't be probed.

## 7. Core domain model

```
USER --books--> SERVICE --provided using/by--> RESOURCE --has--> AVAILABILITY --produces--> SLOT --becomes--> BOOKING
```

- **Resource** — anything reservable: a person, room, facility, piece of equipment, vehicle, desk, court, or a custom type. Industry-specific attributes live in a free-form `metadata` JSON field, not extra columns.
- **Service** — what the customer actually books (a duration, a price, individual or capacity-based).
- **Resource ↔ Service** — many-to-many, with optional per-resource overrides (a different price or duration for the same service on a different resource).
- **Availability** — the intersection of business hours, a resource's own weekly schedule, and any date-specific exceptions (blocks, holidays, special hours), minus existing bookings.
- **Booking** — the reservation itself, with a full status lifecycle (see `docs/database-model.md` and `docs/system-architecture.md`).

Full field-level detail is in `docs/database-model.md`.

## 8. Functional requirements

Each item cites the spec section it satisfies and its build status. All are implemented unless noted.

### 8.1 Authentication & accounts (spec §4)
- FR-1 Register, log in, log out, forgot/reset password, change password, edit profile — all self-service for customers.
- FR-2 Passwords hashed with argon2id; never stored or logged in plain text.
- FR-3 A suspended or inactive account cannot sign in, and an open session for a suspended user is cut on its next request.
- FR-4 A password reset link expires (`PASSWORD_RESET_TTL_MINUTES`) and can be used only once; changing a password revokes every other active session.

### 8.2 Authorization (spec §5)
- FR-5 Every admin endpoint independently re-checks permissions server-side; hiding a button in the UI is never the only protection.
- FR-6 CSRF double-submit token required on every cookie-authenticated, state-changing request; API/Bearer clients are exempt (they can't be forged by a browser).

### 8.3 Catalogue: locations, resources, services (spec §6–10, §45)
- FR-7 CRUD for locations (name, address, IANA timezone), resources (name, type, description, capacity, free-form metadata, location), and services (name, duration, price, capacity, booking type, buffers).
- FR-8 Resource↔service linking with per-link overrides (`custom_price`, `custom_duration`, `custom_buffer_before/after`).
- FR-9 Deleting a resource/service/location with booking history **deactivates** it instead of deleting the row (spec: "resources with historical bookings should generally be deactivated rather than physically deleted"); one with no history is deleted outright.

### 8.4 Availability & slot generation (spec §12–18)
- FR-10 `AVAILABLE = operating hours ∩ resource weekly availability − blocks/holidays/maintenance − existing bookings (with buffers)`.
- FR-11 Multiple availability periods per day, with optional `valid_from`/`valid_until` windows and "break" rows that carve gaps out of otherwise-available time.
- FR-12 Date-specific `SPECIAL_HOURS` exceptions **replace** the recurring schedule for the dates they cover; blocking exceptions (`BLOCKED`, `UNAVAILABLE`, `HOLIDAY`, `MAINTENANCE`) **remove** time from it.
- FR-13 Slots sit on a fixed grid (the configured `slot_interval`, or the service duration when unset); only slots that fit the whole service duration are offered.
- FR-14 The same function that generates slots for display also validates a booking attempt, so the UI can never offer a slot the engine would then reject.

### 8.5 Booking creation & validation (spec §19–28, §60)
- FR-15 A booking passes every rule (hours, availability, notice, advance window, blocks, clashes, capacity) again at the moment it is written, not only when the slot was first shown.
- FR-16 Clash detection uses half-open interval overlap (`newStart < existingEnd AND newEnd > existingStart`); touching bookings do not clash.
- FR-17 Configurable buffer time before/after a booking is treated as occupied time for clash purposes.
- FR-18 Configurable minimum notice and maximum advance-booking window.
- FR-19 An admin may override rules (hours, notice, blocks) with `override_rules`; overriding never bypasses the clash or capacity check, and every override is audited.

### 8.6 Capacity & waitlist (spec §31–32)
- FR-20 Capacity-based services (e.g. a class) accept many bookings up to a configurable capacity per session; individual services allow exactly one.
- FR-21 A user may request more than one place (`quantity`) up to the remaining capacity.
- FR-22 When a session is full, a booking made with `join_waitlist` becomes `WAITLISTED`, in first-come-first-served order, and holds no capacity.
- FR-23 A cancellation promotes the next waitlisted booking(s) automatically, inside the same locked transaction, so a freed seat can never go to two people.
- FR-24 The waitlist can be switched off entirely (`allow_waitlist`).

### 8.7 Reschedule & cancellation (spec §29–30)
- FR-25 Rescheduling creates a linked **replacement booking** (`rescheduled_from_id`); the original is marked `RESCHEDULED` only after the new slot is successfully validated and written, in the same transaction — a failure leaves the original untouched.
- FR-26 Configurable cancellation and rescheduling windows (minutes before start); an admin can act past the window, audited.
- FR-27 Cancelled bookings are kept, never deleted, for history and audit.
- FR-28 A booking that has started or finished cannot be rescheduled or cancelled by its owner (`COMPLETED`/`NO_SHOW`/past-start states are terminal for self-service).

### 8.8 Recurring bookings (spec §34)
- FR-29 Daily/weekly/monthly recurring bookings with a configurable interval and occurrence count (capped by `max_recurring_occurrences`).
- FR-30 Each occurrence is validated independently; a preview shows which occurrences are available before anything is booked.
- FR-31 The customer chooses to book only the available occurrences, or the whole request fails atomically (`skip_conflicts=false`) — never a silent partial series.
- FR-32 Monthly recurrence on a day that doesn't exist in a shorter month clamps to that month's last day.
- FR-33 Recurring bookings can be switched off entirely (`allow_recurring_bookings`).

### 8.9 Multi-resource bookings (spec §33)
- FR-34 A single booking can reserve a primary resource plus additional resources (e.g. a photographer + a studio + a camera kit) for the same time window.
- FR-35 All resources are reserved or none — atomic, all-or-nothing — so a failure never leaves a resource half-reserved.
- FR-36 Extra resources are held for the whole session and clash-checked exactly like the primary resource.

### 8.10 Admin booking management & dashboard (spec §38–41)
- FR-37 Admin dashboard shows upcoming/today's/pending/completed/cancelled bookings, conflict count, total users, active resources and per-resource utilization.
- FR-38 Admins can view, create (with search-by-customer), edit notes on, confirm, cancel, reschedule, reassign, mark completed and mark no-show any booking, each entry timestamped in a per-booking history.
- FR-39 Calendar with day/week/month views, filterable by resource, service, status and location; overlapping entries render side by side, not stacked.
- FR-40 Optional `require_admin_confirmation`: new bookings start `PENDING` until an admin confirms them.

### 8.11 Schedule changes & conflict resolution (spec §35–36)
- FR-41 Any admin change that can shrink availability (resource/location hours, a new block, resource deactivation, a location timezone change) first runs as a **dry run** and reports exactly which existing bookings it would affect, before anything is written.
- FR-42 The admin then chooses: keep the affected bookings as-is, mark them `CONFLICTED`, or cancel them.
- FR-43 Conflicts are resolved from a dedicated Conflicts screen: reschedule (with suggested alternative slots — spec §37), reassign to another resource, cancel, or override and keep the booking as is.

### 8.12 Notifications & reminders (spec §42–43)
- FR-44 Domain events (created, confirmed, cancelled, rescheduled, reassigned, waitlist-promoted, conflicted) each produce an in-app message and a queued e-mail, written in the same transaction as the booking change.
- FR-45 Configurable reminder offsets (default 24 h and 1 h before start); each reminder carries a unique dedupe key so it is never queued twice, even with multiple API replicas running the background job.
- FR-46 In-app, e-mail, SMS and push are modelled channels; SMS/push have no delivery provider and are recorded as skipped rather than attempted (Phase-2 item, see §10).

### 8.13 Timezones & multi-location (spec §44–45)
- FR-47 All instants stored in UTC; each location has an IANA timezone, and every wall-clock value (weekly rules, a picked date, a naive booking time) is interpreted in the resource's own location timezone — never the server's OS timezone.
- FR-48 Slot generation is correct across daylight-saving transitions (no phantom or missing hour) and weekly recurring bookings keep their local wall-clock time across a DST change.
- FR-49 Multiple locations, each with its own timezone and (optionally) its own operating hours.

### 8.14 Search & filters (spec §63)
- FR-50 Customers filter services/resources by name, type, location and price. Admins additionally filter bookings by status, customer, resource, service, date range and location.

### 8.15 Deployment (spec §68–69)
- FR-51 Runs as three containers (PostgreSQL, API, static frontend behind nginx) via `docker compose up --build`, or as two local dev processes (`uvicorn`, `vite`).
- FR-52 Schema managed by Alembic migrations, verifiable with `alembic check`.

## 9. Non-functional requirements

| Area | Requirement | Where enforced |
| --- | --- | --- |
| **Correctness under concurrency** | No resource is ever double-booked, including under simultaneous requests. | `SELECT … FOR UPDATE` on resource rows (sorted by id to avoid deadlocks) + a PostgreSQL `EXCLUDE USING gist` constraint as a database-level backstop that holds even if application code is bypassed. |
| **Security** | See spec §65 in full. | argon2id hashing; JWT backed by a revocable server-side session; httpOnly cookie + CSRF token (or Bearer for API clients); RBAC by permission; per-route rate limiting; pydantic validation on every input; ORM-only queries (no raw SQL injection surface); secure response headers (CSP, `nosniff`, `DENY`, HSTS over HTTPS); refuses to start in production with a default secret or without secure cookies. |
| **Auditability** | Every booking change, schedule change and admin override is recorded. | `audit_logs` table, written in the same transaction as the change. |
| **Timezone correctness** | See FR-47–48. | `zoneinfo`-based conversion at every boundary; no reliance on server locale. |
| **Configurability / reusability** | Business rules are data, not code (spec §64). | `settings` table + Admin → Settings UI; see the rules table in `docs/functional-overview.md`. |
| **Availability of the engine's own decision** | Slot listing and booking validation must never disagree. | Both paths call the same `ResourceSnapshot.check()` function. |

## 10. Explicitly deferred (Phase 2 items not yet built)

The following are named in the spec's Phase 2 list but have no implementation, by design — they are outside this build's scope:
- Payments, refunds.
- Reviews.
- Real SMS/push delivery (channels exist in the data model, no provider is wired up).
- Advanced/BI-style reporting beyond the per-resource utilization endpoint already built.

Everything else in the spec's MVP **and** Phase 2 lists (§70–71) — notifications, waitlists, multiple locations, recurring bookings, multi-resource bookings, automated reminders — is built.

## 11. Success criteria / Definition of Done

The spec's Definition of Done (§73) asks whether the system can reliably answer, for every booking: **who, what, which resource, where, when (start/end), is it available, is there capacity, does it clash, can it be created/cancelled/rescheduled safely.** This is verified by:
- 70 automated backend tests (`backend/tests/`, run against a real PostgreSQL database, including concurrency tests that fire simultaneous requests at one slot).
- A 164-case, 13-area manual/scripted end-to-end test plan covering every functional area in this document (`docs/e2e-test-results.md`), executed twice: once driving the API directly, once driving only the browser UI with no direct API calls from the test harness.
- All four defects found by the first full pass (a reminder-job lock leak, a reschedule-picker restriction, a missing end-date on overnight bookings, a stray waitlist button) have been fixed and re-verified.

## 12. Known open issues

Two low-severity UI issues were found in a later browser-driven retest and are not yet fixed:
1. **Operating-hours save confirmation** flashes for well under a second before disappearing (the form remounts on refetch and loses its "saved" flag) — the save itself works.
2. **A newly created service is visible to customers before any resource offers it** — its public page lists no resource and the booking wizard has nothing to book it with, until an admin links it to a resource.

Neither affects data integrity or the booking rules themselves. See `docs/functional-overview.md` §"Known limitations" for details and suggested fixes.
