import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * Taint-gate refusal set — the check that keeps F4 closed.
 *
 * THE FINDING. `operator/adapter/trust_ceiling.py` carries, at its taint gate,
 * the comment "Mirrors the overlay's live pre_tool_call gate; here for parity
 * (the two cores must agree)". Nothing checked the agreement. The 2026-08-23
 * review recorded this as `trust_ceiling` claiming parity while absent from
 * `operator/contracts/overlay-pairs.json`.
 *
 * WHY ENROLLING IT IN THAT MANIFEST WOULD HAVE BEEN THE WRONG FIX. Every entry
 * in `overlay-pairs.json` is gated by a sha256 of BOTH sides — the manifest
 * enforces byte-identity between a ss-console file and its overlay twin.
 * `trust_ceiling.py` is not a byte-identical twin of anything; it is a
 * re-implementation of the same policy in a differently-shaped core. Adding it
 * to a byte-hash manifest would have produced a hash that could only be kept
 * green by hand-editing it, which is a gate that certifies whatever it is told.
 *
 * WHAT IS ACTUALLY AT RISK, and what this file pins instead. The sibling
 * `tests/operator-action-class-parity.test.ts` already pins the action-class
 * VOCABULARY across the TS/Python seam. It does not pin which subset of that
 * vocabulary the taint gate withholds. Drop `EXTERNAL_SEND_CLIENT` from the
 * gate's tuple and that suite stays green — the enum is untouched — while a
 * turn that ingested untrusted inbound content regains the ability to send to
 * a client. That is the gap the "two cores must agree" comment gestures at,
 * and it is checkable entirely in-repo.
 *
 * THE INVARIANT IS DERIVED, NOT LISTED. The expected set is computed as
 * "every ActionClass except READ and INTERNAL_WRITE" rather than written out
 * here. A hardcoded list would only catch a deletion from today's tuple; the
 * derived one also catches the future case — a NEW sensitive class added to
 * the enum and not added to the gate, which is how this drifts in practice.
 *
 * WHAT WOULD MAKE THIS FALSE (Law 12). Remove any class from the gate's tuple
 * and this goes red. Add a class to the enum without adding it to the gate and
 * this goes red. Both were confirmed by tampering before this file was trusted.
 * The parser assertions below fail loudly rather than returning an empty set,
 * because two empty sets compare equal and would make the whole file inert.
 */

const ADAPTER_PATH = resolve('operator/adapter/trust_ceiling.py')

/** Classes a tainted turn may still perform. Read is inert; internal_write is a draft. */
const ALLOWED_WHEN_TAINTED = new Set(['read', 'internal_write'])

function source(): string {
  return readFileSync(ADAPTER_PATH, 'utf-8')
}

/** Every `NAME = "value"` member of the `ActionClass` enum, as its string value. */
function actionClassValues(src: string): string[] {
  const start = src.indexOf('class ActionClass')
  expect(start, 'ActionClass enum not found — parser is stale, not the code').toBeGreaterThan(-1)
  const after = src.slice(start)
  const endRel = after.indexOf('\nclass ')
  const body = endRel > -1 ? after.slice(0, endRel) : after
  const re = /^\s+[A-Z_]+\s*=\s*"([a-z_]+)"/gm
  const out: string[] = []
  let m: RegExpExecArray | null
  while ((m = re.exec(body)) !== null) out.push(m[1])
  return out
}

/**
 * The action classes named in the taint gate's membership test.
 *
 * The gate reads:
 *
 *   if inbound_trust_class != _TRUST_CLASS_INTERNAL and action in (
 *       ActionClass.EXTERNAL_SEND,
 *       ...
 *   ):
 *
 * so the tuple is the text between that `action in (` and its closing `):`.
 * Anchoring on `_TRUST_CLASS_INTERNAL` rather than on `action in (` alone
 * matters: `enforce()` contains other membership tests, and matching the first
 * one found would silently pin the wrong tuple.
 */
function taintGateMembers(src: string): string[] {
  const anchor = src.indexOf('_TRUST_CLASS_INTERNAL and action in (')
  expect(anchor, 'taint gate not found — the gate was renamed, moved, or removed').toBeGreaterThan(
    -1
  )
  const rest = src.slice(anchor)
  const close = rest.indexOf('):')
  expect(close, 'taint gate tuple is unterminated').toBeGreaterThan(-1)
  const tuple = rest.slice(0, close)
  const re = /ActionClass\.([A-Z_]+)/g
  const out: string[] = []
  let m: RegExpExecArray | null
  while ((m = re.exec(tuple)) !== null) out.push(m[1])
  return out
}

/** `EXTERNAL_SEND_CLIENT` -> `external_send_client`. */
function toValue(member: string): string {
  return member.toLowerCase()
}

describe('operator taint gate', () => {
  it('the parser finds a real enum and a real gate', () => {
    // Law 12 on the instrument itself. Two empty sets are equal, so a parser
    // that silently found nothing would make every assertion below vacuous.
    const src = source()
    expect(actionClassValues(src).length).toBeGreaterThanOrEqual(5)
    expect(taintGateMembers(src).length).toBeGreaterThanOrEqual(5)
  })

  it('withholds every sensitive action class on a tainted turn', () => {
    const src = source()
    const all = actionClassValues(src)
    const expected = all.filter((v) => !ALLOWED_WHEN_TAINTED.has(v)).sort()
    const actual = taintGateMembers(src).map(toValue).sort()

    expect(
      actual,
      `The taint gate must withhold every action class except ${[...ALLOWED_WHEN_TAINTED].join(
        ' and '
      )}.\n` +
        `Missing from the gate: ${expected.filter((v) => !actual.includes(v)).join(', ') || '(none)'}\n` +
        `In the gate but not in the enum: ${actual.filter((v) => !expected.includes(v)).join(', ') || '(none)'}\n` +
        `A class added to ActionClass is sensitive by default — add it to the gate, or, if it is ` +
        `genuinely safe on a tainted turn, add it to ALLOWED_WHEN_TAINTED here and say why in the PR.`
    ).toEqual(expected)
  })

  it('read and internal_write stay reachable on a tainted turn', () => {
    // The other half of the same policy, asserted directly rather than left as
    // a consequence of the set arithmetic above: a gate that refused reads
    // would satisfy "withholds every sensitive class" and still be broken.
    const gate = taintGateMembers(source()).map(toValue)
    for (const allowed of ALLOWED_WHEN_TAINTED) {
      expect(gate, `${allowed} must not be withheld on a tainted turn`).not.toContain(allowed)
    }
  })

  it('the parity claim in the source names what is and is not guaranteed', () => {
    // The comment that started this finding claimed the two cores "must agree"
    // while nothing checked it. Now something does — but only for the tuple.
    // Keep the pointer in the source so the next reader learns the scope of
    // the guarantee from the file itself rather than from a review document.
    expect(source()).toMatch(/operator-taint-gate\.test\.ts/)
  })
})
