/**
 * Compile one seat's spec from resolved inputs, and sign or verify it.
 *
 * The inputs are resolved by the caller (scripts/compile-seat-spec.ts reads
 * them from files the shell fetched from R2), so this module is pure and every
 * refusal rule is testable on values alone. See ./seat-spec.ts for the shape.
 *
 * "CANNOT DETERMINE" NEVER PASSES ON ITS OWN (the image guard's rule, kept).
 * With no readable image manifest or capabilities file, the config may still
 * compile if it adds no requirement over the object the seat already boots
 * from: that claims nothing about the image, only that this publish cannot make
 * the seat worse. A lane whose image digests are unknown is refused outright,
 * because a lane schema has no "already booting" fallback to compare against.
 */

import { createPrivateKey, createPublicKey, sign, verify, type KeyObject } from 'node:crypto'

import { parse as parseYaml } from 'yaml'

import {
  LANE_INPUTS,
  SPEC_SCHEMA_VERSION,
  canonicalJson,
  capabilityNeeds,
  enabledSkills,
  imagePathOf,
  imageRequirements,
  providedByImage,
  providedBySkills,
  scalar,
  sha256Hex,
  unmetCapabilities,
  type Capabilities,
} from './seat-spec'

/** A JSON input that may be absent (undefined), unreadable (Error), or parsed. */
export type MaybeJson = unknown

export interface CompileInputs {
  slug: string
  compiledFrom: string
  customerYaml: Buffer
  laneProofs: Buffer | null
  /** lane -> the bytes of that lane's firm-input manifest in the vault (authored lanes only). */
  lanePins: Record<string, Buffer>
  /** lane -> sha256 of the schema module in the compiling checkout. */
  laneSchemaDigests: Record<string, string>
  imageManifest?: MaybeJson
  imageCapabilities?: MaybeJson
  imageLaneInputs?: MaybeJson
  skillsPointer?: MaybeJson
  skillsManifest?: MaybeJson
  /** The customer.yaml the seat boots from now, for the cannot-determine fallback. */
  published?: Buffer
  /**
   * Provisioning: the image this config will run is built and deployed in the
   * same run, so there is no running image to check against (the reason the
   * image guard never ran there). Requirements are recorded, not checked; the
   * seat checks them against its own image when it verifies the spec.
   */
  unchecked?: boolean
}

export interface CompileResult {
  spec: Record<string, unknown>
  /** Refusals that block the customer.yaml publish as well as the spec. */
  imageRefusals: string[]
  /** Refusals that block only the spec. */
  laneRefusals: string[]
  notes: string[]
}

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v) && !(v instanceof Error)

function describeImage(inputs: CompileInputs): { have: Set<string> | null; note: string } {
  let have: Set<string> | null = null
  let note = 'no image manifest for this seat'
  const m = inputs.imageManifest
  if (m instanceof Error) note = `the image manifest is unreadable (${m.message})`
  else if (m !== undefined) {
    have = providedByImage(m)
    note =
      have === null
        ? 'the image manifest is not a schema_version 1 manifest'
        : `image built from ${scalar((m as Record<string, unknown>).built_from, 'unknown commit')}`
  }
  const sm = inputs.skillsManifest
  if (sm instanceof Error)
    return { have, note: `${note}; the skills manifest is unreadable (${sm.message})` }
  if (sm === undefined) return { have, note }
  const fromSkills = providedBySkills(sm)
  if (fromSkills === null)
    return { have, note: `${note}; the skills manifest is not a fleet skills manifest (ignored)` }
  const version = String((sm as Record<string, unknown>).version)
  const merged = new Set([...(have ?? []), ...fromSkills])
  return {
    have: merged,
    note:
      have !== null
        ? `${note} plus skills release ${version}`
        : `skills release ${version} (${note})`,
  }
}

function publishedConfig(inputs: CompileInputs): { config: unknown } | { error: string } {
  if (!inputs.published) return { error: 'no published object to compare against' }
  try {
    return { config: parseYaml(inputs.published.toString()) }
  } catch (err) {
    return { error: `the published object is unreadable (${(err as Error).message})` }
  }
}

function checkImage(config: unknown, inputs: CompileInputs, notes: string[]): string[] {
  const required = imageRequirements(config)
  const { have, note } = describeImage(inputs)
  if (have !== null) {
    const missing = [...required].filter((r) => !have.has(r)).sort()
    if (missing.length === 0) {
      notes.push(`every skill and pre_run this config names is in the running image (${note})`)
      return []
    }
    return [
      `config names what the seat's running image lacks (${note}):`,
      ...missing.map((m) => `  ${m}`),
      'Rebuild the seat (reprovision) to publish this config with an image that carries it.',
    ]
  }
  const before = publishedConfig(inputs)
  if ('error' in before) {
    return [
      `cannot determine what the seat's running image carries: ${note}, and ${before.error}.`,
      'Refusing to publish; the seat rebuild (reprovision) publishes this config with its image.',
    ]
  }
  const was = imageRequirements(before.config)
  const added = [...required].filter((r) => !was.has(r)).sort()
  if (added.length > 0) {
    return [
      `cannot determine what the seat's running image carries (${note}), and this config adds:`,
      ...added.map((a) => `  ${a}`),
      'Refusing to publish; the seat rebuild (reprovision) publishes this config with its image.',
    ]
  }
  notes.push(
    `${note}; publishing because this config adds no skill or pre_run over the object the seat already boots from`
  )
  return []
}

function checkCapabilities(
  needs: Capabilities,
  config: unknown,
  inputs: CompileInputs,
  notes: string[]
): string[] {
  const total = Object.values(needs).reduce((n, v) => n + v.length, 0)
  if (total === 0) return []
  const caps = inputs.imageCapabilities
  if (isObj(caps)) {
    const missing = unmetCapabilities(needs, caps)
    return missing.length === 0
      ? []
      : [
          `the enabled skills need what the seat's image does not provide:`,
          ...missing.map((m) => `  ${m}`),
        ]
  }
  const why = caps instanceof Error ? `unreadable (${caps.message})` : 'not available'
  const before = publishedConfig(inputs)
  const was =
    'error' in before ? null : capabilityNeeds(enabledSkills(before.config), inputs.skillsManifest)
  const added = Object.entries(needs).flatMap(([kind, values]) =>
    values
      .filter((v) => !was || !(was as Record<string, string[]>)[kind].includes(v))
      .map((v) => `${kind.replace(/s$/, '')}:${v}`)
  )
  if (added.length === 0) {
    notes.push(
      `the image's capabilities are ${why}; the enabled skills need nothing new over the object the seat boots from`
    )
    return []
  }
  return [
    `cannot determine what the seat's image provides (its capabilities are ${why}), and this config adds needs:`,
    ...added.map((a) => `  ${a}`),
  ]
}

function checkLanes(inputs: CompileInputs, lanes: string[]): string[] {
  const out: string[] = []
  const image = isObj(inputs.imageLaneInputs) ? inputs.imageLaneInputs : null
  for (const lane of lanes) {
    const want = inputs.laneSchemaDigests[lane]
    if (!want)
      out.push(`lane ${lane}: this checkout has no schema module ${LANE_INPUTS[lane].module}`)
    else if (image === null)
      out.push(
        `lane ${lane}: cannot determine the seat image's lane schema (no lane-inputs.json for its image)`
      )
    else if (image[lane] !== want) {
      out.push(
        `lane ${lane}: the seat image validates firm inputs against schema ${scalar(image[lane], 'none').slice(0, 12)}, this checkout against ${want.slice(0, 12)}; release the seat onto an image built from this schema`
      )
    }
  }
  return out
}

function skillsRef(pointer: unknown): Record<string, string> | null {
  if (!isObj(pointer)) return null
  const { version, manifest_key: key, manifest_sha256: sha } = pointer
  if (typeof version !== 'string' || typeof key !== 'string' || typeof sha !== 'string') return null
  return { version, manifest_key: key, manifest_sha256: sha }
}

/** Compile a spec. Never throws on a refusal; the caller reads the refusal lists. */
export function compileSpec(inputs: CompileInputs): CompileResult {
  const notes: string[] = []
  const vault = `vaults/${inputs.slug}`
  let config: unknown
  try {
    config = parseYaml(inputs.customerYaml.toString())
  } catch (err) {
    return {
      spec: {},
      imageRefusals: [`customer.yaml is not YAML: ${(err as Error).message}`],
      laneRefusals: [],
      notes,
    }
  }
  const skills = enabledSkills(config)
  const needs = capabilityNeeds(
    skills,
    inputs.skillsManifest instanceof Error ? undefined : inputs.skillsManifest
  )
  const lanes = Object.keys(inputs.lanePins)
    .filter((l) => l in LANE_INPUTS)
    .sort()
  const imageRefusals = inputs.unchecked
    ? []
    : [...checkImage(config, inputs, notes), ...checkCapabilities(needs, config, inputs, notes)]
  const laneRefusals = inputs.unchecked ? [] : checkLanes(inputs, lanes)
  if (inputs.unchecked) {
    notes.push('provisioning: requirements recorded, not checked (the image ships in this run)')
  }
  const spec = {
    schema_version: SPEC_SCHEMA_VERSION,
    slug: inputs.slug,
    compiled_from: inputs.compiledFrom,
    customer_yaml: { key: `${vault}/customer.yaml`, sha256: sha256Hex(inputs.customerYaml) },
    lane_proofs: inputs.laneProofs
      ? { key: `${vault}/lane-proofs.yaml`, sha256: sha256Hex(inputs.laneProofs) }
      : null,
    lanes: Object.fromEntries(
      lanes.map((l) => [
        l,
        { key: `${vault}/${LANE_INPUTS[l].manifest}`, sha256: sha256Hex(inputs.lanePins[l]) },
      ])
    ),
    skills: skillsRef(inputs.skillsPointer),
    requires: {
      skills,
      image: [...imageRequirements(config)].sort(),
      capabilities: needs,
      lane_schema_digests: Object.fromEntries(
        lanes
          .filter((l) => inputs.laneSchemaDigests[l])
          .map((l) => [
            l,
            { path: imagePathOf(LANE_INPUTS[l].module), sha256: inputs.laneSchemaDigests[l] },
          ])
      ),
    },
  }
  return { spec, imageRefusals, laneRefusals, notes }
}

/** The bytes that are hashed, stored and signed. */
export function specBytes(spec: Record<string, unknown>): Uint8Array {
  return new TextEncoder().encode(canonicalJson(spec))
}

function toBase64(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
}

function fromBase64(text: string): Uint8Array {
  return Uint8Array.from(atob(text), (c) => c.charCodeAt(0))
}

/** Equal in everything but the commit it was compiled at: republishing would change nothing on the seat. */
export function sameSpec(a: unknown, b: unknown): boolean {
  if (!isObj(a) || !isObj(b)) return false
  const strip = (s: Record<string, unknown>) => canonicalJson({ ...s, compiled_from: undefined })
  return strip(a) === strip(b)
}

/** first 16 hex of sha256 over the raw 32-byte Ed25519 public key. */
export function keyIdOf(key: KeyObject): string {
  const der = key.export({ format: 'der', type: 'spki' })
  return sha256Hex(der.subarray(der.length - 32)).slice(0, 16)
}

export function specKey(slug: string, sha: string): string {
  return `fleet/specs/${slug}/${sha}.json`
}

/** Sign a spec. Refuses a key whose public half is not the committed one. */
export function signedPointer(
  spec: Record<string, unknown>,
  privatePem: string,
  publicPem: string
): Record<string, unknown> {
  const priv = createPrivateKey(privatePem)
  const committed = keyIdOf(createPublicKey(publicPem))
  const own = keyIdOf(createPublicKey(priv))
  if (own !== committed) {
    throw new Error(
      `the signing key (key_id ${own}) is not the committed seat-spec key (key_id ${committed})`
    )
  }
  const bytes = specBytes(spec)
  const sha = sha256Hex(bytes)
  return {
    schema_version: SPEC_SCHEMA_VERSION,
    slug: spec.slug,
    spec_sha256: sha,
    spec_key: specKey(String(spec.slug), sha),
    compiled_from: spec.compiled_from,
    key_id: own,
    signature: toBase64(sign(null, bytes, priv)),
  }
}

/** Why a pointer and spec bytes do not verify against the committed key, or null when they do. */
export function pointerProblem(
  pointer: unknown,
  bytes: Uint8Array,
  publicPem: string
): string | null {
  if (!isObj(pointer)) return 'the pointer is not a JSON object'
  const pub = createPublicKey(publicPem)
  if (pointer.key_id !== keyIdOf(pub))
    return `the pointer names key_id ${String(pointer.key_id)}, not the committed key`
  if (pointer.spec_sha256 !== sha256Hex(bytes))
    return 'the spec bytes do not hash to the pointer spec_sha256'
  if (typeof pointer.signature !== 'string') return 'the pointer carries no signature'
  let signature: Uint8Array
  try {
    signature = fromBase64(pointer.signature)
  } catch {
    return 'the pointer signature is not base64'
  }
  const ok = verify(null, bytes, pub, signature)
  return ok ? null : 'the signature does not verify against the committed key'
}
