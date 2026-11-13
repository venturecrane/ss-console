/**
 * The capability-framing rule, as data plus a pure scanner (text in, findings
 * out). tests/capability-framing.test.ts reads the rep- and prospect-facing
 * trees and runs this over them.
 *
 * The Captain's ruling (2026-11-13): the Operator is SMD's configurable
 * product, and a capability is true if SMD can configure or change the product
 * to do it. Rep- and prospect-facing text never disclaims a capability by build
 * state. Build state is rep knowledge with exactly three labels: "running for a
 * client today", "built, set up per client", "configured per engagement". No
 * person at SMD reads client files; only AI agents handle them.
 *
 * The three labels are allowed anywhere. Their old forms are allowed nowhere.
 */

export interface BannedPhrase {
  /** Short name printed in a failure. */
  readonly name: string
  /** Matched per line. Every pattern here is a build-state disclaimer or an old label. */
  readonly pattern: RegExp
  /** A sentence the pattern must catch: the planted case that proves the guard can fail. */
  readonly example: string
}

export const BANNED_BUILD_STATE_PHRASES: readonly BannedPhrase[] = [
  // The old third maturity label ("specified, not built"), and its "planned"
  // twin. A capability SMD can configure is never described as unbuilt.
  {
    name: 'specified/planned, not built',
    pattern: /\b(specified|planned),? not (yet )?built\b/i,
    example: 'Matter intake for dental practices: specified, not built.',
  },
  // A bare "not built" / "not yet built" disclaimer. "Not built for an
  // industry" (src/pages/industries.astro) is about design intent, not build
  // state, so a following "for" is allowed.
  {
    name: 'not (yet) built',
    pattern: /\bnot (yet )?built\b(?! for\b)/i,
    example: 'The texting channel is not yet built.',
  },
  // The old second maturity label ("built, not yet in client use") and its
  // variants. The label for that state is now "built, set up per client".
  {
    name: 'not (yet) in client use',
    pattern: /\bnot (yet )?in client use\b/i,
    example: 'Web analytics is built, not yet in client use.',
  },
  // The old first maturity label. Its replacement is "running for a client
  // today"; "in production" as a label read as "everything else is not real".
  {
    name: 'in production (old label)',
    pattern: /\bin production\b/i,
    example: 'Only one pack is in production.',
  },
  // The old rep instruction that limited a pitch to what runs today.
  {
    name: 'show only what is in production',
    pattern: /\bshow only what\b/i,
    example: 'Show only what is in production to a prospect.',
  },
  // The old library rule that a non-running capability is never presented.
  {
    name: 'never described as available',
    pattern: /\bnever (be )?described (to a prospect )?as available\b/i,
    example: 'A built capability is never described as available.',
  },
  // The enablement doc's old upper-case labels.
  {
    name: 'BUILT, UNUSED (old label)',
    pattern: /\bbuilt, unused\b/i,
    example: '| Signed seat spec | BUILT, UNUSED |',
  },
  {
    name: 'SPECIFIED (old label)',
    pattern: /\bSPECIFIED\b/,
    example: '| Other packs | SPECIFIED |',
  },
  // "The code refuses/bans X today": a present limit of the build stated as a
  // limit of the product. A deliberate rule is stated as the rule, without
  // "today".
  {
    name: 'the code refuses/bans X today',
    pattern: /\b(the )?code (refuses|bans|blocks)\b[^.\n]*\btoday\b/i,
    example: 'The code refuses a second role per seat today.',
  },
  // "Not (yet) shipped" / "not yet available": the same disclaimer in release
  // vocabulary.
  {
    name: 'not (yet) shipped / not yet available',
    pattern: /\bnot (yet )?shipped\b|\bnot yet available\b/i,
    example: 'Team chat is not yet available.',
  },
]

/**
 * "SMD staff ... see/read/work ... files/documents": no person at SMD reads
 * client files. Checked per sentence; a sentence that negates it ("no person at
 * SMD reads client files", "SMD staff never see client files") is the ruling
 * itself and passes.
 */
export const SMD_PEOPLE_READ_FILES = {
  name: 'SMD people see/read/work client files',
  subject:
    /\b(SMD(?:'s)? (?:staff|people|employees|personnel|team members?|operators? on staff)|(?:a )?(?:person|people|someone|anyone|staff) (?:at|from) SMD)\b/i,
  verb: /\b(see|sees|seen|read|reads|work|works|open|opens|look at|looks at|review|reviews|handle|handles)\b/i,
  object: /\b(files?|documents?|records|matters?|email|mail)\b/i,
  negation: /\b(no|never|not|cannot|can't|don't|doesn't|none|nobody|neither)\b/i,
  example: 'Work SMD staff must look at stays in the ops workspace with the client documents.',
} as const

export interface Finding {
  readonly file: string
  readonly line: number
  readonly phrase: string
  readonly text: string
}

/**
 * The lines of a YAML `must_not_contain:` list: the guide's rehearsal names the
 * false claims a reply must never make, so those lines state them on purpose.
 * Returned as 0-based line indexes; only `.yaml` / `.yml` files have any.
 */
function mustNotContainLines(file: string, lines: readonly string[]): Set<number> {
  const skip = new Set<number>()
  if (!/\.ya?ml$/.test(file)) return skip
  let indent = -1
  lines.forEach((raw, i) => {
    const lead = raw.length - raw.trimStart().length
    if (/^\s*must_not_contain:\s*$/.test(raw)) {
      indent = lead
      return
    }
    if (indent < 0) return
    if (raw.trim() === '' || lead > indent || (lead === indent && raw.trimStart().startsWith('- ')))
      skip.add(i)
    else indent = -1
  })
  return skip
}

/** Every banned phrase and every unnegated "SMD people read files" sentence in `text`. */
export function scanCapabilityFraming(file: string, text: string): Finding[] {
  const findings: Finding[] = []
  const lines = text.split('\n')
  const skip = mustNotContainLines(file, lines)
  lines.forEach((raw, i) => {
    if (skip.has(i)) return
    for (const p of BANNED_BUILD_STATE_PHRASES) {
      if (p.pattern.test(raw))
        findings.push({ file, line: i + 1, phrase: p.name, text: raw.trim().slice(0, 160) })
    }
    for (const sentence of raw.split(/(?<=[.!?;])\s+/)) {
      const s = SMD_PEOPLE_READ_FILES
      if (!s.subject.test(sentence)) continue
      if (!s.verb.test(sentence) || !s.object.test(sentence)) continue
      if (s.negation.test(sentence)) continue
      findings.push({ file, line: i + 1, phrase: s.name, text: sentence.trim().slice(0, 160) })
    }
  })
  return findings
}

/** One line per finding, `file:line [phrase] text`, for the failure message. */
export function formatFindings(findings: readonly Finding[]): string[] {
  return findings.map((f) => `${f.file}:${f.line} [${f.phrase}] ${f.text}`)
}
