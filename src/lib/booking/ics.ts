/**
 * ICS (RFC 5545) generator for booking calendar invites.
 *
 * Wraps the `ics` npm package with sensible defaults for SMD's booking flow:
 *
 * UID strategy:
 *   `booking-${assessment_schedule.id}@smd.services`
 *   Stable across reschedules. Calendar clients use UID + SEQUENCE to update
 *   an existing event rather than creating a duplicate.
 *
 * SEQUENCE:
 *   - 0 on initial create
 *   - increments by 1 on each reschedule
 *   - any value on cancel (METHOD=CANCEL is the signal)
 *
 * METHOD:
 *   - 'REQUEST' for create + reschedule (Outlook treats it as an invite to accept)
 *   - 'CANCEL' for cancellation emails (recipients can remove from their calendar)
 *
 * Resend MIME content type must match: `text/calendar; method=REQUEST` or
 * `text/calendar; method=CANCEL`. The Resend wrapper at
 * `src/lib/email/resend.ts` already passes content_type through to the API.
 */

import { createEvent } from 'ics'
import type { EventAttributes } from 'ics'

export type IcsMethod = 'REQUEST' | 'CANCEL'

export interface BuildIcsInput {
  /** Stable across reschedules. Use `assessment_schedule.id`. */
  scheduleId: string
  /** Increments on each reschedule (0, 1, 2, ...). */
  sequence: number
  method: IcsMethod
  /** UTC ISO 8601 string. */
  startUtc: string
  durationMinutes: number
  /** Plain text title shown in calendar clients. */
  title: string
  /** Plain text description. URLs allowed; HTML is not. */
  description: string
  /** Optional location string (Google Meet URL works fine here). */
  location?: string
  organizerName: string
  organizerEmail: string
  guestName: string
  guestEmail: string
}

export interface BuildIcsResult {
  ics: string
  /** The MIME content type to set on the email attachment. */
  contentType: string
}

/**
 * Build an ICS file for a booking. Returns the raw ICS string and the
 * matching MIME content type. Caller is responsible for base64-encoding
 * the ICS string for the Resend `attachments[].content` field.
 *
 * Throws on invalid input — the caller should ensure the date is parseable
 * and the duration is positive.
 */
export function buildIcs(input: BuildIcsInput): BuildIcsResult {
  const start = new Date(input.startUtc)
  if (isNaN(start.getTime())) {
    throw new Error(`buildIcs: invalid startUtc "${input.startUtc}"`)
  }
  if (input.durationMinutes <= 0) {
    throw new Error(`buildIcs: durationMinutes must be positive, got ${input.durationMinutes}`)
  }

  // ics expects [year, month, day, hour, minute] in the local time of the
  // `startInputType` you specify. We use 'utc' so the array represents UTC
  // and the resulting ICS includes a Z (UTC) DTSTART.
  const startArray: [number, number, number, number, number] = [
    start.getUTCFullYear(),
    start.getUTCMonth() + 1, // ics is 1-indexed
    start.getUTCDate(),
    start.getUTCHours(),
    start.getUTCMinutes(),
  ]

  const event: EventAttributes = {
    uid: `booking-${input.scheduleId}@smd.services`,
    sequence: input.sequence,
    start: startArray,
    startInputType: 'utc',
    startOutputType: 'utc',
    duration: { minutes: input.durationMinutes },
    title: input.title,
    description: input.description,
    location: input.location,
    organizer: { name: input.organizerName, email: input.organizerEmail },
    attendees: [
      {
        name: input.guestName,
        email: input.guestEmail,
        rsvp: true,
        partstat: 'NEEDS-ACTION',
        role: 'REQ-PARTICIPANT',
      },
    ],
    status: input.method === 'CANCEL' ? 'CANCELLED' : 'CONFIRMED',
    method: input.method,
  }

  const { error, value } = createEvent(event)
  if (error || !value) {
    throw new Error(`buildIcs: failed to generate ICS — ${error?.message ?? 'unknown'}`)
  }

  return {
    ics: value,
    contentType: `text/calendar; method=${input.method}`,
  }
}

/**
 * Base64-encode an ICS string for use as a Resend attachment.
 *
 * Resend's `attachments[].content` field accepts a base64 string. This
 * helper centralizes the encoding so callers don't have to remember.
 */
export function icsToBase64(ics: string): string {
  // Encode to UTF-8 bytes first so non-ASCII characters survive base64
  const utf8Bytes = new TextEncoder().encode(ics)
  let bin = ''
  for (let i = 0; i < utf8Bytes.length; i++) bin += String.fromCharCode(utf8Bytes[i])
  return btoa(bin)
}
