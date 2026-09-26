# User Stories — Generic Booking & Appointment Management System

Format: **As a** `<role>`, **I want to** `<capability>`, **so that** `<benefit>`, with concrete acceptance criteria drawn from the actual implemented and tested behavior (status codes, messages and UI text are what the app really returns — see `docs/e2e-test-results.md` for the executed test cases these are traceable to). Roles: **Customer** (`USER`), **Admin** (`ADMIN`/`STAFF` where noted), **Platform** (non-human, operational).

## Contents
1. Account & authentication
2. Authorization & security
3. Browsing services and resources
4. Checking availability & making a booking
5. Group sessions, capacity & waitlist
6. Rescheduling & cancellation
7. Recurring bookings
8. Multi-resource bookings
9. Admin: booking management & dashboard
10. Admin: calendar
11. Admin: schedule changes & conflict resolution
12. Admin: catalogue & settings administration
13. Notifications & reminders
14. Timezones & multi-location
15. Deployment & operations

---

## 1. Account & authentication

**US-1.1** As a **visitor**, I want to register with my name, e-mail and a password, so that I can start booking.
- Acceptance: e-mail is stored lower-case; the account is created with role `USER` even if I try to send a different role in the request; on success I land on my dashboard.

**US-1.2** As a **visitor**, I want a clear error if I try to register with an e-mail already in use, so that I don't create a duplicate account.
- Acceptance: "An account with this e-mail already exists"; no second account is created.

**US-1.3** As a **customer**, I want to sign in and out, so that my session is my own.
- Acceptance: wrong password and unknown e-mail give the identical message ("Invalid e-mail or password") — nothing reveals which one was wrong; signing out ends the session server-side, so a previously-copied access token stops working immediately.

**US-1.4** As a **customer**, I want to reset a forgotten password by e-mail, so that I'm not locked out.
- Acceptance: requesting a reset always shows the same confirmation, whether or not the e-mail exists (no account enumeration); the reset link works once and expires after a configured time; using an expired or already-used link shows "This reset link is invalid or has expired"; a successful reset signs out every other active session for that account.

**US-1.5** As a **customer**, I want to change my password from my profile, so that I can rotate it without an e-mail round-trip.
- Acceptance: requires my current password; on success, every *other* signed-in session is revoked, but this one stays signed in.

**US-1.6** As a **customer**, I want to update my name/phone, so that my profile stays current.

**US-1.7** As an **admin**, I want a suspended account to be blocked from signing in immediately, so that I can react to an incident.
- Acceptance: sign-in shows "This account is not active"; a tab the user already had open is signed out on its next request, not just on next login.

## 2. Authorization & security

**US-2.1** As the **platform**, I want every admin permission enforced on the server, not just hidden in the UI, so that a modified request can never grant access.
- Acceptance: calling any `/api/admin/*` endpoint as a plain `USER` returns `403`, regardless of what the frontend shows.

**US-2.2** As a **customer**, I want other people's bookings to be invisible to me, so that my booking details are private.
- Acceptance: opening another user's booking, or calling cancel/reschedule on it, returns `404 Not Found` — never `403` — so a booking id can't be used to fingerprint that it exists.

**US-2.3** As the **platform**, I want cookie-authenticated writes to require a matching CSRF token, so that another site can't perform actions using a signed-in user's cookies.
- Acceptance: a replayed write with the cookie but no `X-CSRF-Token` header returns `403 "CSRF token missing or invalid"`.

**US-2.4** As the **platform**, I want repeated failed logins from one source rate-limited, so that password guessing is impractical.
- Acceptance: the 11th failed attempt within 5 minutes from the same client returns `429` with a `Retry-After` header.

**US-2.5** As the **platform**, I want passwords hashed, never stored or logged in plaintext, so that a database leak doesn't expose credentials.
- Acceptance: `password_hash` starts with `$argon2id$`.

**US-2.6** As the **platform**, I want to refuse to start in production with an unsafe configuration, so that an operator can't accidentally deploy insecurely.
- Acceptance: starting with `ENVIRONMENT=production` and the default `JWT_SECRET`, or without `COOKIE_SECURE`, exits non-zero with a clear message.

## 3. Browsing services and resources

**US-3.1** As a **visitor**, I want to browse and search services (by name, price, location), so that I can find what I need before signing in.

**US-3.2** As a **visitor**, I want to see which resources offer a service, with location and price, so that I can pick who or what to book.

**US-3.3** As a **visitor**, I want a resource's page to show its weekly hours and any custom attributes (e.g. specialization, floodlights), so that I know what I'm booking.
- Acceptance: attributes come from the resource's free-form metadata, so this works the same for a trainer's "Specialization" and a court's "Floodlights" with no code difference.

## 4. Checking availability & making a booking

**US-4.1** As a **customer**, I want to see real, live availability for a service and resource on a given date, so that I never see a slot I then can't book.
- Acceptance: the same rule engine that renders the slot grid also validates the booking at submit time, so a slot shown as available is genuinely bookable barring a race with another customer.

**US-4.2** As a **customer**, I want unavailable slots visibly marked with *why* (taken, blocked, too soon, past, outside hours), so that I understand my options.
- Acceptance: labels observed in the wizard: "Conflict", "Blocked", "Past", "Too Soon", "Too Far Ahead", "Capacity Reached".

**US-4.3** As a **customer**, I want to book a resource for a service at a chosen time, so that the time is reserved for me.
- Acceptance: on success I land on the booking's detail page ("You're booked!"), status `Confirmed`, and it appears under My Bookings → Upcoming and as my dashboard's next booking.

**US-4.4** As a **customer**, I want a booking that overlaps an existing one — even partially — rejected, so that nobody is double-booked.
- Acceptance: `409 CONFLICT — "This time overlaps another booking"`; a booking that ends exactly when mine starts (back-to-back, no overlap) succeeds.

**US-4.5** As a **customer**, I want buffer time around a booking (e.g. clean-up time) to also block the following slot, so that the real-world gap is respected.

**US-4.6** As a **customer**, I want to be stopped from booking too close to the start, too far in the future, in the past, outside operating hours, or on a resource's day off, so that only genuinely valid times can be chosen.
- Acceptance codes: `TOO_SOON`, `TOO_FAR_AHEAD`, `PAST`, `OUTSIDE_OPERATING_HOURS`, `OUTSIDE_AVAILABILITY` — each configurable or resource-specific, never hard-coded.

**US-4.7** As a **customer**, I want "any available" resource assignment when I don't care who/what I get, so that I can book faster.
- Acceptance: the system assigns whichever qualifying resource is free; if none is, I see "No resource is available at that time" rather than a confusing partial result.

**US-4.8** As a **customer**, I want to be offered alternative times/resources if my chosen slot is taken by someone else between viewing and confirming, so that I'm not just stuck.
- Acceptance: a race-lost booking shows "This time overlaps another booking" plus a list of nearby alternatives I can pick with one click.

## 5. Group sessions, capacity & waitlist

**US-5.1** As a **customer**, I want to join a group session (e.g. a class) alongside other people, up to its capacity, so that shared resources aren't limited to one booking at a time.
- Acceptance: the slot shows remaining places ("11 left"); I can request more than one place at once, up to what's left.

**US-5.2** As a **customer**, I want to join a waitlist when a session is full, so that I get in automatically if a place opens.
- Acceptance: I see my position ("You're #1 on the waitlist"); the next cancellation promotes the earliest waitlisted person automatically and notifies them; promotions are strictly ordered even if two seats free up at once.

**US-5.3** As an **admin**, I want to be able to turn the waitlist off, so that a full session is simply full.
- Acceptance: with the waitlist disabled, a join-waitlist request returns `409 CAPACITY_REACHED`, and the UI does not offer a waitlist action.

**US-5.4** As a **customer**, I want to be prevented from booking the same session twice, so that I can't accidentally take two of my own places.
- Acceptance: `409 ALREADY_BOOKED`.

## 6. Rescheduling & cancellation

**US-6.1** As a **customer**, I want to move my booking to a new time, so that I don't have to cancel and rebook.
- Acceptance: the original becomes `Rescheduled` and links to the new booking ("See the new booking"); the new booking links back ("Moved from"); the old slot becomes bookable by others immediately.

**US-6.2** As a **customer**, I want to be able to move a booking to a time that's less than its own duration away from its current time (e.g. 30 minutes for a 30-minute slot), so that a small adjustment doesn't get wrongly treated as a clash with myself.
- Acceptance: the reschedule picker excludes the booking's own current time from the clash check and marks it "Current" rather than "Conflict".

**US-6.3** As a **customer**, I want a failed reschedule attempt to leave my original booking completely untouched, so that I never lose a reservation trying to change it.
- Acceptance: on `409`, the original is still `Confirmed` at its original time, verified even under a database failure injected mid-operation.

**US-6.4** As a **customer**, I want to cancel a booking and optionally give a reason, so that I free the slot for others.
- Acceptance: status becomes `Cancelled` with the reason and time recorded; the record is kept, not deleted; the slot is immediately bookable again.

**US-6.5** As a **business owner**, I want configurable cancellation/rescheduling windows, so that last-minute changes can be restricted.
- Acceptance: inside the window the buttons are disabled for the customer and the API returns `409`; an admin can still act, and it's audited (`ADMIN_OVERRIDE`).

**US-6.6** As a **customer**, I want to be prevented from changing a booking that has already completed or been marked no-show, so that history stays accurate.

## 7. Recurring bookings

**US-7.1** As a **customer**, I want to book a recurring series (daily/weekly/monthly), so that I don't have to book each occurrence separately.
- Acceptance: a preview shows each occurrence and whether it's available before anything is created; a struck-through occurrence shows why (e.g. a holiday).

**US-7.2** As a **customer**, I want to book only the available occurrences of a series when some conflict, so that one bad date doesn't block the rest.
- Acceptance: "Book N available dates" creates exactly those, all sharing one series id; none are created on the conflicting date.

**US-7.3** As a **customer**, I want an explicit request for the whole series to fail entirely if any occurrence conflicts (rather than silently booking a partial series), so that I always know exactly what got booked.
- Acceptance: `409 RECURRING_CONFLICTS` naming the conflicting date(s); zero bookings are created.

**US-7.4** As an **admin**, I want to cap how many occurrences a recurring request can create, and to be able to disable recurring bookings entirely, so that I can control load and fit my business's needs.

## 8. Multi-resource bookings

**US-8.1** As a **customer/admin (via API)**, I want to book several resources together for one session (e.g. a photographer, a studio and a camera kit), so that a session that genuinely needs multiple things reserves all of them at once.
- Acceptance: the booking's detail lists every held resource ("Photographer + Studio, Camera Kit").

**US-8.2** As a **customer**, I want a multi-resource booking to succeed or fail as a whole, never partially, so that I'm never left holding some resources but not others.
- Acceptance: if any extra resource clashes, the whole request is rejected (naming which resource clashed) and nothing is reserved — verified by then successfully booking the primary resource alone at the same time.

## 9. Admin: booking management & dashboard

**US-9.1** As an **admin**, I want a dashboard of upcoming/today's/pending/cancelled/completed bookings, active resources, total users, conflicts and per-resource utilization, so that I have an at-a-glance operational view.

**US-9.2** As an **admin/staff member**, I want to search and filter all bookings by status, resource, service, location, date range and customer, with pagination, so that I can find any booking quickly.

**US-9.3** As an **admin**, I want to create a booking on behalf of a customer, including overriding the normal rules when necessary, so that I can handle phone/walk-in bookings and edge cases.
- Acceptance: an override is still blocked from ever double-booking a resource, and is written to the audit log.

**US-9.4** As an **admin**, I want to confirm, cancel, reschedule, reassign, mark completed and mark no-show any booking, with a visible history of every change, so that I have full operational control with accountability.
- Acceptance: "mark completed"/"no-show" are only available once a booking's start time has passed; each action appends a timestamped, attributed entry to that booking's history.

**US-9.5** As an **admin**, I want to leave internal notes on a booking that the customer doesn't see, so that staff can coordinate.

## 10. Admin: calendar

**US-10.1** As an **admin**, I want day/week/month calendar views of all bookings, color-coded by resource, so that I can see the schedule at a glance.
- Acceptance: pending bookings render dashed, conflicted bookings render red, blocked periods render hatched with their reason, and overlapping entries render side by side rather than stacked illegibly.

**US-10.2** As an **admin**, I want to filter the calendar by resource, service, status and location, so that I can focus on what matters right now.

## 11. Admin: schedule changes & conflict resolution

**US-11.1** As an **admin**, I want to see, before I save, exactly which existing bookings a hours/availability/block change would affect, so that I never make a change blind.
- Acceptance: "This change affects N existing bookings" with the list and each one's specific reason, computed as a dry run — nothing is written until I choose what to do next.

**US-11.2** As an **admin**, when a change does affect bookings, I want to choose to keep them, mark them as conflicts, or cancel them, so that I control the outcome rather than the system deciding for me.

**US-11.3** As an **admin**, I want a dedicated Conflicts screen listing every booking a schedule change invalidated, so that nothing gets forgotten.
- Acceptance: each conflict shows the reason and can be resolved by rescheduling (with suggested alternative slots), reassigning to another resource, cancelling, or overriding to keep it as-is — each option resolves the conflict and is audited.

**US-11.4** As an **admin**, I want deleting a resource, service or location that has booking history to deactivate it instead of destroying the data, so that historical bookings and reports stay intact.
- Acceptance: I'm warned ("Resources with booking history are deactivated instead") before it happens; a resource with *no* history is genuinely deleted.

## 12. Admin: catalogue & settings administration

**US-12.1** As an **admin**, I want to create and edit locations (name, address, timezone), so that I can model multiple branches.

**US-12.2** As an **admin**, I want to create resources with a type and free-form custom attributes, so that the same system fits a trainer's specialization or a court's surface without asking a developer for a new field.

**US-12.3** As an **admin**, I want to link services to resources with optional per-link overrides (price, duration, buffers), so that the same service can cost or take different amounts of time depending on who provides it.

**US-12.4** As an **admin**, I want to set a resource's weekly availability, including multiple periods and a break, and have a resource with no rules fall back to business hours, so that I'm never forced to configure something I don't need.

**US-12.5** As an **admin**, I want to create date-specific blocks, holidays, maintenance windows and special hours — scoped to one resource, one whole location, or everything — so that one-off changes don't require touching the recurring schedule.
- Acceptance: special hours *replace* the day's normal schedule; the other types *remove* time from it.

**US-12.6** As an **admin**, I want to manage user accounts (create staff, change roles/status, set a password), with guardrails against creating a `SUPER_ADMIN` I'm not authorized to create or editing my own role/suspending myself, so that privilege escalation and self-lockout are both impossible by mistake.
- Acceptance: setting a user's password immediately signs out their other sessions.

**US-12.7** As an **admin**, I want a live audit log of every booking change, schedule change and override, filterable by action, with before/after detail, so that any disputed change can be traced.

**US-12.8** As an **admin**, I want to change booking rules (notice period, buffer, waitlist on/off, recurring limits, business name, reminder timing…) from a settings screen and have them apply immediately, with no restart, so that I can tune the business without a deploy.
- Acceptance: an invalid value (e.g. a negative notice period) is rejected with a clear message and the previous value is kept.

## 13. Notifications & reminders

**US-13.1** As a **customer**, I want an in-app and e-mail message whenever my booking is created, confirmed, rescheduled, cancelled, or I'm promoted off a waitlist, so that I always know the current state of my bookings without checking manually.
- Acceptance: the wording differs for a self-cancel ("Booking cancelled") versus an admin cancel ("Your booking was cancelled", including any reason given).

**US-13.2** As a **customer**, I want a reminder before my booking starts, so that I don't forget it.
- Acceptance: default offsets are 24 hours and 1 hour before start, both configurable; a reminder is sent at most once per booking per offset, even across an API restart or with multiple API instances running.

**US-13.3** As a **customer**, I want to mark notifications read individually or all at once, so that my unread count is accurate.

**US-13.4** As a **customer**, I want my notifications to be private, so that nobody else can read or mark them read.

## 14. Timezones & multi-location

**US-14.1** As a **customer**, I want times shown in the resource's own timezone, not my browser's, so that "10:00" always means what the business means by 10:00, wherever I am.
- Acceptance: changing my browser/OS timezone has no effect on the times shown or booked.

**US-14.2** As a **customer**, I want daylight-saving transitions handled correctly — no phantom slot in the skipped hour, no missing slot in the repeated hour — so that a booking near a clock change is unambiguous.

**US-14.3** As a **customer**, I want a weekly recurring booking to keep its local wall-clock time across a daylight-saving change, so that "every Monday at 10am" still means 10am after the clocks move.

**US-14.4** As an **admin**, I want to run multiple locations, each with its own timezone and (optionally) its own operating hours, so that a multi-branch business is one deployment.

## 15. Deployment & operations

**US-15.1** As an **operator**, I want to bring the whole stack up with one command and have the database migrate itself, so that setup is fast and repeatable.
- Acceptance: `docker compose up --build` results in a healthy database, a migrated and started API, and a working frontend at `/`.

**US-15.2** As an **operator**, I want demo/seed data I can (re-)apply safely, so that a fresh environment is immediately explorable.
- Acceptance: running the seed twice is a no-op the second time ("Seed data already present.").

**US-15.3** As an **operator**, I want the schema state verifiable against the migrations, so that drift is caught before it causes an incident.
- Acceptance: `alembic check` reports no pending operations.

**US-15.4** As an **operator**, I want to run more than one API replica and still have each reminder sent exactly once, so that scaling out is safe.
- Acceptance: with two replicas running, a booking due for a reminder gets exactly one in-app message and one e-mail — not two, not zero (this is the behavior a real bug once broke and end-to-end testing caught; see `docs/system-architecture.md` §9).

**US-15.5** As an **operator**, I want data to survive a restart, deep-linked pages to survive a reload, and static assets to be aggressively cached, so that the deployment behaves like a normal production web app.
