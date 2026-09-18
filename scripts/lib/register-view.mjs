/**
 * How the obligation register is rendered for a reader.
 *
 * Split out of `.claude/hooks/lib/register.mjs` so the view logic lives in the
 * LINTED tree: eslint.config.js ignores everything under `.claude/`, so nothing
 * there is checked against the 500-line ceiling or anything else. Keeping the
 * capture CLI's rendering here means it gets the same treatment as product code.
 *
 * Two renderings, and the difference between them is a confidentiality boundary
 * rather than a formatting preference:
 *
 *   renderTable  a human at a private terminal. Carries `what`.
 *   projectRow   machine consumption, today the /sos `[owed]` line. Does NOT
 *                carry `what`, and `JSON_FIELDS` pins that by test.
 *
 * The reason is that /sos output lands in a session transcript; transcripts
 * persist under ~/.claude and are sent to model providers, and ss-console is a
 * public repo. Reading the obligation text is a deliberate act at a terminal,
 * never something that rides along in a status line.
 */

import { clientOf } from './seat-clients.mjs'
import { ageInDays } from './sqlite-time.mjs'

/** Whole days since a SQLite (UTC, space-separated) timestamp. */
const ageDays = ageInDays

/**
 * The fields `--json` is allowed to emit.
 *
 * `what` is deliberately absent, and this is a confidentiality boundary rather
 * than a tidiness preference. `--json` feeds /sos, whose output lands in a
 * session transcript; transcripts persist under ~/.claude and are sent to model
 * providers, and this repo is public besides. Counts and identifiers are enough
 * to say what is owed and to whom; reading the text is a deliberate act at a
 * private terminal (`register list`, the table form below).
 *
 * tests/register-cli.test.ts pins this list. Adding `what` breaks it on purpose.
 */
export const JSON_FIELDS = [
  'obligation_id',
  'client',
  'customer_slug',
  'kind',
  'stable_key',
  'state',
  'due_at',
  'age_days',
]

/** Shape a raw row for machine consumption, dropping anything confidential. */
export function projectRow(row, now = Date.now()) {
  return {
    obligation_id: row.obligation_id,
    client: clientOf(row.customer_slug),
    customer_slug: row.customer_slug,
    kind: row.kind,
    stable_key: row.stable_key,
    state: row.state,
    due_at: row.due_at ?? null,
    age_days: ageDays(row.created_at, now),
  }
}

/** Render the human table, grouped by client. Pure so it can be tested. */
export function renderTable(rows, now = Date.now()) {
  if (rows.length === 0) {
    // NEVER an empty table. A broken selector and a clean register look
    // identical in a zero-row table, and the register exists because that
    // confusion let 7 of 16 cadence items go overdue behind a green report.
    return 'register: nothing open.'
  }
  const byClient = new Map()
  for (const row of rows) {
    const client = clientOf(row.customer_slug)
    if (!byClient.has(client)) byClient.set(client, [])
    byClient.get(client).push(row)
  }
  const out = []
  for (const client of [...byClient.keys()].sort()) {
    const group = byClient.get(client)
    out.push(`${client}  (${group.length})`)
    const widest = Math.max(...group.map((r) => String(r.stable_key ?? '').length), 3)
    for (const row of group) {
      const age = ageDays(row.created_at, now)
      out.push(
        [
          '  ',
          String(row.stable_key ?? '').padEnd(widest),
          String(row.kind ?? '').padEnd(20),
          String(row.state ?? '').padEnd(18),
          (row.due_at ? `due ${row.due_at}` : 'no due date').padEnd(16),
          age === null ? '' : `${age}d old`,
        ].join(' ')
      )
      out.push(`     ${row.what ?? ''}`)
    }
    out.push('')
  }
  const dated = rows.filter((r) => r.due_at).length
  out.push(
    `${rows.length} open across ${byClient.size} client${byClient.size === 1 ? '' : 's'}; ${dated} dated.`
  )
  if (dated === 0) {
    // Only a dated row can go overdue, so an all-undated register cannot alarm
    // through that path at all. The reconciler's stale ladder is what covers
    // it; saying so here keeps the gap visible to whoever is reading.
    out.push(
      'No row carries a due date, so none can go overdue. Undated rows alarm at 30d instead.'
    )
  }
  return out.join('\n')
}
