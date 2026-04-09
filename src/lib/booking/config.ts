/**
 * Booking system configuration.
 *
 * Single consultant (Scott Durgan), Phoenix America/Phoenix tz (no DST ever).
 * The hardcoded weekly schedule and slot rules are read by the availability
 * engine (`./availability.ts`) on every slot computation.
 *
 * In v2 we'll expose this via an admin UI backed by a config table; v1 keeps
 * it as a TypeScript const so changes require a deploy. Three lines today
 * vs an admin form + DB read on every booking page load.
 */

export interface WeeklyWindow {
  /** 24h local time, "HH:MM". */
  start: string
  /** 24h local time, "HH:MM" — exclusive (a 16:00 end means the last 30-min slot starts at 15:30). */
  end: string
}

export type WeeklyDayKey = 'sun' | 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat'

export interface BookingConfig {
  consultant: {
    name: string
    email: string
    /** Google Calendar id; 'primary' for the OAuth-connected account's main calendar. */
    calendar_id: string
    /** IANA timezone for the consultant. */
    timezone: string
  }
  /** Slot length in minutes. v1 only supports a single slot length. */
  slot_minutes: number
  /** Buffer enforced before AND after every busy/booked range. */
  buffer_minutes: number
  /** Minimum lead time before the next bookable slot (24h = 1440). */
  min_notice_minutes: number
  /** How far in the future slots may be offered. */
  max_lookahead_days: number
  /** Working hours per local day. Empty array = closed that day. */
  weekly_schedule: Record<WeeklyDayKey, WeeklyWindow[]>
  /** Human-readable label used in emails / ICS / page copy. */
  meeting_label: string
  /** Per-call timeout for Google Calendar API requests. */
  google_call_timeout_ms: number
  /** Number of retries on transient Google failures (5xx, network). */
  google_call_retries: number
  /**
   * Manage token TTL after the slot ends. The guest can cancel/reschedule
   * for this many hours after their assessment slot before the token expires.
   */
  manage_token_ttl_hours_after_slot: number
}

export const BOOKING_CONFIG: BookingConfig = {
  consultant: {
    name: 'Scott Durgan',
    email: 'scott@smd.services',
    calendar_id: 'primary',
    timezone: 'America/Phoenix',
  },
  slot_minutes: 30,
  buffer_minutes: 15,
  min_notice_minutes: 24 * 60,
  max_lookahead_days: 14,
  weekly_schedule: {
    sun: [],
    mon: [{ start: '09:00', end: '16:00' }],
    tue: [{ start: '09:00', end: '16:00' }],
    wed: [{ start: '09:00', end: '16:00' }],
    thu: [{ start: '09:00', end: '16:00' }],
    fri: [{ start: '09:00', end: '16:00' }],
    sat: [],
  },
  meeting_label: '30-minute intro call',
  google_call_timeout_ms: 8_000,
  google_call_retries: 1,
  manage_token_ttl_hours_after_slot: 48,
}
