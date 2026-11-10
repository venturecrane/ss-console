/**
 * Twin drift: a seat that is an exact twin of a client seat (ADR 0107) must
 * author the client's customer.yaml exactly, except the paths it declares.
 *
 * The declaration is `operator/customers/<twin>/twin.yaml`:
 *
 *   twin_of: <client slug>
 *   differences:
 *     - path: connectors.PracticeManagement.environment
 *       why: SMD's own staging tenant, never the client's production one
 *
 * A path is dotted keys; a list element is addressed by its identity key when
 * every element of that list is a mapping carrying a unique scalar one (`slug`,
 * then `name`, then `skill`, then `address`, then `email`), else by index:
 * `personas[slug=operator].skills[name=statute-watch].settings.recipient`.
 * A declared path covers itself and everything beneath it.
 *
 * Two failures, both read by tests/twin-drift.test.ts:
 *   - undeclared: the two files differ at a path no declaration covers (the
 *     client changed and the twin did not, or the twin drifted on its own);
 *   - stale: a declaration that covers no difference (the list stays minimal,
 *     so every line in it is a difference that exists).
 */

import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { parse as parseYaml } from 'yaml'

const IDENTITY_KEYS = ['slug', 'name', 'skill', 'address', 'email'] as const

export interface TwinDeclaration {
  twin: string
  twinOf: string
  differences: Array<{ path: string; why: string }>
}

type Json = unknown

function isMap(v: Json): v is Record<string, Json> {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

/** The identity key a list is addressed by, or null for index addressing. */
function identityKey(list: Json[]): string | null {
  if (!list.length || !list.every(isMap)) return null
  for (const key of IDENTITY_KEYS) {
    const ids = list.map((m) => m[key])
    if (ids.every((id) => typeof id === 'string') && new Set(ids).size === ids.length) return key
  }
  return null
}

function addressed(list: Json[]): Map<string, Json> {
  const key = identityKey(list)
  return new Map(
    list.map((item, i) => [
      key ? `[${key}=${(item as Record<string, Json>)[key] as string}]` : `[${i}]`,
      item,
    ])
  )
}

const join_ = (path: string, seg: string) =>
  seg.startsWith('[') || !path ? path + seg : `${path}.${seg}`

/** Every path at which `a` and `b` differ, as the smallest differing subtree. */
export function diffPaths(a: Json, b: Json, path = ''): string[] {
  if (Array.isArray(a) && Array.isArray(b)) {
    const ka = identityKey(a)
    // Lists addressed differently (one by identity, one by index) differ whole.
    if (ka !== identityKey(b)) return [path]
    const ma = addressed(a)
    const mb = addressed(b)
    const out: string[] = []
    for (const seg of new Set([...ma.keys(), ...mb.keys()])) {
      if (!ma.has(seg) || !mb.has(seg)) out.push(join_(path, seg))
      else out.push(...diffPaths(ma.get(seg), mb.get(seg), join_(path, seg)))
    }
    return out
  }
  if (isMap(a) && isMap(b)) {
    const out: string[] = []
    for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
      const sub = join_(path, key)
      if (!(key in a) || !(key in b)) out.push(sub)
      else out.push(...diffPaths(a[key], b[key], sub))
    }
    return out
  }
  return Object.is(a, b) ? [] : [path]
}

/** True when `declared` is `path` or an ancestor of it. */
export function covers(declared: string, path: string): boolean {
  return path === declared || path.startsWith(`${declared}.`) || path.startsWith(`${declared}[`)
}

export function readTwinDeclaration(customersRoot: string, twin: string): TwinDeclaration | null {
  const file = join(customersRoot, twin, 'twin.yaml')
  if (!existsSync(file)) return null
  const doc = parseYaml(readFileSync(file, 'utf8')) as Record<string, unknown> | null
  if (!doc || typeof doc.twin_of !== 'string' || !Array.isArray(doc.differences))
    throw new Error(`${file}: needs twin_of (a client slug) and differences (a list)`)
  const differences = doc.differences.map((d, i) => {
    const entry = d as Record<string, unknown> | null
    if (
      !entry ||
      typeof entry.path !== 'string' ||
      typeof entry.why !== 'string' ||
      !entry.why.trim()
    )
      throw new Error(`${file}: differences[${i}] needs a path and a why`)
    return { path: entry.path, why: entry.why }
  })
  return { twin, twinOf: doc.twin_of, differences }
}

export interface TwinDriftReport {
  undeclared: string[]
  stale: string[]
}

/** Compare a twin's customer.yaml with its client's against the declaration. */
export function twinDrift(customersRoot: string, decl: TwinDeclaration): TwinDriftReport {
  const load = (slug: string) =>
    parseYaml(readFileSync(join(customersRoot, slug, 'customer.yaml'), 'utf8')) as Json
  const diffs = diffPaths(load(decl.twinOf), load(decl.twin))
  const declared = decl.differences.map((d) => d.path)
  return {
    undeclared: diffs.filter((p) => !declared.some((d) => covers(d, p))),
    stale: declared.filter((d) => !diffs.some((p) => covers(d, p))),
  }
}
