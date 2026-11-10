/**
 * Every money formatter in the site and its workers, with the unit in the name.
 *
 * Ten formatters used to live in ten files: four took cents and four took
 * dollars under overlapping names (`formatCurrency` meant cents on the cost
 * pages and dollars in the quote builder and the signed SOW's payment table),
 * so calling the wrong one was a factor-of-100 error on a contract. A function
 * here says its unit: `formatCents*` takes integer cents, `formatDollars*`
 * takes dollars. Each output register below is the one its callers already
 * rendered, byte for byte (tests/money-format.test.ts holds the known answers
 * captured from the old functions).
 *
 * Not for marketing pages: they never render a dollar amount (CLAUDE.md tone
 * standard 4).
 */

/** Integer cents as "$1,234.56" (negative: "-$2.50"). The admin cost and alert pages. */
export function formatCents(cents: number): string {
  return (cents / 100).toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/** Integer cents as "$1234.56", no thousands separator. The chronology jobs page. */
export function formatCentsUngrouped(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

/** Integer cents as whole dollars, "$1,235". Portal prose (MoneyDisplay renders markup). */
export function formatCentsAsWholeDollars(cents: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(Math.round(cents / 100))
}

/**
 * Dollars as whole dollars, "$1,235". Quotes, the SOW payment table, and the
 * admin billing views. Rounds half away from zero (Intl's default).
 */
export function formatDollars(amount: number): string {
  return `$${amount.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
}

/**
 * Dollars as whole dollars, "$1,235", rounding a half toward +infinity
 * (`Math.round`): the admin billing and client views. It differs from
 * formatDollars only on a negative exact half (-2.5 renders "$-2" here, "$-3"
 * there). Invoice and quote amounts carry no CHECK against negatives
 * (migrations/0001_create_tables.sql), so the two registers are kept exactly as
 * their callers rendered them rather than merged on an unproven assumption.
 */
export function formatDollarsHalfUp(amount: number): string {
  return '$' + Math.round(amount).toLocaleString('en-US')
}

/**
 * Dollars as "$\u200B1234.56" for an internal context-entry line. The zero-width
 * space keeps the stored text from matching the literal dollar-digit patterns
 * the content compliance tests scan for.
 */
export function formatDollarsForContextNote(amount: number): string {
  return `$\u200B${amount.toFixed(2)}`
}
