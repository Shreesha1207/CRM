# Generic Booking & Appointment Management System

## 1. Purpose

Build a reusable appointment and booking management platform that can be adapted to any business where users reserve a person, service, room, facility, equipment, or time slot.

Example use cases:

- Gyms and personal trainers
- Clinics and doctors
- Salons and stylists
- Consultants
- Tutors
- Sports facilities
- Meeting rooms
- Coworking spaces
- Repair/service centres
- Photography studios
- Classes and workshops
- Equipment rentals
- Professional services

The system should not contain business-specific scheduling logic.

Instead, everything revolves around:

```text
User
Resource
Service
Availability
Slot
Booking
```

---

# 2. Core Concept

The generic relationship is:

```text
USER
  │
  │ books
  ▼
SERVICE
  │
  │ provided using/by
  ▼
RESOURCE
  │
  │ has
  ▼
AVAILABILITY
  │
  ▼
TIME SLOT
  │
  ▼
BOOKING
```

A **Resource** is anything that can be reserved.

Examples:

```text
Doctor
Trainer
Stylist
Tutor
Consultant
Room
Court
Desk
Vehicle
Machine
Equipment
Studio
```

This makes the system reusable without changing the core booking engine.

---

# 3. Roles

Minimum roles:

```text
USER
ADMIN
```

The architecture should allow additional roles later:

```text
STAFF
RESOURCE_OWNER
MANAGER
SUPER_ADMIN
```

## USER

Can:

- Register/login.
- Browse services.
- Browse resources.
- Check availability.
- Book.
- Reschedule.
- Cancel.
- View upcoming bookings.
- View booking history.
- Manage profile.

## ADMIN

Can:

- Manage users.
- Manage resources.
- Manage services.
- Manage schedules.
- Manage bookings.
- Configure operating hours.
- Configure blocked periods.
- Configure holidays.
- Override availability.
- Resolve conflicts.
- View reports.
- Manage system settings.

---

# 4. Authentication

Support:

```text
Register
Login
Logout
Forgot Password
Reset Password
```

User fields:

```text
id
name
email
phone
password_hash
role
status
created_at
updated_at
```

Status:

```text
ACTIVE
INACTIVE
SUSPENDED
```

Passwords must never be stored in plain text.

---

# 5. Role-Based Authorization

Authorization must be enforced by the backend.

Example:

```text
GET /api/bookings
```

returns the current user's bookings.

While:

```text
GET /api/admin/bookings
```

allows administrators to see all bookings.

Users must never be able to gain administrative access by manipulating frontend requests.

---

# 6. Resources

A Resource represents something that can be booked.

Generic resource structure:

```text
Resource
------------------------
id
name
description
type
status
location
capacity
metadata
created_at
updated_at
```

Example resource types:

```text
PERSON
ROOM
FACILITY
EQUIPMENT
VEHICLE
DESK
COURT
CUSTOM
```

Examples:

```text
Resource: John
Type: PERSON

Resource: Meeting Room A
Type: ROOM

Resource: Tennis Court 2
Type: FACILITY
```

---

# 7. Resource Metadata

Different industries require different information.

Instead of adding hundreds of business-specific database columns, support flexible metadata.

Example:

```json
{
  "specialization": "Strength Training",
  "experience": 8
}
```

Another implementation could use:

```json
{
  "floor": 3,
  "projector": true,
  "capacity": 12
}
```

This allows businesses to customize resources without modifying the booking engine.

---

# 8. Services

A Service represents what the customer is booking.

Examples:

```text
Personal Training
Consultation
Haircut
Massage
Tutoring Session
Meeting Room
Tennis Court
Photography Session
Equipment Rental
```

Structure:

```text
Service
------------------------
id
name
description
duration
price
capacity
booking_type
status
created_at
updated_at
```

---

# 9. Service Duration

Each service should define its duration.

Examples:

```text
15 minutes
30 minutes
45 minutes
60 minutes
90 minutes
120 minutes
```

Example:

```text
Service:
Consultation

Duration:
30 minutes
```

The booking engine uses this duration when generating slots.

---

# 10. Resource-Service Relationship

Resources can provide one or more services.

Example:

```text
Resource A
    ├── Service 1
    ├── Service 2
    └── Service 3
```

And a service may be provided by multiple resources.

Use:

```text
resource_services
------------------------
resource_id
service_id
custom_price
custom_duration
status
```

This allows resource-specific overrides.

---

# 11. Booking Types

Support two major booking models.

## Individual Booking

One user reserves the resource.

Example:

```text
10:00 - 11:00

Capacity:
1
```

Once booked, nobody else can reserve that resource for that period.

## Capacity-Based Booking

Multiple users can book the same scheduled event/resource.

Example:

```text
Class:
6:00 PM

Capacity:
20
```

Up to 20 users can book.

Useful for:

```text
Classes
Workshops
Tours
Events
Group Sessions
```

---

# 12. Operating Hours

Administrators should configure general business hours.

Example:

```text
Monday
09:00 - 18:00

Tuesday
09:00 - 18:00

Wednesday
09:00 - 18:00

Sunday
CLOSED
```

Bookings cannot normally occur outside operating hours.

Admins may override this when required.

---

# 13. Resource Availability

Each resource can have its own schedule.

Example:

```text
Resource A

Monday
09:00 - 13:00
14:00 - 18:00

Tuesday
10:00 - 16:00

Wednesday
Unavailable
```

Availability structure:

```text
resource_availability
------------------------
id
resource_id
day_of_week
start_time
end_time
valid_from
valid_until
is_available
```

---

# 14. Multiple Availability Periods

A resource may have multiple availability periods on the same day.

Example:

```text
09:00 - 12:00

14:00 - 18:00
```

The period:

```text
12:00 - 14:00
```

is unavailable.

---

# 15. Availability Exceptions

Support date-specific exceptions.

Examples:

```text
Holiday
Leave
Maintenance
Special working hours
Private event
Resource unavailable
```

Structure:

```text
availability_exceptions
------------------------
id
resource_id
date
start_time
end_time
type
reason
```

Types:

```text
UNAVAILABLE
BLOCKED
SPECIAL_HOURS
HOLIDAY
MAINTENANCE
```

---

# 16. Blocked Time

Admins should be able to block arbitrary periods.

Example:

```text
Resource:
Resource A

Date:
25 September

Blocked:
12:00 - 14:00

Reason:
Maintenance
```

No booking should overlap this period.

---

# 17. Slot Generation

Slots should normally be generated dynamically.

Do not create thousands of future slot records unless the application's requirements specifically require pre-generated inventory.

Inputs:

```text
Resource
Service
Date
Operating Hours
Resource Availability
Exceptions
Existing Bookings
Service Duration
Buffer Time
```

Example:

```text
Availability:
09:00 - 12:00

Service Duration:
30 minutes
```

Generate:

```text
09:00
09:30
10:00
10:30
11:00
11:30
```

---

# 18. Availability Calculation

Conceptually:

```text
AVAILABLE TIME

=

Operating Hours

∩

Resource Availability

-

Blocked Periods

-

Exceptions

-

Existing Bookings

-

Required Buffers
```

Only slots capable of containing the complete service duration should be returned.

---

# 19. Booking

Generic booking structure:

```text
Booking
------------------------
id
user_id
resource_id
service_id
start_datetime
end_datetime
quantity
status
notes
created_at
updated_at
```

Optional:

```text
price
payment_status
cancellation_reason
rescheduled_from
created_by
```

---

# 20. Booking Status

Recommended statuses:

```text
PENDING
CONFIRMED
COMPLETED
CANCELLED
RESCHEDULED
NO_SHOW
WAITLISTED
CONFLICTED
```

Do not delete cancelled bookings.

Keep them for history, reporting and auditing.

---

# 21. Booking Flow

Standard booking flow:

```text
Choose Service
      ↓
Choose Resource
      ↓
Choose Date
      ↓
View Availability
      ↓
Choose Slot
      ↓
Enter Booking Details
      ↓
Confirm
      ↓
Backend Revalidates
      ↓
Booking Created
```

Alternatively:

```text
Service
↓
Date
↓
Available Resources
↓
Slot
```

Both flows should be supported by the architecture.

---

# 22. Clash Detection

Two bookings overlap when:

```text
newStart < existingEnd
AND
newEnd > existingStart
```

Example:

Existing:

```text
10:00 - 11:00
```

Requested:

```text
10:30 - 11:30
```

Conflict:

```text
YES
```

Requested:

```text
11:00 - 12:00
```

Conflict:

```text
NO
```

assuming no buffer is required.

---

# 23. Conflict Rules

A booking must be rejected if:

```text
Resource is unavailable

OR

Booking overlaps another exclusive booking

OR

Booking overlaps blocked time

OR

Booking falls outside availability

OR

Booking falls outside operating hours

OR

Capacity is reached

OR

Resource is inactive

OR

Required dependencies are unavailable
```

---

# 24. Race Condition Protection

Two users may select the same available slot simultaneously.

Example:

```text
User A sees 10:00 AVAILABLE

User B sees 10:00 AVAILABLE

Both click Book.
```

The system must guarantee that only the allowed number of bookings succeeds.

Correct architecture:

```text
Client
  ↓
Request Booking
  ↓
Backend Validation
  ↓
Begin Transaction
  ↓
Lock Relevant Resource/Slot
  ↓
Recheck Availability
  ↓
Create Booking
  ↓
Commit
```

The frontend must never be considered the source of truth.

---

# 25. Database-Level Protection

Where possible, enforce booking rules in the database as well as the application.

For fixed exclusive slots, a constraint could prevent duplicate reservations.

For arbitrary start/end times, use:

```text
Transaction
+
Locking
+
Overlap validation
```

For PostgreSQL, range/exclusion constraints can also be considered for preventing overlapping reservations.

---

# 26. Buffer Time

Some services need preparation/cleanup time.

Example:

```text
Service Duration:
60 minutes

Buffer Before:
10 minutes

Buffer After:
15 minutes
```

A:

```text
10:00 - 11:00
```

appointment effectively occupies:

```text
09:50 - 11:15
```

for conflict calculations.

Buffers should be configurable per service/resource.

---

# 27. Minimum Booking Notice

Businesses may prevent last-minute bookings.

Example:

```text
Minimum notice:
2 hours
```

At 10:00 AM, the earliest bookable appointment would therefore be:

```text
12:00 PM
```

---

# 28. Advance Booking Window

Configure how far ahead users can book.

Example:

```text
Maximum:
30 days
```

Users cannot book beyond the configured window.

---

# 29. Rescheduling

Users/admins can move an existing booking.

Example:

```text
Current:
10:00 - 11:00

New:
14:00 - 15:00
```

The new slot must be validated before modifying the existing reservation.

Recommended:

```text
BEGIN TRANSACTION

Validate new slot
Reserve new slot
Update booking
Release previous allocation

COMMIT
```

If any operation fails:

```text
ROLLBACK
```

The original booking must remain intact.

---

# 30. Cancellation

Bookings can be cancelled according to configurable rules.

Example:

```text
Cancellation allowed until:
2 hours before booking
```

Store:

```text
cancelled_at
cancelled_by
cancellation_reason
```

Admins can optionally override cancellation policies.

---

# 31. Waitlist

Capacity-based bookings can optionally support waitlists.

Example:

```text
Capacity:
10

Confirmed:
10

Additional User:
WAITLISTED
```

If one booking is cancelled:

```text
Capacity available
       ↓
First waitlisted user
       ↓
CONFIRMED
```

Promotion should be transactional to avoid accidentally promoting multiple users into one available position.

---

# 32. Capacity

Resources/services can optionally have capacity.

Example:

```text
Meeting Room:
Capacity 10
```

or:

```text
Workshop:
Capacity 25
```

Availability becomes:

```text
remaining_capacity
=
capacity - confirmed_quantity
```

A booking cannot exceed remaining capacity.

---

# 33. Multi-Resource Bookings

Some bookings require multiple resources.

Example:

```text
Service:
Photography Session

Requires:

Photographer
+
Studio
+
Camera Equipment
```

The system should optionally support:

```text
booking_resources
------------------------
booking_id
resource_id
```

All required resources must be available simultaneously.

Booking creation should be atomic.

Either:

```text
ALL resources are reserved
```

or:

```text
NONE are reserved
```

---

# 34. Recurring Bookings

Optionally support:

```text
Daily
Weekly
Monthly
Custom
```

Example:

```text
Every Monday
10:00 - 11:00
for 8 weeks
```

Each occurrence must be validated independently.

If conflicts occur, return them before final confirmation.

Example:

```text
7 bookings available

1 conflict:
19 October — 10:00 AM
```

The admin/user can decide how to handle conflicting occurrences.

---

# 35. Conflict Resolution

Administrative schedule changes can invalidate existing bookings.

Example:

```text
Existing booking:
10:00 - 11:00

Admin blocks:
09:00 - 12:00
```

The system should detect the affected booking.

Mark:

```text
CONFLICTED
```

Admin can:

```text
Reschedule
Cancel
Override Block
Reassign Resource
```

---

# 36. Schedule Change Validation

Before changing resource availability, show:

```text
This change affects 7 existing bookings.
```

The administrator should see the affected bookings before confirming.

Options:

```text
Keep existing bookings

Mark them as conflicts

Reschedule them

Cancel them
```

---

# 37. Alternative Resource Suggestion

If a resource becomes unavailable, the system can search for alternatives.

Example:

```text
Requested:

Resource A
10:00 - 11:00

Unavailable
```

Return:

```text
Resource B
10:00 - 11:00

Resource A
11:00 - 12:00

Resource C
10:30 - 11:30
```

This should be treated as an enhancement rather than a core booking guarantee.

---

# 38. User Dashboard

Display:

```text
Upcoming Bookings

Past Bookings

Cancelled Bookings

Waitlisted Bookings
```

Example:

```text
Upcoming

Service A
Resource B

25 Sep 2026
10:00 AM - 11:00 AM

CONFIRMED

[View]
[Reschedule]
[Cancel]
```

---

# 39. Admin Dashboard

Display metrics such as:

```text
Total Users

Active Resources

Today's Bookings

Upcoming Bookings

Completed Bookings

Cancelled Bookings

Pending Bookings

Conflicts

Resource Utilization
```

---

# 40. Admin Calendar

Provide:

```text
Day
Week
Month
```

Views.

Bookings should be filterable by:

```text
Resource
Service
Status
Location
Date
```

Example:

```text
09:00  Booking A

10:00  Booking B

11:00  AVAILABLE

12:00  BLOCKED

13:00  Booking C
```

---

# 41. Booking Management

Admin can:

```text
View booking

Create booking

Edit booking

Confirm booking

Cancel booking

Reschedule booking

Reassign resource

Mark completed

Mark no-show

Resolve conflict
```

---

# 42. Notifications

Notification triggers:

```text
Booking Created

Booking Confirmed

Booking Rescheduled

Booking Cancelled

Booking Reminder

Waitlist Promotion

Resource Changed
```

Channels may include:

```text
In-App
Email
SMS
Push Notification
```

The architecture should keep notification logic separate from booking logic.

---

# 43. Reminders

Configurable reminders:

```text
24 hours before

1 hour before
```

Store notification state so reminders aren't accidentally sent twice.

---

# 44. Timezones

Store absolute timestamps in:

```text
UTC
```

Each business/location should have a timezone.

Example:

```text
Asia/Kolkata
America/New_York
Europe/London
```

Convert times for display.

Never depend solely on the server's operating-system timezone.

---

# 45. Multi-Location Support

The architecture should optionally support multiple branches.

```text
locations
------------------------
id
name
address
timezone
status
```

Resources can belong to:

```text
Location A
Location B
```

Operating hours can also differ by location.

---

# 46. Core Database

Recommended tables:

```text
users

locations

resources

services

resource_services

resource_availability

availability_exceptions

bookings

booking_resources

notifications

audit_logs

settings
```

Optional:

```text
payments

waitlists

reviews

recurring_bookings
```

---

# 47. Users

```text
users
------------------------
id
name
email
phone
password_hash
role
status
created_at
updated_at
```

---

# 48. Resources

```text
resources
------------------------
id
location_id
name
type
description
capacity
metadata
status
created_at
updated_at
```

---

# 49. Services

```text
services
------------------------
id
name
description
duration_minutes
price
capacity
booking_type
buffer_before
buffer_after
status
created_at
updated_at
```

---

# 50. Resource Services

```text
resource_services
------------------------
resource_id
service_id
custom_duration
custom_price
status
```

---

# 51. Resource Availability

```text
resource_availability
------------------------
id
resource_id
day_of_week
start_time
end_time
valid_from
valid_until
status
```

---

# 52. Availability Exceptions

```text
availability_exceptions
------------------------
id
resource_id
start_datetime
end_datetime
type
reason
created_at
```

---

# 53. Bookings

```text
bookings
------------------------
id
user_id
service_id
primary_resource_id
location_id

start_datetime
end_datetime

quantity

status

notes

price

created_by

cancelled_at
cancelled_by
cancellation_reason

rescheduled_from_id

created_at
updated_at
```

---

# 54. Audit Logs

Important actions should be logged.

```text
audit_logs
------------------------
id
actor_id
action
entity_type
entity_id
old_value
new_value
created_at
```

Examples:

```text
BOOKING_CREATED

BOOKING_CANCELLED

BOOKING_RESCHEDULED

RESOURCE_CREATED

SCHEDULE_CHANGED

BLOCK_CREATED

CONFLICT_RESOLVED
```

---

# 55. REST API

## Authentication

```text
POST /api/auth/register

POST /api/auth/login

POST /api/auth/logout

POST /api/auth/forgot-password

POST /api/auth/reset-password

GET /api/auth/me
```

## Resources

```text
GET /api/resources

GET /api/resources/:id

GET /api/resources/:id/availability
```

Admin:

```text
POST /api/admin/resources

PUT /api/admin/resources/:id

DELETE /api/admin/resources/:id
```

---

# 56. Services API

```text
GET /api/services

GET /api/services/:id
```

Admin:

```text
POST /api/admin/services

PUT /api/admin/services/:id

DELETE /api/admin/services/:id
```

---

# 57. Availability API

```text
GET /api/availability
```

Example parameters:

```text
service_id

resource_id

location_id

date
```

Response:

```json
{
  "date": "2026-09-25",
  "slots": [
    {
      "start": "09:00",
      "end": "10:00",
      "available": true
    },
    {
      "start": "10:00",
      "end": "11:00",
      "available": false
    }
  ]
}
```

---

# 58. Booking API

```text
POST /api/bookings

GET /api/bookings

GET /api/bookings/:id

POST /api/bookings/:id/cancel

POST /api/bookings/:id/reschedule
```

Admin:

```text
GET /api/admin/bookings

POST /api/admin/bookings

PUT /api/admin/bookings/:id

POST /api/admin/bookings/:id/cancel

POST /api/admin/bookings/:id/reassign
```

---

# 59. Conflict API

```text
GET /api/admin/conflicts

GET /api/admin/conflicts/:id

POST /api/admin/conflicts/:id/resolve
```

---

# 60. Booking Creation Algorithm

Every booking request should follow approximately:

```text
1. Authenticate user

2. Validate service

3. Validate resource(s)

4. Validate requested date/time

5. Validate operating hours

6. Validate resource availability

7. Validate exceptions

8. Validate minimum notice

9. Validate advance booking limit

10. Calculate required buffers

11. Check overlapping bookings

12. Check capacity

13. Begin transaction

14. Lock relevant booking/resource data

15. RECHECK availability

16. Create booking

17. Commit

18. Trigger confirmation notification
```

The second availability check inside the transaction is critical.

---

# 61. Booking State Machine

Recommended flow:

```text
                 ┌───────────┐
                 │  PENDING  │
                 └─────┬─────┘
                       │
                       ▼
                ┌─────────────┐
                │  CONFIRMED  │
                └──────┬──────┘
                       │
              ┌────────┼─────────┐
              ▼        ▼         ▼
         COMPLETED   NO_SHOW   CANCELLED
```

Rescheduling can preserve the same booking with an audit trail or create a replacement booking linked through:

```text
rescheduled_from_id
```

Choose one strategy and use it consistently.

---

# 62. Frontend Routes

Public:

```text
/

/login

/register

/services

/resources

/resources/:id
```

User:

```text
/dashboard

/book

/bookings

/bookings/:id

/profile
```

Admin:

```text
/admin

/admin/calendar

/admin/bookings

/admin/resources

/admin/services

/admin/users

/admin/schedules

/admin/conflicts

/admin/locations

/admin/settings
```

---

# 63. Search and Filters

Users should be able to search/filter using:

```text
Service

Resource

Resource Type

Location

Date

Availability

Price
```

Admin should additionally filter by:

```text
Booking Status

User

Resource

Service

Date Range

Location
```

---

# 64. Configurable Booking Rules

Avoid hardcoding business rules.

Store configuration such as:

```text
slot_interval

minimum_booking_notice

maximum_advance_booking_days

cancellation_window

rescheduling_window

default_buffer_before

default_buffer_after

allow_waitlist

allow_recurring_bookings

require_admin_confirmation
```

This is what makes the engine reusable.

---

# 65. Security

Implement:

```text
Secure password hashing

JWT/session authentication

Role-based authorization

Input validation

Rate limiting

SQL injection protection

XSS protection

CSRF protection where applicable

Secure headers

Audit logging
```

A user must only be able to access or modify bookings they own unless their role grants broader access.

---

# 66. Important Edge Cases

The system must explicitly handle:

```text
Two users booking simultaneously

Bookings crossing midnight

Different timezones

Daylight-saving transitions

Resource becomes unavailable after booking

Service duration changes

Operating hours change

Resource availability changes

Cancelled bookings

Partial overlaps

Buffer overlaps

Capacity bookings

Waitlists

Recurring bookings

Multi-resource bookings

Admin overrides

Past slots

Resource deletion with future bookings
```

Resources with historical bookings should generally be deactivated rather than physically deleted.

---

# 67. Core Business Rules

### Rule 1

Never allow an exclusive resource to have overlapping active bookings.

### Rule 2

Never trust frontend availability.

### Rule 3

Availability must be revalidated during booking.

### Rule 4

Use database transactions for booking operations.

### Rule 5

Cancelled bookings should no longer consume capacity.

### Rule 6

Pending bookings consume capacity only if configured to do so.

### Rule 7

Bookings cannot normally occur outside resource availability.

### Rule 8

Blocked periods override regular availability.

### Rule 9

Date-specific availability overrides recurring availability.

### Rule 10

Inactive resources cannot receive new bookings.

### Rule 11

Users cannot modify bookings belonging to another user.

### Rule 12

Every administrative override should be audited.

### Rule 13

Rescheduling must never destroy the original reservation before the new slot has been successfully validated.

### Rule 14

Capacity must never become negative.

### Rule 15

Multi-resource reservations must succeed or fail atomically.

---

# 68. Recommended Architecture

```text
                 CLIENT APPLICATION
                        │
                        ▼
                ┌──────────────┐
                │   REST API   │
                └──────┬───────┘
                       │
       ┌───────────────┼────────────────┐
       │               │                │
       ▼               ▼                ▼

 Authentication    Booking Engine   Admin Service
       │               │                │
       │               ▼                │
       │        Availability Engine     │
       │               │                │
       └───────────────┼────────────────┘
                       │
                       ▼
                 ┌───────────┐
                 │ Database  │
                 └─────┬─────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼

        Background Jobs    Notifications
```

---

# 69. Recommended Tech Stack

One possible implementation:

## Frontend

```text
React
TypeScript
Tailwind CSS
React Router
TanStack Query
```

## Backend

```text
Python + FastAPI
```

## Database

```text
PostgreSQL
```

PostgreSQL is particularly suitable for a scheduling application because of its strong transaction support and range-related functionality.

## Optional

```text
Redis
```

for:

```text
Caching
Temporary holds
Rate limiting
Background jobs
```

---

# 70. MVP

The first version should focus on the core engine:

```text
Authentication
        ↓
Users + Admin
        ↓
Resources
        ↓
Services
        ↓
Resource Availability
        ↓
Dynamic Slot Generation
        ↓
Booking
        ↓
Clash Prevention
        ↓
Rescheduling
        ↓
Cancellation
        ↓
Admin Calendar
```

Do not start with payments, reviews, complex analytics or recurring reservations before the scheduling engine is reliable.

---

# 71. Phase 2

After the core booking system is stable, add:

```text
Email/SMS notifications

Waitlists

Multiple locations

Recurring bookings

Multi-resource bookings

Advanced reporting

Reviews

Payments

Refunds

Resource utilization analytics

Automated reminders
```

---

# 72. Most Important Design Principle

The system should never ask:

```text
"Is the doctor available?"

"Is the trainer available?"

"Is the room available?"
```

The core booking engine should only understand:

```text
"Is RESOURCE X available for SERVICE Y
between START_TIME and END_TIME?"
```

Everything else is configuration.

Therefore:

```text
Doctor        → Resource

Trainer       → Resource

Consultant    → Resource

Meeting Room  → Resource

Court         → Resource

Equipment     → Resource
```

This separation is what allows the same scheduling engine to be reused across completely different industries without rewriting the booking logic.

---

# 73. Definition of Done

The core system can be considered complete when it can reliably answer:

```text
WHO is making the booking?

WHAT service are they booking?

WHICH resource is required?

WHERE does it happen?

WHEN does it start?

WHEN does it end?

IS the resource available?

IS there sufficient capacity?

DOES it clash with another reservation?

CAN the booking be created?

CAN it safely be cancelled?

CAN it safely be rescheduled?
```

If those questions are handled consistently, the platform can serve as the foundation for almost any appointment, reservation, or booking-based application.
