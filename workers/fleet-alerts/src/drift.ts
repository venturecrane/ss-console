/**
 * Authored-versus-running drift (2026-10-22, migration 0135).
 *
 * WHY. Every seat tells D1 what it runs: `image_ref`, `config_digest` and
 * `skills_version` ride the heartbeat into `fleet_status` (migration 0129). The
 * system also records what each seat is SUPPOSED to run. Until this module
 * nothing compared the two, so a seat that diverged from what was authored was
 * found by whoever next looked, and 8 of the 12 incidents before this change
 * were found by a person. Drift is alarmed here, never discovered.
 *
 * WHAT "AUTHORED" MEANS, per signal, each read from where the system already
 * records it:
 *
 *   image   The blessed image is the newest `fleet_releases` row of kind
 *           `image` with `completed_last_ring_at` set (the ADR 0024 amendment,
 *           and `FleetStore.latest_completed` in operator/bin/lib/fleet/d1.py).
 *           `image_drift` opens when a seat whose latest rollout of that release
 *           is `done` now reports an OLDER image: it had the release and lost
 *           it. A seat the release never reached is not drift (it is a rollout
 *           still to run), and a seat on a NEWER release than the blessed one is
 *           a rollout in progress. `image_unknown` opens when the reported image
 *           matches no release at all, which nothing the release system does
 *           can produce. Matching is `image_matches` from
 *           operator/bin/lib/fleet/ids.py, ported below.
 *
 *   config  The seat's `config_digest` is sha256 of the bytes of its root-owned
 *           customer.yaml (overlay shared/fleet_facts.py read_config_digest),
 *           and those bytes are exactly R2 `vaults/<slug>/customer.yaml`: the
 *           entrypoint copies the object on every boot and the config applier
 *           writes the fetched bytes verbatim (overlay config_applier/applier.py
 *           apply, atomic_write(volume, new_bytes)). So the authored digest is
 *           sha256 of that object, computed here from the same bucket binding
 *           the web Worker uses. A mismatch past the grace window means a live
 *           apply was refused, a rebuild-class field is waiting for a
 *           reprovision, or the seat cannot reach R2. A seat reporting a digest
 *           with NO object in R2 runs a config nobody publishes: also drift.
 *
 *   skills  `vaults/<slug>/skills-pointer.json` names the version the seat's
 *           skills_applier must hold (operator/bin/lib/fleet/skills.py). A
 *           pointer whose version the seat does not report, past the grace
 *           window, is drift. No pointer and no reported version is a seat that
 *           has never had a skills release: not drift. A reported version with
 *           no pointer is skills nobody authored: drift.
 *
 * GRACE. A config or pointer written less than DRIFT_GRACE_SECONDS ago (default
 * 900) is still being picked up (the appliers poll every 15s, a skills
 * rollout's read-back waits 3 minutes), so a mismatch inside the window HOLDS:
 * no state is pushed, so it can neither open nor resolve an alert. The window is
 * measured from the R2 object's own `uploaded` time.
 *
 * HOLDS. Like every condition in this Worker, a signal that cannot be read
 * pushes nothing rather than `active: false`, because a false RECOVERED email
 * cancels human urgency. A seat whose heartbeat is older than the red threshold
 * holds every drift signal: its row describes the past, and heartbeat_red
 * already pages for it. A NULL reported value holds that signal. A failed D1 or
 * R2 read holds the whole signal for this tick.
 *
 * SHAPE. Standing conditions through the shared open/resolve machinery
 * (./transition): one email when a seat drifts, one RECOVERED reply when it
 * converges, silence in between. A seat that stays drifted is one open alert,
 * however long it lasts. Fail-soft end to end: nothing here can throw into
 * runOnce.
 */

import type { ConditionState, Env } from './index'
import type { FleetStatusRow } from './fleet-status'

export const IMAGE_DRIFT_CONDITION = 'image_drift'
export const IMAGE_UNKNOWN_CONDITION = 'image_unknown'
export const CONFIG_DRIFT_CONDITION = 'config_drift'
export const SKILLS_DRIFT_CONDITION = 'skills_drift'

export const DEFAULT_DRIFT_GRACE_SECONDS = 900

/** Same shape as operator/bin/lib/fleet/ids.py SLUG_RE: nothing else becomes an R2 key. */
const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$/
const DIGEST_RE = /^sha256:[0-9a-f]{64}$/

/** One image release, as read from fleet_releases. */
export interface ImageRelease {
  release_id: string
  image_ref: string
  image_digest: string | null
  created_at: string
  completed_last_ring_at: string | null
}

/**
 * One seat's authored object in R2. `value` is the sha256 hex of a
 * customer.yaml, or the `version` a skills pointer names (null when the pointer
 * is present but carries no readable version). `uploadedMs` is R2's own write
 * time for the object.
 */
export interface AuthoredObject {
  value: string | null
  uploadedMs: number
}

/** `null` = no object in R2. `undefined` = not read (missing binding, read error): hold. */
export type Authored = AuthoredObject | null | undefined

export interface DriftInputs {
  /** Every image release, or null when fleet_releases/fleet_rollouts could not be read. */
  releases: ImageRelease[] | null
  /** Seats whose LATEST rollout of the blessed release is `done`. */
  blessedDoneSeats: ReadonlySet<string>
  configs: ReadonlyMap<string, Authored>
  skills: ReadonlyMap<string, Authored>
}

export interface DriftOptions {
  nowMs: number
  /** A heartbeat older than this holds every drift signal for the seat. */
  redSeconds: number
  graceSeconds: number
}

function instant(ts: string | null | undefined): number {
  return typeof ts === 'string' && ts.length > 0 ? Date.parse(ts) : Number.NaN
}

/** The `sha256:<hex>` an image ref pins, or null. Port of ids.digest_in_ref. */
export function digestInRef(ref: string | null | undefined): string | null {
  if (!ref || !ref.includes('@sha256:')) return null
  const tail = 'sha256:' + ref.slice(ref.lastIndexOf('@sha256:') + '@sha256:'.length)
  return DIGEST_RE.test(tail) ? tail : null
}

/**
 * Does the image a seat reports equal a release's image? Port of
 * ids.image_matches: a digest on both sides decides; otherwise the full ref or
 * `repo:tag` must be equal.
 */
export function imageMatches(
  reported: string | null | undefined,
  targetRef: string,
  targetDigest: string | null
): boolean {
  if (!reported) return false
  const got = digestInRef(reported)
  if (got && targetDigest) return got === targetDigest
  if (reported === targetRef) return true
  return reported.split('@', 1)[0] === targetRef.split('@', 1)[0]
}

/** The newest image release that completed its last ring (FleetStore.latest_completed). */
export function blessedRelease(releases: ImageRelease[]): ImageRelease | null {
  let best: ImageRelease | null = null
  for (const r of releases) {
    const at = instant(r.completed_last_ring_at)
    if (Number.isNaN(at)) continue
    if (best === null || at > instant(best.completed_last_ring_at)) best = r
  }
  return best
}

/** The newest release whose image the seat reports, or null when none matches. */
function releaseFor(reported: string, releases: ImageRelease[]): ImageRelease | null {
  let best: ImageRelease | null = null
  for (const r of releases) {
    if (!imageMatches(reported, r.image_ref, r.image_digest)) continue
    if (best === null || instant(r.created_at) > instant(best.created_at)) best = r
  }
  return best
}

function state(slug: string, condition: string, active: boolean, detail: string): ConditionState {
  return {
    customer_slug: slug,
    condition: condition as ConditionState['condition'],
    active,
    detail,
  }
}

function imageConditions(
  row: FleetStatusRow,
  releases: ImageRelease[] | null,
  blessedDone: ReadonlySet<string>
): ConditionState[] {
  const reported = row.image_ref
  if (releases === null || !reported) return []
  const slug = row.customer_slug
  const matched = releaseFor(reported, releases)
  if (matched === null) {
    // image_drift holds here: "older than blessed" has no meaning for an image
    // no release recorded, and one page for this seat is enough.
    return [
      state(
        slug,
        IMAGE_UNKNOWN_CONDITION,
        true,
        `seat reports image ${reported}, which matches no release in fleet_releases. ` +
          'Something deployed this seat outside the release system. ' +
          '`operator/bin/fleet status` shows every seat.'
      ),
    ]
  }
  const out = [
    state(slug, IMAGE_UNKNOWN_CONDITION, false, `seat reports release ${matched.release_id}`),
  ]
  const blessed = blessedRelease(releases)
  if (blessed === null) return out
  if (imageMatches(reported, blessed.image_ref, blessed.image_digest)) {
    out.push(state(slug, IMAGE_DRIFT_CONDITION, false, `seat runs blessed ${blessed.release_id}`))
  } else if (instant(matched.created_at) > instant(blessed.created_at)) {
    out.push(
      state(
        slug,
        IMAGE_DRIFT_CONDITION,
        false,
        `seat runs ${matched.release_id}, newer than blessed ${blessed.release_id} (a rollout in progress)`
      )
    )
  } else if (blessedDone.has(slug)) {
    out.push(
      state(
        slug,
        IMAGE_DRIFT_CONDITION,
        true,
        `seat runs ${matched.release_id}, but its rollout of blessed ${blessed.release_id} ` +
          'finished done, so the seat went back to an older image after the release landed. ' +
          `Put it back with \`operator/bin/fleet release ${blessed.release_id} --only ${slug}\`.`
      )
    )
  } else {
    out.push(
      state(
        slug,
        IMAGE_DRIFT_CONDITION,
        false,
        `seat runs ${matched.release_id}; blessed ${blessed.release_id} has not been rolled out to it yet`
      )
    )
  }
  return out
}

function withinGrace(authored: AuthoredObject, o: DriftOptions): boolean {
  return o.nowMs - authored.uploadedMs < o.graceSeconds * 1000
}

function publishedAt(authored: AuthoredObject): string {
  return new Date(authored.uploadedMs).toISOString().replace(/\.\d{3}Z$/, 'Z')
}

function configConditions(
  row: FleetStatusRow,
  authored: Authored,
  o: DriftOptions
): ConditionState[] {
  const reported = row.config_digest
  if (!reported || authored === undefined) return []
  const slug = row.customer_slug
  const key = `vaults/${slug}/customer.yaml`
  if (authored === null) {
    return [
      state(
        slug,
        CONFIG_DRIFT_CONDITION,
        true,
        `seat runs a customer.yaml (sha256 ${reported.slice(0, 12)}) but R2 holds no ${key}: ` +
          'the seat obeys a config nobody publishes.'
      ),
    ]
  }
  if (authored.value === reported) {
    return [state(slug, CONFIG_DRIFT_CONDITION, false, `seat runs the published ${key}`)]
  }
  if (withinGrace(authored, o)) return []
  return [
    state(
      slug,
      CONFIG_DRIFT_CONDITION,
      true,
      `seat runs customer.yaml sha256 ${reported.slice(0, 12)}, but ${key} ` +
        `(published ${publishedAt(authored)}) is sha256 ${(authored.value ?? '').slice(0, 12)}. ` +
        'The seat refused a live apply, a rebuild-class change is waiting for a reprovision, ' +
        "or the seat cannot reach R2; the seat's agent.log names which."
    ),
  ]
}

function skillsConditions(
  row: FleetStatusRow,
  authored: Authored,
  o: DriftOptions
): ConditionState[] {
  if (authored === undefined) return []
  const slug = row.customer_slug
  const reported = row.skills_version ?? null
  const key = `vaults/${slug}/skills-pointer.json`
  if (authored === null) {
    if (reported === null) {
      return [state(slug, SKILLS_DRIFT_CONDITION, false, 'no skills release authored or held')]
    }
    return [
      state(
        slug,
        SKILLS_DRIFT_CONDITION,
        true,
        `seat reports skills ${reported}, but R2 holds no ${key}: skills nobody authored.`
      ),
    ]
  }
  if (authored.value !== null && authored.value === reported) {
    return [state(slug, SKILLS_DRIFT_CONDITION, false, `seat holds pointer version ${reported}`)]
  }
  if (withinGrace(authored, o)) return []
  const want = authored.value ?? '(pointer carries no readable version)'
  return [
    state(
      slug,
      SKILLS_DRIFT_CONDITION,
      true,
      `${key} (written ${publishedAt(authored)}) names ${want}, ` +
        `but the seat reports ${reported ?? 'no skills version'}. ` +
        "The seat's skills_applier has not applied it; its agent.log says why."
    ),
  ]
}

function heartbeatFresh(row: FleetStatusRow, o: DriftOptions): boolean {
  const ts = instant(row.last_heartbeat_ts)
  return !Number.isNaN(ts) && o.nowMs - ts <= o.redSeconds * 1000
}

/** Pure drift evaluation over fleet_status rows. Exported for tests and the dry run. */
export function evaluateDrift(
  rows: FleetStatusRow[],
  inputs: DriftInputs,
  options: DriftOptions
): ConditionState[] {
  const out: ConditionState[] = []
  for (const row of rows) {
    if (!heartbeatFresh(row, options)) continue
    out.push(...imageConditions(row, inputs.releases, inputs.blessedDoneSeats))
    out.push(...configConditions(row, inputs.configs.get(row.customer_slug), options))
    out.push(...skillsConditions(row, inputs.skills.get(row.customer_slug), options))
  }
  return out
}

export function driftGraceSeconds(raw: string | undefined): number {
  const n = Number(raw)
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : DEFAULT_DRIFT_GRACE_SECONDS
}

// ---------------------------------------------------------------- reads

interface RolloutStateRow {
  customer_slug: string
  state: string
}

/**
 * The image releases and the seats whose latest rollout of the blessed one is
 * done. Either read failing returns `releases: null`, which holds both image
 * conditions: an empty done-set would read as "not rolled out" and resolve an
 * open image_drift on a database hiccup.
 */
async function readReleaseInputs(
  db: D1Database
): Promise<Pick<DriftInputs, 'releases' | 'blessedDoneSeats'>> {
  try {
    const rel = await db
      .prepare(
        `SELECT release_id, image_ref, image_digest, created_at, completed_last_ring_at
           FROM fleet_releases
          WHERE kind = 'image' AND image_ref IS NOT NULL`
      )
      .all<ImageRelease>()
    const releases = rel.results ?? []
    const blessed = blessedRelease(releases)
    const done = new Set<string>()
    if (blessed !== null) {
      const ro = await db
        .prepare(
          `SELECT customer_slug, state FROM fleet_rollouts
            WHERE release_id = ?
            ORDER BY started_at ASC, rowid ASC`
        )
        .bind(blessed.release_id)
        .all<RolloutStateRow>()
      // Ascending, last write wins: the newest attempt per seat decides.
      const latest = new Map<string, string>()
      for (const r of ro.results ?? []) latest.set(r.customer_slug, r.state)
      for (const [slug, st] of latest) if (st === 'done') done.add(slug)
    }
    return { releases, blessedDoneSeats: done }
  } catch (err) {
    console.error('[fleet-alerts] drift release read failed:', err)
    return { releases: null, blessedDoneSeats: new Set() }
  }
}

async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

/** The version a pointer names, parsed not cast; null when it names none. */
export function pointerVersion(text: string): string | null {
  try {
    const raw: unknown = JSON.parse(text)
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
    const version = (raw as Record<string, unknown>).version
    return typeof version === 'string' && version.length > 0 ? version : null
  } catch {
    return null
  }
}

async function readAuthored(
  bucket: R2Bucket,
  key: string,
  parse: (body: ArrayBuffer) => string | null | Promise<string | null>
): Promise<Authored> {
  try {
    const obj = await bucket.get(key)
    if (obj === null) return null
    return { value: await parse(await obj.arrayBuffer()), uploadedMs: obj.uploaded.getTime() }
  } catch (err) {
    console.error('[fleet-alerts] drift R2 read failed:', key, err)
    return undefined
  }
}

async function readR2Inputs(
  bucket: R2Bucket | undefined,
  slugs: string[]
): Promise<Pick<DriftInputs, 'configs' | 'skills'>> {
  const configs = new Map<string, Authored>()
  const skills = new Map<string, Authored>()
  if (!bucket) return { configs, skills }
  const decoder = new TextDecoder()
  for (const slug of slugs) {
    if (!SLUG_RE.test(slug)) continue
    configs.set(slug, await readAuthored(bucket, `vaults/${slug}/customer.yaml`, sha256Hex))
    skills.set(
      slug,
      await readAuthored(bucket, `vaults/${slug}/skills-pointer.json`, (b) =>
        pointerVersion(decoder.decode(b))
      )
    )
  }
  return { configs, skills }
}

/** Everything evaluateDrift needs, read fail-soft from D1 and R2. */
export async function readDriftInputs(env: Env, rows: FleetStatusRow[]): Promise<DriftInputs> {
  const release = await readReleaseInputs(env.DB)
  const r2 = await readR2Inputs(
    env.CUSTOMER_CONFIG,
    rows.map((r) => r.customer_slug)
  )
  return { ...release, ...r2 }
}

/** Read, then evaluate. Never throws. */
export async function driftConditions(
  env: Env,
  rows: FleetStatusRow[],
  nowMs: number,
  redSeconds: number
): Promise<ConditionState[]> {
  try {
    const inputs = await readDriftInputs(env, rows)
    return evaluateDrift(rows, inputs, {
      nowMs,
      redSeconds,
      graceSeconds: driftGraceSeconds(env.DRIFT_GRACE_SECONDS),
    })
  } catch (err) {
    console.error('[fleet-alerts] drift evaluation failed:', err)
    return []
  }
}
