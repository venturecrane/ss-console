/**
 * The seat roster block in docs/handbook/clients-and-commitments.md.
 *
 * The handbook assessment (2026-10-21) found that the facts which drift are the
 * ones hand-copied from the tree. Which seats exist is such a fact, so it is
 * rendered from operator/customers/<slug>/customer.yaml between two markers, and
 * tests/handbook-integrity.test.ts fails when the committed block and the tree
 * disagree (the connector-inventory model, scripts/lib/handbook-connector-inventory.ts).
 *
 * Which directories count is the fleet tooling's rule, `live_seats()` in
 * operator/bin/lib/fleet/rings.py: a customer.yaml present, not a `_` template,
 * not `.decommissioned.`, and a valid slug. The integrity test runs the Python
 * function and asserts the same set, so the two cannot quietly diverge.
 *
 * The block carries only what a seat IS (kind, product, role, vertical). Who
 * pays and on what terms is commercial detail and lives in the private
 * engagements repository, never here.
 */

import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { parse as parseYaml } from 'yaml'

export const BEGIN =
  '<!-- BEGIN GENERATED: seat roster. Regenerate with `npm run handbook:roster -- --write`; tests/handbook-integrity.test.ts fails while it is stale. -->'
export const END = '<!-- END GENERATED: seat roster -->'

/** Mirrors SLUG_RE in operator/bin/lib/fleet/ids.py. */
const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$/

/** Display order: the clients first, then SMD's own seats. */
const KIND_ORDER = ['customer', 'proving', 'preprod', 'internal', 'sandbox'] as const
const KIND_LABELS: Record<string, string> = {
  customer: 'Client seats',
  proving: "SMD's own: proving",
  preprod: "SMD's own: pre-production",
  internal: "SMD's own: internal",
  sandbox: "SMD's own: sandbox",
}

export interface SeatEntry {
  slug: string
  kind: string
  product: string
  role: string | null
  vertical: string
}

/** The seat directories, by the same rule as rings.py live_seats(). */
export function liveSeatSlugs(customersRoot: string): string[] {
  return readdirSync(customersRoot, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
    .filter(
      (name) =>
        !name.startsWith('_') &&
        !name.includes('.decommissioned.') &&
        existsSync(join(customersRoot, name, 'customer.yaml')) &&
        SLUG_RE.test(name)
    )
    .sort()
}

export function buildSeatRoster(customersRoot: string): SeatEntry[] {
  return liveSeatSlugs(customersRoot).map((slug) => {
    const doc = (parseYaml(readFileSync(join(customersRoot, slug, 'customer.yaml'), 'utf8')) ??
      {}) as Record<string, unknown>
    const seat = (doc.seat ?? {}) as Record<string, unknown>
    const str = (v: unknown) => (typeof v === 'string' && v.trim() ? v.trim() : null)
    return {
      slug,
      kind: str(seat.kind) ?? 'unset',
      product: str(seat.product) ?? 'unset',
      role: str(seat.role),
      vertical: str(doc.vertical) ?? 'unset',
    }
  })
}

export function renderSeatRoster(entries: SeatEntry[]): string {
  const lines = [
    BEGIN,
    '',
    'Seats declared in the repository (`operator/customers/`). Whether each one is running right now is on the Operator fleet page in the admin console (`/admin/operator`), not here.',
    '',
  ]
  const kinds = [
    ...KIND_ORDER.filter((k) => entries.some((e) => e.kind === k)),
    ...[...new Set(entries.map((e) => e.kind))].filter(
      (k) => !(KIND_ORDER as readonly string[]).includes(k)
    ),
  ]
  for (const kind of kinds) {
    lines.push(`**${KIND_LABELS[kind] ?? `Kind \`${kind}\``}**`, '')
    lines.push('| Seat | Product | Role | Vertical |', '|---|---|---|---|')
    for (const e of entries.filter((x) => x.kind === kind))
      lines.push(`| \`${e.slug}\` | ${e.product} | ${e.role ?? 'none'} | ${e.vertical} |`)
    lines.push('')
  }
  lines.push(END)
  return lines.join('\n')
}

/** The block as committed in the page, markers included, or null when absent. */
export function committedBlock(page: string): string | null {
  const start = page.indexOf(BEGIN)
  const end = page.indexOf(END)
  if (start === -1 || end === -1 || end < start) return null
  return page.slice(start, end + END.length)
}

/** The page with its block replaced by `rendered`; throws when the markers are missing. */
export function withBlock(page: string, rendered: string): string {
  const current = committedBlock(page)
  if (current === null) throw new Error('clients-and-commitments.md has no seat-roster markers')
  return page.replace(current, () => rendered)
}
