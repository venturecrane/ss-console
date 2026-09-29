/**
 * Regression guard: a shipped SKILL.md must not cite a runtime path the image
 * does not carry.
 *
 * The incident (2026-07-30, ss-console #2083, vfy_01KYTXF76E1QHWQ2FQ9CYRGQBV):
 * `operator/templates/drafting/` was absent from the Dockerfile COPY set. A
 * whole-filesystem `find` on the live seat hermes-pilot-smokeball returned
 * neither `drafting-discipline.md` nor `drafting_gate_check.py`. Four
 * work-product skills assert the discipline is "loaded verbatim into every
 * drafting run", and the discipline itself says no draft reaches the attorney
 * without passing the mechanical checker. Neither file was on any seat. The
 * skills were right about what they needed and the image simply did not have it.
 *
 * Adding the COPY closes that instance. This file closes the class, which is the
 * actual deliverable: shipping the two files and calling it done is a `built`
 * claim, and the plan this came from turns entirely on `built` and `wired` being
 * different claims (CLAUDE.md, "Done means the client can do it").
 *
 * Two properties, both derived from the Dockerfile rather than a hardcoded list,
 * so the guard cannot rot the moment someone adds a COPY:
 *
 *   1. PRESENCE. Every repo-relative path cited by a shipped skill is either
 *      inside the COPY set or classified in CITED_TREE_POLICY as authoring canon
 *      with the reason written down. An unclassified tree fails loudly, which
 *      forces the decision to be made by a person rather than by omission.
 *   2. RESOLVABILITY. For a tree the agent actually reads or executes, the COPY
 *      destination must make the CITED path resolve from the container WORKDIR.
 *      Presence at some other path is what a `COPY .../drafting/ /app/drafting/`
 *      would have bought: both files in the image, every citation still dangling.
 *
 * @see operator/templates/Dockerfile
 * @see tests/operator-dockerfile.test.ts
 * @see operator/templates/drafting/drafting-discipline.md
 */

import { describe, it, expect, afterAll } from 'vitest'
import {
  readFileSync,
  existsSync,
  readdirSync,
  statSync,
  mkdtempSync,
  mkdirSync,
  copyFileSync,
  rmSync,
} from 'fs'
import { resolve, join, basename, dirname } from 'path'
import { tmpdir } from 'os'
import { execFileSync } from 'child_process'

const DOCKERFILE_PATH = 'operator/templates/Dockerfile'
const DOCKERFILE = readFileSync(resolve(DOCKERFILE_PATH), 'utf8')

// ---------------------------------------------------------------------------
// Dockerfile parsing: the shipped set is whatever this file says it is.
// ---------------------------------------------------------------------------

interface CopyDirective {
  readonly src: string
  readonly dest: string
  readonly line: number
}

/**
 * COPY directives that read from the BUILD CONTEXT (the repo root). `--from=`
 * stages copy between images, not from the repo, so they ship nothing this
 * guard reasons about.
 */
function parseBuildContextCopies(dockerfile: string): CopyDirective[] {
  const out: CopyDirective[] = []
  dockerfile.split('\n').forEach((raw, i) => {
    if (!/^COPY\s/.test(raw)) return
    if (/\\\s*$/.test(raw)) {
      // A continued COPY would be read half-way and silently under-report the
      // shipped set, which is the failure mode this whole file exists to stop.
      throw new Error(
        `${DOCKERFILE_PATH}:${i + 1}: line-continued COPY is not parsed by ` +
          `tests/shipped-runtime-paths.test.ts. Write it on one line or teach ` +
          `the parser, but do not leave the shipped set half-read.`
      )
    }
    const tokens = raw.trim().split(/\s+/).slice(1)
    const args = tokens.filter((t) => !t.startsWith('--'))
    if (tokens.some((t) => t.startsWith('--from='))) return
    if (args.length < 2) return
    const dest = args[args.length - 1]
    for (const src of args.slice(0, -1)) out.push({ src, dest, line: i + 1 })
  })
  return out
}

const COPIES = parseBuildContextCopies(DOCKERFILE)

/** The last WORKDIR wins; it is the CWD the entrypoint chain inherits. */
function finalWorkdir(dockerfile: string): string {
  const matches = [...dockerfile.matchAll(/^WORKDIR\s+(\S+)\s*$/gm)]
  if (matches.length === 0) throw new Error(`${DOCKERFILE_PATH}: no WORKDIR found`)
  return matches[matches.length - 1][1]
}

const WORKDIR = finalWorkdir(DOCKERFILE)

const posixJoin = (...parts: string[]): string => parts.join('/').replace(/\/{2,}/g, '/')

/**
 * Where a repo path lands in the image under a given COPY, or null if that COPY
 * does not carry it. Follows Docker's semantics: `COPY src/ dest/` copies the
 * CONTENTS of src into dest, so the `src/` prefix is stripped, not preserved.
 */
function containerPathFor(repoPath: string, copy: CopyDirective): string | null {
  if (copy.src.endsWith('/')) {
    if (!repoPath.startsWith(copy.src)) return null
    return posixJoin(copy.dest, repoPath.slice(copy.src.length))
  }
  if (repoPath !== copy.src) return null
  return copy.dest.endsWith('/') ? posixJoin(copy.dest, basename(copy.src)) : copy.dest
}

const containerPaths = (repoPath: string): string[] =>
  COPIES.map((c) => containerPathFor(repoPath, c)).filter((p): p is string => p !== null)

// ---------------------------------------------------------------------------
// The runtime surface of a skill.
// ---------------------------------------------------------------------------

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal -- dir is a repo path from the Dockerfile COPY set; entry is readdirSync output, not user input.
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) walk(full, out)
    else out.push(full)
  }
  return out
}

/**
 * SKILL.md and its `references/*.md` only. A skill's `tests/` dir ships too (the
 * COPY takes the whole tree) but it is an AUTHORING artifact: it cites graded
 * fixtures and canonical sources for a human running a grading pass in the repo,
 * never a path the agent opens on a seat. Scanning it would drag the fixture and
 * grading trees into a guard about what the agent can reach at runtime.
 */
function shippedSkillDocs(): string[] {
  const skillCopy = COPIES.find((c) => c.src === 'operator/skills/')
  expect(skillCopy, 'the Dockerfile must still ship operator/skills/').toBeDefined()
  return walk(skillCopy!.src.replace(/\/$/, ''))
    .filter((p) => p.endsWith('/SKILL.md') || /\/references\/[^/]+\.md$/.test(p))
    .sort()
}

const SKILL_DOCS = shippedSkillDocs()

/**
 * Repo-relative citations, e.g. `operator/templates/drafting/drafting_gate_check.py`.
 * The lookbehind keeps the match anchored at a path boundary so a longer path
 * that merely contains the token is not split.
 */
const REPO_CITATION = /(?<![\w./-])operator\/[A-Za-z0-9_./-]*[A-Za-z0-9_-]/g

/** Skill-relative citations, e.g. `references/output-format.md`. */
const SKILL_RELATIVE_CITATION = /(?<![\w./-])references\/[A-Za-z0-9_.-]+\.[A-Za-z0-9]+/g

interface Citation {
  readonly path: string
  readonly citedBy: string
  readonly line: number
}

function collectCitations(re: RegExp, mapPath: (raw: string, file: string) => string): Citation[] {
  const out: Citation[] = []
  for (const file of SKILL_DOCS) {
    readFileSync(file, 'utf8')
      .split('\n')
      .forEach((text, i) => {
        for (const raw of text.match(re) ?? []) {
          out.push({ path: mapPath(raw, file), citedBy: file, line: i + 1 })
        }
      })
  }
  return out
}

const REPO_CITATIONS = collectCitations(REPO_CITATION, (raw) => raw.replace(/\/+$/, ''))

const SKILL_RELATIVE_CITATIONS = collectCitations(SKILL_RELATIVE_CITATION, (raw, file) =>
  posixJoin(file.split('/').slice(0, 3).join('/'), raw)
)

// ---------------------------------------------------------------------------
// Citation roles.
// ---------------------------------------------------------------------------

/**
 * `runtime-read` - the skill tells the agent to open or execute this path on a
 *   seat. It must be in the image AND must resolve at the cited spelling.
 *
 * `authoring-canon` - the path is a source-of-truth pointer. The rule it holds
 *   is duplicated into the SKILL.md body, so the agent never opens the file; the
 *   citation exists so a human editing the skill knows where the rule is owned.
 *   These are deliberately NOT shipped, and are not required to exist on disk at
 *   all: several point at another repo (client material moved to
 *   venturecrane/engagements, ADR 0081) or at fixtures not yet generated.
 *
 * A tree with no entry here fails the guard. That is the point: the choice
 * between the two roles is a judgment about what the agent can reach, and it
 * should be made deliberately, once, in writing, rather than inferred from
 * whichever verb the sentence happened to use.
 */
type CitationRole = 'runtime-read' | 'authoring-canon'

interface TreePolicy {
  readonly prefix: string
  readonly role: CitationRole
  readonly reason: string
}

const CITED_TREE_POLICY: readonly TreePolicy[] = [
  {
    prefix: 'operator/templates/drafting/',
    role: 'runtime-read',
    reason:
      'The four work-product drafters load drafting-discipline.md VERBATIM into every drafting ' +
      'run and invoke drafting_gate_check.py by its literal relative path; the skeletons are the ' +
      'fallback shells they render from. This is the tree #2083 found missing from every seat.',
  },
  {
    prefix: 'operator/verticals/',
    role: 'authoring-canon',
    reason:
      'Deliberate, and documented at operator/verticals/law-firm/addons/pi/references/' +
      '_shared-delivery-channels.md: the pack duplicates each shared rule into every SKILL.md ' +
      'body BECAUSE only operator/skills/ ships. The citation names where the rule is owned so ' +
      'the two copies stay in step. Shipping this tree instead would not be a fix, it would ' +
      'reverse a design decision.',
  },
  {
    prefix: 'operator/references/',
    role: 'authoring-canon',
    reason:
      'send-posture.md is the one source every skill DEFERS to (ADR 0025/0035). Each citing ' +
      'skill states its own authored ceiling inline and then points here, so the runtime rule ' +
      'is already in the SKILL.md. The doc itself links out with repo-relative ../../docs/adr ' +
      'paths, which only resolve in the repo.',
  },
  {
    prefix: 'operator/fixtures/',
    role: 'authoring-canon',
    reason:
      'Graded input/expected pairs, cited only from references/test-cases.md. They are read by a ' +
      'human or a grading harness in the repo. An agent on a seat that read its own frozen ' +
      'expected output would be grading itself.',
  },
  {
    prefix: 'operator/grading/',
    role: 'authoring-canon',
    reason: 'Rubrics and archived grading runs. Repo-side evaluation artifacts, never seat input.',
  },
  {
    prefix: 'operator/workspace_broker/',
    role: 'authoring-canon',
    reason:
      'Cited as "canonical at" / "byte-identical to" for escalation_ledger.py, whose working copy ' +
      'ships INSIDE the skill dir. Provenance for a twin-file sync, not a path to read. The same ' +
      'stamped-copy pattern as operator/templates/pre_run_gate.py. This tree DOES ship, but to ' +
      '/opt/workspace-broker/ for the broker uid, which is not where the citation points and is ' +
      'not a path the agent may read.',
  },
  {
    prefix: 'operator/customers/',
    role: 'authoring-canon',
    reason:
      'Per-engagement material. It lives in venturecrane/engagements (ADR 0081), so these ' +
      'citations are cross-repo pointers and several do not resolve in this tree by design. A ' +
      'seat reads its engagement config from the volume, never from the image.',
  },
]

function policyFor(citedPath: string): TreePolicy | undefined {
  return CITED_TREE_POLICY.filter((p) => citedPath.startsWith(p.prefix)).sort(
    (a, b) => b.prefix.length - a.prefix.length
  )[0]
}

const describeCite = (c: Citation): string => `${c.citedBy}:${c.line} cites ${c.path}`

/**
 * Skill-relative citations with no file behind them. Every path here is built
 * from a repo file's own text plus the repo dir it was found in, never from
 * caller input.
 */
function missingSkillReferences(): Set<string> {
  const out = new Set<string>()
  for (const c of SKILL_RELATIVE_CITATIONS) {
    // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal -- c.path is derived from a checked-in skill doc under operator/skills, not from user input.
    if (!existsSync(resolve(c.path))) out.add(c.path)
  }
  return out
}

// ---------------------------------------------------------------------------

describe('shipped skills cite only runtime paths the image carries', () => {
  it('classifies every repo tree a shipped skill cites', () => {
    // An unclassified tree is the open end of the guard. Failing here is the
    // mechanism that stops a new citation from inheriting a waiver it was never
    // weighed against.
    const unclassified = REPO_CITATIONS.filter((c) => !policyFor(c.path))
    expect(
      unclassified.map(describeCite).sort(),
      'Add an entry to CITED_TREE_POLICY. runtime-read means the agent opens or ' +
        'executes it on a seat, so it must ship at the cited spelling; ' +
        'authoring-canon means the rule is duplicated into the SKILL.md and the ' +
        'citation only records where it is owned. Write the reason down.'
    ).toEqual([])
  })

  it('ships every runtime-read path, at the spelling the skill cites', () => {
    // The #2083 assertion. `present in the image` is necessary and not
    // sufficient: the skills invoke `python3 operator/templates/drafting/
    // drafting_gate_check.py`, so a copy parked at /app/drafting/ resolves to
    // nothing. Both halves are checked against the Dockerfile, so re-pointing a
    // COPY breaks this test rather than the seat.
    const failures: string[] = []
    const runtimeReads = new Map<string, Citation>()
    for (const c of REPO_CITATIONS) {
      if (policyFor(c.path)?.role !== 'runtime-read') continue
      if (!runtimeReads.has(c.path)) runtimeReads.set(c.path, c)
    }

    for (const [path, cite] of [...runtimeReads].sort()) {
      if (!existsSync(resolve(path))) {
        failures.push(`${describeCite(cite)} - no such file in this repo`)
        continue
      }
      const landings = containerPaths(path)
      if (landings.length === 0) {
        failures.push(
          `${describeCite(cite)} - not in the ${DOCKERFILE_PATH} COPY set, so it is on no seat`
        )
        continue
      }
      const expectedAtWorkdir = posixJoin(WORKDIR, path)
      if (!landings.includes(expectedAtWorkdir)) {
        failures.push(
          `${describeCite(cite)} - lands at ${landings.join(', ')} but the skill spells it ` +
            `relative, and WORKDIR is ${WORKDIR}, so it must also land at ${expectedAtWorkdir}`
        )
      }
    }

    expect(failures, 'a runtime-read citation must resolve on the seat').toEqual([])
  })

  it('keeps the authoring-canon waivers honest', () => {
    // A waiver whose premise has quietly changed is worse than no waiver: it
    // reads as a considered decision while describing a world that no longer
    // exists. The premise being checked is not "this tree ships nowhere" but the
    // narrower and truer one: it does not land AT THE CITED SPELLING. The
    // distinction is load-bearing. operator/workspace_broker/ ships to
    // /opt/workspace-broker/ for the broker uid, which leaves its citations
    // provenance; a COPY landing a waived tree under WORKDIR would turn every
    // citation of it into a resolvable read and the role would need re-deciding.
    const probe = '__waiver_probe__'
    const landsAtCitedSpelling = CITED_TREE_POLICY.filter(
      (p) =>
        p.role === 'authoring-canon' &&
        containerPaths(p.prefix + probe).includes(posixJoin(WORKDIR, p.prefix + probe))
    ).map((p) => p.prefix)
    expect(
      landsAtCitedSpelling,
      'These trees are waived because a citation of them resolves to nothing on a ' +
        'seat. They now land where the citation points. Re-decide the role rather ' +
        'than leaving a stale reason in place.'
    ).toEqual([])

    const deliveryChannels =
      'operator/verticals/law-firm/addons/pi/references/_shared-delivery-channels.md'
    expect(
      readFileSync(resolve(deliveryChannels), 'utf8'),
      `${deliveryChannels} is the written basis for the operator/verticals/ waiver`
    ).toContain('only `operator/skills/` ships to the Machine image')
  })

  it('has no new skill-relative reference citation without a file', () => {
    // A skill pointing at its own `references/<file>` that does not exist is the
    // same class one layer down: the SKILL.md instructs a read of something the
    // image cannot carry because nothing authored it. The pre-existing gaps are
    // frozen in KNOWN_MISSING_SKILL_REFERENCES below; this assertion is the
    // ratchet that stops the list from growing.
    const missing = [...missingSkillReferences()].sort()
    const known = new Set(KNOWN_MISSING_SKILL_REFERENCES)
    expect(
      missing.filter((p) => !known.has(p)),
      'Author the reference file, or fix the citation. Do not extend the frozen ' +
        'list: it is a record of debt that predates the guard, not a place to put more.'
    ).toEqual([])
  })

  it('has no stale entry in the frozen missing-reference list', () => {
    // The other half of the ratchet, so the list can only ever shrink and always
    // reads as true.
    //
    // Note the two ways an entry goes stale, because the remedy is the same but
    // the cause is not: the reference was AUTHORED, or the CITATION was fixed to
    // point where the file actually lives. The first three entries retired here
    // were the second kind — three skills spelled the pack's
    // _shared-training-output.md as `references/...`, as if it had been copied
    // down into their own dir. Nothing was written; a path was corrected.
    const stillMissing = missingSkillReferences()
    expect(
      KNOWN_MISSING_SKILL_REFERENCES.filter((p) => !stillMissing.has(p)),
      'These entries no longer describe an unresolved citation - either the file was ' +
        'authored or the citation was corrected. Remove them from ' +
        'KNOWN_MISSING_SKILL_REFERENCES.'
    ).toEqual([])
  })
})

/**
 * Skill-relative `references/<file>` citations with no file behind them, as
 * found when this guard was written (ss-console #2083, 2026-07-30).
 *
 * These are AUTHORING gaps, not plumbing: the fix for each is to write the
 * reference the SKILL.md already promises, or to drop the promise. #2083 named
 * six skills missing `references/output-format.md`; the guard found twelve, plus
 * a wider set of missing rubric/voice/test-case references, plus three skills
 * pointing at a `references/_shared-training-output.md` that lives in the
 * verticals pack and was never copied down. The list is deliberately literal so
 * the debt is greppable and shrinks one line at a time.
 */
const KNOWN_MISSING_SKILL_REFERENCES: readonly string[] = [
  'operator/skills/client-matter-digest/references/output-format.md',
  'operator/skills/client-matter-digest/references/test-cases.md',
  'operator/skills/client-matter-digest/references/voice.md',
  'operator/skills/conflict-intake-router/references/output-format.md',
  'operator/skills/conflict-intake-router/references/test-cases.md',
  'operator/skills/document-receipt-logger/references/output-format.md',
  'operator/skills/document-receipt-logger/references/test-cases.md',
  'operator/skills/intake-to-system-sync/references/output-format.md',
  'operator/skills/intake-to-system-sync/references/test-cases.md',
  'operator/skills/matter-inbox-router/references/output-format.md',
  'operator/skills/matter-inbox-router/references/test-cases.md',
  'operator/skills/matter-status-digest/references/output-format.md',
  'operator/skills/matter-status-digest/references/test-cases.md',
  'operator/skills/referral-source-acknowledgment/references/output-format.md',
  'operator/skills/referral-source-acknowledgment/references/test-cases.md',
  'operator/skills/referral-source-acknowledgment/references/voice.md',
]

// ===========================================================================
// The distillation compilers (ADR 0085 §4).
// ===========================================================================

/**
 * The compilers are shipped for a consumer the guard above cannot see. Every
 * assertion up to here starts from a SKILL.md citation — the agent is the
 * reader, WORKDIR is the frame. These five files have no skill citation and
 * never will: the reader is the overlay's establishment-intake daemon, running
 * as root, invoking them by ABSOLUTE path.
 *
 * That makes the failure mode different in kind, and worse. A skill citing a
 * missing path produces an agent that cannot find its instructions, which is
 * loud. A missing compiler produces a gate that did not run — and the intake's
 * own docstring is explicit about why that is the dangerous one: "a gate that
 * silently did not run reads exactly like a gate that passed" (Law 12). The
 * daemon's `missing_compilers` probe is the runtime half of that defence and
 * refuses the run; this is the build-time half, so the refusal is something we
 * never have to see.
 *
 * Three properties, in the order they can fail:
 *
 *   1. PIN. The container paths equal the constants in the overlay's
 *      establish_intake/gates.py. Hardcoded HERE on purpose — this is one side
 *      of a cross-repo contract, and deriving it from the Dockerfile would make
 *      the test agree with whatever the Dockerfile said, which is a check that
 *      cannot fail (Law 12).
 *   2. EXHAUSTIVE. The mirror carries exactly those five and nothing else, so a
 *      sixth compiler cannot land here without the overlay learning about it.
 *   3. FAITHFUL. The three import relationships actually resolve, proven by
 *      EXECUTING the loaders in a tree built to the Dockerfile's destinations —
 *      not by reading the COPY lines, which is how a layout gets asserted into
 *      correctness. Two false controls below prove this property can fail.
 *
 * @see operator/templates/Dockerfile (the COPY block and its rationale)
 * @see hermes-smd-overlay establish_intake/gates.py (REQUIRED_COMPILERS)
 */

const MIRROR_ROOT = '/opt/smd'

interface ShippedCompiler {
  /** Repo source. */
  readonly repo: string
  /** The constant the overlay's establish_intake/gates.py names. */
  readonly container: string
  /** Why the intake needs it, in one line. */
  readonly role: string
}

const INTAKE_COMPILER_CONSTANTS: readonly ShippedCompiler[] = [
  {
    repo: 'operator/bin/voice_profile.py',
    container: '/opt/smd/operator/bin/voice_profile.py',
    role: 'VOICE_PROFILE - the profile card; the digit invariant refuses asserted numbers.',
  },
  {
    repo: 'operator/bin/spec_fixed_strings.py',
    container: '/opt/smd/operator/bin/spec_fixed_strings.py',
    role: 'SPEC_FIXED_STRINGS - the approved fixed-string layer. Loads voice_profile as a sibling.',
  },
  {
    repo: 'operator/bin/spec_leak_check.py',
    container: '/opt/smd/operator/bin/spec_leak_check.py',
    role: 'SPEC_LEAK_CHECK - refuses client prose beyond the fixed strings. Loads the drafting gate check via parents[2].',
  },
  {
    repo: 'operator/bin/spec_selftest.py',
    container: '/opt/smd/operator/bin/spec_selftest.py',
    role: "SPEC_SELFTEST - demotes any block rule the firm's own writing violates. Loads voice_profile as a sibling.",
  },
  {
    repo: 'operator/templates/drafting/drafting_gate_check.py',
    container: '/opt/smd/operator/templates/drafting/drafting_gate_check.py',
    role: 'DRAFTING_GATE_CHECK - imported by spec_leak_check for _HELD_OUT_NGRAM, so one constant answers both containment questions.',
  },
]

/** The four the intake invokes directly; the fifth is a library for them. */
const COMPILER_ENTRYPOINTS = INTAKE_COMPILER_CONSTANTS.filter((c) =>
  c.repo.startsWith('operator/bin/')
)

const PY_IMPORT_DRIVER = [
  'import importlib.util, sys',
  'spec = importlib.util.spec_from_file_location("_probe", sys.argv[1])',
  'mod = importlib.util.module_from_spec(spec)',
  'sys.modules["_probe"] = mod',
  'spec.loader.exec_module(mod)',
].join('\n')

interface ImportResult {
  readonly ok: boolean
  readonly detail: string
}

/** Execute a module top-to-bottom in its own process. Every loader in this set
 *  runs at MODULE level, so a plain import is the whole probe. */
function importsCleanly(modulePath: string): ImportResult {
  try {
    execFileSync('python3', ['-c', PY_IMPORT_DRIVER, modulePath], {
      encoding: 'utf8',
      stdio: 'pipe',
    })
    return { ok: true, detail: '' }
  } catch (err) {
    const e = err as { stderr?: string; message?: string }
    return { ok: false, detail: (e.stderr || e.message || 'unknown failure').trim().slice(-400) }
  }
}

/** Materialize `layout` (container path -> repo source) under a fresh temp root. */
function materialize(layout: ReadonlyMap<string, string>): string {
  const root = mkdtempSync(join(tmpdir(), 'smd-compiler-layout-'))
  for (const [containerPath, repoSource] of layout) {
    // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal -- containerPath comes from this repo's Dockerfile COPY set, repoSource from the checked-in constant list; neither is user input.
    const dest = join(root, containerPath)
    mkdirSync(dirname(dest), { recursive: true })
    copyFileSync(resolve(repoSource), dest)
  }
  return root
}

/** Destinations as the DOCKERFILE states them, so the tree under test is the
 *  image's layout rather than the one this test would have preferred. */
function mirrorLayoutFromDockerfile(): Map<string, string> {
  const layout = new Map<string, string>()
  for (const c of INTAKE_COMPILER_CONSTANTS) {
    for (const landing of containerPaths(c.repo)) {
      if (landing.startsWith(MIRROR_ROOT + '/')) layout.set(landing, c.repo)
    }
  }
  return layout
}

const TEMP_ROOTS: string[] = []
const trackTemp = (root: string): string => {
  TEMP_ROOTS.push(root)
  return root
}

afterAll(() => {
  for (const root of TEMP_ROOTS) rmSync(root, { recursive: true, force: true })
})

describe('the establishment intake finds its compilers where it looks for them', () => {
  it('has python3 available to run the layout probes', () => {
    // Stated as an assertion rather than a skip. A layout suite that quietly
    // skips itself on a runner without python3 is the same defect it exists to
    // catch: silence indistinguishable from success.
    let available: boolean
    try {
      execFileSync('python3', ['-c', 'pass'], { stdio: 'pipe' })
      available = true
    } catch {
      available = false
    }
    expect(
      available,
      'python3 is required to prove the compiler layout resolves; the image installs it via apt ' +
        'and ubuntu-latest carries it. Do not weaken this to a skip.'
    ).toBe(true)
  })

  it('ships every compiler at the exact path the overlay names as a constant', () => {
    const failures: string[] = []
    for (const c of INTAKE_COMPILER_CONSTANTS) {
      if (!existsSync(resolve(c.repo))) {
        failures.push(`${c.repo} - no such file in this repo (${c.role})`)
        continue
      }
      const landings = containerPaths(c.repo)
      if (!landings.includes(c.container)) {
        failures.push(
          `${c.repo} - lands at [${landings.join(', ') || 'nowhere'}] but establish_intake/` +
            `gates.py resolves it at ${c.container}. ${c.role}`
        )
      }
    }
    expect(
      failures,
      'The overlay probes these paths before every establishment run and REFUSES the run when one ' +
        'is absent. Add or repoint the COPY in operator/templates/Dockerfile.'
    ).toEqual([])
  })

  it('ships nothing into the mirror that the overlay does not know about', () => {
    // The other direction of the same contract. A compiler added here but never
    // declared in REQUIRED_COMPILERS would ship, never be probed, and never run
    // — present on the seat and absent from the control.
    const mirrored = COPIES.filter((c) => c.dest.startsWith(MIRROR_ROOT + '/'))
      .map((c) => c.src)
      .sort()
    expect(
      mirrored,
      `Every file under ${MIRROR_ROOT} is part of the cross-repo compiler contract. Adding one ` +
        'means adding it to REQUIRED_COMPILERS in the overlay and to ' +
        'INTAKE_COMPILER_CONSTANTS here, in the same change.'
    ).toEqual(INTAKE_COMPILER_CONSTANTS.map((c) => c.repo).sort())
  })

  it('keeps the drafting gate check at BOTH paths, because two readers resolve it differently', () => {
    // The reconciliation. #2083 put drafting_gate_check.py under WORKDIR because
    // four SKILL.md bodies invoke it by a literal relative path; the intake
    // resolves the same file through spec_leak_check's parents[2]. Satisfying
    // the intake by MOVING the /app copy would silently un-fix #2083.
    const gateCheck = 'operator/templates/drafting/drafting_gate_check.py'
    const landings = containerPaths(gateCheck)
    expect(landings, 'the skill-facing copy under WORKDIR must survive (#2083)').toContain(
      posixJoin(WORKDIR, gateCheck)
    )
    expect(landings, 'the intake-facing copy under the mirror root must exist').toContain(
      `${MIRROR_ROOT}/${gateCheck}`
    )
  })

  it('resolves every loader when the shipped layout is materialized', () => {
    const layout = mirrorLayoutFromDockerfile()
    expect(layout.size, 'the Dockerfile must place all five files under the mirror root').toBe(
      INTAKE_COMPILER_CONSTANTS.length
    )

    const root = trackTemp(materialize(layout))
    const failures: string[] = []
    for (const c of COMPILER_ENTRYPOINTS) {
      const result = importsCleanly(join(root, c.container))
      if (!result.ok) failures.push(`${c.repo} failed to import: ${result.detail}`)
    }
    expect(
      failures,
      'Each of these resolves a sibling or an ancestor-relative path at MODULE level, so an ' +
        'import is the whole test. A COPY that lands the files in a flat or re-rooted layout ' +
        'passes every string assertion above and fails here.'
    ).toEqual([])
  })

  // --- False controls. --------------------------------------------------
  // Law 12: a check that cannot fail has measured nothing. The two tests below
  // deliberately break the layout the test above asserts, and would themselves
  // fail if the probe were vacuous (a python3 that no-ops, a driver that never
  // executes the module, an import with no path dependency at all).

  it('FALSE CONTROL: a flattened layout breaks the ancestor-relative gate-check import', () => {
    const flat = new Map<string, string>()
    for (const c of INTAKE_COMPILER_CONSTANTS) flat.set(`flat/${basename(c.repo)}`, c.repo)
    const root = trackTemp(materialize(flat))

    const leak = INTAKE_COMPILER_CONSTANTS.find((c) => c.repo.endsWith('spec_leak_check.py'))!
    const result = importsCleanly(join(root, `flat/${basename(leak.repo)}`))
    expect(
      result.ok,
      'spec_leak_check resolves the drafting gate check at parents[2]/operator/templates/' +
        'drafting/. If it imports from a flat dir, the probe is not exercising the path ' +
        'dependency and the positive test above proves nothing.'
    ).toBe(false)
  })

  it('FALSE CONTROL: removing the sibling voice_profile breaks the sibling loaders', () => {
    const layout = mirrorLayoutFromDockerfile()
    const withoutProfile = new Map(
      [...layout].filter(([, repoSource]) => !repoSource.endsWith('voice_profile.py'))
    )
    const root = trackTemp(materialize(withoutProfile))

    const siblingLoaders = COMPILER_ENTRYPOINTS.filter(
      (c) => c.repo.endsWith('spec_selftest.py') || c.repo.endsWith('spec_fixed_strings.py')
    )
    expect(siblingLoaders).toHaveLength(2)

    const stillImported = siblingLoaders
      .filter(
        (c) =>
          // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal -- root is mkdtempSync output; c.container is a checked-in constant in this file, not user input.
          importsCleanly(join(root, c.container)).ok
      )
      .map((c) => c.repo)
    expect(
      stillImported,
      'Both load voice_profile.py from Path(__file__).resolve().parent at module level. If ' +
        'either imports without it, it found the module some other way (an installed copy, a ' +
        'stale sys.path entry) and the sibling relationship is not what the probe measured.'
    ).toEqual([])
  })
})
