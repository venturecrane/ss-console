/**
 * Booking system operational alerts.
 *
 * Records errors to the append-only context table (audit trail) and sends
 * a rate-limited email to the admin when the booking system is degraded.
 *
 * Rate limiting: one email per error kind per 30 minutes. Context rows are
 * always written regardless of whether an email is sent.
 */

import { appendContextRaw } from '../db/context.js'
import { sendEmail } from '../email/resend.js'
import { ORG_ID, SYSTEM_ENTITY_ID } from '../constants.js'
import { BOOKING_CONFIG } from './config.js'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type BookingAlertKind = 'google_sync_error' | 'integration_invalid_grant' | 'freebusy_error'

export interface BookingAlertDetails {
  assessmentId?: string
  scheduleId?: string
  entityId?: string
  message: string
  httpStatus?: number
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Admin receives operational alerts. */
const ALERT_EMAIL = BOOKING_CONFIG.consultant.email // scott@smd.services

/** Only one email per kind in this window. */
const RATE_LIMIT_MINUTES = 30

// ---------------------------------------------------------------------------
// Human-readable descriptions per alert kind
// ---------------------------------------------------------------------------

const ALERT_DESCRIPTIONS: Record<
  BookingAlertKind,
  { title: string; meaning: string; action: string }
> = {
  google_sync_error: {
    title: 'Google Calendar sync failed',
    meaning:
      'A booking was confirmed but the Google Calendar event could not be created or updated. ' +
      'The guest received a confirmation email with a fallback note. The calendar is out of sync ' +
      'until this is resolved manually.',
    action:
      'Check the Google Calendar integration in admin. If the error persists, re-authorize ' +
      'the Google OAuth connection. The assessment row in D1 has the booking details.',
  },
  integration_invalid_grant: {
    title: 'Google OAuth token revoked or expired',
    meaning:
      'The stored refresh token for Google Calendar is no longer valid. All calendar operations ' +
      '(event creation, free/busy queries) will fail until re-authorized. Bookings will still ' +
      'succeed via the email fallback path, but calendar sync is fully down.',
    action:
      'Go to the admin Google integration page and re-authorize. This requires signing in to ' +
      'the Google account and granting calendar permissions again.',
  },
  freebusy_error: {
    title: 'Google Calendar free/busy query failed',
    meaning:
      'The availability engine could not check the consultant calendar for conflicts. Slots are ' +
      'being shown based on the weekly schedule and D1 data only, which may lead to double-bookings ' +
      'if the consultant has external calendar events.',
    action:
      'Check the Google Calendar integration status. If the error is transient (5xx), it may ' +
      'resolve on its own. If persistent, re-authorize the OAuth connection.',
  },
}

// ---------------------------------------------------------------------------
// Core function
// ---------------------------------------------------------------------------

/**
 * Record a booking system error and conditionally alert the admin.
 *
 * 1. Always writes an `alert` context row (audit trail).
 * 2. Checks if this is the first error of this kind in the last 30 minutes.
 * 3. If first in window, sends an email to the admin.
 *
 * Designed to be called with `ctx.waitUntil()` so it never blocks the user
 * response path.
 */
export async function recordBookingError(
  db: D1Database,
  resendApiKey: string | undefined,
  kind: BookingAlertKind,
  details: BookingAlertDetails
): Promise<void> {
  const entityId = details.entityId ?? SYSTEM_ENTITY_ID
  const timestamp = new Date().toISOString()

  // 1. Always write the context row
  await appendContextRaw(db, ORG_ID, {
    entity_id: entityId,
    type: 'alert',
    content: `Booking alert: ${kind} — ${details.message}`,
    source: 'booking_system',
    metadata: {
      kind,
      assessmentId: details.assessmentId ?? null,
      scheduleId: details.scheduleId ?? null,
      httpStatus: details.httpStatus ?? null,
      timestamp,
    },
  })

  // 2. Check if first error of this kind in the 30-minute window.
  //    The count includes the row we just inserted. n=1 means this is the
  //    only alert of this kind in the window, so we should email.
  const row = await db
    .prepare(
      `SELECT COUNT(*) as n FROM context
       WHERE type = 'alert'
         AND json_extract(metadata, '$.kind') = ?
         AND created_at > datetime('now', '-${RATE_LIMIT_MINUTES} minutes')`
    )
    .bind(kind)
    .first<{ n: number }>()

  const count = row?.n ?? 0

  if (count !== 1) {
    // Not the first in window — suppress the email
    return
  }

  // 3. Send the alert email
  const desc = ALERT_DESCRIPTIONS[kind]

  const emailResult = await sendEmail(resendApiKey, {
    to: ALERT_EMAIL,
    subject: `[SMD] Booking system alert: ${kind}`,
    html: buildAlertEmailHtml(kind, desc, details, timestamp),
  })

  if (!emailResult.success) {
    console.error(`[booking/alerts] Failed to send alert email for ${kind}:`, emailResult.error)
  }
}

// ---------------------------------------------------------------------------
// Email template
// ---------------------------------------------------------------------------

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function buildAlertEmailHtml(
  kind: BookingAlertKind,
  desc: { title: string; meaning: string; action: string },
  details: BookingAlertDetails,
  timestamp: string
): string {
  const detailRows: string[] = []
  if (details.assessmentId) {
    detailRows.push(`<strong>Assessment:</strong> ${escapeHtml(details.assessmentId)}`)
  }
  if (details.scheduleId) {
    detailRows.push(`<strong>Schedule:</strong> ${escapeHtml(details.scheduleId)}`)
  }
  if (details.httpStatus) {
    detailRows.push(`<strong>HTTP status:</strong> ${details.httpStatus}`)
  }
  detailRows.push(`<strong>Error:</strong> ${escapeHtml(details.message)}`)
  detailRows.push(`<strong>Time:</strong> ${escapeHtml(timestamp)}`)

  const detailHtml = detailRows.map((r) => `<p style="margin:4px 0;">${r}</p>`).join('')

  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#fef2f2;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:520px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #fecaca;overflow:hidden;">
    <div style="background-color:#dc2626;padding:16px 24px;">
      <h1 style="font-size:16px;font-weight:700;color:#ffffff;margin:0;">
        ${escapeHtml(desc.title)}
      </h1>
      <p style="font-size:12px;color:#fecaca;margin:4px 0 0;">
        ${escapeHtml(kind)}
      </p>
    </div>
    <div style="padding:24px;">
      <h2 style="font-size:14px;color:#0f172a;font-weight:600;margin:0 0 8px;">What happened</h2>
      <p style="font-size:14px;color:#334155;margin:0 0 16px;">
        ${escapeHtml(desc.meaning)}
      </p>

      <h2 style="font-size:14px;color:#0f172a;font-weight:600;margin:0 0 8px;">What to do</h2>
      <p style="font-size:14px;color:#334155;margin:0 0 16px;">
        ${escapeHtml(desc.action)}
      </p>

      <div style="background:#f8fafc;border-radius:6px;padding:16px;margin:16px 0 0;">
        <h3 style="font-size:13px;color:#64748b;margin:0 0 8px;text-transform:uppercase;letter-spacing:0.05em;">Details</h3>
        ${detailHtml}
      </div>

      <p style="font-size:12px;color:#94a3b8;margin:16px 0 0;">
        This alert is rate-limited to one email per error type every ${RATE_LIMIT_MINUTES} minutes.
        All occurrences are logged in the context table regardless of email suppression.
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} SMD Services &middot; Booking System Alert
      </p>
    </div>
  </div>
</body>
</html>`
}
