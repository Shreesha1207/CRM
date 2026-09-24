# End-to-End Test Results – Booking & Appointment System

**160 of 164 cases passed; 4 failed (1 high, 1 medium, 2 low severity).** Every case was executed. Run on 2026-09-24, 16:24–16:42 IST, against commit `0574d9d` on branch `claude/gracious-goldberg-outqt8`, following *End-to-End Test Cases – Booking & Appointment System* step by step.

| Result | Cases |
| --- | --- |
| Pass | 160 |
| Fail | 4 |

| Area | Cases | Pass | Fail |
| --- | --- | --- | --- |
| AUTH — Authentication and accounts | 14 | 14 | 0 |
| SEC — Authorization and security | 14 | 14 | 0 |
| AVL — Browsing and availability | 14 | 14 | 0 |
| BKG — Making bookings and booking rules | 17 | 16 | 1 |
| CAP — Group sessions, waitlists and simultaneous bookings | 15 | 14 | 1 |
| CHG — Rescheduling and cancellation | 9 | 8 | 1 |
| REC — Recurring and multi-resource bookings | 12 | 12 | 0 |
| ADM — Admin booking management, dashboard and calendar | 15 | 15 | 0 |
| SCH — Schedule changes and conflict resolution | 14 | 14 | 0 |
| CFG — Catalogue, users and settings administration | 15 | 15 | 0 |
| NTF — Notifications and reminders | 11 | 10 | 1 |
| TZ — Timezones and daylight saving | 6 | 6 | 0 |
| OPS — Deployment smoke checks | 8 | 8 | 0 |

## Failures

### NTF-08 — One-hour reminder, sent once (High)

- **Expected:** Exactly one "Reminder: your booking starts in 1 hour" in-app message and one reminder e-mail, also across an API restart.
- **Observed:** The long-running API queued no reminder in 130 s (two job runs), and the job's advisory lock was held by an idle pooled database connection. After an API restart, the next job run sent 1 in-app reminder ("Reminder: your booking starts in 1 hour") and 1 e-mail (status SENT).
- **Impact:** In a long-running API process, reminders (and retries of queued e-mails) silently stop once the database pool holds more than one connection. With several replicas, one leaked lock stops the job on all of them.
- **Root cause:** `app/jobs/runner.py` takes `pg_try_advisory_lock` through a SQLAlchemy Session. `queue_reminders()` and `dispatch_pending()` commit, which hands that connection back to the pool, so `pg_advisory_unlock` runs on a different connection, returns false, and the lock stays held by an idle pooled connection. Later runs that check out any other connection cannot get the lock and skip all work. Reproduced in isolation: lock taken on database backend 17182, unlock ran on 17184 and returned false, 17182 kept the lock. During the run the long-lived API sent no reminder in 130 s; right after a restart (fresh pool) the same reminder went out within seconds.
- **Suggested fix:** Hold the lock on one dedicated connection for the whole run (`engine.connect()` → `pg_try_advisory_lock` → do the work in a separate Session → `pg_advisory_unlock` on that same connection), and add a regression test that calls `run_once()` with several pooled connections.

### CHG-02 — Move by less than the duration (Medium)

- **Expected:** Moving a John Carter 10:00 booking to 10:30 (slot interval 30) succeeds; the booking's own old time does not count as a clash.
- **Observed:** In the reschedule dialog 10:30 is disabled and labelled "Conflict": the picker counts the booking's own current time as a clash, so the move cannot be made in the UI. The API accepts the same move (POST /reschedule → 200, new start 10:30).
- **Impact:** Customers cannot shift a booking by less than its duration from the app, although the booking engine allows it.
- **Suggested fix:** Let GET /api/availability take the id of the booking being moved (honoured only for its owner or staff) and exclude it from the occupancy check; pass it from the reschedule dialog.

### BKG-12 — Booking across midnight (Low)

- **Expected:** Booking 23:30–00:30 is created; the detail page shows the end on D+1.
- **Observed:** Booking stored Mon 28 Sep 23:30–Tue 29 Sep 00:30 IST, but the detail page shows only "Mon, Sep 28, 2026 · 11:30 PM – 12:30 AM": the end date (Tue Sep 29) is not displayed
- **Impact:** Bookings that cross midnight read as ending on the start date, which can mislead customers and staff.
- **Suggested fix:** In the frontend's formatRange, include the end date when it falls on a different local day than the start.

### CAP-07 — Waitlist disabled (Low)

- **Expected:** With the waitlist switched off, a full session shows no Join waitlist button; the API returns 409 CAPACITY_REACHED.
- **Observed:** A disabled "Join waitlist" button is still shown (message: "This session is full. Please choose another time."); the doc expects no Join waitlist button. API correctly refuses: 409 CAPACITY_REACHED "This session is full and the waitlist is disabled"
- **Impact:** Cosmetic: users see a disabled Join waitlist button next to "Please choose another time". The API behaves correctly.
- **Suggested fix:** In the booking wizard, hide the Join waitlist button when the session is full and allow_waitlist is off.

## How the run was done

- **One shared instance** of each app for the whole run: the API (uvicorn on 127.0.0.1:8000, background jobs every 60 s, e-mail to the console log) and the Vite dev server on http://localhost:5173. Before each case the database was reset to the seeded demo data without restarting either.
- **UI steps** ran in headless Chromium (Playwright 1.63, 1360×900) through the web app; **API steps** called the same instance directly. Signed-in browser sessions used the same cookies the login form sets; the login form itself is exercised in the AUTH cases.
- **Simulated users** each used their own loopback address, so the per-IP rate limits of the single instance behaved as they would for separate people.
- **Dates:** D = 2026-09-28 (the next Monday); all times Asia/Kolkata unless a case says otherwise.
- **SEC-10 web headers and OPS** ran against the Docker Compose stack on http://localhost:8080.

## Deviations from the written steps

| Case | What was done differently |
| --- | --- |
| AUTH-10 | Expiry simulated by moving the token's expires_at 1 minute into the past, instead of restarting the shared API with PASSWORD_RESET_TTL_MINUTES=1 and waiting 2 minutes. |
| SEC-14 | Each attempt was a separate short-lived process on port 8099; the shared instance was not touched. |
| BKG-06 | Meeting Room A closes at 18:00, so the 3-hour request used Tennis Court 2 (open 07:00–22:00) to stay within hours. |
| CHG-07 | The started booking was created by the admin with override_rules, starting at 15:58 IST (30 minutes before the case ran), since users cannot book in the past. |
| REC-05 | Previewed through the same API the wizard's Check dates button calls. |
| ADM-06 | The started booking was created by the admin with override_rules, starting at 15:59 IST (30 minutes before the case ran), since users cannot book in the past. |
| NTF-08 | Booked via the API for 17:17 IST (45 minutes ahead); the hourly slot grid had no slot 30–60 minutes away. |
| NTF-08 | Restarted the shared API instance as the case requires; it kept running afterwards. |
| NTF-09 | The later 1-hour reminder was not observed live (it is due 22 hours after booking); the same mechanism is exercised end to end in NTF-08. |
| NTF-10 | The shared API instance was restarted with EMAIL_BACKEND=smtp pointing at a local debugging SMTP server for this case, then restarted again with its normal console mail settings. |
| OPS-01 | docker compose --build cannot pull packages through this sandbox's TLS interception, so the images were built from the repo Dockerfiles with the sandbox CA added and compose ran them via an override file; the stack definition is unchanged. |

## Corrections for the test plan

These cases passed, but their written steps or expected results should be updated.

| Case | Correction |
| --- | --- |
| BKG-15 | The expected U3 message only appears if U3 selected 10:00 before U1 and U2 confirmed. Opened afterwards, the slot correctly shows as Taken and cannot be selected. The case was run with all three selecting first. |
| BKG-16 | The example alternative "John Carter 12:00" cannot be offered: U2's 11:00 Personal Training plus its 15-minute buffer keeps John busy until 12:15. Replace the example with "John Carter 09:00 or 14:00". |
| CAP-10 | Give Studio hire a duration that fits the studio's 18:00–19:00 hours (the run used 30 minutes); a 60-minute service at 18:30 is refused as outside availability before the clash check. |
| AVL-13 | "status" lives on each resource's slots (resources[].slots[]); the aggregated top-level slots carry start, end, available and resource_ids. Reword the expected result. |

## Results by case

### AUTH — Authentication and accounts

| ID | Scenario | Result | Observed |
| --- | --- | --- | --- |
| AUTH-01 | Register a new account | Pass | Landed on /dashboard with heading "Hello, Ann"; users row: email=ann@example.com, role=USER. |
| AUTH-02 | Duplicate e-mail rejected | Pass | Form showed "An account with this e-mail already exists"; still exactly 1 account for ann@example.com; page stayed on /register. |
| AUTH-03 | Registration field validation | Pass | Browser blocked both submissions (7-char password and 'not-an-email' flagged invalid, no request sent); direct API calls returned 422 and 422. |
| AUTH-04 | Sign in lands on the right home | Pass | U1 landed on /dashboard with 'Demo User' and the notification bell in the header; after sign-out Admin landed on /admin. |
| AUTH-05 | Bad credentials | Pass | Wrong password and unknown e-mail both showed "Invalid e-mail or password"; stayed on /login. |
| AUTH-06 | Deep link after sign-in | Pass | Signed-out visit went to /login?next=%2Fbookings; after sign-in landed on /bookings. |
| AUTH-07 | Sign-out revokes the session | Pass | Token worked before sign-out (200); after sign-out the UI returned home, /dashboard redirected to /login?next=%2Fdashboard, and the same token got 401. |
| AUTH-08 | Forgot password request | Pass | Both requests showed "If that e-mail is registered, a reset link has been sent."; API log has 1 reset e-mail (with link) to user@example.com and none to nobody@example.com. |
| AUTH-09 | Reset password with the link | Pass | Reset succeeded; old password → 401, new password → 200; reusing the link showed "This reset link is invalid or has expired"; a session from before the reset now gets 401. |
| AUTH-10 | Reset link expires | Pass | Expired link showed "This reset link is invalid or has expired"; password unchanged (old password still signs in: 200). |
| AUTH-11 | Edit profile | Pass | "Saved." shown; after reload the header shows Demo Person and the form holds the new name and phone. |
| AUTH-12 | Change password | Pass | Wrong current password → "Current password is incorrect"; correct one → "Password updated. Other sessions were signed out."; the second browser was sent to /login?next=%2Fdashboard; this browser stayed signed in. |
| AUTH-13 | Suspended account | Pass | Admin set Demo User to Suspended; sign-in showed "This account is not active"; U1's open tab went to /login?next=%2Fdashboard on refresh. |
| AUTH-14 | Anonymous visit is clean | Pass | Home loaded with no console errors; API calls: /api/auth/session 200, /api/config 200, /api/services 200. |

### SEC — Authorization and security

| ID | Scenario | Result | Observed |
| --- | --- | --- | --- |
| SEC-01 | Users cannot open the admin area | Pass | Nav: Services · Resources · Dashboard · Book · My bookings (no Admin); /admin redirected to /dashboard. |
| SEC-02 | Users are refused by admin APIs | Pass | GET /api/admin/bookings → 403; GET /api/admin/users → 403; GET /api/admin/dashboard → 403; GET /api/admin/settings → 403; POST /api/admin/resources → 403. |
| SEC-03 | Other users' bookings are hidden | Pass | U2's page showed "Booking not found"; cancel → 404, reschedule → 404; U2's list total = 0; U1's booking still CONFIRMED. |
| SEC-04 | Role cannot be self-assigned | Pass | 201 Created; response role=USER, stored role=USER; permissions=[]. |
| SEC-05 | No booking for others, no rule override | Pass | override_rules → 403 (Only administrators can override booking rules); booking sent with U2's user_id → 201 owned by user@example.com. |
| SEC-06 | Unauthenticated calls | Pass | /api/bookings → 401; /api/admin/bookings → 401; Bearer not-a-jwt → 401. |
| SEC-07 | CSRF protection on cookie sessions | Pass | Replay with cookie only → 403 "CSRF token missing or invalid"; with X-CSRF-Token → 200 (name now Changed). |
| SEC-08 | Login rate limit | Pass | Attempts 1-10 → 401; attempt 11 → 429 "Too many requests, please try again later" with Retry-After: 300. |
| SEC-09 | Passwords are hashed | Pass | password_hash starts with argon2id ($argon2id$v=19$m=65536,t=3,p=4…); the plain password appears nowhere in the row. |
| SEC-10 | Security headers | Pass | API: X-Content-Type-Options nosniff, X-Frame-Options DENY, CSP "default-src 'none'; frame-ancestors 'none'". Docker web app CSP: "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'". |
| SEC-11 | Script injection is shown as text | Pass | Name and notes containing <img src=x onerror=alert(1)> were displayed literally on booking detail, Admin → Bookings list and modal; no alert fired and no <img> element was created. |
| SEC-12 | SQL injection in search | Pass | /services search → 0 results, no error (API 200 []); Admin → Bookings search → "No bookings match these filters." with 1 booking in the system. |
| SEC-13 | STAFF role sees only its tools | Pass | STAFF admin nav: Overview, Calendar, Bookings; GET /api/admin/settings → 403. |
| SEC-14 | Unsafe production config refused | Pass | Default secret: exit code 1, "Set JWT_SECRET to a random value of at least 32 characters in production". Strong secret + COOKIE_SECURE=false: exit code 1, "COOKIE_SECURE must be true in production (serve the app over HTTPS)". |

### AVL — Browsing and availability

| ID | Scenario | Result | Observed |
| --- | --- | --- | --- |
| AVL-01 | Browse and filter services | Pass | All: Consultation, Meeting Room, Personal Training, Tennis Court, Yoga Class; 'yoga' → ['Yoga Class']; max 15 → ['Meeting Room', 'Yoga Class']; Riverside → ['Tennis Court', 'Yoga Class']. |
| AVL-02 | Service detail | Pass | Rows: "John Carter Person · Downtown · 1 h · 40.00 Book" / "Priya Nair Person · Downtown · 1 h · 45.00 Book"; Book → /book with Personal Training and John Carter pre-selected. |
| AVL-03 | Browse and filter resources | Pass | Person → ['John Carter', 'Priya Nair']; Riverside Sports Club → ['Tennis Court 2', 'Yoga Studio']. |
| AVL-04 | Resource detail | Pass | John: "Monday 09:00–13:00, 14:00–18:00", "Saturday Unavailable", "Sunday Unavailable"; details "Experience 8 Specialization Strength Training". Meeting Room A: "Available during business hours". |
| AVL-05 | Hours and lunch break respected | Pass | 2026-09-28 slots: 09:00, 10:00, 11:00, 12:00, 14:00, 15:00, 16:00, 17:00; "Times shown in Asia/Kolkata.". |
| AVL-06 | Slot interval follows duration (spec example) | Pass | 16 slots every 30 min: 09:00–12:30 and 14:00–17:30. |
| AVL-07 | Operating hours apply; closed day | Pass | 2026-09-28: 09:00, 10:00, 11:00, 12:00, 13:00, 14:00, 15:00, 16:00, 17:00; 2026-10-04 (Sunday): "No times on this date. Try another day." |
| AVL-08 | Resource day off | Pass | 2026-09-30 (Wednesday) with Priya Nair: "No times on this date. Try another day." |
| AVL-09 | Only slots that fit the duration | Pass | 45-minute Quick Check on Meeting Room A (Mon 09:00–10:30): slots ['09:00', '09:45']. |
| AVL-10 | Past and too-soon slots disabled | Pass | At 16:25 IST with 120 min notice: 09:00 Past, 10:00 Past, 11:00 Past, 12:00 Past, 13:00 Past, 14:00 Past, 15:00 Past, 16:00 Past, 17:00 Too Soon. |
| AVL-11 | Advance booking window | Pass | 2026-10-29: all 9 slots disabled and labelled "Too Far Ahead". |
| AVL-12 | Time-first flow | Pass | 10:00 "2 free", 09:00 "1 free"; after choosing 10:00: Any available, John Carter, Priya Nair. |
| AVL-13 | Availability API shape | Pass | date=2026-09-28; slots[0]={start: 2026-09-28T09:00:00+05:30, end: 2026-09-28T09:30:00+05:30, available: True}; resources[0].slots[0].status=AVAILABLE. |
| AVL-14 | Booking summary panel and date navigation | Pass | Summary: "Summary Service Personal Training With Priya Nair When Mon, Sep 28, 2026 · 10:00 AM – 11:00 AM Price 45.00"; ← disabled on 2026-09-24. |

### BKG — Making bookings and booking rules

| ID | Scenario | Result | Observed |
| --- | --- | --- | --- |
| BKG-01 | Book an individual slot | Pass | Booking 59d95cf1: "You're booked!", status Confirmed, price 40.00, note shown; listed under My bookings → Upcoming and as the dashboard's Next booking. |
| BKG-02 | Booked slot shows as taken | Pass | 10:00 is disabled, struck through and labelled "Conflict". |
| BKG-03 | Partial overlap rejected | Pass | 409 CONFLICT "This time overlaps another booking". |
| BKG-04 | Back-to-back allowed | Pass | 201 Created: Consultation 09:30–10:00 right before the 10:00 booking. |
| BKG-05 | Buffer blocks the following time | Pass | Personal Training 11:00 and Consultation 11:00 disabled (Conflict); Personal Training 12:00 and Consultation 11:30 available. |
| BKG-06 | Minimum notice enforced | Pass | Meeting Room A at 17:30 (1 h ahead) → 409 TOO_SOON; Tennis Court 2 at 19:30 (3 h ahead) → 201 created (now 16:26 IST). |
| BKG-07 | Past time rejected | Pass | 2026-09-23 10:00 → 409 PAST "This time is in the past". |
| BKG-08 | Outside resource availability | Pass | 2026-09-28 13:00 → 409 OUTSIDE_AVAILABILITY; 2026-10-03 (Saturday) 10:00 → 409 OUTSIDE_AVAILABILITY ("The resource is not available at this time"). |
| BKG-09 | Outside operating hours | Pass | 2026-09-28 19:00 → 409 OUTSIDE_OPERATING_HOURS "This time is outside operating hours". |
| BKG-10 | Blocked period overrides availability | Pass | Block saved from Admin → Schedules; 12:00 and 13:00 show Blocked (14:00 open); POST 13:00 → 409 BLOCKED "Maintenance: Deep clean". |
| BKG-11 | Special hours replace the weekly schedule | Pass | 2026-09-28: ['10:00', '11:00']; 2026-09-29: 07:00–21:00 (15 slots). |
| BKG-12 | Booking across midnight | **Fail** | Booking stored Mon 28 Sep 23:30–Tue 29 Sep 00:30 IST, but the detail page shows only "Mon, Sep 28, 2026 · 11:30 PM – 12:30 AM": the end date (Tue Sep 29) is not displayed |
| BKG-13 | Inactive resource cannot be booked | Pass | Priya set Inactive from Admin → Resources; /resources lists ['John Carter', 'Meeting Room A', 'Tennis Court 2', 'Yoga Studio']; wizard offers no Priya; POST → 409 RESOURCE_INACTIVE "Priya Nair is not accepting bookings". |
| BKG-14 | Service the resource does not offer | Pass | 422 SERVICE_NOT_OFFERED "John Carter does not provide Meeting Room". |
| BKG-15 | "Any available" assigns a free resource | Pass | U1 → John Carter; U2 → Priya Nair; U3 → "No resource is available at that time". A fresh view afterwards shows 10:00 as "Taken" (disabled). |
| BKG-16 | Slot taken while choosing → alternatives | Pass | U1 saw "This time overlaps another booking" with suggestions: Priya Nair Mon, Sep 28, 2026, 11:00 AM; Priya Nair Mon, Sep 28, 2026, 10:00 AM; Priya Nair Mon, Sep 28, 2026, 12:00 PM; John Carter Mon, Sep 28, 2026, 09:00 AM; Priya Nair Mon, Sep 28, 2026, 01:00 PM; John Carter Mon, Sep 28, 2026, 02:00 PM. Picking the first booked Priya Nair at 11:00. |
| BKG-17 | Admin confirmation required | Pass | U1 saw "Request received" and Pending; slot shown to U2 as CONFLICT; after Admin → Confirm the status is Confirmed and U1 has notifications ['Booking confirmed', 'Booking received – awaiting confirmation']. |

### CAP — Group sessions, waitlists and simultaneous bookings

| ID | Scenario | Result | Observed |
| --- | --- | --- | --- |
| CAP-01 | Join a group class | Pass | Booked 18:00 (was "12 left"), status Confirmed; slot now shows "11 left". |
| CAP-02 | Book several places | Pass | Booked with quantity 3, price 36.00; the session now has 8 places left ("8 left"). |
| CAP-03 | More places than the session holds | Pass | With Places = 13 the 18:00 slot is disabled ("Quantity Exceeds Capacity"); POST → 409 QUANTITY_EXCEEDS_CAPACITY "The requested quantity exceeds the capacity". |
| CAP-04 | Full session | Pass | 18:00 shows "Capacity Reached"; after selecting it: warning "This session is full…" and an enabled Join waitlist button. |
| CAP-05 | Join the waitlist | Pass | U3: "You're #1 on the waitlist. We'll confirm you automatically if a place opens." (Waitlisted); U4: "You're #2 on the waitlist. We'll confirm you automatically if a place opens."; both dashboards show "On a waitlist 1". |
| CAP-06 | Cancellation promotes the waitlist | Pass | U1 cancelled in the UI → U3 CONFIRMED with "A place opened up – you're booked!"; U4 now #1; remaining 0. |
| CAP-07 | Waitlist disabled | **Fail** | A disabled "Join waitlist" button is still shown (message: "This session is full. Please choose another time."); the doc expects no Join waitlist button. API correctly refuses: 409 CAPACITY_REACHED "This session is full and the waitlist is disabled" |
| CAP-08 | Leave the waitlist | Pass | Leave waitlist → CANCELLED; after U1 cancelled a seat U4 is still CANCELLED (not promoted). |
| CAP-09 | One booking per person per session | Pass | Second attempt → 409 ALREADY_BOOKED "You already have a booking for this session". |
| CAP-10 | A group session blocks private use of the room | Pass | Studio hire at 18:30 during the 18:00 yoga session → 409 CONFLICT "This time overlaps another booking". |
| CAP-11 | Simultaneous requests for one slot | Pass | 10 parallel POSTs → one 201 and nine 409 CONFLICT; 1 active booking at 15:00. |
| CAP-12 | Simultaneous overlapping times | Pass | Parallel requests for 15:00, 15:10, 15:20, 15:30, 15:40, 15:50 → exactly one succeeded (15:50); the rest got [409]. |
| CAP-13 | Simultaneous requests for a 3-seat session | Pass | 12 parallel requests → 3 CONFIRMED and 9 WAITLISTED (responses and database agree). |
| CAP-14 | Simultaneous cancellations | Pass | Both confirmed users cancelled at once → the first two waitlisted users were promoted; the third is still waitlisted at #1. |
| CAP-15 | Database refuses double bookings | Pass | Overlapping active row rejected: "conflicting key value violates exclusion constraint "ex_booking_resources_no_overlap""; overlapping inactive row accepted. |

### CHG — Rescheduling and cancellation

| ID | Scenario | Result | Observed |
| --- | --- | --- | --- |
| CHG-01 | Reschedule to a free time | Pass | New booking 8d0a6047 at 14:00 with "Your booking has been moved." and a Moved from link; original is RESCHEDULED with "See the new booking"; 10:00 bookable again. |
| CHG-02 | Move by less than the duration | **Fail** | In the reschedule dialog 10:30 is disabled and labelled "Conflict": the picker counts the booking's own current time as a clash, so the move cannot be made in the UI. The API accepts the same move (POST /reschedule → 200, new start 10:30). |
| CHG-03 | Failed reschedule keeps the original | Pass | Reschedule to 14:30 → 409 CONFLICT; original still CONFIRMED at 10:00 and 10:00 still taken (CONFLICT). |
| CHG-04 | Rescheduling window | Pass | Booking 2026-09-25 10:00 (<24 h away): Reschedule disabled, note "You can reschedule online until 24 h before the start."; API → 409 RESCHEDULING_WINDOW_PASSED; admin moved it to 12:00 and the audit log has 1 ADMIN_OVERRIDE entry. |
| CHG-05 | Cancel a booking | Pass | Detail shows Cancelled with the time and reason "Sick"; listed under Cancelled; 10:00 bookable again; the row is kept in the database (status CANCELLED). |
| CHG-06 | Cancellation window | Pass | Cancel disabled for U1; API → 409 CANCELLATION_WINDOW_PASSED; admin cancellation → CANCELLED; audit log has an ADMIN_OVERRIDE entry. |
| CHG-07 | Finished bookings cannot change | Pass | Marked completed from Admin → Bookings; U1's Reschedule and Cancel are disabled; API cancel → 409 INVALID_STATUS_TRANSITION "A completed booking cannot be cancelled". |
| CHG-08 | Rescheduling out of a full session promotes the waitlist | Pass | U1 moved to 2026-09-29 18:00 → U3 promoted to CONFIRMED and notified. |
| CHG-09 | Pending booking stays pending when moved | Pass | Replacement booking at 11:00 is PENDING; the original is RESCHEDULED. |

### REC — Recurring and multi-resource bookings

| ID | Scenario | Result | Observed |
| --- | --- | --- | --- |
| REC-01 | Weekly series preview | Pass | "8 available · 0 unavailable": 8 Mondays from 2026-09-28 listed as Available; button "Book 8 available dates". |
| REC-02 | Conflicting occurrence is flagged | Pass | "7 available · 1 unavailable"; struck through: "Mon, Oct 19, 2026 · 10:00 AM – 11:00 AM Holiday: Public holiday". |
| REC-03 | Book only the available dates | Pass | "Your bookings were created."; 7 CONFIRMED bookings sharing series c54181c9; none on 2026-10-19. |
| REC-04 | No silent partial series | Pass | 409 RECURRING_CONFLICTS "1 of 8 occurrences are unavailable" listing 2026-10-19; 0 bookings created. |
| REC-05 | Daily and monthly patterns | Pass | Daily every 2 days: ['2026-09-28', '2026-09-30', '2026-10-02']; monthly from 2026-10-31: ['2026-10-31', '2026-11-30', '2026-12-31']. |
| REC-06 | Recurring bookings switched off | Pass | No Repeat option in the wizard; API preview → 422 RECURRING_DISABLED "Recurring bookings are not enabled". |
| REC-07 | Occurrence limit | Pass | 422 "At most 5 occurrences are allowed". |
| REC-08 | Series needs a specific resource | Pass | With Any available there is no Repeat option; after choosing John Carter it appears. |
| REC-09 | Book several resources together | Pass | 201; booking detail shows "Photographer + Studio, Camera Kit". |
| REC-10 | All or nothing | Pass | Shoot with extras → 409 "Camera Kit: This time overlaps another booking"; Photographer alone at 10:00 → 201 (nothing was half-reserved). |
| REC-11 | Extra resources are held for the whole session | Pass | Camera Kit on its own at 14:00 (inside the 13:00–15:00 shoot) → 409 CONFLICT. |
| REC-12 | Extras only on individual services | Pass | 422 "Additional resources can only be attached to individual bookings". |

### ADM — Admin booking management, dashboard and calendar

| ID | Scenario | Result | Observed |
| --- | --- | --- | --- |
| ADM-01 | Overview metrics | Pass | Stats {'upcoming': '2', 'active resources': '5', 'total users': '2', 'conflicts': '0'}; utilization John Carter "3% · 1 h of 40 h", Yoga Studio "14% · 1 h of 7 h"; Coming up lists both bookings. |
| ADM-02 | Bookings list and filters | Pass | 32 bookings: page 1 shows 25 ("1–25 of 32"), page 2 shows 7 ("26–32 of 32"); filters → {'status Cancelled': 3, 'resource Meeting Room A': 1, 'service Personal Training': 1, 'location Downtown': 2, 'dates 2026-09-29': 15, "customer 'Uma'": 15}. |
| ADM-03 | Booking detail and history | Pass | Modal: Demo User, 40.00, no actions (status Rescheduled); History: Booking Created · Demo User · 9/24/2026, 10:59:35 AM \| Booking Rescheduled · Demo User · 9/24/2026, 10:59:35 AM. |
| ADM-04 | Create a booking for a customer | Pass | Created from Admin → Bookings → New booking; list row "Mon, Sep 28, 2026, 11:00 AM until 12:00 PM Demo User user@example.com Meeting Room Meeting Room A Confirmed Open"; Demo User notified ("Booking confirmed"). |
| ADM-05 | Override rules, never clashes | Pass | Override at 19:00 (after hours) created and audited (1 ADMIN_OVERRIDE); override at 11:00 (taken) refused: "This time overlaps another booking". |
| ADM-06 | Complete and no-show only after start | Pass | Future booking: no Complete/No-show buttons, API → 422 NOT_STARTED. Started booking: Mark completed → COMPLETED; No-show afterwards → 409 (A completed booking cannot become no_show). |
| ADM-07 | Reassign to another resource | Pass | Booking now with Priya Nair at 10:00; U1 notified "Your booking was moved"; history includes Booking Reassigned. |
| ADM-08 | Internal notes | Pass | Notes saved ("VIP customer"); history shows: Booking Created · Demo User · 9/24/2026, 10:59:53 AM \| Booking Updated · Admin · 9/24/2026, 10:59:54 AM. |
| ADM-09 | Calendar week view | Pass | Week of 2026-09-28: Personal Training at 10:00 (top 192px = 4 h below 06:00) in John's colour (sky); Pending entry dashed; Conflicted entry red; on the current week a red now-line is drawn on today's column. |
| ADM-10 | Calendar day view | Pass | Day 2026-09-28: columns ['John Carter', 'Meeting Room A', 'Priya Nair', 'Tennis Court 2', 'Yoga Studio']; hatched "MAINTENANCE" area in Meeting Room A; clicking the booking opened its modal. |
| ADM-11 | Calendar month view | Pass | 2026-09-28 cell: ['07:00 AM Tennis Court', '08:00 AM Tennis Court', '09:00 AM Tennis Court', '+1 more']; clicking 28 opened the day view "Monday, September 28, 2026". |
| ADM-12 | Calendar filters | Pass | Default week shows 5 entries (no 14:00 rescheduled original); John → 2 Personal Training; Meeting Room → 1; Riverside → Tennis + Yoga; Status Cancelled → the Yoga entry, struck through. |
| ADM-13 | Overlapping entries stay readable | Pass | Both 10:00 entries render side by side in the Monday column (each 50% wide, left 0% and 50%). |
| ADM-14 | Audit log | Pass | 2 entries with time, actor and action; filter ADMIN_OVERRIDE → 1 row(s) "9/24/2026, 11:00:17 AM Admin Admin Override booking c9861648 View"; View shows before/after JSON. |
| ADM-15 | Utilization report API | Pass | 5 resources; e.g. John Carter available 2400 min, booked 60 min, utilization 0.025. |

### SCH — Schedule changes and conflict resolution

| ID | Scenario | Result | Observed |
| --- | --- | --- | --- |
| SCH-01 | Edit operating hours with no impact | Pass | Saved directly ("Operating hours saved."); Meeting Room A on 2026-10-03: 09:00–13:00. |
| SCH-02 | Impact preview, then back out | Pass | Modal "This change affects 1 existing booking" listing "Meeting Room · Meeting Room A Mon, Sep 28, 2026, 04:00 PM · Demo User Confirmed This time is outside operating hours"; after "Don't change anything" Monday is still 09:00–18:00. |
| SCH-03 | Block time and mark conflicts | Pass | Preview "This change affects 1 existing booking"; after Mark as conflicts: booking CONFLICTED ("Blocked: Staff training"); Overview Conflicts 1 with warning banner; U1 notified. |
| SCH-04 | Keep existing bookings | Pass | Block saved; existing booking still CONFIRMED; new booking at 11:00 → 409 BLOCKED "Blocked: Staff training". |
| SCH-05 | Cancel affected bookings | Pass | Preview "This change affects 1 existing booking"; Cancel them → booking CANCELLED ("Schedule change: The resource is not available at this time"); U1 notified. |
| SCH-06 | Resolve by overriding the block | Pass | Booking back to CONFIRMED; Conflicts page shows "No conflicts"; audit log has Conflict Resolved. |
| SCH-07 | Resolve by rescheduling | Pass | Suggestions: ['Priya Nair Mon, Sep 28, 2026, 10:00 AM', 'Priya Nair Mon, Sep 28, 2026, 11:00 AM', 'John Carter Mon, Sep 28, 2026, 12:00 PM', 'Priya Nair Mon, Sep 28, 2026, 12:00 PM', 'Priya Nair Mon, Sep 28, 2026, 01:00 PM', 'John Carter Mon, Sep 28, 2026, 02:00 PM', 'Override & keep booking', 'Cancel booking']. Chose John Carter 12:00 → new CONFIRMED booking linked to the original (now RESCHEDULED); no conflicts left. |
| SCH-08 | Resolve by reassigning | Pass | Chose Priya Nair 10:00 → booking moved to Priya at the same time; U1 notified "Your booking was moved". |
| SCH-09 | Resolve by cancelling | Pass | Booking CANCELLED with reason "Sorry, trainer unavailable"; U1 notified. |
| SCH-10 | Location-wide holiday | Pass | Bookable slots on 2026-09-28: Downtown {'John Carter': 0, 'Priya Nair': 0, 'Meeting Room A': 0}; Riverside {'Tennis Court 2': 15, 'Yoga Studio': 1}. |
| SCH-11 | Block everything | Pass | 12:00 on 2026-09-28: {'John Carter': 'BLOCKED', 'Priya Nair': 'BLOCKED', 'Meeting Room A': 'BLOCKED', 'Tennis Court 2': 'BLOCKED', 'Yoga Studio': 'closed'}. |
| SCH-12 | Remove a block | Pass | Holiday removed → John 10:00 on 2026-09-28 bookable again; removing John's Saturday special hours (with a booking inside) opened "This change affects 1 existing booking" first. |
| SCH-13 | Deleting a resource with bookings deactivates it | Pass | Confirm prompt "Delete John Carter? Resources with booking history are deactivated instead."; preview "This change affects 1 existing booking"; John now INACTIVE (not deleted), booking CONFLICTED; unused Temp Desk deleted (GET → 404). |
| SCH-14 | Timezone change on a location | Pass | Preview "This change affects 1 existing booking": "Personal Training · John Carter Mon, Sep 28, 2026, 10:00 AM · Demo User Confirmed This time is outside operating hours"; applied → booking CONFLICTED, Downtown resources now in Europe/London. |

### CFG — Catalogue, users and settings administration

| ID | Scenario | Result | Observed |
| --- | --- | --- | --- |
| CFG-01 | Create a location | Pass | Row "Uptown — Europe/London Active Edit Delete"; POST with timezone Mars/Base → 422. |
| CFG-02 | Create a resource with custom attributes | Pass | List row "Court 1 surface: clay floodlights: true Court Uptown Europe/London — Active Edit Delete"; public page shows Surface clay, Floodlights Yes and Europe/London. |
| CFG-03 | Link services with a price override | Pass | Saved; Court 1's public page lists Tennis Court at 25.00 and the Tennis Court page lists Court 1. |
| CFG-04 | Weekly hours with a break | Pass | Monday 09:00–17:00 with a 12:00–13:00 break saved ("Schedule saved."); slots on 2026-09-28: ['09:00', '10:00', '11:00', '13:00', '14:00', '15:00', '16:00']. |
| CFG-05 | Rule valid from a future date | Pass | Valid from 2026-10-05: 2026-09-28 has 0 slots, 2026-10-05 has 8. |
| CFG-06 | No weekly hours = business hours | Pass | With no periods and no Uptown operating hours, Court 1 is bookable all day: 24 slots 00:00–23:00 (Europe/London). |
| CFG-07 | Create a service | Pass | Row "Pilates Group (8) 45 min 0 / 10 min 15.00 Active Edit Delete"; after linking it to Yoga Studio it appears on /services. |
| CFG-08 | Duration change spares existing bookings | Pass | Existing booking still 10:00–11:00; new Personal Training slots are 90 minutes (09:00–10:30). |
| CFG-09 | Delete a service | Pass | Unused Service removed; Personal Training row now "Personal Training Individual 1 h 0 / 15 min 40.00 Inactive Edit Delete", hidden from /services; the existing booking is still CONFIRMED. |
| CFG-10 | Delete a location | Pass | Empty Site removed; Downtown row now "Downtown 12 MG Road Asia/Kolkata Inactive Edit Delete". |
| CFG-11 | User management guards | Pass | STAFF created ("Sam Staff staff@example.com Staff Active 9/24/2026 Edit"); SUPER_ADMIN → "You cannot create SUPER_ADMIN accounts"; own role → "You cannot change your own role"; self-suspend → "You cannot deactivate your own account". |
| CFG-12 | Admin sets a user's password | Pass | U1's open tab was sent to /login?next=%2Fdashboard; signing in with the new password landed on /dashboard. |
| CFG-13 | Settings validation | Pass | -5 → "minimum_booking_notice must be a non-negative integer" (value stays 0); unknown key → 422 "Unknown settings: unknown_key". |
| CFG-14 | Settings apply immediately | Pass | /api/config now minimum_booking_notice=90, allow_waitlist=false; today's Tennis Court slots within 90 min of 16:31: [('17:00', 'TOO_SOON'), ('18:00', 'TOO_SOON')]. |
| CFG-15 | Business name and reminder offsets | Pass | Header shows Acme Clinic after reload; offsets stored as [1440, 120]. |

### NTF — Notifications and reminders

| ID | Scenario | Result | Observed |
| --- | --- | --- | --- |
| NTF-01 | Booking confirmation | Pass | Bell: "Notifications (1 unread)"; item "Booking confirmed Personal Training with John Carter, Mon 28 Sep 2026, 10:00–11:00 (Asia/Kolkata)" opens the booking; API log: EMAIL to=user@example.com subject='Booking confirmed'. |
| NTF-02 | Pending and waitlist messages | Pass | U1's bell: ['You are on the waitlist', 'Booking received – awaiting confirmation']. |
| NTF-03 | Cancellation messages | Pass | Own cancel → "Booking cancelled"; admin cancel → "Your booking was cancelled" with body "Meeting Room with Meeting Room A, Mon 28 Sep 2026, 12:00–13:00 (Asia/Kolkata) Reason: Trainer ill". |
| NTF-04 | Reschedule and reassign messages | Pass | "Booking rescheduled": "New time: Personal Training with John Carter, Mon 28 Sep 2026, 14:00–15:00 (Asia/Kolkata)"; "Your booking was moved": "New time: Personal Training with Priya Nair, Mon 28 Sep 2026, 14:00–15:00 (Asia/Kolkata)". |
| NTF-05 | Waitlist promotion message | Pass | U3 got "A place opened up – you're booked!" in-app, and the e-mail with that subject is in the API log. |
| NTF-06 | Conflict message is in-app only | Pass | In-app "Your booking needs attention" present; 0 e-mail notifications queued or logged for it. |
| NTF-07 | Mark as read | Pass | Badge "Notifications (2 unread)" → "Notifications"; unread-count API returns 0. |
| NTF-08 | One-hour reminder, sent once | **Fail** | The long-running API queued no reminder in 130 s (two job runs), and the job's advisory lock was held by an idle pooled database connection. After an API restart, the next job run sent 1 in-app reminder ("Reminder: your booking starts in 1 hour") and 1 e-mail (status SENT). |
| NTF-09 | 24-hour reminder | Pass | Booking Fri 15:35; one "Reminder: your booking starts in 24 hours" after the first job run and still one after the next. |
| NTF-10 | Real e-mail delivery and failure | Pass | With the catcher up the confirmation arrived ("Subject: Booking confirmed"); with it stopped the booking still returned 201 and its e-mail is FAILED ("[Errno 111] Connection refused…"). |
| NTF-11 | Notifications are private | Pass | U2's bell shows "Nothing yet."; POST /api/notifications/<U1's id>/read as U2 → 404. |

### TZ — Timezones and daylight saving

| ID | Scenario | Result | Observed |
| --- | --- | --- | --- |
| TZ-01 | Browser timezone does not matter | Pass | Browser in America/New_York: slots 09:00–17:00, "Times shown in Asia/Kolkata."; booking detail "Mon, Sep 28, 2026 · 10:00 AM – 11:00 AM". |
| TZ-02 | Times without an offset are local | Pass | Start "2026-09-28T10:00" (no offset) → start_datetime 2026-09-28T04:30:00Z (10:00 Asia/Kolkata). |
| TZ-03 | Locations in different zones | Pass | Court slots 07:00–21:00 with "Times shown in Europe/London."; My bookings shows both at 10:00 AM in their own zones (Europe/London, Asia/Kolkata), which are 04:30 and 09:00 UTC. |
| TZ-04 | Clocks go forward | Pass | Night Desk (America/New_York) on 2027-03-14: ['00:00', '01:00', '03:00', '04:00', '05:00'] — no 02:00. |
| TZ-05 | Clocks go back | Pass | Night Desk on 2026-11-01: ['00:00', '01:00', '01:00', '02:00', '03:00', '04:00', '05:00']; the two 01:00 slots are distinct instants (UTC 05:00 and 06:00). |
| TZ-06 | Weekly series keeps the wall-clock time | Pass | Weekly 10:00 series across the 2027-03-14 change: ['2027-03-08 10:00', '2027-03-15 10:00', '2027-03-22 10:00'] New York time (UTC 15:00, 14:00, 14:00), all available. |

### OPS — Deployment smoke checks

| ID | Scenario | Result | Observed |
| --- | --- | --- | --- |
| OPS-01 | Stack starts clean | Pass | crm-backend:test built; crm-frontend:test built; db running healthy; backend logged "Running upgrade -> 0001" and "Application startup complete"; http://localhost:8080 → 200; /api/health → {'status': 'ok'}. |
| OPS-02 | Seeding is repeatable | Pass | First run: "Seeded. Admin: admin@example.com / admin12345 User: user@example.com / user12345"; second run: "Seed data already present.". |
| OPS-03 | Deep links survive a reload | Pass | Reloading http://localhost:8080/admin/calendar showed "Calendar"; reloading /bookings/83c56919… showed "Booking details". |
| OPS-04 | Data persists across restarts | Pass | Booking 3ac83c89 created, `docker compose restart`, signed in again: still CONFIRMED. |
| OPS-05 | Schema matches the code | Pass | `alembic check` → "No new upgrade operations detected.". |
| OPS-06 | Automated suites pass | Pass | pytest: "70 passed in 28.75s"; frontend: type-check + build "✓ built in 717ms". |
| OPS-07 | Two API replicas send each reminder once | Pass | 2 backend replicas running; booking at 17:19 IST; after 150 s exactly 1 in-app and 1 e-mail reminder. (Scaled back to 1 replica afterwards.) |
| OPS-08 | Static asset caching | Pass | /assets/index-nlIYtgIw.js → Cache-Control "public, max-age=31536000, immutable"; / → Content-Security-Policy "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'". |
