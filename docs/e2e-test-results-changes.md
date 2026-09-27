# Test Results – Changes to Booking, Waitlist, Maps and the UI

**48 of 48 end-to-end cases for the changes pass**. The automated suites pass too: 85 backend tests and 21 frontend unit tests. Re-running the original 164-case plan through the frontend found no regressions (161 of 164 pass; the other three are a Sunday-only block and the two issues already known from the earlier run).

Tested on branch `claude/gracious-goldberg-outqt8` at commit `66a7b52`, 2026-09-27 (Asia/Kolkata), covering these commits:

- `ae46083 feat(ui): add dark theme and phone-friendly layouts`
- `395a391 feat(booking): let customers choose the booking length, priced per hour`
- `6ea6d01 feat(booking): lock the chosen service in the booking wizard`
- `d62645c feat(booking): offer "who or what first" as its own choice`
- `6177df1 feat(bookings): show how to get on a waitlist`
- `09e661c feat(locations): link locations to Google Maps`
- `797dc42 docs(readme): describe themes and the booking length parameter`
- `156b32a fix(ui): say "Unavailable" for a resource's days off`
- `98944f3 test(backend): cover chosen lengths, map links and the rate migration`
- `0dc1810 test(frontend): unit-test the booking price and length helpers`
- `8c1c201 fix(ui): raise faint text to WCAG AA contrast`
- `66a7b52 fix(ui): make small links and controls at least 24px to tap`

## Summary

| Area | Cases | Pass | Fail |
| --- | --- | --- | --- |
| LEN — Booking length and hourly prices | 13 | 13 | 0 |
| WIZ — Locked service in the booking wizard | 3 | 3 | 0 |
| HOW — Two ways to book | 5 | 5 | 0 |
| WL — Finding the waitlist | 6 | 6 | 0 |
| MAP — Google Maps links | 7 | 7 | 0 |
| THM — Light and dark theme | 5 | 5 | 0 |
| MOB — Phone layouts | 8 | 8 | 0 |
| GIT — Commit messages | 1 | 1 | 0 |

## Findings

### THM-04 — Faint text was below WCAG AA contrast (found and fixed)

- **Found:** The faint text colour measured 2.96:1 (light theme, on the page background) to 3.91:1 (dark theme), under the 4.5:1 that WCAG AA asks of normal text. It carries real information: notification times, "Unavailable" days on resource pages, calendar hour labels, "(you)" in the user list, placeholders.
- **Fix:** `8c1c201 fix(ui): raise faint text to WCAG AA contrast`: #6f6b62 in the light theme and #8e8b82 in the dark one, still visibly lighter than secondary text.
- **Re-test:** THM-04 passes: all 17 text/background pairs reach at least 4.5:1 in both themes (lowest: light faint text on the page background 4.82:1, dark faint text on cards 5.13:1).

### MOB-08 — Some links and controls were too small to tap (found and fixed)

- **Found:** On a phone the Open in Google Maps links were 172×20 px (150×16 in the booking summary), the Repeat this booking checkbox label 164×20, the ← My bookings link 110×20 and the All tab 17×38: under the 24×24 px minimum target size of WCAG 2.2.
- **Fix:** `66a7b52 fix(ui): make small links and controls at least 24px to tap` (Mark all read in the notifications panel too).
- **Re-test:** MOB-08 passes on the home page, the wizard (with Repeat open), a booking and My bookings.

### Seen while testing — Asia/Kolkata is missing from the admin timezone list (predates these changes, not fixed)

- **Found:** The admin location form fills its Timezone list from the browser (Intl.supportedValuesOf). Chromium lists India's zone only under its old name, Asia/Calcutta, so the app's own default, Asia/Kolkata, is not in the list: MAP-07's first attempt could not choose it, and editing a location in Asia/Kolkata shows no matching entry.
- **Impact:** Confusing rather than harmful: saving without touching the field keeps Asia/Kolkata.
- **Fix:** Suggested: always add the location's current timezone (and the default one) to the list when the browser leaves it out.

## Automated tests

- **Backend** (pytest against a real PostgreSQL): 85 passed. 13 of them cover these changes: booking in length steps priced per hour; fixed-length services; 30-minute and group prices; moving a longer booking; repeating a longer booking; validation of the longest booking; time-first and "any available" bookings with a length; alternatives with a length; an admin booking with a length; location map links (validation, own link, address search, no address, on offerings and bookings, in the e-mail); changing and clearing a map link; reschedule and reminder messages; migration 0002 up and down.
- **Frontend unit tests** (`npm test`, Node's built-in runner, no new dependency): 21 passed: the booking price (checked against the server's rounding), length options and ranges, hourly rates and booking times.
- **Build and checks:** type-check and production build pass; `alembic check` finds no drift; ruff is clean.

## Regression: the original plan through the frontend

The whole original plan (164 cases) was run again through the frontend after these changes, with the harness adapted to the new screens (see Corrections for the test plan in [e2e-test-results-frontend.md](e2e-test-results-frontend.md)): **161 pass**. AVL-10 was Blocked only because the run fell on a Sunday, when Downtown is closed; it checks today's slots there and passed on weekdays before. SCH-01 (the "Operating hours saved." confirmation flashes for a split second) and CFG-07 (a service that no resource offers is still listed to customers) fail as they did in the previous frontend run; both predate these changes. After the two fixes above, the ten plan cases that use the adjusted controls (AVL-04, BKG-01, CAP-05, CHG-01, REC-01, REC-03, ADM-01, CFG-03, NTF-01, NTF-07) were run again and pass.

## How the end-to-end cases were run

- Every case ran in headless Chromium through the web app (the Vite dev server at http://localhost:5173, which forwards /api to the API). The test process refused any connection to the API's port and every browser aborted requests to it, so pages reached the API only through the web app.
- The database was reset to the seeded demo data before each case. D is the next Monday; times are Asia/Kolkata.
- Phones: a 390×844 touch viewport flagged as mobile. Desktops: 1360×900. Light and dark: the browser's system colour-scheme setting was emulated.
- A few steps send a request from the signed-in page with fetch(), for example to check that the server refuses a length the app never offers; they are listed with the case.
- THM-05 used the Docker stack (nginx with the production Content-Security-Policy) at http://localhost:8080. MAP-06 read the confirmation e-mail from the API's e-mail log (console e-mail backend). GIT-01 read `git log`.
- First pass: 44 of 48. THM-04 and MOB-08 found the two issues above, which were fixed. THM-03, MAP-07 and MOB-08's first attempt were test-script problems, corrected and re-run: the dev server adds its own module scripts to the page; the timezone list lacks Asia/Kolkata (see Findings); ticking Repeat turns Confirm booking into Check dates. After the fixes, all 48 cases were run again together with ten plan cases that use the adjusted controls.

## Cases

### LEN — Booking length and hourly prices

**LEN-01 — Length slider only where a longer booking is allowed** · Pass

- Steps: Wizard as U1: Consultation with Priya Nair; Personal Training with John Carter; Yoga Class with Yoga Studio.
- Expected: Consultation: a Length slider from 30 min to 2 h in 30-minute steps (4 marks), starting at 30 min. Personal Training: 1 h to 2 h in 1-hour steps. Yoga Class (group session): no slider.
- Observed: Consultation: slider 30–120 min, step 30, 4 marks, reads "Length 30 min 30 min 2 h"; Personal Training: 60–120 min, step 60; Yoga Class: no slider.

**LEN-02 — Longer lengths only offer starts that fit** · Pass

- Steps: Wizard as U1: Consultation, Priya Nair (10:00–16:00), D. Read the offered times at 30 min, then set Length to 2 h.
- Expected: 30 min: 12 starts, 10:00 to 15:30. 2 h: 9 starts, 10:00 to 14:00 (each still ends by 16:00).
- Observed: 30 min: 12 starts 10:00–15:30; 2 h: 9 starts 10:00–14:00.

**LEN-03 — The price is the hourly rate times the booked hours** · Pass

- Steps: U1: Consultation (50.00 / h) with Priya Nair, Length 1 h 30 min, D 10:00; read the summary; Confirm booking; open My bookings.
- Expected: Summary: Length 1 h 30 min, Price 75.00 with "50.00 / h × 1 h 30 min". The booking reads 10:00–11:30, Length 1 h 30 min, Price 75.00, and its row in My bookings shows 75.00.
- Observed: Summary "…Length 1 h 30 min Price 75.00 50.00 / h × 1 h 30 min"; booking 165643da: 10:00 AM – 11:30 AM, Length 1 h 30 min, Price 75.00; My bookings row "SEP 28 Consultation Confirmed Mon · 10:00 AM – 11:30 AM Priya Nair · Downtown 75.00".

**LEN-04 — A longer booking holds all of its time** · Pass

- Steps: U1 books Tennis Court 2 on D at 08:00 for 3 h. U2 opens Tennis Court on D (1 h) and sends POST /api/bookings for 09:00 from the page.
- Expected: U1 pays 60.00 (20.00 / h × 3 h). For U2, 08:00, 09:00 and 10:00 are taken and 07:00 and 11:00 are free; the POST gets 409 CONFLICT.
- Observed: U1: 08:00 AM – 11:00 AM, Price 60.00. U2 sees 08:00/09:00/10:00 "Conflict" and 07:00, 11:00 free; POST 09:00 → 409 CONFLICT.
- Requests sent from the page: POST /api/bookings → 409 CONFLICT

**LEN-05 — A resource's own rate is used** · Pass

- Steps: U1: Personal Training with Priya Nair (her rate: 45.00 / h), Length 2 h, D 10:00, Confirm booking.
- Expected: Summary and booking: Price 90.00 (45.00 / h × 2 h).
- Observed: Summary "Price 90.00 45.00 / h × 2 h"; booking 10:00 AM – 12:00 PM, Price 90.00.

**LEN-06 — Group sessions keep their set length** · Pass

- Steps: U1: Yoga Class, Yoga Studio, D, Places 3, 18:00; read the summary. POST /api/bookings for the same session with duration_minutes 120 from the page.
- Expected: No Length slider; summary Length 1 h, Price 36.00 (12.00 / h × 1 h × 3 places). The POST gets 422 INVALID_DURATION.
- Observed: No slider; summary "Length 1 h … Price 36.00 12.00 / h × 1 h × 3 places"; POST with duration_minutes 120 → 422 INVALID_DURATION "Yoga Class with Yoga Studio can be booked for 60 minutes".
- Requests sent from the page: POST /api/bookings → 422 INVALID_DURATION

**LEN-07 — Lengths outside the steps are refused** · Pass

- Steps: From U1's page: POST /api/bookings for Consultation with Priya Nair on D 10:00 with duration_minutes 45, then 150.
- Expected: Both get 422 INVALID_DURATION, naming the lengths on offer (30, 60, 90, 120 minutes).
- Observed: 45 → 422 INVALID_DURATION "Consultation with Priya Nair can be booked for 30, 60, 90, 120 minutes"; 150 → 422 INVALID_DURATION.
- Requests sent from the page: POST /api/bookings → 422 INVALID_DURATION; POST /api/bookings → 422 INVALID_DURATION

**LEN-08 — Rescheduling keeps the length and price** · Pass

- Steps: U1 books Consultation with Priya Nair for 2 h on D 10:00, then Reschedule: read the times, choose 13:00, Move booking.
- Expected: The dialog marks 10:00 Current and offers starts up to 14:00 (2 h still fit). The new booking reads 13:00–15:00, Length 2 h, Price 100.00.
- Observed: Dialog: 10:00 "Current", starts up to 14:00; after Move booking: 01:00 PM – 03:00 PM, Length 2 h, Price 100.00.

**LEN-09 — Time first with a longer length** · Pass

- Steps: U1: Personal Training, Pick a time first, D, Length 2 h, 10:00; keep Any available; Confirm booking.
- Expected: 10:00 shows 2 free; after choosing it, Any available, John Carter and Priya Nair are offered. The booking goes to John Carter (first free) for 10:00–12:00 at 80.00.
- Observed: 10:00 "2 free"; offered Any available, John Carter, Priya Nair; booked with John Carter, 10:00 AM – 12:00 PM, Price 80.00.

**LEN-10 — Repeating bookings use the chosen length** · Pass

- Steps: U1: Meeting Room A, D, Length 2 h, 10:00, Repeat this booking weekly, 3 occurrences, Check dates, Book 3 available dates.
- Expected: The check shows 3 available; My bookings then lists three 10:00–12:00 bookings at 30.00 each (15.00 / h × 2 h).
- Observed: "3 available · 0 unavailable"; My bookings: SEP 28 Meeting Room Confirmed Mon · 10:00 AM – 12:00 PM Meeting Room A · Downtown 30.00 \| OCT 5 Meeting Room Confirmed Mon · 10:00 AM – 12:00 PM Meeting Room A · Downtown 30.00 \| OCT 12 Meeting Room Confirmed Mon · 10:00 AM – 12:00 PM Meeting Room A · Downtown 30.00.

**LEN-11 — Admins set the longest booking** · Pass

- Steps: Admin → Services → Edit Consultation: Longest booking 60 → Save; U1 opens the wizard. Then Longest booking 20 → Save. Then Edit Yoga Class.
- Expected: The row reads 30 min – 1 h and 50.00 / h, and U1's slider now ends at 1 h. 20 is refused (the longest booking can't be shorter than the duration). Yoga Class (group) has no Longest booking field.
- Observed: Row "Consultation Individual 30 min – 1 h — 50.00 / h Active Edit Delete"; U1's slider max 60 min; 20 → "Value error, The longest booking cannot be shorter than the duration"; Yoga Class has no Longest booking field.

**LEN-12 — Admins can book a chosen length** · Pass

- Steps: Admin → Bookings → New booking: Demo User, Tennis Court, Tennis Court 2, Length 2 h, D, 10:00, Create booking; open it.
- Expected: Listed for D 10:00 until 12:00 PM; the booking shows 40.00 (20.00 / h × 2 h).
- Observed: Length choices ['1 h', '2 h', '3 h']; row "Mon, Sep 28, 2026, 10:00 AM until 12:00 PM Demo User user@example.com Tennis Court Tennis Court 2 Confirmed Open"; the booking shows 40.00.

**LEN-13 — Prices read as hourly rates** · Pass

- Steps: Signed out: /services; the Consultation page; Priya Nair's resource page.
- Expected: Consultation reads 50.00 / h and 30 min – 2 h; Yoga Class 12.00 / h and 1 h. The Consultation page reads "30 min – 2 h · from 50.00 / h". Priya's page: Personal Training 1 h – 2 h · 45.00 / h, Consultation 30 min – 2 h · 50.00 / h.
- Observed: /services: "Consultation A consultation, from 30 minutes to 2 hours. 50.00 / h 30 min – 2 h", "Yoga Class Group · 12 places Group vinyasa flow class. 12.00 / h 1 h"; Consultation page "30 min – 2 h · from 50.00 / h"; Priya: Consultation 30 min – 2 h · 50.00 / h Book \| Personal Training 1 h – 2 h · 45.00 / h Book \| Monday 10:00–16:00 \| Tuesday 10:00–16:00 \| Wednesday 10:00–16:00 \| Thursday 10:00–16:00 \| Friday 10:00–16:00 \| Saturday 10:00–16:00 \| Sunday Unavailable.

### WIZ — Locked service in the booking wizard

**WIZ-01 — The chosen service is locked** · Pass

- Steps: U1: /book, choose Tennis Court.
- Expected: Step 1 shows only Tennis Court (1 h – 3 h · 20.00 / h) with Change service; no other service can be chosen anywhere on the page.
- Observed: Step 1: "Choose a service Tennis Court 1 h – 3 h · 20.00 / h Change service"; other services selectable: 0.

**WIZ-02 — Change service starts over** · Pass

- Steps: U1: Yoga Class, Yoga Studio, D, Places 3, 18:00; Change service; then Tennis Court, Tennis Court 2, D, 10:00, Confirm booking.
- Expected: After Change service the service list is back, the later steps are gone and the link has no service. The Tennis Court booking has no places carried over (quantity 1, Price 20.00).
- Observed: After Change service: 5 services listed, no later steps, url /book; Tennis Court booking: quantity 1, Price 20.00.

**WIZ-03 — A link can pre-select the service** · Pass

- Steps: Signed in as U1: the Personal Training page → Book this service.
- Expected: The wizard opens with Personal Training locked in step 1 and asks how to book.
- Observed: /book?service=… opened with "Choose a service Personal Training 1 h – 2 h · 40.00 / h Change service" and the Choose how to book step.

### HOW — Two ways to book

**HOW-01 — Neither way of booking is preselected** · Pass

- Steps: U1: /book, Personal Training.
- Expected: Both options (Pick who or what first, Pick a time first) are shown unselected, with a one-line explanation each; no resources and no date/time step yet.
- Observed: Options: Pick who or what first Choose the person, room or item you want, then see when it's free. \| Pick a time first See all free times, then pick from what's available at that time.; nothing selected, no resources, no date step.

**HOW-02 — Pick who or what first** · Pass

- Steps: U1: Personal Training → Pick who or what first → John Carter.
- Expected: John Carter (Person · Downtown · 40.00 / h) and Priya Nair (… 45.00 / h) are listed; choosing John shows the date and time step with his slots.
- Observed: Listed: John Carter Person · Downtown · 40.00 / h \| Priya Nair Person · Downtown · 45.00 / h; John's first free time on D: 09:00.

**HOW-03 — Pick a time first** · Pass

- Steps: U1: Personal Training → Pick a time first, D, 10:00.
- Expected: Times show how many are free; at 10:00 the choices are Any available, John Carter and Priya Nair, and the summary reads With: Any available.
- Observed: 10:00 "2 free"; choices Any available, John Carter, Priya Nair; summary "With Any available".

**HOW-04 — Switching between the two ways** · Pass

- Steps: U1: Personal Training → who first → Priya Nair → switch to Pick a time first → switch back.
- Expected: Time first hides the resource list and shows the combined times; switching back shows the list again with Priya still chosen and her times.
- Observed: Time first: no resource list, 10:00 "2 free"; back: Priya Nair still chosen, first time 10:00 (her hours start at 10:00).

**HOW-05 — A resource's Book link starts with who or what first** · Pass

- Steps: U1: Priya Nair's resource page → Book (Personal Training).
- Expected: The wizard opens with Personal Training locked, Pick who or what first selected, Priya Nair chosen and the date and time step open.
- Observed: Personal Training locked; "Pick who or what first" selected; Priya Nair chosen; date and time step open.

### WL — Finding the waitlist

**WL-01 — An empty Waitlisted tab explains how to join one** · Pass

- Steps: U1 (on no waitlist): My bookings → Waitlisted → Book Yoga Class.
- Expected: "You're not on any waitlists", a line saying a full group session can be chosen on the booking page to join its waitlist, and a Book Yoga Class link that opens the wizard on Yoga Class.
- Observed: Waitlisted tab: "You're not on any waitlists — When a group session is full, choose it on the booking page to join its waitlist…"; Book Yoga Class opened the wizard on Yoga Class.

**WL-02 — A full session leads to the waitlist** · Pass

- Steps: Admin sets Yoga Class capacity 2; U1 and U2 book D 18:00. U3 opens Yoga Class on D, chooses 18:00, Join waitlist; then My bookings → Waitlisted and the dashboard.
- Expected: 18:00 reads Full · waitlist and can be chosen, with the note "Choose a full session to join its waitlist."; U3 is #1 on the waitlist, listed under Waitlisted, and the dashboard shows On a waitlist 1.
- Observed: 18:00 "Full · waitlist" (enabled) with the note; U3: "You're #1 on the waitlist…"; Waitlisted tab "SEP 28 Yoga Class Waitlisted #1 on the waitlist Mon · 06:00 PM – 07:00 PM Yoga Studio · Riverside Sports Club 12.00"; dashboard "On a waitlist 1".

**WL-03 — No dead ends when the waitlist is off** · Pass

- Steps: Yoga Class capacity 2, U1 and U2 book D 18:00; Admin turns Allow Waitlist off. U3 opens My bookings, the dashboard and Yoga Class on D.
- Expected: No Waitlisted tab and no On a waitlist count; 18:00 reads Full and can't be chosen, with no waitlist note.
- Observed: Tabs ['Upcoming', 'Past', 'Cancelled', 'All']; no dashboard waitlist count; 18:00 "Full", disabled, no note.

**WL-04 — Still on a waitlist after it is switched off** · Pass

- Steps: Yoga Class capacity 2, U1 and U2 book D 18:00, U3 joins the waitlist; Admin turns Allow Waitlist off; U3 opens My bookings → Waitlisted.
- Expected: The Waitlisted tab is still there and lists U3's place.
- Observed: Waitlisted tab still shown: "SEP 28 Yoga Class Waitlisted #1 on the waitlist Mon · 06:00 PM – 07:00 PM Yoga Studio · Riverside Sports Club 12.00".

**WL-05 — A move can't join a waitlist** · Pass

- Steps: Yoga Class capacity 2; U1 and U2 book D+1 (Tuesday) 18:00; U3 books D 18:00, then Reschedule → D+1.
- Expected: In the reschedule dialog the full 18:00 session reads Full and can't be chosen.
- Observed: Reschedule dialog on 2026-09-29: 18:00 "Full", disabled.

**WL-06 — Group service pages mention the waitlist** · Pass

- Steps: Signed out: the Yoga Class page; Admin turns Allow Waitlist off; reload it.
- Expected: With the waitlist on, the page says full sessions have a waitlist; with it off, that line is gone.
- Observed: Waitlist on: "Full sessions have a waitlist: choose a full time when you book to join it…"; off: no such line.

### MAP — Google Maps links

**MAP-01 — The home page shows where to find each location** · Pass

- Steps: Signed out: /.
- Expected: "Where to find us" lists Downtown (12 MG Road, Bengaluru) and Riverside Sports Club (4 River Lane, Bengaluru), each with an Open in Google Maps link to a Maps search for its name and address, opening in a new tab (rel noopener).
- Observed: Cards: Downtown 12 MG Road, Bengaluru Open in Google Maps \| Riverside Sports Club 4 River Lane, Bengaluru Open in Google Maps; links https://www.google.com/maps/search/?api=1&query=Downtown%2C+12+MG+Road%2C+Bengaluru and https://www.google.com/maps/search/?api=1&query=Riverside+Sports+Club%2C+4+River+Lane%2C+Bengaluru (target _blank, rel noopener noreferrer).

**MAP-02 — A booking shows where to go** · Pass

- Steps: U1 books Personal Training with John Carter on D 10:00 and reads the booking.
- Expected: Where: Downtown, 12 MG Road, Bengaluru, with an Open in Google Maps link to Downtown.
- Observed: Booking page: "Where Downtown 12 MG Road, Bengaluru Open in Google Maps" → Downtown's Maps search.

**MAP-03 — Resource pages and the wizard link to the map** · Pass

- Steps: U1: Tennis Court 2's resource page; then the wizard with Tennis Court 2, D, 10:00.
- Expected: The resource page has a Location card (Riverside Sports Club, 4 River Lane, Bengaluru) with the map link; the wizard summary reads Where Riverside Sports Club with the same link.
- Observed: Location card "Location Riverside Sports Club 4 River Lane, Bengaluru Open in Google Maps"; wizard summary "Where Riverside Sports Club Open in Google Maps"; both link to Riverside's Maps search.

**MAP-04 — An admin's own link replaces the search** · Pass

- Steps: Admin → Locations → Edit Downtown → Google Maps link https://maps.app.goo.gl/AbC123 → Save. Check the admin list, the home page and a Downtown booking. Then clear the link and Save.
- Expected: Everywhere Downtown links to https://maps.app.goo.gl/AbC123; after clearing it, back to the Maps search.
- Observed: With the link: admin list, booking page and home all → https://maps.app.goo.gl/AbC123; after clearing: → https://www.google.com/maps/search/?api=1&query=Downtown%2C+12+MG+Road%2C+Bengaluru.

**MAP-05 — Only web links are accepted** · Pass

- Steps: Admin → Locations → Edit Downtown → Google Maps link javascript:alert(1) → Save.
- Expected: The form shows an error asking for a link starting with https:// and nothing changes: Downtown still links to its Maps search.
- Observed: Error shown: "map_url: Value error, Enter a web link starting with https://"; Downtown still links to its Maps search.

**MAP-06 — Messages say where to go** · Pass

- Steps: U1 books Personal Training with John Carter on D 10:00; open the bell; wait for the confirmation e-mail in the API's e-mail log.
- Expected: The bell's "Booking confirmed" message ends with Where: Downtown, 12 MG Road, Bengaluru; the e-mail adds Map: <Downtown's Maps link>.
- Observed: Bell: "Booking confirmed Personal Training with John Carter, Mon 28 Sep 2026, 10:00–11:00 (Asia/Kolkata) Where: Downtown, 12 MG Road, Bengaluru Sun, Sep 27, 2026, 04:09 PM"; e-mail log: "…Where: Downtown, 12 MG Road, Bengaluru / Map: https://www.google.com/maps/search/?api=1&query=Downtown%2C+12+MG+Road%2C+Bengaluru".
- Note: The e-mail was read from the API's e-mail log (console e-mail backend), as in AUTH-08.

**MAP-07 — No address, no map link** · Pass

- Steps: Admin → Locations → New location Pop-up Studio (no address); open /. Then add the address 7 Lake Road, Bengaluru and reload /.
- Expected: Without an address Pop-up Studio is listed with no map link; with one, it links to a Maps search for "Pop-up Studio, 7 Lake Road, Bengaluru".
- Observed: Pop-up Studio without an address: listed, no map link; with "7 Lake Road, Bengaluru": links to its Maps search.

### THM — Light and dark theme

**THM-01 — The first visit follows the system setting** · Pass

- Steps: Open / in a browser whose system setting is dark, and in one set to light.
- Expected: Dark system: the page opens in the dark theme (dark background); light system: light theme.
- Observed: Dark system → ('dark', 'rgb(17, 18, 16)'); light system → ('light', 'rgb(245, 244, 240)').

**THM-02 — The header toggle switches the theme and remembers it** · Pass

- Steps: Light system, /: press Use dark theme; reload; go to /services and sign in; press Use light theme.
- Expected: The page turns dark at once and stays dark after the reload, on other pages and after signing in; the toggle then brings back light. The choice is saved in the browser.
- Observed: Toggle → dark at once; still dark after reload, on /services and signed in (saved "dark"); toggled back → light (saved "light").

**THM-03 — No flash of the wrong theme** · Pass

- Steps: With dark saved in the browser and a light system setting, open / and record the theme at the moment the page body is created.
- Expected: The theme is already dark before the body exists: /theme.js, a same-origin script (allowed by the CSP), is the first blocking script in the head.
- Observed: Theme when <body> was created: dark; the first blocking script in <head> is /theme.js (the rest are deferred modules).

**THM-04 — Text is readable in both themes** · Pass

- Steps: In each theme, read the colour tokens from the page and compute the WCAG contrast of every text colour on the backgrounds it is used on (body, secondary and faint text, links, buttons, badges, alerts).
- Expected: Every pair reaches WCAG AA for normal text: 4.5:1.
- Observed: All 17 pairs ≥ 4.5:1 in both themes; lowest: light faint on canvas 4.82:1, dark faint on surface 5.13:1.

**THM-05 — The theme works in the production build** · Pass

- Steps: Docker stack (nginx with the strict Content-Security-Policy) at http://localhost:8080 in a dark-system browser: open / and /services.
- Expected: The pages open in the dark theme; /theme.js loads (200) and the browser reports no CSP or script errors.
- Observed: http://localhost:8080: / and /services dark; /theme.js → 200; no console errors.

### MOB — Phone layouts

**MOB-01 — No sideways scrolling on a phone** · Pass

- Steps: On a 390×844 phone, open every public page, every signed-in customer page (including a booking and the wizard with a time chosen) and every admin page.
- Expected: No page is wider than the screen (the calendar grid scrolls inside its own box).
- Observed: 23 pages checked at 390 px; none scrolls sideways.

**MOB-02 — The header folds into a menu** · Pass

- Steps: U1 on a phone: open the menu; choose My bookings; open the menu again and Sign out.
- Expected: The header shows the name, theme toggle, bell and a Menu button instead of the links. The menu lists Services, Resources, Dashboard, Book, My bookings and U1's name/e-mail with Sign out; choosing a link navigates and closes it; Sign out signs U1 out.
- Observed: Menu: Services, Resources, Dashboard, Book, My bookings + "Demo User user@example.com Sign out"; My bookings navigated and closed it; Sign out → home, signed out.

**MOB-03 — Tables become labelled rows** · Pass

- Steps: U1 books on D; Admin → Bookings on a phone, then on a desktop.
- Expected: Phone: the header row is hidden and each cell carries its column name (When, Customer, Service, Resource, Status). Desktop: a normal table.
- Observed: Phone: header hidden, cells labelled When, Customer, Service, Resource, Status; desktop: normal table.

**MOB-04 — Dialogs open as bottom sheets on phones** · Pass

- Steps: U1 books on D; opens Cancel booking on a phone, then on a desktop.
- Expected: Phone: the dialog spans the width and sits on the bottom edge. Desktop: centred near the top.
- Observed: Phone: x 0, width 390, bottom 844 of 844; desktop: centred (x 424, width 512), top 64.

**MOB-05 — The summary moves into the Confirm step on phones** · Pass

- Steps: U1 on a phone: Consultation, Priya Nair, D, Length 1 h, 10:00.
- Expected: No sidebar; the Confirm step itself lists Service, With, Where, When, Length and Price (50.00).
- Observed: Sidebar hidden; Confirm step: "4 Confirm Service Consultation With Priya Nair Where Downtown Open in Google Maps When Mon, Sep 28, 2026 · 10:00 AM – 11:00 AM Length 1 h Price 50.00 50.00 / h × 1 h Note…".

**MOB-06 — Notifications fit the phone screen** · Pass

- Steps: U1 books on D; on a phone opens the bell.
- Expected: The panel lies within the 390 px screen and shows the Booking confirmed message.
- Observed: Panel from x 12 to 378 of 390, showing "Booking confirmed".

**MOB-07 — The calendar opens on the day view on phones** · Pass

- Steps: Admin → Calendar on a phone, then on a desktop.
- Expected: Phone: Day view (a week of columns would not fit). Desktop: Week view.
- Observed: Phone opens on Day, desktop on Week.

**MOB-08 — Controls are big enough to tap** · Pass

- Steps: U1 on a phone: the home page, the wizard up to the Confirm step (with Repeat this booking open), My bookings and a booking.
- Expected: Every button, field, and button-styled link is at least 24×24 px (WCAG 2.2 target size); checkboxes and radios count their whole label.
- Observed: All buttons, fields and button links on the home page, the wizard (with Repeat open), the booking and My bookings are at least 24×24 px.

### GIT — Commit messages

**GIT-01 — Commit messages follow Conventional Commits** · Pass

- Steps: git log of the branch after the documentation commit (3a35670): read each commit's subject and body.
- Expected: Every subject is type(scope): description in lower case, at most 72 characters, and the body is separated by a blank line.
- Observed: 12 commits, all conventional (docs, feat, fix, test), subjects ≤ 72 characters, bodies after a blank line: fix(ui): make small links and controls at least 24px to tap; fix(ui): raise faint text to WCAG AA contrast; test(frontend): unit-test the booking price and length helpers; test(backend): cover chosen lengths, map links and the rate migration; fix(ui): say "Unavailable" for a resource's days off; docs(readme): describe themes and the booking length parameter; feat(locations): link locations to Google Maps; feat(bookings): show how to get on a waitlist; feat(booking): offer "who or what first" as its own choice; feat(booking): lock the chosen service in the booking wizard; feat(booking): let customers choose the booking length, priced per hour; feat(ui): add dark theme and phone-friendly layouts.
- Note: Command-line step by design: the commits were read with git log.
