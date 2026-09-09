/**
 * Invoice due dates: the D1 row holds a calendar date (YYYY-MM-DD, the value
 * of the admin form's date input); Stripe wants an instant. The instant a
 * calendar due date means is the end of that day where the firm operates,
 * so a client who pays on the due date, in their own afternoon, is on time.
 *
 * The timezone is the venture's operating timezone (Phoenix, no DST), the
 * same constant the booking engine uses.
 */

import { fromZonedTime } from 'date-fns-tz'
import { BOOKING_CONFIG } from '../booking/config'

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/

/** True when `value` is a real calendar date written YYYY-MM-DD. */
export function isIsoCalendarDate(value: string): boolean {
  const m = ISO_DATE.exec(value)
  if (!m) return false
  const [, y, mo, d] = m
  const probe = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d)))
  return (
    probe.getUTCFullYear() === Number(y) &&
    probe.getUTCMonth() === Number(mo) - 1 &&
    probe.getUTCDate() === Number(d)
  )
}

/**
 * End of the given calendar day in the venture timezone, as the Unix
 * timestamp (seconds) Stripe's `due_date` takes. Null for anything that is
 * not a YYYY-MM-DD calendar date.
 */
export function dueDateToStripeTimestamp(
  isoDate: string,
  timeZone: string = BOOKING_CONFIG.consultant.timezone
): number | null {
  if (!isIsoCalendarDate(isoDate)) return null
  const endOfDay = fromZonedTime(`${isoDate}T23:59:59`, timeZone)
  return Math.floor(endOfDay.getTime() / 1000)
}
