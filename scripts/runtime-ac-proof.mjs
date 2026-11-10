/**
 * Runtime-AC proof: the merge gate behind Law 9.
 *
 * Why this exists (2026-07-28, the entitlement-control incident). Four PRs each
 * defined "done" as the artifact it added. Each was individually honest; one
 * even wrote "Next slices, unbuilt and not implied here." The artifacts summed
 * to less than the feature, the epic closed green, and a real client could not
 * perform the act.
 *
 * The CI chain made that outcome look certified. tick-acs-on-merge parses the
 * MERGING PR's own "Acceptance criteria status" table, ticks every row the PR
 * marked `met` on the linked issue, and unmet-ac-on-close explicitly skips
 * PR-driven closes. So the author declares its slice met, CI ticks the epic,
 * the epic closes. The system certifies the author's own definition of done.
 *
 * This gate narrows what a PR is allowed to self-certify. An AC tagged
 * `(runtime)` describes a gate on a live layer (D1, R2, the Fly volume, the
 * running Machine, monitoring, external records). Every such row is one of:
 *
 *   met       with a crane_verify ID in the Evidence column, RESOLVED against
 *             the verify ledger (since the 2026-09-25 review): the ID must
 *             exist and be an observation (`live_state` or `fresh_process`).
 *             A `vendor_docs` row is reading, not observing.
 *   deferred  naming who switches it on and by when, anywhere in the row:
 *             `owner: <who>; enforce_by: YYYY-MM-DD`. A date already passed
 *             fails, because the switch-on is then overdue.
 *   n/a       the AC does not apply to this PR.
 *
 * Anything else ("not met", "unmet", "pending", "not yet") fails. Until
 * 2026-11-09 those rows passed silently: #3852 merged with two `(runtime)` rows
 * "not met" and no owner, under a "## Acceptance" heading the gate did not read
 * (critique PREV-6).
 *
 * Where it looks. Every markdown table in the body whose header row has a
 * Status (or State) column, under any heading, with columns found by header
 * name. Until 2026-11-09 it read only a section headed exactly
 * "## Acceptance criteria status", positionally. Of the 40 merged PRs carrying
 * a `(runtime)` row from 2026-10-16 to 2026-11-09, 3 used that heading; the
 * rest wrote "## Acceptance", "## Acceptance criteria", "## Evidence",
 * "## Status" or "## Contract", or put the tag in a Layer column, and the gate
 * never saw them.
 *
 * Fail-closed. When the ledger cannot be consulted (no key on this run, the
 * worker unreachable, a response of the wrong shape) and there is a claim to
 * check, the gate is red with a message saying so. "Could not look" is never
 * "looked and found it".
 *
 * Silent on PRs that claim nothing at runtime. The check is required by the
 * main ruleset, so it runs on every PR event and must report on every PR; a
 * chore with no status table or no `(runtime)` row passes without a ledger call.
 *
 * Repo-layer ACs are untouched. A file:line is the right evidence for those,
 * and gating them would be ceremony.
 *
 * @see docs/doctrine/wired-contract.md - the contract that authors the tags
 * @see docs/doctrine/agent-operating-doctrine.md - Law 9
 */

/** A header cell naming the status column. */
const STATUS_HEADER_RE = /^(status|state)\b/i

/** A header cell naming the evidence column. */
const EVIDENCE_HEADER_RE = /\b(evidence|proof)\b/i

/**
 * Anything that looks like an attempt at an ID. Deliberately loose, so a
 * near-miss reads as a typo to fix rather than as "no ID present".
 */
const LOOSE_ID_RE = /\bvfy_[0-9A-Za-z]{4,}\b/g

/** What crane_verify actually returns: `vfy_` + a 26-char ULID (Crockford base32). */
const CANONICAL_ID_RE = /^vfy_[0-9A-HJKMNP-TV-Z]{26}$/

/**
 * Layer tag authored per the reachability contract, carried verbatim into the
 * PR table: `(runtime)` inside the AC text, or `runtime` alone in a Layer or
 * Kind column.
 */
const RUNTIME_TAG_RE = /\(\s*runtime[^)]*\)/i
const RUNTIME_CELL_RE = /^\(?\s*runtime\s*\)?$/i

/** A markdown table separator: cells of dashes and optional alignment colons. */
const SEPARATOR_CELL_RE = /^:?-{2,}:?$/

/**
 * The template's own placeholder row ships in every PR body until edited. Its
 * status cell enumerates the options rather than picking one.
 */
const TEMPLATE_STATUS_RE = /met\s*\/\s*deferred/i

/**
 * The deferral's two required parts. `enforce-by` and `enforce by` read the
 * same as `enforce_by`.
 */
const OWNER_RE = /\bowner\s*:\s*`?([^;,|`]+)/i
const ENFORCE_BY_RE = /\benforce[_\- ]by\s*:\s*`?(\d{4}-\d{2}-\d{2})\b/i
const PLACEHOLDER_OWNER_RE = /^(tbd|tba|none|nobody|unknown|n\/a|\?+|-+|_+)$/i

/** Methods that observe the running system. Reading docs does not. */
export const OBSERVATION_METHODS = ['live_state', 'fresh_process']

/** The crane-context worker that owns the verify ledger. */
export const DEFAULT_LEDGER_BASE = 'https://crane-context.automation-ab6.workers.dev'

/** The worker's per-request cap on /verify/lookup (crane-context constants/verify.ts). */
export const LOOKUP_BATCH = 50

/**
 * Split one markdown table row into trimmed cells. A `\|` inside a cell is
 * content, not a boundary.
 * @param {string} line
 * @returns {string[]}
 */
function cells(line) {
  const trimmed = line
    .trim()
    .replace(/^\|/, '')
    .replace(/(?<!\\)\|$/, '')
  return trimmed.split(/(?<!\\)\|/).map((c) => c.trim())
}

/**
 * What a status cell says. Only the first word decides, so "not met" is never
 * read as met; anything that is not met, deferred or n/a is `unmet`.
 * @param {string} status
 * @returns {'met' | 'deferred' | 'na' | 'unmet'}
 */
export function statusKind(status) {
  const s = String(status ?? '')
    .replace(/[*`_]/g, '')
    .trim()
    .toLowerCase()
  if (/^met\b/.test(s)) return 'met'
  if (/^deferred\b/.test(s)) return 'deferred'
  if (/^(n\/a|na|not applicable)\b/.test(s)) return 'na'
  return 'unmet'
}

/**
 * The markdown tables in a body. `header` is null when the table has no
 * separator line, and such a table is read positionally as AC | Status |
 * Evidence. Fenced code and HTML comments hold no tables.
 * @param {string} body
 * @returns {{ header: string[] | null, rows: string[][] }[]}
 */
function tables(body) {
  const text = String(body ?? '')
    .replace(/\r/g, '')
    .replace(/<!--[\s\S]*?-->/g, '')
  const out = []
  let block = []
  let fenced = false
  const flush = () => {
    if (block.length > 0) {
      const rows = block.map(cells)
      const hasHeader = rows.length > 1 && rows[1].every((c) => SEPARATOR_CELL_RE.test(c))
      out.push(hasHeader ? { header: rows[0], rows: rows.slice(2) } : { header: null, rows })
    }
    block = []
  }
  for (const line of text.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) {
      flush()
      fenced = !fenced
      continue
    }
    if (!fenced && line.trim().startsWith('|')) block.push(line)
    else flush()
  }
  flush()
  return out
}

/** Where the status and evidence sit in a table, or null when it is not a status table. */
function columnsOf(header) {
  if (!header) return { statusAt: 1, evidenceAt: 2 }
  const statusAt = header.findIndex((h) => STATUS_HEADER_RE.test(h.replace(/[*`]/g, '')))
  if (statusAt === -1) return null
  const evidenceAt = header.findIndex((h, i) => i !== statusAt && EVIDENCE_HEADER_RE.test(h))
  return { statusAt, evidenceAt }
}

/**
 * Every `(runtime)` row in the body's status tables, read by its header.
 *
 * `ids` holds the well-formed IDs in the evidence; `malformed` the near-misses,
 * kept so the failure can quote them back. `row` is every cell joined, which
 * is where a deferral's owner and date are looked for.
 *
 * @param {string} body - the pull request description
 * @returns {{ ac: string, status: string, evidence: string, kind: 'met' | 'deferred' | 'na' | 'unmet', row: string, ids: string[], malformed: string[] }[]}
 */
export function runtimeRows(body) {
  const found = []
  for (const table of tables(body)) {
    const cols = columnsOf(table.header)
    if (!cols) continue
    const { statusAt, evidenceAt } = cols
    for (const row of table.rows) {
      if (row.every((c) => SEPARATOR_CELL_RE.test(c) || c === '')) continue
      const status = row[statusAt] ?? ''
      if (TEMPLATE_STATUS_RE.test(status)) continue
      const rest = row.filter((_, i) => i !== statusAt && i !== evidenceAt)
      if (!rest.some((c) => RUNTIME_TAG_RE.test(c) || RUNTIME_CELL_RE.test(c))) continue

      const evidence = evidenceAt === -1 ? rest.join(' | ') : (row[evidenceAt] ?? '')
      const ids = [...new Set(evidence.match(LOOSE_ID_RE) ?? [])]
      const described = rest.filter((c) => c !== '' && !/^\d+$/.test(c) && !RUNTIME_CELL_RE.test(c))
      found.push({
        ac: described.join(' | ') || rest.join(' | '),
        status,
        evidence,
        kind: statusKind(status),
        row: row.join(' | '),
        ids: ids.filter((id) => CANONICAL_ID_RE.test(id)),
        malformed: ids.filter((id) => !CANONICAL_ID_RE.test(id)),
      })
    }
  }
  return found
}

/**
 * Every `(runtime)` AC the PR marks `met`, with the IDs its evidence cites.
 * @param {string} body
 */
export function runtimeClaims(body) {
  return runtimeRows(body).filter((r) => r.kind === 'met')
}

/**
 * Why a deferral does not count, or null when it names an owner and an
 * enforce_by date that has not passed.
 * @param {string} row - every cell of the row, joined
 * @param {Date} [now]
 * @returns {string | null}
 */
export function deferralReason(row, now = new Date()) {
  const owner = row.match(OWNER_RE)?.[1]?.trim() ?? ''
  const date = row.match(ENFORCE_BY_RE)?.[1]
  const missing = []
  if (!owner || PLACEHOLDER_OWNER_RE.test(owner)) missing.push('an `owner:`')
  if (!date) missing.push('an `enforce_by: YYYY-MM-DD`')
  if (missing.length > 0) return `deferred without ${missing.join(' or ')}`
  const parsed = new Date(`${date}T00:00:00Z`)
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) {
    return `enforce_by ${date} is not a calendar date`
  }
  if (date < now.toISOString().slice(0, 10)) {
    return `enforce_by ${date} has passed, so the switch-on is overdue: prove it, or set a new date`
  }
  return null
}

/**
 * The format stage, needing no ledger: runtime ACs marked `met` without a
 * well-formed ID, runtime ACs left neither met nor deferred, and deferrals
 * that do not name an owner and an enforce_by date.
 *
 * Deliberately silent when there is no status table at all: a chore with no
 * ACs has nothing to prove.
 *
 * @param {string} body - the pull request description
 * @param {{ now?: Date }} [opts]
 * @returns {{ ac: string, status: string, evidence: string, reason: string }[]} violations, in table order
 */
export function findUnprovenRuntimeAcs(body, opts = {}) {
  const violations = []
  for (const r of runtimeRows(body)) {
    let reason = null
    if (r.kind === 'met' && r.ids.length === 0) {
      reason =
        r.malformed.length > 0
          ? `malformed ID ${r.malformed.join(', ')} (crane_verify returns vfy_ plus 26 characters)`
          : 'marked met with no crane_verify ID'
    } else if (r.kind === 'unmet') {
      reason = `status "${r.status || '(empty)'}" is neither met nor deferred`
    } else if (r.kind === 'deferred') {
      reason = deferralReason(r.row, opts.now)
    }
    if (reason) violations.push({ ac: r.ac, status: r.status, evidence: r.evidence, reason })
  }
  return violations
}

/** Raised when the ledger cannot answer. Carries a message fit for the check annotation. */
export class LedgerUnavailable extends Error {}

/**
 * Resolve IDs against the verify ledger.
 *
 * Returns a Map of id to `{ method }` for every ID the ledger holds; an ID
 * absent from the map does not exist. Throws LedgerUnavailable on anything
 * short of a well-formed answer, so the caller cannot mistake an outage for a
 * clean result.
 *
 * @param {string[]} ids - canonical IDs
 * @param {{ relayKey?: string, apiBase?: string, fetchImpl?: typeof fetch }} opts
 * @returns {Promise<Map<string, { method: string }>>}
 */
export async function resolveVerifyIds(ids, opts = {}) {
  const found = new Map()
  if (ids.length === 0) return found
  if (!opts.relayKey) {
    throw new LedgerUnavailable(
      'CRANE_RELAY_KEY is not available to this run, so the verify ledger cannot be consulted. ' +
        'Pull requests from forks do not receive repository secrets; push the branch to ' +
        'venturecrane/ss-console instead.'
    )
  }
  const base = (opts.apiBase || DEFAULT_LEDGER_BASE).replace(/\/$/, '')
  const fetchImpl = opts.fetchImpl ?? fetch

  for (let i = 0; i < ids.length; i += LOOKUP_BATCH) {
    const batch = ids.slice(i, i + LOOKUP_BATCH)
    const answer = await lookupBatch(fetchImpl, base, opts.relayKey, batch)
    for (const id of batch) {
      if (answer.exists[id] !== true) continue
      const method = answer.records?.[id]?.method
      if (typeof method !== 'string') {
        throw new LedgerUnavailable(
          `the ledger confirmed ${id} exists but returned no method for it; cannot tell an observation from a doc read`
        )
      }
      found.set(id, { method })
    }
  }
  return found
}

/**
 * One /verify/lookup call, validated.
 * @returns {Promise<{ exists: Record<string, boolean>, records?: Record<string, { method?: unknown }> }>}
 */
async function lookupBatch(fetchImpl, base, relayKey, batch) {
  const url = `${base}/verify/lookup?ids=${batch.map(encodeURIComponent).join(',')}`
  let res
  try {
    res = await fetchImpl(url, { headers: { 'X-Relay-Key': relayKey } })
  } catch (err) {
    throw new LedgerUnavailable(
      `the verify ledger is unreachable (${err instanceof Error ? err.message : String(err)})`,
      { cause: err }
    )
  }
  if (!res.ok) {
    throw new LedgerUnavailable(`the verify ledger answered HTTP ${res.status} to /verify/lookup`)
  }
  let parsed
  try {
    parsed = await res.json()
  } catch (err) {
    throw new LedgerUnavailable('the verify ledger answered with a body that is not JSON', {
      cause: err,
    })
  }
  if (
    !parsed ||
    typeof parsed !== 'object' ||
    typeof parsed.exists !== 'object' ||
    !parsed.exists
  ) {
    throw new LedgerUnavailable('the verify ledger answered without an `exists` map')
  }
  return parsed
}

/**
 * The whole gate: format first, then the ledger.
 *
 * A met row passes when at least one of its IDs exists and is an observation.
 * Rows that fail the format stage never reach the ledger, and a PR whose rows
 * all fail format needs no key to fail.
 *
 * @param {string} body
 * @param {{ relayKey?: string, apiBase?: string, fetchImpl?: typeof fetch, now?: Date }} opts
 * @returns {Promise<{ violations: { ac: string, evidence: string, reason: string }[], ledgerError: string | null, checked: number }>}
 */
export async function checkRuntimeAcProof(body, opts = {}) {
  const claims = runtimeClaims(body)
  const violations = findUnprovenRuntimeAcs(body, opts)
  const withIds = claims.filter((c) => c.ids.length > 0)
  const ids = [...new Set(withIds.flatMap((c) => c.ids))]

  let ledger
  try {
    ledger = await resolveVerifyIds(ids, opts)
  } catch (err) {
    if (!(err instanceof LedgerUnavailable)) throw err
    return { violations, ledgerError: err.message, checked: ids.length }
  }

  for (const claim of withIds) {
    const reason = ledgerReason(claim.ids, ledger)
    if (reason)
      violations.push({ ac: claim.ac, status: claim.status, evidence: claim.evidence, reason })
  }
  return { violations, ledgerError: null, checked: ids.length }
}

/** Why a claim's IDs do not prove it, or null when one of them does. */
function ledgerReason(ids, ledger) {
  if (ids.some((id) => OBSERVATION_METHODS.includes(ledger.get(id)?.method))) return null
  const missing = ids.filter((id) => !ledger.has(id))
  if (missing.length === ids.length) return `not in the verify ledger: ${missing.join(', ')}`
  const methods = ids
    .filter((id) => ledger.has(id))
    .map((id) => `${id} is ${ledger.get(id).method}`)
  return `no observation among the cited IDs (${methods.join('; ')}); a runtime AC needs live_state or fresh_process`
}

/**
 * Human-readable failure text for the workflow annotation.
 * @param {{ ac: string, evidence: string, reason?: string }[]} violations
 * @returns {string}
 */
export function formatViolations(violations) {
  const rows = violations
    .map(
      (v) =>
        `  - ${v.ac}\n      evidence given: ${v.evidence || '(empty)'}` +
        (v.reason ? `\n      why it does not pass: ${v.reason}` : '')
    )
    .join('\n')
  return [
    `${violations.length} runtime AC(s) neither proven met nor deferred with an owner and a date:`,
    rows,
    '',
    'A (runtime) AC is a gate on a live layer: D1, R2, the Fly volume, the running',
    'Machine, monitoring, or an external record. Each one is, in its Status column:',
    '',
    '  met       with a crane_verify ID (method live_state or fresh_process) in the',
    '            Evidence column, from an observation of the real deployment;',
    '  deferred  with `owner: <who switches it on>; enforce_by: YYYY-MM-DD` in the row;',
    '  n/a       when the AC does not apply to this PR.',
    '',
    '"not met", "unmet", "pending" and "not yet" are not states this gate accepts.',
    'See docs/doctrine/wired-contract.md.',
  ].join('\n')
}
