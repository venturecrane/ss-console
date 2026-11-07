/**
 * The `firm_inputs` heartbeat field (migration 0149): which version of each
 * lane's firm inputs a seat runs, as the overlay's root firm-inputs applier
 * records it (overlay firm_inputs_applier, shared/fleet_facts.py
 * read_firm_inputs). Its own module because heartbeat-parsers.ts sits at the
 * file-size ceiling.
 *
 * The lanes that have firm inputs, the version label (the first 8 hex of the
 * marker object's ETag), and the two verdicts the seat reports: fetched (the
 * live copy is the vault's) and rejected (the vault holds a version the
 * validator refused; the live copy stands). The validator's own words never
 * leave the seat, so an entry carrying any other key is refused.
 */

import { isRecord } from '../api/helpers'

const FIRM_INPUT_LANES = new Set([
  'demand',
  'drafting',
  'litigation',
  'casefile',
  'records',
  'medchron',
  // The firm's attorney roster: a firm input every document path reads, not a
  // job lane (operator_connector_sdk/library/roster.py).
  'attorneys',
])
const FIRM_INPUT_VERDICTS = new Set(['fetched', 'rejected'])
const ENTRY_KEYS = new Set(['etag8', 'verdict', 'applied_at', 'rejected_etag8'])
const ETAG8_RE = /^[0-9a-f]{8}$/
const ISO_Z_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/

type Entry = Record<string, string | null>

/** A string matching `re`, or null; `undefined` when it is neither (refuse). */
function nullableMatch(value: unknown, re: RegExp): string | null | undefined {
  if (value === null) return null
  return typeof value === 'string' && re.test(value) ? value : undefined
}

function parseEntry(raw: unknown): Entry | null {
  if (!isRecord(raw)) return null
  if (Object.keys(raw).some((k) => !ENTRY_KEYS.has(k))) return null
  const verdict = raw.verdict
  if (typeof verdict !== 'string' || !FIRM_INPUT_VERDICTS.has(verdict)) return null
  const etag8 = nullableMatch(raw.etag8, ETAG8_RE)
  const appliedAt = nullableMatch(raw.applied_at, ISO_Z_RE)
  if (etag8 === undefined || appliedAt === undefined) return null
  const row: Entry = { etag8, verdict, applied_at: appliedAt }
  if (raw.rejected_etag8 === undefined) return row
  const rejected = nullableMatch(raw.rejected_etag8, ETAG8_RE)
  if (verdict !== 'rejected' || typeof rejected !== 'string') return null
  row.rejected_etag8 = rejected
  return row
}

/**
 * `{"records": {"etag8": "1a2b3c4d", "verdict": "fetched", "applied_at": "<ISO>"}}`,
 * with `rejected_etag8` on a `rejected` lane, as canonical JSON text (keys
 * sorted), or NULL. Validated whole, like `bundles`: one bad entry stores NULL
 * (unknown) rather than a partial map `fleet status` would misreport. etag8
 * and applied_at may be null (a lane with no live copy yet).
 */
export function parseFirmInputs(value: unknown): string | null {
  if (!isRecord(value)) return null
  const entries = Object.entries(value)
  if (entries.length > FIRM_INPUT_LANES.size) return null
  const out: Record<string, Entry> = {}
  for (const [lane, raw] of entries.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) {
    if (!FIRM_INPUT_LANES.has(lane)) return null
    const row = parseEntry(raw)
    if (row === null) return null
    out[lane] = row
  }
  return JSON.stringify(out)
}
