/**
 * spec_unverified: the seat cannot verify the signed spec it should be running
 * (migrations 0139/0140; ss-console Wave 2 W4, shadow mode).
 *
 * THE SIGNAL. The deploy job's publisher compiles every seat's config into one
 * signed spec and writes vaults/<slug>/spec-pointer.json naming it. The seat's
 * root config_applier verifies the pointer's signature against the key baked
 * into its image and the spec's requirements against that image, and ships the
 * verdict on its heartbeat (`spec`, stored in spec_json).
 *
 * THE DECISION, a tri-state like selftest_failing:
 *   spec_json NULL, or a stale heartbeat
 *                       push nothing (hold).
 *   a failure verdict   ACTIVE: bad_signature, bad_digest, unknown_key,
 *                       wrong_seat, spec_missing, malformed,
 *                       unmet_requirement, and `stale`/`unreadable` (the
 *                       verifier stopped reporting; silence is not health).
 *   otherwise           compare with the published pointer read from R2:
 *     pointer unreadable                      hold.
 *     no pointer, seat reports none           inactive (nothing published yet).
 *     seat's spec_sha256 == pointer's         inactive (verified and current).
 *     differs, pointer written < grace ago    hold: the seat polls every 15 s
 *                                             and re-verifies within 5 min.
 *     differs past the grace                  ACTIVE: the seat holds an older
 *                                             spec than the one published.
 *   `fetch_error` (R2 unreachable from the seat) is judged by the same pointer
 *   comparison on the last spec it verified: an outage that outlives the grace
 *   pages, a blip does not.
 *
 * Fail-soft: an R2 read error holds every seat's comparison for the tick, and
 * nothing here can throw into runOnce.
 */

import type { ConditionState, Env } from './index'
import type { FleetStatusRow } from './fleet-status'

export const SPEC_UNVERIFIED_CONDITION = 'spec_unverified'

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$/
const SHA256_RE = /^[0-9a-f]{64}$/

const FAILURES = new Set([
  'bad_signature',
  'bad_digest',
  'unknown_key',
  'wrong_seat',
  'spec_missing',
  'malformed',
  'unmet_requirement',
  'stale',
  'unreadable',
])
const OK_VERDICTS = new Set(['verified', 'absent', 'fetch_error'])

export interface SpecVerdict {
  verdict: string
  checked_at: string
  spec_sha256: string | null
  unmet: string[]
  unmet_count: number
}

/** A published pointer: the spec it names and R2's write time. null = none. undefined = not read. */
export type PublishedPointer = { specSha256: string | null; uploadedMs: number } | null | undefined

/** Parse a row's spec_json defensively: one corrupt row is a hold, never a throw. */
export function parseSpecVerdict(json: string | null | undefined): SpecVerdict | null {
  if (json === null || json === undefined) return null
  let raw: unknown
  try {
    raw = JSON.parse(json)
  } catch {
    return null
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
  const r = raw as Record<string, unknown>
  const verdict = r.verdict
  if (typeof verdict !== 'string' || !(FAILURES.has(verdict) || OK_VERDICTS.has(verdict))) {
    return null
  }
  const unmet = Array.isArray(r.unmet)
    ? r.unmet.filter((u): u is string => typeof u === 'string')
    : []
  const count = typeof r.unmet_count === 'number' ? r.unmet_count : unmet.length
  return {
    verdict,
    checked_at: typeof r.checked_at === 'string' ? r.checked_at : 'an unknown time',
    spec_sha256:
      typeof r.spec_sha256 === 'string' && SHA256_RE.test(r.spec_sha256) ? r.spec_sha256 : null,
    unmet,
    unmet_count: count,
  }
}

const WHY: Record<string, string> = {
  bad_signature: 'its signature does not verify against the key baked into the image',
  bad_digest: 'the spec bytes do not hash to the digest the pointer names',
  unknown_key: "the pointer is signed by a key this seat's image does not carry",
  wrong_seat: 'the spec the pointer names is for another seat',
  spec_missing: 'the pointer names a spec object that does not exist',
  malformed: 'the pointer or the spec is not a well-formed seat spec',
  unmet_requirement: "the seat's image does not meet what the spec requires",
  stale: "the seat's spec verifier has stopped reporting",
  unreadable: "the seat's spec verdict could not be read",
}

function failureDetail(slug: string, v: SpecVerdict): string {
  const shown = v.unmet.slice(0, 8).join(', ')
  const more = v.unmet_count > 8 ? ` and ${v.unmet_count - 8} more` : ''
  const unmet = v.verdict === 'unmet_requirement' ? ` Unmet: ${shown}${more}.` : ''
  return (
    `the seat cannot verify its spec (verdict ${v.verdict} at ${v.checked_at}): ${WHY[v.verdict]}.${unmet} ` +
    `Spec: ${v.spec_sha256 ?? 'none'}. The seat applies customer.yaml as before (shadow mode); ` +
    `the verifier's own state is on the seat: fly ssh console -a hermes-${slug} -C "cat /run/smd-spec/state.json". ` +
    'Auto-resolves when the seat verifies the published spec.'
  )
}

function stateOf(slug: string, active: boolean, detail: string): ConditionState {
  return {
    customer_slug: slug,
    condition: SPEC_UNVERIFIED_CONDITION,
    active,
    detail,
  }
}

function compare(
  slug: string,
  v: SpecVerdict,
  pointer: PublishedPointer,
  o: SpecOptions
): ConditionState[] {
  if (pointer === undefined) return []
  if (pointer === null) {
    return v.spec_sha256 === null
      ? [stateOf(slug, false, 'no seat spec is published for this seat yet')]
      : [stateOf(slug, true, `the seat holds spec ${v.spec_sha256} but no pointer is published`)]
  }
  if (pointer.specSha256 !== null && pointer.specSha256 === v.spec_sha256) {
    return [stateOf(slug, false, `the seat verified the published spec ${v.spec_sha256}`)]
  }
  if (o.nowMs - pointer.uploadedMs < o.graceSeconds * 1000) return []
  const at = new Date(pointer.uploadedMs).toISOString().replace(/\.\d{3}Z$/, 'Z')
  return [
    stateOf(
      slug,
      true,
      `vaults/${slug}/spec-pointer.json (written ${at}) names ${pointer.specSha256 ?? 'an unreadable digest'}, ` +
        `but the seat reports ${v.spec_sha256 ?? 'no spec'} (verdict ${v.verdict}). ` +
        'The seat has not picked up the published spec; its agent.log names why.'
    ),
  ]
}

export interface SpecOptions {
  nowMs: number
  redSeconds: number
  graceSeconds: number
}

function heartbeatFresh(row: FleetStatusRow, o: SpecOptions): boolean {
  const ts = Date.parse(row.last_heartbeat_ts ?? '')
  return !Number.isNaN(ts) && o.nowMs - ts <= o.redSeconds * 1000
}

/** Pure evaluation over rows and the pointers read for them. */
export function evaluateSpec(
  rows: FleetStatusRow[],
  pointers: ReadonlyMap<string, PublishedPointer>,
  o: SpecOptions
): ConditionState[] {
  const out: ConditionState[] = []
  for (const row of rows) {
    const v = parseSpecVerdict(row.spec_json)
    if (v === null || !heartbeatFresh(row, o)) continue
    const slug = row.customer_slug
    if (FAILURES.has(v.verdict)) out.push(stateOf(slug, true, failureDetail(slug, v)))
    else out.push(...compare(slug, v, pointers.get(slug), o))
  }
  return out
}

/** The spec a pointer names, parsed not cast; null when it names none. */
export function pointerSpecSha(text: string): string | null {
  try {
    const raw: unknown = JSON.parse(text)
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
    const sha = (raw as Record<string, unknown>).spec_sha256
    return typeof sha === 'string' && SHA256_RE.test(sha) ? sha : null
  } catch {
    return null
  }
}

async function readPointer(bucket: R2Bucket, slug: string): Promise<PublishedPointer> {
  try {
    const obj = await bucket.get(`vaults/${slug}/spec-pointer.json`)
    if (obj === null) return null
    return { specSha256: pointerSpecSha(await obj.text()), uploadedMs: obj.uploaded.getTime() }
  } catch (err) {
    console.error('[fleet-alerts] spec pointer read failed:', slug, err)
    return undefined
  }
}

/** Read the pointers for the rows that carry a verdict, then evaluate. Never throws. */
export async function specConditions(
  env: Env,
  rows: FleetStatusRow[],
  o: SpecOptions
): Promise<ConditionState[]> {
  try {
    const pointers = new Map<string, PublishedPointer>()
    const bucket = env.CUSTOMER_CONFIG
    for (const row of rows) {
      if (!row.spec_json || !SLUG_RE.test(row.customer_slug)) continue
      pointers.set(
        row.customer_slug,
        bucket ? await readPointer(bucket, row.customer_slug) : undefined
      )
    }
    return evaluateSpec(rows, pointers, o)
  } catch (err) {
    console.error('[fleet-alerts] spec evaluation failed:', err)
    return []
  }
}
