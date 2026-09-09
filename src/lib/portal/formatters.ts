/**
 * Portal-facing formatters. Centralized so no list/detail surface redefines
 * its own formatDate/formatCurrency variant (the pattern that produced the
 * three-different-date-formats drift this registry is fixing).
 *
 * Money rendering: use `<MoneyDisplay amountCents={N}>` as the default.
 */

/**
 * Short date: "Apr 13, 2026" / "Apr 13" when within the current year.
 * Returns an empty string for invalid ISO input so callers can chain without
 * guarding.
 */
export function formatShortDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const sameYear = d.getFullYear() === now.getFullYear()
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  })
}
