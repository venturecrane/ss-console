/**
 * The nosemgrep justification gate (scripts/nosemgrep-audit.mjs), replayed
 * against fixtures.
 *
 * The regex it replaced (security.yml, to 2026-09-25) passed every
 * rule-id-only suppression whose id ran past nineteen characters, which is
 * every real Semgrep id, so the first fixtures below are those lines. Each
 * refusal is paired with an acceptance that must keep working: a gate that
 * refused everything would pass every refusal case and be useless.
 */
import { describe, expect, it } from 'vitest'
import { auditRepo, auditText, sameLineReason } from '../scripts/nosemgrep-audit.mjs'

// The marker is assembled, never written, so the fixtures below are not
// themselves suppressions: the repo census this file runs (and security.yml)
// would otherwise flag its own test cases.
const M = ['nose', 'mgrep'].join('')
const PY_ID = 'python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected'
const JS_ID =
  'javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal'

describe('refuses a suppression with no reason', () => {
  it.each([
    ['python, own line', `x = 1\n    # ${M}: ${PY_ID}\n    urlopen(u)`],
    ['python, trailing', `x = 1\nurlopen(u)  # ${M}: ${PY_ID}`],
    ['typescript, own line', `const a = 1\n// ${M}: ${JS_ID}\njoin(a, b)`],
    ['two rule ids, no reason', `x = 1\n# ${M}: ${PY_ID},python.other.rule-id`],
    ['a bare marker', `x = 1\nfoo()  # ${M}`],
    ['a short reason', `x = 1\n# ${M}: ${PY_ID} - safe here`],
    ['a blank comment above', `#\n# ${M}: ${PY_ID}`],
    [
      'another suppression above, even a justified one',
      `# ${M}: ${PY_ID} - the first line's own reason, long enough\n# ${M}: ${PY_ID}`,
    ],
  ])('%s', (_name, text) => {
    expect(auditText(text)).toHaveLength(1)
  })
})

describe('accepts a suppression that says why', () => {
  it.each([
    ['em dash reason', `# ${M}: ${PY_ID} — the URL is the module's https constant`],
    ['hyphen reason', `# ${M}: ${PY_ID} - owner-only; the rule misreads the mode`],
    ['colon reason', `// ${M}: ${JS_ID}: static repo path, no user input reaches it`],
    ['parenthesised reason', `foo()  # ${M}: ${PY_ID} (fixed https host, path allowlisted)`],
    [
      'comment line above',
      `    # Fixed https vendor host; path allowlisted.\n    # ${M}: ${PY_ID}`,
    ],
    [
      'comment above a trailing marker',
      `// Static path, no user input.\njoin(a, b) // ${M}: ${JS_ID}`,
    ],
    ['a line with no annotation at all', 'urlopen(u)  # noqa: S310 - fixed host'],
  ])('%s', (_name, text) => {
    expect(auditText(text)).toEqual([])
  })
})

describe('reason extraction', () => {
  it('strips the ids and the separator, leaving only the prose', () => {
    expect(sameLineReason(`: ${PY_ID} — scheme is https`)).toBe('scheme is https')
    expect(sameLineReason(`: ${PY_ID}`)).toBe('')
  })

  it('reports the line number of the offending annotation', () => {
    expect(auditText(`a\nb\n# ${M}: ${PY_ID}`)).toEqual([{ line: 3, text: `# ${M}: ${PY_ID}` }])
  })
})

describe('the repository', () => {
  it('carries no unjustified suppression (the same census CI runs)', () => {
    expect(auditRepo()).toEqual([])
  })
})
