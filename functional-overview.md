# Functional Overview — Generic Booking & Appointment Management System

A plain-language description of what the application does and how its pieces fit together. For precise technical detail see `docs/system-architecture.md` (how it's built), `docs/database-model.md` (what's stored) and `docs/PRD.md` (why); for the exhaustive list of behaviors see `docs/user-stories.md` and the executed test plan in `docs/e2e-test-results.md`.

## 1. What the system is

A booking platform that lets people reserve *something* — a person's time, a room, a court, a piece of equipment, a class — for a period, with one engine that works the same way regardless of what that something is. A clinic, a gym, a meeting-room desk, a tennis club and a photography studio can all run on the same deployment, configured differently, with zero code differences between them.

## 2. Who uses it

| User | What they can do |
| --- | --- |
| **Anyone (signed out)** | Browse services and resources, see prices, hours and custom attributes. |
| **Customer** (signed in) | Everything above, plus: check real-time availability, book, reschedule, cancel, join a waitlist, book recurring series, see their booking history, manage their profile, get notified. |
| **Staff** | Everything a customer can see about *every* booking (not just their own), plus confirm/complete/cancel/reschedule/reassign bookings, and view reports — no catalogue or settings access. |
| **Admin** | Everything, including the full catalogue (locations, resources, services), schedules and rules, user management, conflict resolution and the audit log. |

## 3. The core mental model

```
A USER books a SERVICE, which is provided using a RESOURCE, which has an AVAILABILITY,
which produces bookable TIME SLOTS, which become a BOOKING.
```

A **Resource** is deliberately abstract — it's whatever the business reserves: John the trainer, Meeting Room A, Tennis Court 2, a camera kit. The booking engine only ever asks one question:

> *Is this resource available for this service between this start and this end time?*

Everything that makes one deployment "a clinic" and another "a tennis club" — resource types, custom attributes, service catalogues, hours — is configuration entered through the admin screens, not a code change.

## 4. Customer journey

1. **Discover** — browse `/services` or `/resources`, filter by name/type/location/price, open a resource to see its hours and custom attributes.
2. **Book** — the booking wizard walks through four steps: *Choose a service* → *Choose how to book* (pick a specific resource first, or pick a time first and see who's free then) → *Pick a date and time* (a live slot grid, each slot's status shown: available, taken, blocked, too soon…) → *Confirm* (add a note, optionally repeat it as a recurring series). On success the customer lands on the booking's own page: "You're booked!"
3. **Manage** — from *My bookings* (Upcoming / Past / Cancelled / Waitlisted tabs) or a booking's own detail page: reschedule (a picker just like step 3 of the wizard, opened against the new time), cancel (with an optional reason), or — if waitlisted — leave the waitlist. Both actions respect any configured cancellation/rescheduling window.
4. **Stay informed** — a notification bell in the header shows unread messages for every state change on their bookings, and reminders before each one starts; the same events are also queued as e-mail.

## 5. Admin journey

1. **Set up the catalogue** — Locations (name, address, timezone) → Resources (name, type, capacity, free-form custom attributes, which location) → Services (name, duration, price, individual or shared-capacity) → link resources to the services they provide, with optional per-link price/duration/buffer overrides.
2. **Set the schedule** — business hours (globally or per location), each resource's own weekly availability (multiple periods per day, an optional break), and date-specific exceptions: a block, a holiday, a maintenance window, or special hours that replace the normal schedule for those dates.
3. **Run the business** — the Overview dashboard (bookings today/upcoming/pending/completed/cancelled, conflicts, utilization); the Bookings screen to search, filter, create, and manage any booking; the Calendar (day/week/month) to see the schedule visually; the Conflicts screen when a schedule change has invalidated existing bookings.
4. **Handle change safely** — any admin action that could shrink availability (new hours, a new block, deactivating a resource, changing a location's timezone) first shows *"This change affects N existing bookings"* with the specific list, before anything is saved. The admin then chooses to keep those bookings, mark them as conflicts (to resolve individually later), or cancel them.
5. **Tune the rules** — Settings lets an admin change booking notice, advance-booking window, cancellation/rescheduling windows, buffers, whether waitlists or recurring bookings are allowed, whether new bookings need confirmation, and reminder timing — all live, no deploy.
6. **Audit** — every booking change, schedule change and admin override is in the Audit Log, filterable, with a before/after view.

## 6. Feature matrix

| Feature | Customer | Admin/Staff | Notes |
| --- | --- | --- | --- |
| Register / sign in / reset password | ✓ | ✓ | Self-service creates a `USER`; staff/admin accounts are created by an admin. |
| Browse services & resources | ✓ | ✓ | Public, no sign-in required. |
| Check availability | ✓ | ✓ | Live-computed, never pre-generated. |
| Book (individual) | ✓ | ✓ (on behalf of a customer) | |
| Book (group/capacity) + waitlist | ✓ | ✓ | Waitlist is a feature toggle. |
| Recurring bookings | ✓ | – (customer-initiated) | Toggle + occurrence cap. |
| Multi-resource bookings | via API | via API | No dedicated wizard step yet — built and tested at the API level. |
| Reschedule / cancel own booking | ✓ | ✓ (any booking) | Windows are configurable; admin acts are audited. |
| Manage any booking (confirm/complete/no-show/reassign) | – | ✓ | |
| Manage catalogue (locations/resources/services) | – | ✓ | |
| Manage schedules (hours/blocks/holidays/special hours) | – | ✓ | Dry-run impact preview before every change. |
| Resolve conflicts | – | ✓ | Reschedule / reassign / cancel / override. |
| Manage users | – | ✓ (admin only) | Role-guarded, with self-edit guardrails. |
| Configure booking rules | – | ✓ (admin only) | Runtime, no deploy. |
| Notifications & reminders | ✓ (receives) | ✓ (receives, for admin overrides too) | In-app + e-mail; SMS/push modelled, not delivered. |
| Audit log | – | ✓ | |
| Reports (utilization) | – | ✓ | Per-resource booked vs. available minutes. |

## 7. Business rules, in plain English

These are the fifteen rules the original specification calls out as the system's non-negotiables, and how this build satisfies each:

1. **A resource that's booked exclusively can never have two overlapping active bookings.** — Enforced twice: once when the booking is written, and permanently by a database constraint that would refuse the overlap even if the application code had a bug.
2. **The frontend's idea of availability is never trusted.** — Every booking is re-validated on the server at write time.
3. **Availability is re-checked at the moment of booking**, not just when the slot was first shown to the user.
4. **Booking operations are transactional** — either everything about a booking write succeeds, or none of it does.
5. **A cancelled booking stops counting against capacity** immediately.
6. **A pending booking only counts against capacity if configured to** — a business can choose whether an unconfirmed request holds the slot.
7. **Bookings can't normally happen outside a resource's availability** — unless an admin explicitly overrides it, which is then recorded.
8. **A blocked period always wins over the regular schedule**, even during otherwise-open hours.
9. **A date-specific override (special hours) replaces the recurring weekly schedule** for the dates it covers, rather than adding to it.
10. **An inactive resource can't receive new bookings.**
11. **You can't modify someone else's booking** — attempting to shows "not found", not "forbidden", so a booking's existence can't even be probed.
12. **Every administrative override is logged**, with who did it and what changed.
13. **Rescheduling never destroys the original booking before the new one is confirmed** — if the new slot can't be secured, the old one is exactly as it was.
14. **Capacity can never go negative** — the seat count and the waitlist promotion logic run inside the same lock that prevents a resource from being double-booked.
15. **A booking across several resources succeeds or fails as one unit** — never half-reserved.

## 8. Booking lifecycle

```
PENDING ──► CONFIRMED ──► COMPLETED
   │            │      └─► NO_SHOW
   │            └─────────► CANCELLED / RESCHEDULED / CONFLICTED
   │
WAITLISTED ──► CONFIRMED (a seat opened up) / CANCELLED (left the waitlist)
CONFLICTED ──► CONFIRMED (admin override) / CANCELLED / RESCHEDULED
```

A booking is `PENDING` only when the business requires admin confirmation; otherwise it starts `CONFIRMED`. Nothing is ever deleted — every past state is kept for history and audit.

## 9. Notification triggers

| Trigger | Customer sees |
| --- | --- |
| Booking created (needs confirmation) | "Booking received – awaiting confirmation" |
| Booking created (joins a waitlist) | "You are on the waitlist" |
| Booking confirmed | "Booking confirmed" |
| Booking cancelled by the customer | "Booking cancelled" |
| Booking cancelled by staff/admin | "Your booking was cancelled" (+ reason, if given) |
| Booking rescheduled by the customer | "Booking rescheduled" |
| Booking moved by staff/admin (reassigned/rescheduled) | "Your booking was moved" |
| A waitlist seat opens up | "A place opened up – you're booked!" |
| A schedule change conflicts with the booking | "Your booking needs attention" (in-app only — no e-mail noise for something the business still needs to resolve) |
| Reminder | "Reminder: your booking starts in 1 hour" / "…24 hours" (both configurable) |

Delivered in-app immediately and queued as e-mail (console log in development, real SMTP in production). SMS and push are recognized channels in the data model but have no delivery provider wired up — they are recorded as skipped, not silently dropped.

## 10. Configuration knobs (Admin → Settings)

| Setting | Default | What it controls |
| --- | --- | --- |
| `business_name` | "Booking Platform" | Shown in the header and in e-mails. |
| `default_timezone` | `UTC` | Used by resources with no location. |
| `slot_interval` | 0 (= service duration) | Minutes between offered slot starts. |
| `minimum_booking_notice` | 0 | Minutes of advance notice required. |
| `maximum_advance_booking_days` | 90 | How far ahead a booking can be made. |
| `cancellation_window` / `rescheduling_window` | 0 | Minutes before start after which a customer can no longer self-serve (admin still can, audited). |
| `default_buffer_before` / `default_buffer_after` | 0 | Fallback buffer when a service/resource-link doesn't set its own. |
| `allow_waitlist` | true | Whether a full capacity session offers a waitlist. |
| `allow_recurring_bookings` | true | Whether the "repeat this booking" option is offered. |
| `max_recurring_occurrences` | 52 | Upper bound on one recurring request. |
| `require_admin_confirmation` | false | New bookings start `PENDING` instead of `CONFIRMED`. |
| `pending_consumes_capacity` | true | Whether a `PENDING` booking already holds its seat/slot. |
| `enforce_operating_hours` | true | Whether business hours are intersected into availability. |
| `reminder_offsets` | [1440, 60] minutes | When reminders go out before a booking starts. |

## 11. Multi-industry reuse (the point of the whole design)

The same deployment, reconfigured only through the admin screens, fits:

| Business | Resource examples | Service examples |
| --- | --- | --- |
| Gym / personal training | Trainer (Person) | 1:1 session (individual) |
| Clinic | Doctor (Person) | Consultation (individual) |
| Salon | Stylist (Person) | Haircut (individual) |
| Tutoring | Tutor (Person) | Session (individual) |
| Sports facility | Court (Facility) | Court hire (individual) |
| Coworking / meeting rooms | Room (Room) | Room booking (individual) |
| Fitness studio | Studio (Room) | Class (capacity, e.g. yoga for 15) |
| Photography | Photographer, Studio, Camera Kit (multiple resource types) | Photo session (multi-resource booking) |
| Equipment rental | Equipment (Equipment) | Rental (individual, priced per link) |

No code differs between these; only the rows in `locations`, `resources`, `services` and `resource_services`, and the settings that apply business rules.

## 12. Known limitations

- **Payments, refunds and reviews are not implemented** — deliberately out of scope for this build (see `docs/PRD.md` §10).
- **SMS and push notifications are modelled but not delivered** — no provider is wired up; they're recorded as skipped rather than attempted.
- **Multi-resource bookings have no dedicated wizard step** — they work correctly and are tested at the API level, but the customer-facing booking wizard only offers a single resource per booking today.
- Two low-severity UI issues, found in a browser-driven retest and not yet fixed:
  - **Operating-hours save confirmation is too brief to read.** The "Operating hours saved." message appears for well under a second before the form's own data refresh remounts it and clears the flag. The save itself is correct and takes effect immediately; only the visible confirmation is affected. (`frontend/src/pages/admin/schedules.tsx`, the form's `key` includes `hours.dataUpdatedAt`.)
  - **A brand-new service is visible to customers before any resource offers it.** It's listed on the public services page and offered in the booking wizard, but its own page shows no resource and the wizard has nothing to book it with — a dead end until an admin links it to a resource. Suggested fix: only list a service publicly once it has at least one active resource link (admins should keep seeing every service, linked or not).

## 13. Where to go next

- **`docs/PRD.md`** — full functional & non-functional requirements, traced to the original specification, plus scope decisions.
- **`docs/user-stories.md`** — every capability as a testable user story.
- **`docs/system-architecture.md`** — how the pieces are built and talk to each other, with sequence diagrams for the core flows.
- **`docs/database-model.md`** — every table, column, constraint and the reasoning behind the schema's key design choices.
- **`docs/e2e-test-results.md`** — the 164-case test plan actually executed against the running application, with results.
