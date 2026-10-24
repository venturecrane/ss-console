/**
 * The compiled seat spec (Wave 2, W4): one canonical, signed description of what
 * a seat is configured to run and what that configuration requires of the
 * image it runs on. Pure: nothing here touches R2, git or the disk except
 * `readLaneSchemaDigests`, which hashes this checkout's schema modules.
 *
 * WHAT A SPEC IS. `fleet/specs/<slug>/<spec_sha256>.json`, content-addressed,
 * plus a pointer `vaults/<slug>/spec-pointer.json` naming it and carrying an
 * Ed25519 signature over the spec's exact bytes. The spec carries references
 * (sha256 and object key), never the referenced bytes: the customer.yaml, the
 * lane-proofs seed, each lane's firm-input manifest, the skills release. Firm
 * inputs live in the private engagements repo and in the seat's vault; a
 * digest of them is all that may appear in this public repo or its CI logs.
 *
 * REQUIREMENTS. What the config asks of an image, in three families:
 *   image         skill:<name>, pin:<name>@<6hex>, pre_run:<skill>/<file>,
 *                 checked against the image's image-manifest.json plus the
 *                 seat's skills release (the 2026-10-13 outage guard);
 *   capabilities  the paths, tools, broker verbs and action classes the
 *                 enabled skills need (the skills manifest's `needs`),
 *                 checked against the image's image-capabilities.json;
 *   lanes         for each lane whose firm inputs are staged, the digest of
 *                 the schema module those inputs are validated against,
 *                 checked against the image's lane-inputs.json.
 *
 * Image refusals block the customer.yaml publish (as the image guard always
 * has); a lane refusal blocks only the spec, because a schema the image does
 * not run says nothing about whether the customer.yaml is runnable.
 */

import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

export const SPEC_SCHEMA_VERSION = 1

/**
 * lane -> the runner module its firm inputs are validated against, and the
 * vault object that pins every firm input of that lane by sha256. The module
 * table equals operator/bin/lib/fleet/build.py LANE_SCHEMA_MODULES, the one
 * `fleet build` hashes into lane-inputs.json (tests/compile-seat-spec.test.ts
 * holds the two equal).
 */
export const LANE_INPUTS: Record<string, { module: string; manifest: string }> = {
  medchron: {
    module: 'operator/runners/medchron/medchron/config.py',
    manifest: 'medchron-firm.yaml',
  },
  demand: {
    module: 'operator/runners/medchron/medchron/demand/firm.py',
    manifest: 'demand/demand-firm.yaml',
  },
  drafting: {
    module: 'operator/runners/medchron/medchron/drafting/firm.py',
    manifest: 'drafting/drafting-firm.yaml',
  },
  litigation: {
    module: 'operator/runners/medchron/medchron/litigation/firm.py',
    manifest: 'litigation/litigation-firm.yaml',
  },
}

const NEED_KINDS = ['paths', 'tools', 'verbs', 'action_classes'] as const
type NeedKind = (typeof NEED_KINDS)[number]
export type Capabilities = Record<NeedKind, string[]>

type Json = null | boolean | number | string | Json[] | { [k: string]: Json }
type Obj = Record<string, unknown>

const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])
const obj = (v: unknown): Obj | null =>
  typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Obj) : null

export function sha256Hex(bytes: Uint8Array | string): string {
  return createHash('sha256').update(bytes).digest('hex')
}

/** Canonical JSON: keys sorted at every depth, no whitespace. The signed bytes. */
export function canonicalJson(value: unknown): string {
  const norm = (v: unknown): Json => {
    if (Array.isArray(v)) return v.map(norm)
    const o = obj(v)
    if (o === null) return v as Json
    const out: { [k: string]: Json } = {}
    for (const k of Object.keys(o).sort()) if (o[k] !== undefined) out[k] = norm(o[k])
    return out
  }
  return JSON.stringify(norm(value))
}

// ---------------------------------------------------------------- requirements

function enabledSkillEntries(config: unknown): Obj[] {
  const out: Obj[] = []
  for (const persona of list(obj(config)?.personas)) {
    for (const s of list(obj(persona)?.skills)) {
      const skill = obj(s)
      if (skill && skill.enabled === true && typeof skill.name === 'string' && skill.name)
        out.push(skill)
    }
  }
  return out
}

/** Names of every enabled skill, sorted and unique. */
export function enabledSkills(config: unknown): string[] {
  return [...new Set(enabledSkillEntries(config).map((s) => scalar(s.name)))].sort()
}

/** A YAML scalar as text, or `fallback` for anything else (absent, a map, a list). */
export function scalar(value: unknown, fallback = ''): string {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
    ? String(value)
    : fallback
}

function cronRequirements(config: unknown, out: Set<string>): void {
  for (const persona of list(obj(config)?.personas)) {
    for (const e of list(obj(persona)?.cron)) {
      const entry = obj(e)
      const skill = scalar(entry?.skill).trim()
      const preRun = scalar(entry?.pre_run).trim()
      if (skill && preRun && scalar(entry?.wake_policy).trim() === 'pre_run_decides')
        out.add(`pre_run:${skill}/${preRun}`)
    }
  }
}

/** The image requirements a parsed customer.yaml carries (skill:, pin:, pre_run:). */
export function imageRequirements(config: unknown): Set<string> {
  const out = new Set<string>()
  for (const skill of enabledSkillEntries(config)) {
    const name = scalar(skill.name)
    out.add(`skill:${name}`)
    const version = scalar(skill.version, 'pending')
    if (version !== 'pending') out.add(`pin:${name}@${version}`)
  }
  cronRequirements(config, out)
  return out
}

/** What an image manifest provides, or null when it is not a schema_version 1 manifest. */
export function providedByImage(manifest: unknown): Set<string> | null {
  const m = obj(manifest)
  const skills = obj(m?.skills)
  if (!m || m.schema_version !== 1 || !skills) return null
  const out = new Set<string>()
  for (const [name, body] of Object.entries(skills)) {
    out.add(`skill:${name}`)
    const b = obj(body)
    if (typeof b?.pin === 'string') out.add(`pin:${name}@${b.pin}`)
    for (const file of list(b?.files)) out.add(`pre_run:${name}/${String(file)}`)
  }
  return out
}

/** What a skills release manifest provides (fleet/skills/<version>.json), or null. */
export function providedBySkills(manifest: unknown): Set<string> | null {
  const m = obj(manifest)
  const files = obj(m?.files)
  if (!m || !files || typeof m.version !== 'string') return null
  const out = new Set<string>()
  for (const rel of Object.keys(files)) {
    const [name, ...rest] = rel.split('/')
    if (!name || rest.length === 0) continue
    out.add(`skill:${name}`)
    out.add(`pre_run:${name}/${rest.join('/')}`)
  }
  for (const [name, pin] of Object.entries(obj(m.pins) ?? {}))
    if (typeof pin === 'string') out.add(`pin:${name}@${pin}`)
  return out
}

/** The union of the needs the skills manifest records for the given skills. */
export function capabilityNeeds(skills: string[], skillsManifest: unknown): Capabilities {
  const needs = obj(obj(skillsManifest)?.needs) ?? {}
  const out = Object.fromEntries(NEED_KINDS.map((k) => [k, new Set<string>()])) as Record<
    NeedKind,
    Set<string>
  >
  for (const skill of skills) {
    const n = obj(needs[skill])
    for (const kind of NEED_KINDS) for (const v of list(n?.[kind])) out[kind].add(String(v))
  }
  return Object.fromEntries(NEED_KINDS.map((k) => [k, [...out[k]].sort()])) as Capabilities
}

function directories(paths: Set<string>): Set<string> {
  const out = new Set<string>()
  for (const p of paths) {
    const parts = p.split('/')
    for (let i = 2; i < parts.length; i++) out.add(parts.slice(0, i).join('/'))
  }
  return out
}

/** Every need the image's capabilities do not provide (port of fleet/skills.py unmet_needs). */
export function unmetCapabilities(needs: Capabilities, caps: unknown): string[] {
  const c = obj(caps) ?? {}
  const have = Object.fromEntries(NEED_KINDS.map((k) => [k, new Set(list(c[k]).map(String))]))
  const dirs = directories(have.paths)
  const missing: string[] = []
  for (const kind of NEED_KINDS) {
    for (const need of needs[kind]) {
      const ok =
        kind === 'paths'
          ? have.paths.has(need) || dirs.has(need.replace(/\/+$/, ''))
          : have[kind].has(need)
      if (!ok) missing.push(`${kind.replace(/s$/, '')}:${need}`)
    }
  }
  return missing
}

/** sha256 of each lane's schema module in a checkout. */
export function readLaneSchemaDigests(repo: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [lane, { module }] of Object.entries(LANE_INPUTS)) {
    const path = join(repo, module)
    if (existsSync(path)) out[lane] = sha256Hex(readFileSync(path))
  }
  return out
}

/** The image path a lane's schema module is installed at (operator/ -> /app/). */
export function imagePathOf(module: string): string {
  return `/app/${module.replace(/^operator\//, '')}`
}
