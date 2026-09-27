export type Role = "USER" | "ADMIN" | "STAFF" | "RESOURCE_OWNER" | "MANAGER" | "SUPER_ADMIN";
export type UserStatus = "ACTIVE" | "INACTIVE" | "SUSPENDED";
export type RecordStatus = "ACTIVE" | "INACTIVE";
export type ResourceType = "PERSON" | "ROOM" | "FACILITY" | "EQUIPMENT" | "VEHICLE" | "DESK" | "COURT" | "CUSTOM";
export type BookingType = "INDIVIDUAL" | "CAPACITY";
export type BookingStatus =
  | "PENDING"
  | "CONFIRMED"
  | "COMPLETED"
  | "CANCELLED"
  | "RESCHEDULED"
  | "NO_SHOW"
  | "WAITLISTED"
  | "CONFLICTED";
export type ExceptionType = "UNAVAILABLE" | "BLOCKED" | "SPECIAL_HOURS" | "HOLIDAY" | "MAINTENANCE";
export type ConflictAction = "keep" | "mark_conflicted" | "cancel";

export const RESOURCE_TYPES: ResourceType[] = ["PERSON", "ROOM", "FACILITY", "EQUIPMENT", "VEHICLE", "DESK", "COURT", "CUSTOM"];
export const ROLES: Role[] = ["USER", "STAFF", "RESOURCE_OWNER", "MANAGER", "ADMIN", "SUPER_ADMIN"];
export const BOOKING_STATUSES: BookingStatus[] = [
  "PENDING",
  "CONFIRMED",
  "CONFLICTED",
  "WAITLISTED",
  "COMPLETED",
  "NO_SHOW",
  "CANCELLED",
  "RESCHEDULED",
];
export const EXCEPTION_TYPES: ExceptionType[] = ["BLOCKED", "UNAVAILABLE", "HOLIDAY", "MAINTENANCE", "SPECIAL_HOURS"];

export interface Page<T> {
  items: T[];
  total: number;
}

export interface User {
  id: string;
  name: string;
  email: string;
  phone: string | null;
  role: Role;
  status: UserStatus;
  created_at: string;
}

export interface Me extends User {
  permissions: string[];
}

export interface Location {
  id: string;
  name: string;
  address: string | null;
  /** The location's own Google Maps link, if an admin set one. */
  map_url: string | null;
  /** Where the location opens in Google Maps: its own link, or a search for its address. */
  google_maps_url: string | null;
  timezone: string;
  status: RecordStatus;
}

export interface LocationRef {
  id: string;
  name: string;
  address: string | null;
  google_maps_url: string | null;
}

export interface Resource {
  id: string;
  name: string;
  description: string | null;
  type: ResourceType;
  capacity: number | null;
  metadata: Record<string, unknown>;
  location_id: string | null;
  location: Location | null;
  timezone: string;
  status: RecordStatus;
  created_at: string;
  updated_at: string;
}

export interface AvailabilityRule {
  id?: string;
  day_of_week: number;
  start_time: string;
  end_time: string;
  valid_from: string | null;
  valid_until: string | null;
  is_available: boolean;
}

export interface Offering {
  resource_id: string;
  resource_name: string;
  resource_type: ResourceType;
  service_id: string;
  service_name: string;
  booking_type: BookingType;
  duration_minutes: number;
  /** Longest bookable length here; equals duration_minutes when the length is fixed. */
  max_duration_minutes: number;
  /** Hourly rate. */
  price: string | null;
  custom_duration: number | null;
  custom_price: string | null;
  custom_buffer_before: number | null;
  custom_buffer_after: number | null;
  location_name: string | null;
  location: LocationRef | null;
  status: RecordStatus;
}

export interface ResourceDetail extends Resource {
  services: Offering[];
  availability: AvailabilityRule[];
}

export interface Service {
  id: string;
  name: string;
  description: string | null;
  duration_minutes: number;
  /** Customers may book any multiple of duration_minutes up to this; null = fixed length. */
  max_duration_minutes: number | null;
  /** Hourly rate. */
  price: string | null;
  capacity: number | null;
  booking_type: BookingType;
  buffer_before: number | null;
  buffer_after: number | null;
  status: RecordStatus;
  created_at: string;
  updated_at: string;
}

export interface ServiceDetail extends Service {
  resources: Offering[];
}

export interface Slot {
  start: string;
  end: string;
  start_time: string;
  end_time: string;
  available: boolean;
  status: string;
  message: string;
  remaining: number | null;
  capacity: number | null;
}

export interface ResourceSlots {
  resource_id: string;
  resource_name: string;
  resource_type: ResourceType;
  timezone: string;
  duration_minutes: number;
  price: string | null;
  booking_type: BookingType;
  slots: Slot[];
}

export interface AggregatedSlot {
  start: string;
  end: string;
  start_time: string;
  end_time: string;
  available: boolean;
  resource_ids: string[];
}

export interface Availability {
  date: string;
  service_id: string;
  resources: ResourceSlots[];
  slots: AggregatedSlot[];
}

export interface Alternative {
  resource_id: string;
  resource_name: string;
  timezone: string;
  start: string;
  end: string;
  same_resource: boolean;
  remaining: number | null;
}

export interface Ref {
  id: string;
  name: string;
}

export interface Booking {
  id: string;
  user: Ref & { email: string };
  service: Ref & { booking_type: BookingType; duration_minutes: number };
  resource: Ref & { type: ResourceType };
  additional_resources: (Ref & { type: ResourceType })[];
  location: LocationRef | null;
  timezone: string;
  start_datetime: string;
  end_datetime: string;
  quantity: number;
  status: BookingStatus;
  notes: string | null;
  price: string | null;
  created_at: string;
  updated_at: string;
  confirmed_at: string | null;
  cancelled_at: string | null;
  cancellation_reason: string | null;
  conflict_reason: string | null;
  rescheduled_from_id: string | null;
  rescheduled_to_id: string | null;
  recurring_series_id: string | null;
  waitlist_position: number | null;
  can_cancel: boolean;
  can_reschedule: boolean;
}

export interface Occurrence {
  start: string;
  end: string;
  available: boolean;
  code: string;
  message: string;
}

export interface RecurringPreview {
  occurrences: Occurrence[];
  available_count: number;
  conflict_count: number;
}

export interface OperatingHours {
  id?: string;
  location_id?: string | null;
  day_of_week: number;
  start_time: string;
  end_time: string;
}

export interface AvailabilityException {
  id: string;
  resource_id: string | null;
  location_id: string | null;
  start_datetime: string;
  end_datetime: string;
  type: ExceptionType;
  reason: string | null;
  created_at: string;
}

export interface AffectedBooking {
  booking_id: string;
  code: string;
  message: string;
  booking: {
    id: string;
    user_name: string;
    user_email: string;
    service_name: string;
    resource_name: string;
    start_datetime: string;
    end_datetime: string;
    status: BookingStatus;
  };
}

export interface ScheduleChange<T = unknown> {
  applied: boolean;
  affected_count: number;
  affected_bookings: AffectedBooking[];
  result: T;
}

export interface NotificationItem {
  id: string;
  booking_id: string | null;
  type: string;
  channel: string;
  title: string;
  body: string;
  read_at: string | null;
  created_at: string;
}

export interface AuditLog {
  id: string;
  actor_id: string | null;
  actor_name: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  old_value: Record<string, unknown> | null;
  new_value: Record<string, unknown> | null;
  created_at: string;
}

export interface PublicConfig {
  business_name: string;
  default_timezone: string;
  allow_waitlist: boolean;
  allow_recurring_bookings: boolean;
  require_admin_confirmation: boolean;
  maximum_advance_booking_days: number;
  minimum_booking_notice: number;
  cancellation_window: number;
  rescheduling_window: number;
}

export interface Utilization {
  resource_id: string;
  resource_name: string;
  available_minutes: number;
  booked_minutes: number;
  utilization: number;
}

export interface Dashboard {
  timezone: string;
  total_users: number;
  active_resources: number;
  todays_bookings: number;
  upcoming_bookings: number;
  completed_bookings: number;
  cancelled_bookings: number;
  pending_bookings: number;
  waitlisted_bookings: number;
  conflicts: number;
  resource_utilization: Utilization[];
}

export interface CalendarData {
  start: string;
  end: string;
  timezone: string;
  resources: Resource[];
  bookings: Booking[];
  blocks: AvailabilityException[];
}

export interface SettingDefinition {
  key: string;
  default: unknown;
  description: string;
}
