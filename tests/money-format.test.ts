/**
 * The money formatters' one home (src/lib/format/money.ts), held to the
 * strings the formatters it replaced rendered.
 *
 * Every expected string below was produced by running the old function on
 * origin/main f72a4d411 (the formatters named in each `replaces`), so a pass
 * here means each caller renders the same text it did before the move. The
 * last case shows why the unit is in the name: the same number rendered as
 * cents and as dollars differs by a factor of 100, the trap the shared name
 * `formatCurrency` used to hide.
 */
import { describe, expect, it } from 'vitest'
import * as money from '../src/lib/format/money'

type Fn = keyof typeof money
type Known = { replaces: string; cases: Array<[number, string]> }

const CENTS_IN = [
  0, 1, 50, 99, 100, 150, 12345, 99999, 100000, 123456, 500000, 123456789, -250, -123456,
]
const DOLLARS_IN = [
  0, 1, 1234, 10500, 1.4, 1.5, 2.5, 10.49, 999.99, 1234.5, 1234.6, 1234567.89, -5, -1234.4, -2.5,
  -1234.5,
]

function zip(inputs: number[], outputs: string[]): Array<[number, string]> {
  expect(outputs).toHaveLength(inputs.length)
  return inputs.map((n, i) => [n, outputs[i]])
}

const KNOWN: Record<Fn, Known> = {
  formatCents: {
    replaces:
      'formatCurrency(cents) in costs/index.astro, costs/[customer_slug].astro, alerts.astro and workers/cost-anomaly/src/notify.ts',
    cases: zip(CENTS_IN, [
      '$0.00',
      '$0.01',
      '$0.50',
      '$0.99',
      '$1.00',
      '$1.50',
      '$123.45',
      '$999.99',
      '$1,000.00',
      '$1,234.56',
      '$5,000.00',
      '$1,234,567.89',
      '-$2.50',
      '-$1,234.56',
    ]),
  },
  formatCentsUngrouped: {
    replaces: 'formatCents(cents) in lib/admin/medchron-jobs-read.ts',
    cases: zip(CENTS_IN, [
      '$0.00',
      '$0.01',
      '$0.50',
      '$0.99',
      '$1.00',
      '$1.50',
      '$123.45',
      '$999.99',
      '$1000.00',
      '$1234.56',
      '$5000.00',
      '$1234567.89',
      '$-2.50',
      '$-1234.56',
    ]),
  },
  formatCentsAsWholeDollars: {
    replaces: 'formatWholeDollars(cents) in lib/portal/billing.ts',
    cases: zip(CENTS_IN, [
      '$0',
      '$0',
      '$1',
      '$1',
      '$1',
      '$2',
      '$123',
      '$1,000',
      '$1,000',
      '$1,235',
      '$5,000',
      '$1,234,568',
      '-$2',
      '-$1,235',
    ]),
  },
  formatDollars: {
    replaces:
      'formatCurrency (lib/admin/quote-builder-page.ts, api/admin/quotes/[id].ts) and formatDollars (lib/admin/quote-builder-client.ts), all in dollars',
    cases: zip(DOLLARS_IN, [
      '$0',
      '$1',
      '$1,234',
      '$10,500',
      '$1',
      '$2',
      '$3',
      '$10',
      '$1,000',
      '$1,235',
      '$1,235',
      '$1,234,568',
      '$-5',
      '$-1,234',
      '$-3',
      '$-1,235',
    ]),
  },
  formatDollarsHalfUp: {
    replaces: 'formatMoney(dollars) in lib/admin/client-hub.ts',
    cases: zip(DOLLARS_IN, [
      '$0',
      '$1',
      '$1,234',
      '$10,500',
      '$1',
      '$2',
      '$3',
      '$10',
      '$1,000',
      '$1,235',
      '$1,235',
      '$1,234,568',
      '$-5',
      '$-1,234',
      '$-2',
      '$-1,234',
    ]),
  },
  formatDollarsForContextNote: {
    replaces: 'formatAmount(dollars) in lib/stripe/milestone-invoicing.ts',
    cases: zip(
      DOLLARS_IN,
      [
        '0.00',
        '1.00',
        '1234.00',
        '10500.00',
        '1.40',
        '1.50',
        '2.50',
        '10.49',
        '999.99',
        '1234.50',
        '1234.60',
        '1234567.89',
        '-5.00',
        '-1234.40',
        '-2.50',
        '-1234.50',
      ].map((s) => `$\u200B${s}`)
    ),
  },
}

describe('money formatters: one home, unit in the name', () => {
  for (const [fn, { replaces, cases }] of Object.entries(KNOWN) as Array<[Fn, Known]>) {
    it(`${fn} renders what ${replaces} rendered`, () => {
      for (const [input, expected] of cases) expect(money[fn](input), String(input)).toBe(expected)
    })
  }

  it('every exported formatter is held to known answers', () => {
    expect(Object.keys(money).sort()).toEqual(Object.keys(KNOWN).sort())
  })

  it('the unit is in the name: 1500 as dollars and as cents differ by a factor of 100', () => {
    expect(money.formatDollars(1500)).toBe('$1,500')
    expect(money.formatCents(1500)).toBe('$15.00')
  })
})
