# End-to-End Test Results (through the frontend) – Booking & Appointment System

**161 of 164 cases passed; 2 failed (2 low severity).** 1 could not be run. This second run drove every case through the web app in a browser, with no direct API calls from the test harness. It ran on 2026-09-27, 14:46–16:12 IST on branch `claude/gracious-goldberg-outqt8` at commit `156b32a` (the build with the four fixes from the first run, the redesigned light/dark UI and the new booking features), following *End-to-End Test Cases – Booking & Appointment System* case by case. The later commits up to `66a7b52` only add tests and raise the contrast and tap size of a few controls; the ten plan cases that use those controls were run again on `66a7b52` and pass.

| Result | Cases |
| --- | --- |
| Pass | 161 |
| Fail | 2 |
| Blocked | 1 |

| Area | Cases | Pass | Fail |
| --- | --- | --- | --- |
| AUTH — Authentication and accounts | 14 | 14 | 0 |
| SEC — Authorization and security | 14 | 14 | 0 |
| AVL — Browsing and availability | 14 | 13 | 0 |
| BKG — Making bookings and booking rules | 17 | 17 | 0 |
| CAP — Group sessions, waitlists and simultaneous bookings | 15 | 15 | 0 |
| CHG — Rescheduling and cancellation | 9 | 9 | 0 |
| REC — Recurring and multi-resource bookings | 12 | 12 | 0 |
| ADM — Admin booking management, dashboard and calendar | 15 | 15 | 0 |
| SCH — Schedule changes and conflict resolution | 14 | 13 | 1 |
| CFG — Catalogue, users and settings administration | 15 | 14 | 1 |
| NTF — Notifications and reminders | 11 | 11 | 0 |
| TZ — Timezones and daylight saving | 6 | 6 | 0 |
| OPS — Deployment smoke checks | 8 | 8 | 0 |

The four failures from the first run (NTF-08, CHG-02, BKG-12, CAP-07) all pass in this run.

## Failures

### SCH-01 — Edit operating hours with no impact (Low)

- **Expected:** Saving Saturday 09:00–14:00 for Downtown saves straight away and shows "Operating hours saved."; Meeting Room A's Saturday slots end at 13:00.
- **Observed:** The hours were saved straight away (no preview; Downtown Saturday now ends 14:00 and Meeting Room A's Saturday slots end at 13:00), but the "Operating hours saved." confirmation was shown for 60 ms (4 frames, 109–153 ms after clicking Save), then disappeared when the form reloaded - too briefly for a person to read.
- **Impact:** The save works, but the admin gets no visible confirmation: the message flashes for about a tenth of a second.
- **Root cause:** In `frontend/src/pages/admin/schedules.tsx` the hours form is rendered with `key={`${locationId}-${hours.dataUpdatedAt}`}`. After a save the change hook invalidates the queries, the operating-hours query refetches, `dataUpdatedAt` changes, the form remounts and its `saved` state resets to false, removing the alert.
- **Suggested fix:** Keep the form mounted across refetches (key it on the scope only and reset the rows when new data arrives), or keep the "saved" flag in the parent `OperatingHoursEditor` so a remount does not clear it.

### CFG-07 — Create a service (Low)

- **Expected:** Pilates is listed in Admin → Services as Group (8), and appears on /services once linked to a resource.
- **Observed:** Created and listed as "Pilates Group (8) 45 min 0 / 10 min 15.00 / h Active Edit Delete", but Pilates was already on /services before any resource offered it: its page read "Services Pilates 45 min · from 15.00 / h Group · 8 places Bo" with no resource listed, and in the booking wizard choosing it offered 0 resources to book with - a dead end for customers. After linking it to Yoga Studio it is listed with Yoga Studio as expected.
- **Impact:** A service that no resource offers yet is shown to customers on /services and in the booking wizard, where it leads nowhere: its page lists no resource and the wizard has nothing to book it with.
- **Root cause:** `list_services` in `backend/app/api/routes/catalog.py` returns every active service; it only checks resource links when a resource or location filter is given. The wizard's service list uses the same endpoint.
- **Suggested fix:** In the public service list (and the service detail), only include services with at least one active link to an active resource; admins keep seeing every service in Admin → Services.

## How the run was done

- **Only the web app talks to the API.** Every step was carried out in headless Chromium (Playwright 1.63, 1360×900) on the web app at http://localhost:5173 (Vite dev server, which forwards /api to the API) or, for the Docker cases, http://localhost:8080 (nginx).
- **Enforced, not just intended.** The test process refused any network connection to the API's port 8000, and every browser aborted any request to port 8000, so a page could reach the API only through the web app's own origin; a case that tried anyway would have been marked Error. No case tried.
- **Set-up through the admin screens.** Rules were changed in Admin → Settings, hours and blocks in Admin → Schedules and the resource editor, services, resources, locations and users in their admin pages, and extra customers registered at /register. Customers booked in the wizard, moved and cancelled bookings on their booking pages, and read their messages in the notification bell.
- **Plan steps written as API requests** (for example "POST /api/bookings …" or "call GET /api/admin/settings") were sent from inside the signed-in page with fetch() to the web app's origin, carrying that browser's session cookie and CSRF token, the way a tester would from the browser console. 43 cases did this (83 requests in all); each is marked "UI + page requests" below and its requests are listed with the case. The simultaneous-booking cases CAP-11 and CAP-12 used real clicks instead: ten (or six) people each had the wizard open and pressed Confirm booking at the same instant.
- **111 cases used the UI alone.** Database and command-line steps that the plan writes that way (psql in SEC-09 and CAP-15, starting the API in SEC-14, docker compose, alembic and pytest in OPS) were run as written, and NTF-10 read the notifications table as the plan says; the "Through" column below shows this per case.
- **One shared instance** of the API (uvicorn, background jobs every 60 s, e-mail to its console log) for the whole run; before each case the database was reset to the seeded demo data. AUTH-10, NTF-08 and NTF-10 restarted it because their steps say so.
- **Each simulated person had their own browser** with its own client address (passed as X-Forwarded-For, as a reverse proxy would), so the API's per-address rate limits treated them as separate people.
- **Dates:** D = 2026-09-28 (the next Monday); all times Asia/Kolkata unless a case says otherwise.
- **AVL-10 needs a weekday.** It checks today's slots at Downtown, which is closed on Sundays, and this run fell on a Sunday; it passed in the earlier runs, which were on weekdays.
- **Docker cases** ran the stack from docker-compose.yml with an override that uses images built from the repo's Dockerfiles with the sandbox's CA added, and leaves the database's port 5432 unpublished: the test host's own PostgreSQL already listens there.
- **Adapted to the redesigned screens.** The harness follows the new layout (for example "Pick who or what first" in the wizard, booking rows instead of View buttons, "Full · waitlist" on full sessions); where the plan's wording no longer matches the app, the change is listed under Corrections for the test plan.

## Deviations from the written steps

| Case | What was done differently |
| --- | --- |
| BKG-06 | Run at 14:52 IST: to have Meeting Room times up to 5 hours ahead inside opening hours, Downtown's operating hours were set to 00:00–23:59 every day in Admin → Schedules. |
| CAP-11 | Ten people each had John Carter D 15:00 open at the Confirm step in their own browser; all ten pressed Confirm booking at the same instant, so the web app sent the ten POST /api/bookings in parallel. |
| CAP-12 | Slot interval set to 10 in Admin → Settings so the wizard offers 15:00, 15:10 … 15:50; six people pressed Confirm booking at the same instant. |
| CAP-13 | The plan's request carries join_waitlist = true, which the wizard only sends once a session is full, so the 12 POSTs were sent at the same instant from 12 signed-in pages with fetch(). |
| CHG-07 | The started booking was made in Admin → Bookings → New booking with Override, starting at 14:29 IST (30 minutes before the case ran), since customers cannot book in the past. |
| REC-03 | The shared series id is not shown in the UI; it was read from the list the My bookings page loads. |
| REC-10 | Camera rental (60 min) was also created and linked to Camera Kit so the kit could be booked on its own; the plan does not name a service for that. |
| REC-10 | Slot interval set to 60 in Admin → Settings: with the default (the service length) the wizard offers the 2-hour Photography Session at 09:00, 11:00 … and never at 10:00. |
| REC-11 | Camera rental (60 min) was also created and linked to Camera Kit so the kit could be booked on its own; the plan does not name a service for that. |
| ADM-06 | The started booking was made in Admin → Bookings → New booking with Override, starting at 14:34 IST (30 minutes before the case ran), since customers cannot book in the past. |
| NTF-08 | Slot interval set to 15 in Admin → Settings so the wizard offers a Tennis Court 2 time 30–60 minutes ahead. |
| NTF-09 | Slot interval 15 set in Admin → Settings to book about 23 hours ahead. The later 1-hour reminder was not observed live (it is due 22 hours after booking); the same mechanism is exercised end to end in NTF-08. |
| TZ-05 | The instants behind the two 01:00 buttons were read from the availability response the wizard itself loaded. |
| OPS-01 | docker compose --build cannot pull packages through this sandbox's TLS interception, so the images were built from the repo Dockerfiles with the sandbox CA added and compose ran them via an override file, which also leaves the database's port 5432 unpublished because this host's own PostgreSQL uses it; the stack definition is otherwise unchanged. |
| OPS-07 | Slot interval set to 15 in Admin → Settings (on :8080) so the wizard offers a court time within the hour. |

## Method notes

How some written steps were carried out; these follow the plan.

| Case | Note |
| --- | --- |
| AUTH-07 | The token was read from the browser's cookies (as in DevTools → Application → Cookies); GET /api/auth/me was sent from the page with that token. |
| AUTH-10 | Restarted the shared API with PASSWORD_RESET_TTL_MINUTES=1 as the case says, then back to its normal settings. |
| SEC-09 | Database step by design: the users table was queried with psql after registering through /register. |
| SEC-14 | Command-line step by design: each attempt started a separate API process (port 8099) that refused to start; the shared instance was not touched. |
| CAP-15 | Database step by design: the rows were inserted with psql-equivalent SQL. |
| REC-09 | Admin set-up in the UI: Services → New service Photography Session (120 min); Resources → New resource Photographer (Person), Studio (Room), Camera Kit (Equipment), each with Mon–Fri 09:00–17:00 weekly availability; Photographer linked to Photography Session. |
| REC-10 | Admin set-up in the UI: Services → New service Photography Session (120 min); Resources → New resource Photographer (Person), Studio (Room), Camera Kit (Equipment), each with Mon–Fri 09:00–17:00 weekly availability; Photographer linked to Photography Session. |
| REC-11 | Admin set-up in the UI: Services → New service Photography Session (120 min); Resources → New resource Photographer (Person), Studio (Room), Camera Kit (Equipment), each with Mon–Fri 09:00–17:00 weekly availability; Photographer linked to Photography Session. |
| NTF-08 | Restarted the shared API instance as the case requires. |
| NTF-10 | The shared API instance was restarted with EMAIL_BACKEND=smtp pointing at a local debugging SMTP server for this case, then restarted again with its normal console mail settings. |
| TZ-04 | Set up in the admin UI as the TZ section says: Locations → NY Test (America/New_York, no operating hours); Services → Night Slot (60 min); Resources → Night Desk at NY Test, Sundays 00:00–06:00, offering Night Slot; Settings → Maximum Advance Booking Days 400. |
| TZ-05 | Set up in the admin UI as the TZ section says: Locations → NY Test (America/New_York, no operating hours); Services → Night Slot (60 min); Resources → Night Desk at NY Test, Sundays 00:00–06:00, offering Night Slot; Settings → Maximum Advance Booking Days 400. |
| TZ-06 | Set up in the admin UI as the TZ section says: Locations → NY Test (America/New_York, no operating hours); Services → Night Slot (60 min); Resources → Night Desk at NY Test, Sundays 00:00–06:00 and Mondays 09:00–12:00, offering Night Slot; Settings → Maximum Advance Booking Days 400. |

## Corrections for the test plan

These cases passed, but their written steps or expected results should be updated.

| Case | Correction |
| --- | --- |
| OPS-06 | The expected "70 passed" no longer holds: the fixes and the new features added backend tests (79 now). Reword it as "all backend tests pass". |
| BKG-01 | In "Choose how to book", first choose "Pick who or what first", then the resource: neither way of booking is preselected any more. The same applies to every case that picks a resource in the wizard. |
| BKG-01 | My bookings lists each booking as a row that opens it; there is no separate View button. |
| CAP-04 | A full session's slot now reads "Full · waitlist" (it read "Capacity Reached"). |
| CAP-07 | With the waitlist off, a full session reads "Full" and can't be chosen at all, so there is no warning to read; the expected result (no Join waitlist button, 409 CAPACITY_REACHED from the API) is unchanged. |
| AVL-02 | Prices are now hourly rates shown as "40.00 / h", with the bookable length range ("1 h – 2 h"); a booking of the standard length costs what it did before. |
| BKG-16 | The example alternative "John Carter 12:00" cannot be offered: U2's 11:00 Personal Training plus its 15-minute buffer keeps John busy until 12:15. Replace the example with "John Carter 09:00 or 14:00" (carried over from the first run). |
| CAP-10 | Give Studio hire a duration that fits the studio's 18:00–19:00 hours (the run used 30 minutes) (carried over from the first run). |
| AVL-13 | "status" lives on each resource's slots (resources[].slots[]); the aggregated top-level slots carry start, end, available and resource_ids (carried over from the first run). |
| BKG-15 | U3's expected message only appears if U3 selected 10:00 before U1 and U2 confirmed; the run had all three select first (carried over from the first run). |

## Results by case

### AUTH — Authentication and accounts

| ID | Scenario | Result | Through | Observed |
| --- | --- | --- | --- | --- |
| AUTH-01 | Register a new account | Pass | UI | Landed on /dashboard with heading "Hello, Ann"; Profile shows ann@example.com; Admin → Users lists "Ann Lee ann@example.com User Active 9/27/2026 Edit" (lower-case e-mail, role User). |
| AUTH-02 | Duplicate e-mail rejected | Pass | UI | Form showed "An account with this e-mail already exists" and stayed on /register; Admin → Users still lists exactly 1 account for ann@example.com. |
| AUTH-03 | Registration field validation | Pass | UI + page requests | The form refused both (7-character password and 'not-an-email' flagged invalid, no request sent); the same data sent from the page with fetch() → 422 and 422.Page requests: POST /api/auth/register → 422; POST /api/auth/register → 422. |
| AUTH-04 | Sign in lands on the right home | Pass | UI | U1 landed on /dashboard with 'Demo User' and the notification bell in the header; after Sign out, Admin landed on /admin with name and bell. |
| AUTH-05 | Bad credentials | Pass | UI | Wrong password and unknown e-mail both showed "Invalid e-mail or password"; both stayed on /login. |
| AUTH-06 | Deep link after sign-in | Pass | UI | Signed-out visit went to /login?next=%2Fbookings; after sign-in landed on /bookings. |
| AUTH-07 | Sign-out revokes the session | Pass | UI + page requests | Token worked before sign-out (200); after Sign out the app returned home, /dashboard redirected to /login?next=%2Fdashboard, and the same token got 401.Page requests: GET /api/auth/me → 200; GET /api/auth/me → 401 Session expired. |
| AUTH-08 | Forgot password request | Pass | UI | Both requests showed "If that e-mail is registered, a reset link has been sent."; the API's e-mail log has 1 reset e-mail (with link) to user@example.com and none to nobody@example.com. |
| AUTH-09 | Reset password with the link | Pass | UI | Opened the e-mailed link and set a new password; old password → "Invalid e-mail or password", new password → /dashboard; reusing the link → "This reset link is invalid or has expired"; a browser signed in before the reset was sent to /login on refresh. |
| AUTH-10 | Reset link expires | Pass | UI | Link used after 2 minutes (TTL 1 minute) → "This reset link is invalid or has expired"; password unchanged (old password still signs in). |
| AUTH-11 | Edit profile | Pass | UI | "Saved." shown; after reload the header shows Demo Person and the form holds the new name and phone. |
| AUTH-12 | Change password | Pass | UI | Wrong current password → "Current password is incorrect"; correct one → "Password updated. Other sessions were signed out."; the second browser was sent to /login?next=%2Fdashboard; this browser stayed signed in. |
| AUTH-13 | Suspended account | Pass | UI | Admin → Users → Edit Demo User → Suspended (row now "Demo User user@example.com User Suspended 9/27/2026 Edit"); sign-in showed "This account is not active"; U1's open tab went to /login?next=%2Fdashboard on refresh. |
| AUTH-14 | Anonymous visit is clean | Pass | UI | Home loaded with no console errors; the app's requests: /api/auth/session 200, /api/config 200, /api/locations 200, /api/services 200; session check returned {'user': None}. |

### SEC — Authorization and security

| ID | Scenario | Result | Through | Observed |
| --- | --- | --- | --- | --- |
| SEC-01 | Users cannot open the admin area | Pass | UI | Nav: Services · Resources · Dashboard · Book · My bookings (no Admin); typing /admin redirected to /dashboard. |
| SEC-02 | Users are refused by admin APIs | Pass | UI + page requests | Sent from U1's signed-in page: GET /api/admin/bookings → 403; GET /api/admin/users → 403; GET /api/admin/dashboard → 403; GET /api/admin/settings → 403; POST /api/admin/resources → 403.Page requests: GET /api/admin/bookings → 403 You do not have permission to do that; GET /api/admin/users → 403 You do not have permission to do that; GET /api/admin/dashboard → 403 You do not have permission to do that; GET /api/admin/settings → 403 You do not have permission to do that; POST /api/admin/resources → 403 You do not have permission to do that. |
| SEC-03 | Other users' bookings are hidden | Pass | UI + page requests | U2 opening /bookings/<U1's id> saw "Booking not found"; cancel → 404, reschedule → 404 (sent from U2's page); U2's My bookings is empty; U1's booking still Confirmed.Page requests: POST /api/bookings/be1a5a30-d741-4a64-b815-768fefb2a7af/cancel → 404 NOT_FOUND; POST /api/bookings/be1a5a30-d741-4a64-b815-768fefb2a7af/reschedule → 404 NOT_FOUND. |
| SEC-04 | Role cannot be self-assigned | Pass | UI + page requests | Sent from the /register page with "role": "ADMIN" → 201; response role=USER; Admin → Users lists "Mallory mallory@example.com User Active 9/27/2026 Edit".Page requests: POST /api/auth/register → 201. |
| SEC-05 | No booking for others, no rule override | Pass | UI + page requests | Sent from U1's page: override_rules → 403 ("Only administrators can override booking rules"); booking with U2's user_id → 201, and it appears in U1's My bookings while U2's list stays empty.Page requests: POST /api/bookings/recurring → 403 FORBIDDEN; POST /api/bookings → 201. |
| SEC-06 | Unauthenticated calls | Pass | UI + page requests | From a signed-out page: /api/bookings → 401; /api/admin/bookings → 401; with "Bearer not-a-jwt" → 401.Page requests: GET /api/bookings → 401 Not authenticated; GET /api/admin/bookings → 401 Not authenticated; GET /api/bookings → 401 Invalid or expired token. |
| SEC-07 | CSRF protection on cookie sessions | Pass | UI + page requests | Replayed from the signed-in page with the session cookie only → 403 "CSRF token missing or invalid"; with X-CSRF-Token → 200, and the header now shows "Changed".Page requests: PUT /api/auth/me → 403 CSRF token missing or invalid; PUT /api/auth/me → 200. |
| SEC-08 | Login rate limit | Pass | UI | From one browser: attempts 1–10 showed "Invalid e-mail or password" (401); attempt 11 showed "Too many requests, please try again later" (429, Retry-After: 300). |
| SEC-09 | Passwords are hashed | Pass | UI + database (psql query) | Registered through /register; password_hash starts with argon2id ($argon2id$v=19$m=65536,t=3,p=4…); the plain password appears nowhere in the row. |
| SEC-10 | Security headers | Pass | UI | Browser → /api/health through the web app: X-Content-Type-Options nosniff, X-Frame-Options DENY, CSP "default-src 'none'; frame-ancestors 'none'". Docker web app (http://localhost:8080/): CSP "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'". |
| SEC-11 | Script injection is shown as text | Pass | UI | Profile name and booking notes <img src=x onerror=alert(1)> were shown literally on the booking detail, Admin → Bookings list and booking modal; the bell opened normally (1 message); no alert fired and no <img> element was created. |
| SEC-12 | SQL injection in search | Pass | UI | /services search → 0 results, no error; Admin → Bookings search → "No bookings match these filters." with 1 booking in the system; no server errors. |
| SEC-13 | STAFF role sees only its tools | Pass | UI + page requests | Admin → Users → New user (Staff); signed in as STAFF: admin nav shows Overview, Calendar, Bookings; GET /api/admin/settings from that page → 403.Page requests: GET /api/admin/settings → 403 You do not have permission to do that. |
| SEC-14 | Unsafe production config refused | Pass | Command line (starting the API) | Default secret: exit code 1, "Set JWT_SECRET to a random value of at least 32 characters in production". Strong secret + COOKIE_SECURE=false: exit code 1, "COOKIE_SECURE must be true in production (serve the app over HTTPS)". |

### AVL — Browsing and availability

| ID | Scenario | Result | Through | Observed |
| --- | --- | --- | --- | --- |
| AVL-01 | Browse and filter services | Pass | UI | All: Consultation, Meeting Room, Personal Training, Tennis Court, Yoga Class; 'yoga' → ['Yoga Class']; max 15 → ['Meeting Room', 'Yoga Class']; Riverside → ['Tennis Court', 'Yoga Class']. |
| AVL-02 | Service detail | Pass | UI | Rows: "John Carter Person · Downtown · 1 h – 2 h · 40.00 / h Book" / "Priya Nair Person · Downtown · 1 h – 2 h · 45.00 / h Book"; Book → /book with Personal Training and John Carter pre-selected. |
| AVL-03 | Browse and filter resources | Pass | UI | Person → ['John Carter', 'Priya Nair']; Riverside Sports Club → ['Tennis Court 2', 'Yoga Studio']. |
| AVL-04 | Resource detail | Pass | UI | John: "Monday 09:00–13:00, 14:00–18:00", "Saturday Unavailable", "Sunday Unavailable"; details "Experience 8 Specialization Strength Training". Meeting Room A: "Available during business hours". |
| AVL-05 | Hours and lunch break respected | Pass | UI | 2026-09-28 slots: 09:00, 10:00, 11:00, 12:00, 14:00, 15:00, 16:00, 17:00; "Times shown in Asia/Kolkata.". |
| AVL-06 | Slot interval follows duration (spec example) | Pass | UI | 16 slots every 30 min: 09:00–12:30 and 14:00–17:30. |
| AVL-07 | Operating hours apply; closed day | Pass | UI | 2026-09-28: 09:00, 10:00, 11:00, 12:00, 13:00, 14:00, 15:00, 16:00, 17:00; 2026-10-04 (Sunday): "No times on this date. Try another day." |
| AVL-08 | Resource day off | Pass | UI | 2026-09-30 (Wednesday) with Priya Nair: "No times on this date. Try another day." |
| AVL-09 | Only slots that fit the duration | Pass | UI | Admin: Services → New service Quick Check (45 min); Meeting Room A → Services: ticked it; Weekly availability: Monday 09:00–10:30. Wizard on 2026-09-28: ['09:00', '09:45']. |
| AVL-10 | Past and too-soon slots disabled | **Blocked** | UI | Downtown is closed on Sundays; run on Monday–Saturday. |
| AVL-11 | Advance booking window | Pass | UI | 2026-11-02: all 9 slots disabled and labelled "Too Far Ahead". |
| AVL-12 | Time-first flow | Pass | UI | 10:00 "2 free", 09:00 "1 free"; after choosing 10:00: Any available, John Carter, Priya Nair. |
| AVL-13 | Availability API shape | Pass | UI + page requests | Sent from the web app's page: date=2026-09-28; slots[0]={start: 2026-09-28T09:00:00+05:30, end: 2026-09-28T09:30:00+05:30, available: True}; resources[0].slots[0].status=AVAILABLE.Page requests: GET /api/availability → 200. |
| AVL-14 | Booking summary panel and date navigation | Pass | UI | Summary: "Summary Service Personal Training With Priya Nair Where Downtown Open in Google Maps When Mon, Sep 28, 2026 · 10:00 AM – 11:00 AM Length 1 h Price 45.00 45.00 / h × 1 h"; → moved to 2026-09-29, ← back to 2026-09-28; ← disabled on 2026-09-27. |

### BKG — Making bookings and booking rules

| ID | Scenario | Result | Through | Observed |
| --- | --- | --- | --- | --- |
| BKG-01 | Book an individual slot | Pass | UI | Booking ea71deb4: "You're booked!", status Confirmed, price 40.00, note shown; listed under My bookings → Upcoming and as the dashboard's Next booking. |
| BKG-02 | Booked slot shows as taken | Pass | UI | After booking 10:00, reopening John Carter on D shows 10:00 disabled, struck through and labelled "Conflict". |
| BKG-03 | Partial overlap rejected | Pass | UI + page requests | U2's wizard shows Consultation 10:30 disabled ("Conflict"); POST /api/bookings from U2's page → 409 CONFLICT "This time overlaps another booking".Page requests: POST /api/bookings → 409 CONFLICT. |
| BKG-04 | Back-to-back allowed | Pass | UI | U2 booked Consultation with John Carter at 09:30 in the wizard: "Mon, Sep 28, 2026 · 09:30 AM – 10:00 AM", Confirmed, right before U1's 10:00 booking. |
| BKG-05 | Buffer blocks the following time | Pass | UI | Personal Training 11:00 and Consultation 11:00 disabled (Conflict); Personal Training 12:00 and Consultation 11:30 available. |
| BKG-06 | Minimum notice enforced | Pass | UI + page requests | From U1's page (now 14:52 IST): Meeting Room at 15:55 (62 min ahead) → 409 TOO_SOON "This time is inside the minimum booking notice"; at 17:55 (182 min ahead) → 201, listed under My bookings.Page requests: POST /api/bookings → 409 TOO_SOON; POST /api/bookings → 201. |
| BKG-07 | Past time rejected | Pass | UI + page requests | POST from U1's page for 2026-09-26 10:00 → 409 PAST "This time is in the past".Page requests: POST /api/bookings → 409 PAST. |
| BKG-08 | Outside resource availability | Pass | UI + page requests | The wizard offers no 13:00; POSTs from U1's page: 2026-09-28 13:00 → 409 OUTSIDE_AVAILABILITY; 2026-10-03 (Saturday) 10:00 → 409 OUTSIDE_AVAILABILITY ("The resource is not available at this time").Page requests: POST /api/bookings → 409 OUTSIDE_AVAILABILITY; POST /api/bookings → 409 OUTSIDE_AVAILABILITY. |
| BKG-09 | Outside operating hours | Pass | UI + page requests | POST from U1's page for 2026-09-28 19:00 → 409 OUTSIDE_OPERATING_HOURS "This time is outside operating hours".Page requests: POST /api/bookings → 409 OUTSIDE_OPERATING_HOURS. |
| BKG-10 | Blocked period overrides availability | Pass | UI + page requests | Block saved in Admin → Schedules (Maintenance, "Deep clean"); wizard shows 12:00 and 13:00 Blocked, 14:00 open; POST 13:00 from U1's page → 409 BLOCKED "Maintenance: Deep clean".Page requests: POST /api/bookings → 409 BLOCKED. |
| BKG-11 | Special hours replace the weekly schedule | Pass | UI | Special Hours added in Admin → Schedules; wizard 2026-09-28: ['10:00', '11:00']; 2026-09-29: 07:00–21:00 (15 slots). |
| BKG-12 | Booking across midnight | Pass | UI | Admin: Riverside Monday 07:00–02:00, Tennis Court 2 Monday 22:00–02:00, Slot interval 30; booked 2026-09-28 23:30 in the wizard; detail shows "Mon, Sep 28, 2026 · 11:30 PM – Tue, Sep 29, 2026 · 12:30 AM". |
| BKG-13 | Inactive resource cannot be booked | Pass | UI + page requests | Admin → Resources → Priya Nair → Status Inactive (Keep); /resources lists ['John Carter', 'Meeting Room A', 'Tennis Court 2', 'Yoga Studio']; the wizard offers no Priya; POST from U1's page → 409 RESOURCE_INACTIVE "Priya Nair is not accepting bookings".Page requests: POST /api/bookings → 409 RESOURCE_INACTIVE. |
| BKG-14 | Service the resource does not offer | Pass | UI + page requests | The wizard offers only Meeting Room A for the Meeting Room service; POST with John Carter from U1's page → 422 SERVICE_NOT_OFFERED "John Carter does not provide Meeting Room".Page requests: POST /api/bookings → 422 SERVICE_NOT_OFFERED. |
| BKG-15 | "Any available" assigns a free resource | Pass | UI | U1 → John Carter; U2 → Priya Nair; U3 → "No resource is available at that time". A fresh view afterwards shows 10:00 as "Taken" (disabled: True). |
| BKG-16 | Slot taken while choosing → alternatives | Pass | UI | U1 saw "This time overlaps another booking" with suggestions: Priya Nair Mon, Sep 28, 2026, 11:00 AM; Priya Nair Mon, Sep 28, 2026, 10:00 AM; Priya Nair Mon, Sep 28, 2026, 12:00 PM; John Carter Mon, Sep 28, 2026, 09:00 AM; Priya Nair Mon, Sep 28, 2026, 01:00 PM; John Carter Mon, Sep 28, 2026, 02:00 PM. Picking the first and confirming booked Booking details (Mon, Sep 28, 2026 · 11:00 AM – 12:00 PM). |
| BKG-17 | Admin confirmation required | Pass | UI | U1 saw "Request received" and Pending; U2's wizard shows 10:00 as "Conflict" (disabled); Admin → Bookings → Open → Confirm; U1's booking now Confirmed and the bell has ['Booking confirmed', 'Booking received – awaiting confirmation']. |

### CAP — Group sessions, waitlists and simultaneous bookings

| ID | Scenario | Result | Through | Observed |
| --- | --- | --- | --- | --- |
| CAP-01 | Join a group class | Pass | UI | Booked 18:00 (was "12 left"), status Confirmed; the slot now shows "11 left". |
| CAP-02 | Book several places | Pass | UI | U2 set Places = 3 and booked: Confirmed, quantity 3, price 36.00; the slot now shows "8 left". |
| CAP-03 | More places than the session holds | Pass | UI + page requests | With Places = 13 the 18:00 slot is disabled ("Quantity Exceeds Capacity"); POST from U1's page → 409 QUANTITY_EXCEEDS_CAPACITY "The requested quantity exceeds the capacity".Page requests: POST /api/bookings → 409 QUANTITY_EXCEEDS_CAPACITY. |
| CAP-04 | Full session | Pass | UI | Admin → Services → Yoga Class capacity 2; U1 and U2 booked; U3 sees 18:00 "Full · waitlist", the warning "This session is full…" and an enabled Join waitlist button. |
| CAP-05 | Join the waitlist | Pass | UI | U3: "You're #1 on the waitlist…" (Waitlisted); U4: "You're #2 on the waitlist…"; both dashboards show "On a waitlist 1". |
| CAP-06 | Cancellation promotes the waitlist | Pass | UI | U1 cancelled in the UI → U3's booking is Confirmed and U3's bell has "A place opened up – you're booked!"; U4's page shows #1 on the waitlist; the session still shows "Full · waitlist" (0 left). |
| CAP-07 | Waitlist disabled | Pass | UI + page requests | Allow Waitlist off in Admin → Settings; U3's 18:00 reads "Full" and can't be chosen, so there is no Join waitlist button; POST with join_waitlist = true from U3's page → 409 CAPACITY_REACHED "This session is full and the waitlist is disabled".Page requests: POST /api/bookings → 409 CAPACITY_REACHED. |
| CAP-08 | Leave the waitlist | Pass | UI | U4 → Leave waitlist → Cancelled; after U1 cancelled a seat U4 is still Cancelled (not promoted). |
| CAP-09 | One booking per person per session | Pass | UI | U1 booked the 18:00 session, then chose it again and pressed Confirm booking: the app got 409 and showed "You already have a booking for this session". |
| CAP-10 | A group session blocks private use of the room | Pass | UI + page requests | Admin created Studio hire (30 min) on Yoga Studio; with U1's yoga booking at 18:00, U2's wizard shows Studio hire 18:30 as "Conflict" and POST from U2's page → 409 CONFLICT "This time overlaps another booking".Page requests: POST /api/bookings → 409 CONFLICT. |
| CAP-11 | Simultaneous requests for one slot | Pass | UI + page requests | 10 simultaneous Confirm booking clicks → one 201 and nine 409, the nine seeing "This time overlaps another booking"; Admin → Bookings shows exactly one Confirmed booking at 15:00.Page requests: POST /api/bookings (Confirm booking clicked together) → 409; POST /api/bookings (Confirm booking clicked together) → 409; POST /api/bookings (Confirm booking clicked together) → 409; POST /api/bookings (Confirm booking clicked together) → 409; POST /api/bookings (Confirm booking clicked together) → 201; POST /api/bookings (Confirm booking clicked together) → 409; POST /api/bookings (Confirm booking clicked together) → 409; POST /api/bookings (Confirm booking clicked together) → 409; POST /api/bookings (Confirm booking clicked together) → 409; POST /api/bookings (Confirm booking clicked together) → 409. |
| CAP-12 | Simultaneous overlapping times | Pass | UI + page requests | Confirm booking pressed together for 15:00, 15:10, 15:20, 15:30, 15:40, 15:50 → exactly one succeeded (15:00); the rest got [409].Page requests: POST /api/bookings (Confirm booking clicked together) → 201; POST /api/bookings (Confirm booking clicked together) → 409; POST /api/bookings (Confirm booking clicked together) → 409; POST /api/bookings (Confirm booking clicked together) → 409; POST /api/bookings (Confirm booking clicked together) → 409; POST /api/bookings (Confirm booking clicked together) → 409. |
| CAP-13 | Simultaneous requests for a 3-seat session | Pass | UI + page requests | Capacity 3 set in Admin → Services; 12 simultaneous POSTs from 12 signed-in pages → 3 CONFIRMED and 9 WAITLISTED; Admin → Bookings agrees (3 Confirmed, 9 Waitlisted).Page requests: POST /api/bookings (simultaneous) → 201; POST /api/bookings (simultaneous) → 201; POST /api/bookings (simultaneous) → 201; POST /api/bookings (simultaneous) → 201; POST /api/bookings (simultaneous) → 201; POST /api/bookings (simultaneous) → 201; POST /api/bookings (simultaneous) → 201; POST /api/bookings (simultaneous) → 201; POST /api/bookings (simultaneous) → 201; POST /api/bookings (simultaneous) → 201; POST /api/bookings (simultaneous) → 201; POST /api/bookings (simultaneous) → 201. |
| CAP-14 | Simultaneous cancellations | Pass | UI | Capacity 2, two Confirmed and three Waitlisted (all booked in the wizard); both confirmed users pressed Cancel booking at the same instant → the first two waitlisted users are Confirmed; the third is still Waitlisted, #1 on the waitlist. |
| CAP-15 | Database refuses double bookings | Pass | Database (psql inserts) | Overlapping active row rejected: "conflicting key value violates exclusion constraint "ex_booking_resources_no_overlap""; overlapping inactive row accepted. |

### CHG — Rescheduling and cancellation

| ID | Scenario | Result | Through | Observed |
| --- | --- | --- | --- | --- |
| CHG-01 | Reschedule to a free time | Pass | UI | Reschedule → 14:00 → Move booking opened the new booking (Mon, Sep 28, 2026 · 02:00 PM – 03:00 PM) with "Your booking has been moved." and a Moved from link; the original shows Rescheduled with "See the new booking"; 10:00 is bookable again. |
| CHG-02 | Move by less than the duration | Pass | UI | Slot interval 30; in the reschedule dialog 10:30 is selectable (10:00 marked "Current"); Move booking → new booking Mon, Sep 28, 2026 · 10:30 AM – 11:30 AM. |
| CHG-03 | Failed reschedule keeps the original | Pass | UI + page requests | POST /reschedule to 14:30 from U1's page → 409 CONFLICT "This time overlaps another booking"; U1's booking still Confirmed at 10:00 and 10:00 still shows "Conflict".Page requests: POST /api/bookings/03a1b640-2ea7-46c3-8f32-121e0dde4bbd/reschedule → 409 CONFLICT. |
| CHG-04 | Rescheduling window | Pass | UI + page requests | Booking Mon 11:00 (<24 h away): Reschedule disabled with "You can reschedule online until 24 h before the start."; POST from U1's page → 409 RESCHEDULING_WINDOW_PASSED; the admin moved it to 13:00 in Admin → Bookings and Audit log shows 1 ADMIN_OVERRIDE entry.Page requests: POST /api/bookings/32316a27-ab0b-4bfd-8c18-d2fbe8124dc2/reschedule → 409 RESCHEDULING_WINDOW_PASSED. |
| CHG-05 | Cancel a booking | Pass | UI | Cancel booking → reason "Sick": detail shows Cancelled with the time and reason; listed under My bookings → Cancelled; 10:00 bookable again; the booking is still listed in Admin → Bookings with status Cancelled (not deleted). |
| CHG-06 | Cancellation window | Pass | UI + page requests | Booking Mon 11:00 (<24 h away): Cancel disabled for U1; POST cancel from U1's page → 409 CANCELLATION_WINDOW_PASSED; Admin → Bookings → Cancel → Cancelled; Audit log shows ADMIN_OVERRIDE.Page requests: POST /api/bookings/2cb02a07-733a-4d48-9ca4-16abd27ca7ae/cancel → 409 CANCELLATION_WINDOW_PASSED. |
| CHG-07 | Finished bookings cannot change | Pass | UI + page requests | Admin → Bookings → Mark completed; U1's page shows Completed with Reschedule and Cancel disabled; POST cancel from U1's page → 409 INVALID_STATUS_TRANSITION "A completed booking cannot be cancelled".Page requests: POST /api/bookings/d28e32cd-2a5b-441a-a3bf-f646c27b49f1/cancel → 409 INVALID_STATUS_TRANSITION. |
| CHG-08 | Rescheduling out of a full session promotes the waitlist | Pass | UI | U1 moved to 2026-09-29 18:00 in the reschedule dialog → U3's waitlisted booking for 2026-09-28 18:00 is now Confirmed and U3's bell has "A place opened up – you're booked!". |
| CHG-09 | Pending booking stays pending when moved | Pass | UI | With Require Admin Confirmation on, the Pending 10:00 booking was moved to 11:00 in the dialog: the replacement is Pending and the original Rescheduled. |

### REC — Recurring and multi-resource bookings

| ID | Scenario | Result | Through | Observed |
| --- | --- | --- | --- | --- |
| REC-01 | Weekly series preview | Pass | UI | "8 available · 0 unavailable": the 8 Mondays from 2026-09-28 listed as Available; button "Book 8 available dates". |
| REC-02 | Conflicting occurrence is flagged | Pass | UI | Holiday added in Admin → Schedules; preview header "7 available · 1 unavailable"; struck through: "Mon, Oct 19, 2026 · 10:00 AM – 11:00 AM Holiday: Public holiday". |
| REC-03 | Book only the available dates | Pass | UI | "Your bookings were created."; My bookings → Upcoming lists 7 Confirmed Meeting Room bookings (one series); none on 2026-10-19. |
| REC-04 | No silent partial series | Pass | UI + page requests | POST from U1's page with skip_conflicts = false → 409 RECURRING_CONFLICTS "1 of 8 occurrences are unavailable" listing 2026-10-19; My bookings stays empty.Page requests: POST /api/bookings/recurring → 409 RECURRING_CONFLICTS. |
| REC-05 | Daily and monthly patterns | Pass | UI | Wizard → Repeat → Check dates: Daily every 2 × 3 from 2026-09-28: ['2026-09-28', '2026-09-30', '2026-10-02']; Monthly × 3 from 2026-10-31: ['2026-10-31', '2026-11-30', '2026-12-31']. |
| REC-06 | Recurring bookings switched off | Pass | UI + page requests | Allow Recurring Bookings off: the wizard shows no Repeat option; POST /api/bookings/recurring/preview from U1's page → 422 RECURRING_DISABLED "Recurring bookings are not enabled".Page requests: POST /api/bookings/recurring/preview → 422 RECURRING_DISABLED. |
| REC-07 | Occurrence limit | Pass | UI | Max Recurring Occurrences 5; the wizard's Check dates with 8 occurrences got 422 and showed "At most 5 occurrences are allowed". |
| REC-08 | Series needs a specific resource | Pass | UI | With Any available there is no Repeat option; after choosing John Carter it appears. |
| REC-09 | Book several resources together | Pass | UI + page requests | POST from U1's page with additional_resource_ids = [Studio, Camera Kit] at 2026-09-28 13:00 → 201; the booking page shows "Photographer + Studio, Camera Kit".Page requests: POST /api/bookings → 201. |
| REC-10 | All or nothing | Pass | UI + page requests | U2 booked Camera Kit 11:00–12:00 in the wizard; the shoot at 10:00 with both extras (POST from U1's page) → 409 "Camera Kit: This time overlaps another booking"; Photographer alone at 10:00 booked in the wizard → Confirmed (nothing was half-reserved).Page requests: POST /api/bookings → 409 CONFLICT. |
| REC-11 | Extra resources are held for the whole session | Pass | UI + page requests | After the 13:00–15:00 shoot, U2's wizard shows Camera Kit 14:00 as "Conflict"; POST from U2's page → 409 CONFLICT.Page requests: POST /api/bookings → 201; POST /api/bookings → 409 CONFLICT. |
| REC-12 | Extras only on individual services | Pass | UI + page requests | POST from U1's page → 422 "Additional resources can only be attached to individual bookings".Page requests: POST /api/bookings → 422 UNSUPPORTED. |

### ADM — Admin booking management, dashboard and calendar

| ID | Scenario | Result | Through | Observed |
| --- | --- | --- | --- | --- |
| ADM-01 | Overview metrics | Pass | UI | Stats {'upcoming': '2', 'active resources': '5', 'total users': '2', 'conflicts': '0'}; utilization John Carter "3% · 1 h of 40 h", Yoga Studio "14% · 1 h of 7 h"; Coming up lists both bookings. |
| ADM-02 | Bookings list and filters | Pass | UI | 28 bookings made in the wizard (3 cancelled): page 1 shows 25 ("1–25 of 28"), page 2 shows 3 ("26–28 of 28"); filters → {'status Cancelled': 3, 'resource Meeting Room A': 1, 'service Personal Training': 1, 'location Downtown': 2, 'dates 2026-09-29': 11, "customer 'Uma'": 13}; cleared → 28. |
| ADM-03 | Booking detail and history | Pass | UI | Modal of the rescheduled original: Demo User, 40.00, no actions (status Rescheduled); History: Booking Created · Demo User · 9/27/2026, 9:33:50 AM \| Booking Rescheduled · Demo User · 9/27/2026, 9:33:52 AM. |
| ADM-04 | Create a booking for a customer | Pass | UI | New booking → Demo → Demo User → Meeting Room → Meeting Room A → 2026-09-28 → 11:00 → Create booking; list row "Mon, Sep 28, 2026, 11:00 AM until 12:00 PM Demo User user@example.com Meeting Room Meeting Room A Confirmed Open"; Demo User's bell has "Booking confirmed". |
| ADM-05 | Override rules, never clashes | Pass | UI | Override at 19:00 (after hours) created (Demo User's list shows it) and Audit log shows ADMIN_OVERRIDE; override at 11:00 (taken by U2) refused: "This time overlaps another booking". |
| ADM-06 | Complete and no-show only after start | Pass | UI + page requests | Future booking: no Mark completed / Mark no-show buttons, and POST complete from the admin page → 422 NOT_STARTED. Started booking: Mark completed → Completed; no-show afterwards → 409 ("A completed booking cannot become no_show").Page requests: POST /api/admin/bookings/c44ee2d8-1034-4280-855f-cfc8fa03b1b0/complete → 422 NOT_STARTED; POST /api/admin/bookings/b272cf7e-75ef-47dc-8c4a-c47c6c56aea9/no-show → 409 INVALID_STATUS_TRANSITION. |
| ADM-07 | Reassign to another resource | Pass | UI | Reassign resource → Priya Nair: U1's booking is now with Priya Nair at 10:00; U1's bell has "Your booking was moved"; the History shows Booking Reassigned. |
| ADM-08 | Internal notes | Pass | UI | Notes saved (reopened modal shows "VIP customer"); History: Booking Created · Demo User · 9/27/2026, 9:34:49 AM \| Booking Updated · Admin · 9/27/2026, 9:34:52 AM. |
| ADM-09 | Calendar week view | Pass | UI | Set up in the UI (a Pending booking via Require Admin Confirmation, a Conflicted one via a block); week of 2026-09-28: Personal Training at 10:00 (top 192px = 4 h below 06:00) in John's colour (sky); Pending entry dashed; Conflicted entry red; on the current week a red now-line is drawn on today's column. |
| ADM-10 | Calendar day view | Pass | UI | Day 2026-09-28: columns ['John Carter', 'Meeting Room A', 'Priya Nair', 'Tennis Court 2', 'Yoga Studio']; hatched "MAINTENANCE" area in Meeting Room A; clicking the booking opened its modal. |
| ADM-11 | Calendar month view | Pass | UI | 2026-09-28 cell: ['07:00 AM Tennis Court', '08:00 AM Tennis Court', '09:00 AM Tennis Court', '+1 more']; clicking 28 opened the day view "Monday, September 28, 2026". |
| ADM-12 | Calendar filters | Pass | UI | Default week shows 5 entries (no 14:00 rescheduled original); John → 2 Personal Training; Meeting Room → 1; Riverside → Tennis + Yoga; Status Cancelled → the Yoga entry, struck through. |
| ADM-13 | Overlapping entries stay readable | Pass | UI | Both 10:00 entries render side by side in the Monday column (each 50% wide, left 0% and 50%). |
| ADM-14 | Audit log | Pass | UI | 2 entries with time, actor and action; filter ADMIN_OVERRIDE → 1 row(s) "9/27/2026, 9:36:14 AM Admin Admin Override booking 390ed1ca View"; View shows before/after JSON. |
| ADM-15 | Utilization report API | Pass | UI + page requests | GET from the admin's page → 5 resources; e.g. John Carter available 2400 min, booked 60 min, utilization 0.025.Page requests: GET /api/admin/reports/utilization → 200. |

### SCH — Schedule changes and conflict resolution

| ID | Scenario | Result | Through | Observed |
| --- | --- | --- | --- | --- |
| SCH-01 | Edit operating hours with no impact | **Fail** | UI | The hours were saved straight away (no preview; Downtown Saturday now ends 14:00 and Meeting Room A's Saturday slots end at 13:00), but the "Operating hours saved." confirmation was shown for 60 ms (4 frames, 109–153 ms after clicking Save), then disappeared when the form reloaded - too briefly for a person to read. |
| SCH-02 | Impact preview, then back out | Pass | UI | Modal "This change affects 1 existing booking" listing "Meeting Room · Meeting Room A Mon, Sep 28, 2026, 04:00 PM · Demo User Confirmed This time is outside operating hours"; after "Don't change anything" Downtown Monday still ends at 18:00. |
| SCH-03 | Block time and mark conflicts | Pass | UI | Preview "This change affects 1 existing booking" → Mark them as conflicts → Apply change: U1's booking Conflicted ("Blocked: Staff training"); Overview Conflicts 1 with a warning banner; U1's bell has "Your booking needs attention". |
| SCH-04 | Keep existing bookings | Pass | UI + page requests | Block saved with Keep existing bookings; U1's booking still Confirmed; U2's wizard shows 11:00 Blocked and POST from U2's page → 409 BLOCKED "Blocked: Staff training".Page requests: POST /api/bookings → 409 BLOCKED. |
| SCH-05 | Cancel affected bookings | Pass | UI | John's Monday set to 13:00–17:00 in Weekly availability → preview "This change affects 1 existing booking" → Cancel them: U1's booking Cancelled ("Schedule change: The resource is not available at this time"); U1's bell has "Your booking was cancelled". |
| SCH-06 | Resolve by overriding the block | Pass | UI | Conflicts → Resolve → Override & keep booking: U1's booking back to Confirmed; Conflicts shows "No conflicts"; Audit log shows Conflict Resolved. |
| SCH-07 | Resolve by rescheduling | Pass | UI | Suggestions: ['Override & keep booking', 'Cancel booking']. Chose John Carter 12:00 → new Confirmed booking at 12:00 linked to the original (now Rescheduled); Conflicts empty. |
| SCH-08 | Resolve by reassigning | Pass | UI | Resolve → Priya Nair 10:00: U1's booking is now with Priya Nair at the same time; U1's bell has "Your booking was moved". |
| SCH-09 | Resolve by cancelling | Pass | UI | Resolve → note "Sorry, trainer unavailable" → Cancel booking: U1's booking Cancelled with the note as reason; U1's bell has "Your booking was cancelled". |
| SCH-10 | Location-wide holiday | Pass | UI | Holiday for the whole Downtown location on 2026-09-28; bookable slots in the wizard: Downtown {'John Carter': 0, 'Priya Nair': 0, 'Meeting Room A': 0}; Riverside {'Tennis Court 2': 15, 'Yoga Studio': 1}. |
| SCH-11 | Block everything | Pass | UI | Block "Everything" 2026-09-28 12:00–13:00; 12:00 in the wizard: {'John Carter': 'Blocked', 'Priya Nair': 'Blocked', 'Meeting Room A': 'Blocked', 'Tennis Court 2': 'Blocked', 'Yoga Studio': 'closed'}. |
| SCH-12 | Remove a block | Pass | UI | Upcoming blocks & exceptions → Remove the Festival holiday → John 10:00 on 2026-09-28 bookable again; removing John's Saturday Special Hours (U1 booked inside them) first showed "This change affects 1 existing booking". |
| SCH-13 | Deleting a resource with bookings deactivates it | Pass | UI | Delete John Carter → prompt "Delete John Carter? Resources with booking history are deactivated instead." → preview "This change affects 1 existing booking" → Mark as conflicts: John's row now "John Carter experience: 8 specialization: Strength Training Person Downtown Asia/Kolkata — Inactive Edit Delete" (Inactive, not deleted) and U1's booking Conflicted; deleting the unused Temp Desk removed it from the list. |
| SCH-14 | Timezone change on a location | Pass | UI | Locations → Downtown → Timezone Europe/London → Save: preview "This change affects 1 existing booking" listing "Personal Training · John Carter Mon, Sep 28, 2026, 10:00 AM · Demo User Confirmed This time is outside operating hours"; applied with Mark as conflicts → U1's booking Conflicted; John Carter now shows Europe/London. |

### CFG — Catalogue, users and settings administration

| ID | Scenario | Result | Through | Observed |
| --- | --- | --- | --- | --- |
| CFG-01 | Create a location | Pass | UI + page requests | Locations → New location Uptown, Europe/London → row "Uptown — Europe/London Active Edit Delete"; POST with timezone "Mars/Base" from the admin's page → 422.Page requests: POST /api/admin/locations → 422. |
| CFG-02 | Create a resource with custom attributes | Pass | UI | Resources → New resource Court 1 (Court, Uptown, surface = clay, floodlights = true): list row "Court 1 surface: clay floodlights: true Court Uptown Europe/London — Active Edit Delete"; public page shows Surface clay, Floodlights Yes and Europe/London. |
| CFG-03 | Link services with a price override | Pass | UI | Court 1 → Services → Tennis Court with price 25.00 → Save services ("Saved."); Court 1's public page lists Tennis Court at 25.00 and the Tennis Court page lists Court 1. |
| CFG-04 | Weekly hours with a break | Pass | UI | Monday 09:00–17:00 Available + 12:00–13:00 Break → Save schedule → "Schedule saved."; the wizard's Monday slots: ['09:00', '10:00', '11:00', '13:00', '14:00', '15:00', '16:00']. |
| CFG-05 | Rule valid from a future date | Pass | UI | Monday period valid from 2026-10-05: the wizard shows 0 slots on 2026-09-28 and 8 on 2026-10-05. |
| CFG-06 | No weekly hours = business hours | Pass | UI | Every Court 1 period removed → Save schedule; with no Uptown operating hours Court 1 is bookable all day: 24 slots 00:00–23:00 ("Times shown in Europe/London."). |
| CFG-07 | Create a service | **Fail** | UI | Created and listed as "Pilates Group (8) 45 min 0 / 10 min 15.00 / h Active Edit Delete", but Pilates was already on /services before any resource offered it: its page read "Services Pilates 45 min · from 15.00 / h Group · 8 places Bo" with no resource listed, and in the booking wizard choosing it offered 0 resources to book with - a dead end for customers. After linking it to Yoga Studio it is listed with Yoga Studio as expected. |
| CFG-08 | Duration change spares existing bookings | Pass | UI | Personal Training changed to 90 minutes: the existing booking still reads "Mon, Sep 28, 2026 · 10:00 AM – 11:00 AM"; a new slot on 2026-09-29 reads 09:00 AM – 10:30 AM in the wizard summary. |
| CFG-09 | Delete a service | Pass | UI | Unused Service removed; Personal Training row now "Personal Training Individual 1 h – 2 h 0 / 15 min 40.00 / h Inactive Edit Delete" and gone from /services; U1's booking is still Confirmed. |
| CFG-10 | Delete a location | Pass | UI | Empty Site removed; Downtown row now "Downtown 12 MG Road, Bengaluru Map Asia/Kolkata Inactive Edit Delete". |
| CFG-11 | User management guards | Pass | UI | STAFF created ("Sam Staff staff@example.com Staff Active 9/27/2026 Edit"); SUPER_ADMIN → "You cannot create SUPER_ADMIN accounts"; own role → "You cannot change your own role"; self-suspend → "You cannot deactivate your own account". |
| CFG-12 | Admin sets a user's password | Pass | UI | Users → Edit Demo User → new password → Save; U1's open tab was sent to /login on refresh; signing in with the new password landed on /dashboard. |
| CFG-13 | Settings validation | Pass | UI + page requests | Minimum Booking Notice = -5 → "minimum_booking_notice must be a non-negative integer" (after reload the field still shows 0); PUT with an unknown key from the admin's page → 422 "Unknown settings: unknown_key".Page requests: PUT /api/admin/settings → 422 INVALID_SETTING. |
| CFG-14 | Settings apply immediately | Pass | UI + page requests | "Settings saved."; /api/config (read from U1's page) shows minimum_booking_notice=90, allow_waitlist=false; today's Tennis Court slots within 90 min of 15:11: [('16:00', 'Too Soon')] – no restart.Page requests: GET /api/config → 200. |
| CFG-15 | Business name and reminder offsets | Pass | UI | Header shows Acme Clinic after reload; the Reminder Offsets field now reads "1440, 120". |

### NTF — Notifications and reminders

| ID | Scenario | Result | Through | Observed |
| --- | --- | --- | --- | --- |
| NTF-01 | Booking confirmation | Pass | UI | Bell: "Notifications (1 unread)"; item "Booking confirmed Personal Training with John Carter, Mon 28 Sep 2026, 10:00–11:00 (Asia/Kolkata) Where: Downtown, 12 MG Road, Bengaluru Sun, Sep 27, 2026, 04:06 PM" opens the booking; API log: EMAIL to=user@example.com subject='Booking confirmed'. |
| NTF-02 | Pending and waitlist messages | Pass | UI | U1 booked with Require Admin Confirmation on, then joined a full Yoga session's waitlist; U1's bell: ['You are on the waitlist', 'Booking received – awaiting confirmation']. |
| NTF-03 | Cancellation messages | Pass | UI | Own cancel → "Booking cancelled"; admin cancel with a reason → "Your booking was cancelled": "Meeting Room with Meeting Room A, Mon 28 Sep 2026, 12:00–13:00 (Asia/Kolkata) Reason: Trainer ill". |
| NTF-04 | Reschedule and reassign messages | Pass | UI | "Booking rescheduled": "New time: Personal Training with John Carter, Mon 28 Sep 2026, 14:00–15:00 (Asia/Kolkata) Where: Downtown, 12 MG Road, Bengaluru"; then "Your booking was moved": "New time: Personal Training with Priya Nair, Mon 28 Sep 2026, 14:00–15:00 (Asia/Kolkata) Where: Downtown, 12 MG Road, Bengaluru". |
| NTF-05 | Waitlist promotion message | Pass | UI | After U1 cancelled (CAP-06), U3's bell has "A place opened up – you're booked!" and the e-mail with that subject is in the API log. |
| NTF-06 | Conflict message is in-app only | Pass | UI | After SCH-03, U1's bell has "Your booking needs attention"; the API's e-mail log has no e-mail for it. |
| NTF-07 | Mark as read | Pass | UI + page requests | Badge "Notifications (2 unread)" → Mark all read → "Notifications"; GET /api/notifications/unread-count from the page → 0.Page requests: GET /api/notifications/unread-count → 200. |
| NTF-08 | One-hour reminder, sent once | Pass | UI | Booked 2026-09-27 15:45 (31 min ahead) in the wizard; after 2 job runs the bell has exactly one "Reminder: your booking starts in 1 hour"; after an API restart and another run still one in-app and one reminder e-mail. |
| NTF-09 | 24-hour reminder | Pass | UI | Booked 2026-09-28 14:15 (about 23 h ahead) in the wizard; the bell has one "Reminder: your booking starts in 24 hours" after the first job run and still one after the next. |
| NTF-10 | Real e-mail delivery and failure | Pass | UI + database check (notifications table) | With the catcher up the confirmation arrived ("Subject: Booking confirmed"); with it stopped the second booking still showed "You're booked!" and its e-mail row is FAILED ("[Errno 111] Connection refused…"). |
| NTF-11 | Notifications are private | Pass | UI + page requests | U2's bell shows "Nothing yet."; POST /api/notifications/<U1's id>/read from U2's page → 404.Page requests: POST /api/notifications/225c29e1-9563-461a-90cc-03b08a484eea/read → 404 NOT_FOUND. |

### TZ — Timezones and daylight saving

| ID | Scenario | Result | Through | Observed |
| --- | --- | --- | --- | --- |
| TZ-01 | Browser timezone does not matter | Pass | UI | Browser in America/New_York: slots 09:00–17:00, "Times shown in Asia/Kolkata."; booked 10:00 and the detail reads "Mon, Sep 28, 2026 · 10:00 AM – 11:00 AM" (Timezone Asia/Kolkata). |
| TZ-02 | Times without an offset are local | Pass | UI + page requests | POST from U1's page with start "2026-09-28T10:00" (no offset) → start_datetime 2026-09-28T04:30:00Z (10:00 Asia/Kolkata); the booking page reads "Mon, Sep 28, 2026 · 10:00 AM – 11:00 AM".Page requests: POST /api/bookings → 201. |
| TZ-03 | Locations in different zones | Pass | UI | Locations → Riverside → Europe/London; court slots 07:00–21:00 with "Times shown in Europe/London."; My bookings shows both at 10:00 AM, each in its own zone (Europe/London and Asia/Kolkata, i.e. 09:00 and 04:30 UTC). |
| TZ-04 | Clocks go forward | Pass | UI | Night Desk (America/New_York) on 2027-03-14: ['00:00', '01:00', '03:00', '04:00', '05:00'] — no 02:00. |
| TZ-05 | Clocks go back | Pass | UI | Night Desk on 2026-11-01: ['00:00', '01:00', '01:00', '02:00', '03:00', '04:00', '05:00'] (7 one-hour slots); the two 01:00 slots are distinct times (-04:00 and -05:00 offsets, UTC 05:00 and 06:00). |
| TZ-06 | Weekly series keeps the wall-clock time | Pass | UI | Weekly 10:00 × 3 from 2027-03-08 across the 2027-03-14 change: ['Mon, Mar 8, 2027 · 10:00 AM – 11:00 AM Available', 'Mon, Mar 15, 2027 · 10:00 AM – 11:00 AM Available', 'Mon, Mar 22, 2027 · 10:00 AM – 11:00 AM Available'] (New York time; UTC 15:00, 14:00, 14:00), all Available. |

### OPS — Deployment smoke checks

| ID | Scenario | Result | Through | Observed |
| --- | --- | --- | --- | --- |
| OPS-01 | Stack starts clean | Pass | Command line (docker compose) + UI | crm-backend:test built; crm-frontend:test built; db running healthy; backend logged "Running upgrade -> 0001" and "Application startup complete"; http://localhost:8080 loaded the app in the browser; the browser at /api/health shows {'status': 'ok'}. |
| OPS-02 | Seeding is repeatable | Pass | Command line (docker compose exec) | First run: "Seeded. Admin: admin@example.com / admin12345 User: user@example.com / user12345"; second run: "Seed data already present.". |
| OPS-03 | Deep links survive a reload | Pass | UI | On http://localhost:8080: reloading /admin/calendar showed "Calendar"; reloading /bookings/1b79f383… (booked in the wizard) showed "Booking details". |
| OPS-04 | Data persists across restarts | Pass | UI + command line (docker compose restart) | Booked Tennis Court 2 2026-09-29 08:00 in the wizard on :8080; `docker compose restart`; signed in again: My bookings still lists it as Confirmed. |
| OPS-05 | Schema matches the code | Pass | Command line (alembic check) | `alembic check` → "No new upgrade operations detected.". |
| OPS-06 | Automated suites pass | Pass | Command line (pytest, npm run build) | pytest: "79 passed in 24.28s"; frontend: type-check + build "✓ built in 672ms". |
| OPS-07 | Two API replicas send each reminder once | Pass | UI + command line (docker compose --scale) | 2 backend replicas running; booked 2026-09-27 16:15 in the wizard on :8080; after 150 s the bell has exactly 1 "Reminder: your booking starts in 1 hour" and the replicas' logs show 1 reminder e-mail. (Scaled back to 1 replica afterwards.) |
| OPS-08 | Static asset caching | Pass | UI | /assets/index-B7aAO1sY.js (loaded by the page) → Cache-Control "public, max-age=31536000, immutable"; / → Content-Security-Policy "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'". |
