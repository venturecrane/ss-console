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
 * running Machine, monitoring, external records). Marking one `met` requires a
 * crane_verify ID in the Evidence column, and since the 2026-09-25 review that
 * ID is RESOLVED against the verify ledger rather than pattern-matched: a
 * string shaped like an ID proves nothing, and a regex was all this checked
 * before. A resolved ID must exist and must be an observation (`live_state` or
 * `fresh_process`). A `vendor_docs` row is reading, not observing.
 *
 * Fail-closed. When the ledger cannot be consulted (no key on this run, the
 * worker unreachable, a response of the wrong shape) and there is a claim to
 * check, the gate is red with a message saying so. "Could not look" is never
 * "looked and found it".
 *
 * Silent on PRs that claim nothing at runtime. The check is required by the
 * main ruleset, so it runs on every PR event and must report on every PR; a
 * chore with no AC table or no `(runtime)` row passes without a ledger call.
 *
 * Repo-layer ACs are untouched. A file:line is the right evidence for those,
 * and gating them would be ceremony.
 *
 * @see docs/doctrine/wired-contract.md - the contract that authors the tags
 * @see docs/doctrine/agent-operating-doctrine.md - Law 9
 */

/** Section whose table the AC-tick workflow already parses. Same anchor. */
const SECTION_RE = /^##\s+Acceptance criteria status\s*$/im

/**
 * Anything that looks like an attempt at an ID. Deliberately loose, so a
 * near-miss reads as a typo to fix rather than as "no ID present".
 */
const LOOSE_ID_RE = /\bvfy_[0-9A-Za-z]{4,}\b/g

/** What crane_verify actually returns: `vfy_` + a 26-char ULID (Crockford base32). */
const CANONICAL_ID_RE = /^vfy_[0-9A-HJKMNP-TV-Z]{26}$/

/** Layer tag authored per the reachability contract into the issue's ACs, carried verbatim into the PR table. */
const RUNTIME_TAG_RE = /\(\s*runtime[^)]*\)/i

/** A markdown table separator: cells of dashes and optional alignment colons. */
const SEPARATOR_CELL_RE = /^:?-{2,}:?$/

/**
 * The template's own placeholder row ships in every PR body until edited. Its
 * status cell enumerates the options rather than picking one, which is how it
 * is told apart from a real row.
 */
const TEMPLATE_STATUS_RE = /\//

/** Methods that observe the running system. Reading docs does not. */
export const OBSERVATION_METHODS = ['live_state', 'fresh_process']

/** The crane-context worker that owns the verify ledger. */
export const DEFAULT_LEDGER_BASE = 'https://crane-context.automation-ab6.workers.dev'

/** The worker's per-request cap on /verify/lookup (crane-context constants/verify.ts). */
export const LOOKUP_BATCH = 50

/**
 * Split one markdown table row into trimmed cells.
 * @param {string} line
 * @returns {string[]}
 */
function cells(line) {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  return trimmed.split('|').map((c) => c.trim())
}

/**
 * Extract the "Acceptance criteria status" section body from a PR description.
 * Returns '' when the section is absent.
 * @param {string} body
 * @returns {string}
 */
export function extractAcSection(body) {
  const lines = String(body ?? '').split('\n')
  const start = lines.findIndex((l) => SECTION_RE.test(l))
  if (start === -1) return ''
  const rest = lines.slice(start + 1)
  const end = rest.findIndex((l) => /^##\s+/.test(l))
  return (end === -1 ? rest : rest.slice(0, end)).join('\n')
}

/**
 * Is this table row a claim, as opposed to a header, separator, or the
 * template's untouched placeholder?
 * @param {string[]} cols
 */
function isClaimRow(cols) {
  const [ac, status] = cols
  if (cols.every((c) => SEPARATOR_CELL_RE.test(c) || c === '')) return false
  if (/^ac\b/i.test(ac) && /status/i.test(status)) return false
  return !TEMPLATE_STATUS_RE.test(status)
}

/**
 * Every `(runtime)` AC the PR marks `met`, with the IDs its evidence cites.
 *
 * `ids` holds the well-formed IDs; `malformed` the near-misses, kept so the
 * failure can quote them back.
 *
 * @param {string} body - the pull request description
 * @returns {{ ac: string, status: string, evidence: string, ids: string[], malformed: string[] }[]}
 */
export function runtimeClaims(body) {
  const section = extractAcSection(body)
  if (!section) return []

  const claims = []
  for (const line of section.split('\n')) {
    if (!line.trim().startsWith('|')) continue
    const cols = cells(line)
    if (cols.length < 3 || !isClaimRow(cols)) continue

    const [ac, status, evidence] = cols
    if (status.toLowerCase() !== 'met') continue
    if (!RUNTIME_TAG_RE.test(ac)) continue

    const found = [...new Set(evidence.match(LOOSE_ID_RE) ?? [])]
    claims.push({
      ac,
      status,
      evidence,
      ids: found.filter((id) => CANONICAL_ID_RE.test(id)),
      malformed: found.filter((id) => !CANONICAL_ID_RE.test(id)),
    })
  }
  return claims
}

/**
 * The format stage: runtime ACs marked `met` without a well-formed ID.
 *
 * Deliberately silent when there is no AC table at all: the PR template and
 * review own that, and duplicating the check here would fail chore PRs that
 * legitimately carry no ACs.
 *
 * @param {string} body - the pull request description
 * @returns {{ ac: string, status: string, evidence: string, reason: string }[]} violations, in table order
 */
export function findUnprovenRuntimeAcs(body) {
  return runtimeClaims(body)
    .filter((c) => c.ids.length === 0)
    .map(({ ac, status, evidence, malformed }) => ({
      ac,
      status,
      evidence,
      reason:
        malformed.length > 0
          ? `malformed ID ${malformed.join(', ')} (crane_verify returns vfy_ plus 26 characters)`
          : 'no crane_verify ID',
    }))
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
 * A row passes when at least one of its IDs exists and is an observation.
 * Rows that fail the format stage never reach the ledger, and a PR whose rows
 * all fail format needs no key to fail.
 *
 * @param {string} body
 * @param {{ relayKey?: string, apiBase?: string, fetchImpl?: typeof fetch }} opts
 * @returns {Promise<{ violations: { ac: string, evidence: string, reason: string }[], ledgerError: string | null, checked: number }>}
 */
export async function checkRuntimeAcProof(body, opts = {}) {
  const claims = runtimeClaims(body)
  const violations = findUnprovenRuntimeAcs(body)
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
        (v.reason ? `\n      why it does not prove the AC: ${v.reason}` : '')
    )
    .join('\n')
  return [
    `${violations.length} runtime AC(s) marked "met" without a resolvable crane_verify observation:`,
    rows,
    '',
    'A (runtime) AC is a gate on a live layer: D1, R2, the Fly volume, the running',
    'Machine, monitoring, or an external record. Marking it met means someone',
    'performed the act on the real deployment and observed the far end change.',
    '',
    'Record that observation with crane_verify (method live_state or fresh_process)',
    'and put the returned vfy_... ID in the Evidence column. If the gate is not',
    'actually closed, mark the AC deferred and say which gate is open, per',
    'docs/doctrine/wired-contract.md.',
  ].join('\n')
}
