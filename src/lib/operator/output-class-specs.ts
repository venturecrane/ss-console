/**
 * Console → vault writer for the customer's authored output-class specs
 * (ADR 0083), and the reader that renders them back into the authoring form.
 *
 * THE OBJECT. `vaults/<slug>/output-classes.json`, in the `CUSTOMER_CONFIG`
 * R2 bucket. The consumer already exists: the overlay's `spec_applier` polls
 * this key, verifies every declared hash, installs the bodies root-owned under
 * `${SMD_SPEC_DIR}/classes/<class>/<prop>.md`, and commits a root-computed
 * manifest last. This module is the missing producer.
 *
 * TWO WRITERS, TWO KEY SPACES, NEVER THE SAME OBJECT. `customer.yaml` beside
 * this key is git-authoritative and auto-published on merge by
 * `scripts/ci-publish-customer-configs.sh`. A portal write there would be
 * clobbered by the next unrelated merge to that slug — and would reopen #1898,
 * which forbids R2 diverging from git out of band. That publisher is
 * structurally barred from this key; this module is structurally barred from
 * that one, by the mirror image of its guard: the basename is a literal
 * constant, the slug is charset-constrained so it cannot carry a `.` or a `/`,
 * and `assertSpecKey` re-checks the assembled key against a whole-string
 * pattern before any read or write. There is no input to this module that
 * produces any other key.
 *
 * THE DECLARED HASH IS INTEGRITY, NOT AUTHENTICATION. Each body carries a
 * sha256 the applier verifies, which catches a truncated body or a torn write.
 * It authenticates nothing — whoever can write the object can write a matching
 * hash next to it. So the hash is computed HERE, server-side, over the bytes
 * about to be written, and a hash arriving in a request body is not read at
 * all. Trusting a submitted digest would mean the client could hand the seat a
 * body/hash pair that verifies against itself while disagreeing with what they
 * typed.
 *
 * THE STORED BYTES ARE LF-ONLY. A browser textarea submits its value with CRLF
 * line endings, per the HTML form-submission spec. Nothing downstream converts
 * them, and nothing would fail loudly if they survived: the digest computed here
 * would match its own CRLF body and the applier would verify and install it. The
 * seat would then hold a markdown file whose every line carries a trailing `\r` —
 * different bytes from the LF file every existing proof was taken against, and
 * a trailing character that any line-oriented format check would silently
 * inherit. So `buildSpecDocument` normalises CRLF and lone CR to LF BEFORE
 * trimming, hashing, and length-checking. The document, the digest, and the
 * installed file therefore agree, and agree on LF.
 *
 * FAIL-CLOSED ON THE EMPTY DOCUMENT. The applier refuses a document declaring
 * no bodies (`output-classes.json declares no spec bodies`) and, refusing it,
 * keeps the previously installed tree standing. So writing an empty document
 * would not clear anything — it would leave the seat serving the old specs
 * while the portal showed none. This module refuses to write it instead, and
 * the caller says so. Clearing the LAST authored spec is not something this
 * surface can do; clearing one of several is (the applier prunes what the new
 * document no longer declares).
 */

import { assertionsApplyTo, parseAssertions, type Assertions } from './format-assertions'
import { isRecord } from '../api/helpers'

/** Schema version of the vault document. Must match the applier's constant. */
export const SPEC_SCHEMA_VERSION = 1

/**
 * The ONLY object name this module may address. Deliberately a constant, for
 * the reason in the header: an object name that is an input is an object name
 * that can become `customer.yaml`.
 */
const SPEC_OBJECT_BASENAME = 'output-classes.json'

/** Whole-string shape of every key this module may touch. */
const SPEC_KEY_PATTERN = /^vaults\/[a-z0-9-]+\/output-classes\.json$/

/** Customer slug charset. No `.`, no `/` — the two characters that could reach a neighbouring key. */
const SLUG_PATTERN = /^[a-z0-9-]+$/

/** Output-class slug charset. Mirrors the applier's `_safe_slug`. */
const CLASS_SLUG_PATTERN = /^[a-z0-9_-]+$/

/** The two spec properties an output class can carry (ADR 0083). */
export const SPEC_PROPERTIES = ['voice', 'format'] as const
export type SpecProperty = (typeof SPEC_PROPERTIES)[number]

/**
 * Upper bound on one spec body, mirroring the applier's ceiling. Enforced here
 * so an oversize body is refused at the surface the person is typing into,
 * rather than silently accepted and then rejected by a seat they cannot see.
 */
export const MAX_SPEC_BODY_BYTES = 256 * 1024

/**
 * Form field name for one class property, shared by the authoring form and the
 * endpoint that reads it back. One definition: a field name that two files
 * spell independently is a field name that silently stops being submitted.
 */
export function specFieldName(outputClass: string, property: SpecProperty): string {
  return `specs[${outputClass}].${property}`
}

/**
 * One authored body, with the digest this module computed over it, and — for
 * the `format` property only — the machine-checkable rules that ride beside it.
 *
 * TWO HALVES OF ONE SUBMISSION, NEITHER DERIVED FROM THE OTHER. `body` is the
 * prose that goes in front of the model; `assertions` is what goes in front of
 * the seat's checker. The seat's `shared/format_check.py` says it first:
 * nothing parses English into rules, and nothing infers prose from rules. They
 * are authored together and stored together. See `./format-assertions`.
 */
export interface SpecBody {
  body: string
  sha256: string
  assertions?: Assertions
}

/** The vault document: per class, per property, an authored body. */
export type SpecDocument = {
  schema_version: number
  classes: Record<string, Partial<Record<SpecProperty, SpecBody>>>
}

export class SpecKeyError extends Error {}

/**
 * The R2 key for a customer's authored specs, or a throw. A blank or
 * out-of-charset slug is refused rather than sanitized: both are bugs, and a
 * quietly-rewritten path is how a write lands somewhere nobody looked.
 */
export function specObjectKey(slug: string): string {
  const trimmed = typeof slug === 'string' ? slug.trim() : ''
  if (!SLUG_PATTERN.test(trimmed)) {
    throw new SpecKeyError(`refusing a customer slug outside [a-z0-9-]: ${JSON.stringify(slug)}`)
  }
  const key = `vaults/${trimmed}/${SPEC_OBJECT_BASENAME}`
  assertSpecKey(key)
  return key
}

/**
 * The last gate before any read or write. Belt to the constant basename's
 * braces: even if a future edit made the basename variable, a key outside this
 * one shape throws rather than reaching a neighbouring key space.
 */
export function assertSpecKey(key: string): void {
  if (!SPEC_KEY_PATTERN.test(key)) {
    throw new SpecKeyError(`refusing an R2 key outside the output-classes key space: ${key}`)
  }
}

// ---------------------------------------------------------------------------
// Normalisation
// ---------------------------------------------------------------------------

/**
 * Collapse CRLF and lone CR to LF.
 *
 * Called before trimming, hashing, and the byte-length check, so all three see
 * the bytes that will actually be stored and installed. See the header note.
 */
export function normalizeLineEndings(text: string): string {
  return text.replace(/\r\n?/g, '\n')
}

// ---------------------------------------------------------------------------
// Hashing
// ---------------------------------------------------------------------------

/** Hex sha256 of a UTF-8 string, via WebCrypto (present in Workers). */
export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

// ---------------------------------------------------------------------------
// Parse
// ---------------------------------------------------------------------------

export type SpecParseResult =
  { ok: true; doc: SpecDocument } | { ok: false; errors: readonly string[] }

/**
 * Parse the vault document from raw text. External input, so every field is
 * checked rather than cast — this text comes back from R2, where a previous
 * writer (or a hand edit) may have left any shape at all.
 *
 * A declared `sha256` in the parsed input is retained ONLY so the reader can
 * round-trip an untouched class; it is recomputed before any write.
 */
export function parseSpecDocument(text: string): SpecParseResult {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch (err) {
    return { ok: false, errors: [`not valid JSON: ${(err as Error).message}`] }
  }
  if (!isRecord(raw)) return { ok: false, errors: ['document must be a JSON object'] }
  if (raw['schema_version'] !== SPEC_SCHEMA_VERSION) {
    return {
      ok: false,
      errors: [
        `schema_version must be ${SPEC_SCHEMA_VERSION}; got ${String(raw['schema_version'])}`,
      ],
    }
  }
  const classes = raw['classes']
  if (!isRecord(classes)) {
    return { ok: false, errors: ['classes must be an object keyed by class slug'] }
  }

  const errors: string[] = []
  const out: SpecDocument['classes'] = {}
  for (const [slug, entry] of Object.entries(classes)) {
    if (!CLASS_SLUG_PATTERN.test(slug) || slug.length > 64) {
      errors.push(`classes.${slug}: class slug must match [a-z0-9_-] and be at most 64 characters`)
      continue
    }
    if (!isRecord(entry)) {
      errors.push(`classes.${slug}: must be an object`)
      continue
    }
    const parsed = parseClassEntry(slug, entry, errors)
    if (Object.keys(parsed).length > 0) out[slug] = parsed
  }
  if (errors.length > 0) return { ok: false, errors }
  return { ok: true, doc: { schema_version: SPEC_SCHEMA_VERSION, classes: out } }
}

function parseClassEntry(
  slug: string,
  entry: Record<string, unknown>,
  errors: string[]
): Partial<Record<SpecProperty, SpecBody>> {
  const parsed: Partial<Record<SpecProperty, SpecBody>> = {}
  for (const prop of SPEC_PROPERTIES) {
    const value = entry[prop]
    if (value === undefined || value === null) continue
    if (!isRecord(value)) {
      errors.push(`classes.${slug}.${prop}: must be an object with body and sha256`)
      continue
    }
    const body = value['body']
    const declared = value['sha256']
    if (typeof body !== 'string' || body.trim().length === 0) {
      errors.push(`classes.${slug}.${prop}.body: must be a non-empty string`)
      continue
    }
    if (typeof declared !== 'string' || declared.trim().length === 0) {
      errors.push(`classes.${slug}.${prop}.sha256: must be a hex sha256 string`)
      continue
    }
    const stored: SpecBody = { body, sha256: declared.trim().toLowerCase() }
    // Assertions on a non-format property are refused rather than dropped: a
    // stored rule this surface will not render is a rule the client cannot see
    // and cannot have chosen. See `assertionsApplyTo`.
    if (value['assertions'] !== undefined && !assertionsApplyTo(prop)) {
      errors.push(`classes.${slug}.${prop}.assertions: only the format property carries rules`)
      continue
    }
    const rules = parseAssertions(value['assertions'], `classes.${slug}.${prop}.assertions`, errors)
    if (rules !== null) stored.assertions = rules
    parsed[prop] = stored
  }
  return parsed
}

// ---------------------------------------------------------------------------
// Build
// ---------------------------------------------------------------------------

/**
 * Read one body per DECLARED class × property out of a submitted form.
 *
 * The iteration is over the declared classes, never over the form's own keys.
 * That is the security property: a hand-crafted POST naming a class the
 * engagement never declared contributes nothing, because nothing here ever
 * looks for it. An absent field and a whitespace-only field both mean "no spec
 * here" — the builder drops them and the seat's applier prunes the installed
 * file on its next cycle.
 */
export function collectAuthoredBodies(form: FormData, classes: readonly string[]): AuthoredBody[] {
  const bodies: AuthoredBody[] = []
  for (const outputClass of classes) {
    for (const property of SPEC_PROPERTIES) {
      const raw = form.get(specFieldName(outputClass, property))
      if (typeof raw !== 'string') continue
      bodies.push({ outputClass, property, body: raw })
    }
  }
  return bodies
}

/** One authored body on its way in, before this module has hashed it. */
export interface AuthoredBody {
  outputClass: string
  property: SpecProperty
  body: string
}

/**
 * Why a build was refused, as a value rather than a string to match on.
 *
 * The caller turns this into what a client reads, and the three reasons need
 * three different sentences: one is "shorten it", one is "this surface cannot
 * do that", and one is a defect they cannot act on. A caller that had to
 * pattern-match `errors` to tell them apart would show the wrong one the first
 * time an error string was reworded.
 */
export type SpecBuildFailure = 'body_too_long' | 'invalid_class' | 'no_bodies'

export type SpecBuildResult =
  | { ok: true; doc: SpecDocument }
  | { ok: false; reason: SpecBuildFailure; errors: readonly string[] }

/**
 * Build the document to write, hashing every body server-side.
 *
 * THE SERVER HOLDS THE CEILING. The authoring form carries a `maxlength`, but a
 * browser attribute is a convenience, not an invariant — it is absent from a
 * hand-crafted POST and it counts LF-normalised characters while the wire
 * carries CRLF. The check that matters is this one, over the exact bytes about
 * to be written, after line endings are normalised and the body trimmed.
 *
 * Bodies are trimmed and an empty one means "no spec for this property" — that
 * is how a class property is cleared, and the applier prunes the installed
 * file on the next cycle. A document that ends up with no bodies at all is
 * refused here; see the fail-closed note in the header.
 */
export async function buildSpecDocument(
  bodies: readonly AuthoredBody[],
  assertionsByClass: ReadonlyMap<string, Assertions> = new Map()
): Promise<SpecBuildResult> {
  const errors: string[] = []
  let invalidClass = false
  let tooLong = false
  const classes: SpecDocument['classes'] = {}
  const encoder = new TextEncoder()

  for (const authored of bodies) {
    const slug = authored.outputClass
    if (!CLASS_SLUG_PATTERN.test(slug) || slug.length > 64) {
      errors.push(`${slug}: class slug must match [a-z0-9_-] and be at most 64 characters`)
      invalidClass = true
      continue
    }
    const body = normalizeLineEndings(authored.body).trim()
    if (body.length === 0) continue
    const byteLength = encoder.encode(body).length
    if (byteLength > MAX_SPEC_BODY_BYTES) {
      errors.push(
        `${slug}.${authored.property}: ${byteLength} bytes exceeds the ${MAX_SPEC_BODY_BYTES}-byte ceiling`
      )
      tooLong = true
      continue
    }
    const entry = classes[slug] ?? {}
    const stored: SpecBody = { body, sha256: await sha256Hex(body) }
    // Rules attach to the format body and to nothing else. A class whose format
    // prose is blank stores no rules either: the seat would install no format
    // file, so the manifest would carry no entry for the checker to read them
    // from, and a rule stored with nowhere to live is one the client believes
    // is enforced while nothing checks it.
    if (assertionsApplyTo(authored.property)) {
      const rules = assertionsByClass.get(slug)
      if (rules !== undefined && Object.keys(rules).length > 0) stored.assertions = rules
    }
    entry[authored.property] = stored
    classes[slug] = entry
  }

  // A bad class slug outranks an over-long body: the first is a defect the
  // client cannot act on and must not be told to shorten something for.
  if (invalidClass) return { ok: false, reason: 'invalid_class', errors }
  if (tooLong) return { ok: false, reason: 'body_too_long', errors }
  if (countBodies(classes) === 0) {
    return {
      ok: false,
      reason: 'no_bodies',
      errors: [
        'the document would declare no spec bodies. A seat refuses an empty document and keeps ' +
          'the specs it already installed, so writing one would leave the portal and the ' +
          'Operator disagreeing. Removing the last authored spec is not something this surface can do.',
      ],
    }
  }
  return { ok: true, doc: { schema_version: SPEC_SCHEMA_VERSION, classes } }
}

export function countBodies(classes: SpecDocument['classes']): number {
  let n = 0
  for (const entry of Object.values(classes)) {
    for (const prop of SPEC_PROPERTIES) if (entry[prop]) n++
  }
  return n
}

/** Serialize deterministically: class slugs sorted, properties in declared order. */
export function serializeSpecDocument(doc: SpecDocument): string {
  const classes: SpecDocument['classes'] = {}
  for (const slug of Object.keys(doc.classes).sort()) {
    const entry = doc.classes[slug]
    if (!entry) continue
    const ordered: Partial<Record<SpecProperty, SpecBody>> = {}
    for (const prop of SPEC_PROPERTIES) {
      const value = entry[prop]
      if (value) ordered[prop] = value
    }
    classes[slug] = ordered
  }
  return `${JSON.stringify({ schema_version: doc.schema_version, classes }, null, 2)}\n`
}

// ---------------------------------------------------------------------------
// Read / write
// ---------------------------------------------------------------------------

export type SpecReadResult =
  | { kind: 'absent' }
  | { kind: 'document'; doc: SpecDocument }
  | { kind: 'unreadable'; errors: readonly string[] }

/**
 * Read the customer's authored specs.
 *
 * `absent` is the ORDINARY state of a seat whose customer has authored
 * nothing, and is deliberately distinct from `unreadable`: the first renders an
 * empty authoring form, the second must not, because overwriting a document we
 * could not parse would destroy authored prose we failed to display.
 */
export async function readSpecDocument(bucket: R2Bucket, slug: string): Promise<SpecReadResult> {
  const object = await bucket.get(specObjectKey(slug))
  if (object === null) return { kind: 'absent' }
  const parsed = parseSpecDocument(await object.text())
  if (!parsed.ok) return { kind: 'unreadable', errors: parsed.errors }
  return { kind: 'document', doc: parsed.doc }
}

export type SpecWriteResult =
  { ok: true; key: string; bodies: number } | { ok: false; errors: readonly string[] }

/**
 * Write the document and PROVE the write landed by reading the object back and
 * comparing bytes.
 *
 * The read-back is the same proof the git→R2 publisher takes, and for the same
 * reason: the caller reports success to a client, and a success state for a
 * write that did not happen is the defect this whole change set exists to
 * remove. A put that resolves is not an object that is there.
 */
export async function writeSpecDocument(
  bucket: R2Bucket,
  slug: string,
  doc: SpecDocument
): Promise<SpecWriteResult> {
  const key = specObjectKey(slug)
  const serialized = serializeSpecDocument(doc)
  await bucket.put(key, serialized, {
    httpMetadata: { contentType: 'application/json; charset=utf-8' },
  })

  const readback = await bucket.get(key)
  if (readback === null) {
    return { ok: false, errors: ['the object was written but is not readable back'] }
  }
  if ((await readback.text()) !== serialized) {
    return {
      ok: false,
      errors: ['the object read back does not match the bytes that were written'],
    }
  }
  return { ok: true, key, bodies: countBodies(doc.classes) }
}
