/**
 * Relationship-surface view-model for the admin Operator console
 * (`/admin/operator/[customer]/memory`) — ADR 0048.
 *
 * Loads two lanes of the working relationship, both live across the isolation
 * boundary via the frozen runtime-read seam (ADR 0043 path A):
 *   • the authored **standing-preferences lane** — the per-person working
 *     preferences from the `customer.yaml` `relationship:` block, read via
 *     `config_export?section=relationship`. The authored *seed* of the
 *     per-person working relationship.
 *   • the **learned-preferences lane** — per-peer working-preference memory the
 *     operator captured from how each person works with it (stated or
 *     demonstrated), read via `memory_export?table=peer_preferences` from
 *     Hermes' native memory loop. Only active rows surface (a superseded row was
 *     replaced).
 * The inferred lane (Honcho) is rejected doctrine (2026-06-16) and not read.
 *
 * Same fail-closed discipline as runtime-observe: when the read path is not
 * configured we return `not_enabled` WITHOUT a read (no audit noise on a dark
 * feature); otherwise we read and classify honestly (unreachable / empty /
 * items). This module owns the standing-preferences read and a **defensive
 * parse** of the opaque row payload — never a cast (coding-standards: parse
 * external inputs).
 */

import {
  readMachineRuntime,
  type MachineRuntimeTransport,
  type RuntimeReadAudit,
  type RuntimeReadActor,
} from '../operator/runtime-read'

interface RuntimeReadDeps {
  transport: MachineRuntimeTransport
  audit: RuntimeReadAudit
}

/** One authored person on the standing-preferences (authored behavioral) lane —
 * a `relationship.people[]` entry served by `config_export?section=relationship`
 * (ADR 0048). */
export interface StandingPreferencePerson {
  id: string
  name: string
  role: string | null
  prefers: string[]
  avoid: string[]
}

export type StandingPreferencesResult =
  | { status: 'not_enabled' }
  | { status: 'unreachable'; reason: string }
  | { status: 'empty' }
  | { status: 'items'; people: StandingPreferencePerson[] }

/**
 * Load the standing-preferences lane (authored `relationship:` block) for one
 * customer. Fail-closed: no read (and no audit row) when the seam is
 * unconfigured; otherwise reads `config_export?section=relationship`
 * via the fail-closed seam and classifies the outcome. The Machine serves the
 * live `customer.yaml` block normalized to the closed-set fields, so this is the
 * truth the running Operator works from — not a possibly-stale console copy.
 */
export async function loadStandingPreferences(
  deps: RuntimeReadDeps,
  customerSlug: string,
  actor: RuntimeReadActor,
  configured: boolean
): Promise<StandingPreferencesResult> {
  if (!configured) return { status: 'not_enabled' }
  const result = await readMachineRuntime(
    deps,
    customerSlug,
    { kind: 'config_export', section: 'relationship' },
    actor
  )
  if (!result.ok) return { status: 'unreachable', reason: result.reason }
  const people = parseStandingPreferences(result.data)
  return people.length === 0 ? { status: 'empty' } : { status: 'items', people }
}

/**
 * Parse the opaque `config_export` payload (`{ entries: [...] }`) into authored
 * people. Defensive by construction: id and name are required (a row missing
 * either is skipped rather than half-rendered); role/prefers/avoid normalize to
 * `null`/`[]`; non-string list items are dropped. (The Machine already
 * normalizes to the closed set, but the console never trusts the wire shape —
 * coding-standards: parse external inputs, never cast.)
 */
export function parseStandingPreferences(data: unknown): StandingPreferencePerson[] {
  const out: StandingPreferencePerson[] = []
  for (const entry of extractEntries(data)) {
    if (!entry || typeof entry !== 'object') continue
    const row = entry as Record<string, unknown>
    const id = asNonEmptyString(row.id)
    const name = asNonEmptyString(row.name)
    if (id === null || name === null) continue
    out.push({
      id,
      name,
      role: asStringOrNull(row.role),
      prefers: asStringList(row.prefers),
      avoid: asStringList(row.avoid),
    })
  }
  return out
}

/** One active learned preference on the learned lane — a `peer_preferences`
 * row the operator captured from how a person works with it, served by
 * `memory_export?table=peer_preferences` (the relationship model's learned
 * lane on Hermes' native memory loop). `source` records whether the person
 * stated the preference or the operator demonstrated/observed it. */
export interface LearnedPreference {
  preference: string
  why: string | null
  howToApply: string | null
  source: 'stated' | 'demonstrated'
  recordedAt: string | null
}

/** One person on the learned lane — keyed by the stable sender id (`peer_id`),
 * with their active preferences newest-first. No display name: the learned lane
 * has only the peer id, not an authored name. */
export interface LearnedPreferencePerson {
  peerId: string
  preferences: LearnedPreference[]
}

export type LearnedPreferencesResult =
  | { status: 'not_enabled' }
  | { status: 'unreachable'; reason: string }
  | { status: 'empty' }
  | { status: 'items'; people: LearnedPreferencePerson[] }

/**
 * Load the learned-preferences lane (per-peer working-preference memory) for one
 * customer. Same fail-closed discipline as {@link loadStandingPreferences}: no
 * read (and no audit row) when the seam is unconfigured; otherwise reads
 * `memory_export?table=peer_preferences` via the fail-closed seam and classifies
 * the outcome. The Machine serves the live `peer_preferences` table, so this is
 * what the running Operator has actually learned — not a console copy.
 */
export async function loadLearnedPreferences(
  deps: RuntimeReadDeps,
  customerSlug: string,
  actor: RuntimeReadActor,
  configured: boolean
): Promise<LearnedPreferencesResult> {
  if (!configured) return { status: 'not_enabled' }
  const result = await readMachineRuntime(
    deps,
    customerSlug,
    { kind: 'memory_export', table: 'peer_preferences' },
    actor
  )
  if (!result.ok) return { status: 'unreachable', reason: result.reason }
  const people = parseLearnedPreferences(result.data)
  return people.length === 0 ? { status: 'empty' } : { status: 'items', people }
}

/**
 * Parse the opaque `memory_export` payload (`{ entries: [...] }`) into people
 * with their active learned preferences. Defensive by construction:
 *   • a row missing `peer_id` or `preference` is skipped (never half-rendered);
 *   • a row whose `superseded_by` is non-null was replaced — it is filtered out
 *     so only ACTIVE preferences surface;
 *   • a row of kind `memory` (space-keyed remembered items, ADR 0091) is not a
 *     working preference and is skipped;
 *   • `source` must be one of the two valid values (`stated`/`demonstrated`); a
 *     row with any other source is skipped rather than mislabeled;
 *   • `why`/`how_to_apply`/`recorded_at` normalize to `null`.
 * Rows are grouped by `peer_id` and each person's list is sorted newest-first by
 * `recorded_at` (a null `recorded_at` sorts last). Total on malformed payloads
 * (null/garbage → `[]`). The console never trusts the wire shape — parse, never
 * cast (coding-standards: parse external inputs).
 */
export function parseLearnedPreferences(data: unknown): LearnedPreferencePerson[] {
  const byPeer = new Map<string, LearnedPreference[]>()
  for (const entry of extractEntries(data)) {
    if (!entry || typeof entry !== 'object') continue
    const row = entry as Record<string, unknown>
    if (asStringOrNull(row.superseded_by) !== null) continue
    // A space-mode seat's remembered items (kind 'memory', ADR 0091) share this
    // table. They are a person's or the couple's own words, not working
    // preferences, so this lane never renders them.
    if (row.kind === 'memory') continue
    const peerId = asNonEmptyString(row.peer_id)
    const preference = asNonEmptyString(row.preference)
    if (peerId === null || preference === null) continue
    const source = asLearnedSource(row.source)
    if (source === null) continue
    const pref: LearnedPreference = {
      preference,
      why: asStringOrNull(row.why),
      howToApply: asStringOrNull(row.how_to_apply),
      source,
      recordedAt: asStringOrNull(row.recorded_at),
    }
    const list = byPeer.get(peerId)
    if (list) list.push(pref)
    else byPeer.set(peerId, [pref])
  }
  const out: LearnedPreferencePerson[] = []
  for (const [peerId, preferences] of byPeer) {
    preferences.sort(byRecordedAtDescending)
    out.push({ peerId, preferences })
  }
  return out
}

/** Newest-first by `recordedAt`. A null `recordedAt` (unknown time) sorts last,
 * so dated preferences always lead. */
function byRecordedAtDescending(a: LearnedPreference, b: LearnedPreference): number {
  if (a.recordedAt === null && b.recordedAt === null) return 0
  if (a.recordedAt === null) return 1
  if (b.recordedAt === null) return -1
  if (a.recordedAt < b.recordedAt) return 1
  if (a.recordedAt > b.recordedAt) return -1
  return 0
}

/** Coerce an unknown to one of the two valid `source` values, or null. */
function asLearnedSource(v: unknown): 'stated' | 'demonstrated' | null {
  return v === 'stated' || v === 'demonstrated' ? v : null
}

/** Pull the row list from the seam payload shape (`{ entries: [...] }`). */
function extractEntries(data: unknown): unknown[] {
  if (data && typeof data === 'object') {
    const entries = (data as { entries?: unknown }).entries
    if (Array.isArray(entries)) return entries
  }
  return []
}

function asNonEmptyString(v: unknown): string | null {
  return typeof v === 'string' && v.length > 0 ? v : null
}

function asStringOrNull(v: unknown): string | null {
  return typeof v === 'string' && v.length > 0 ? v : null
}

/** Coerce an unknown to a list of non-empty strings, dropping anything else. */
function asStringList(v: unknown): string[] {
  if (!Array.isArray(v)) return []
  return v.filter((item): item is string => typeof item === 'string' && item.length > 0)
}
