/**
 * Read a SQLite timestamp as the UTC instant it actually is.
 *
 * SQLite's CURRENT_TIMESTAMP writes `YYYY-MM-DD HH:MM:SS` in UTC, with no zone
 * marker. JavaScript's Date parses that space-separated form as LOCAL time, so
 * on a UTC-7 machine every row reads seven hours younger than it is -- and a row
 * written minutes ago in UTC reads as created in the future. The register's
 * first table printed "-1d old" for a row created moments earlier, which is how
 * this was found.
 *
 * It matters beyond cosmetics: the reconciler's stale ladder measures a row's
 * age against a 30-day threshold, and a silent seven-hour skew in the direction
 * of "younger" delays every alarm.
 *
 * Shared by the reconciler (tsx) and register.mjs (bare node), hence `.mjs`.
 */

/**
 * @param {string | null | undefined} value a SQLite timestamp, ISO string, or null
 * @returns {number | null} epoch milliseconds, or null when unparseable
 */
export function sqliteUtcMs(value) {
  if (!value) return null
  const raw = String(value).trim()
  // Already carries a zone or a T-separator with offset: trust it.
  const normalized = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(raw) ? raw : `${raw.replace(' ', 'T')}Z`
  const ms = new Date(normalized).getTime()
  return Number.isNaN(ms) ? null : ms
}

/**
 * Whole days elapsed since a SQLite timestamp. Negative values are clamped to
 * 0: a row cannot be younger than new, and surfacing "-1d old" to a reader
 * teaches them the number is untrustworthy.
 *
 * @param {string | null | undefined} value
 * @param {number} now epoch milliseconds
 * @returns {number | null} whole days, or null when unparseable
 */
export function ageInDays(value, now = Date.now()) {
  const ms = sqliteUtcMs(value)
  if (ms === null) return null
  return Math.max(0, Math.floor((now - ms) / 86400000))
}
