/**
 * Rework measure: the pure linker.
 *
 * Why this exists (2026-11-09, the development-process retrospective). The
 * retrospective measured rework as "a feature got any fix PR within 10 days".
 * Its review rejected that construct: 51% of merged PRs here are fixes, so
 * nearly every feature has SOME fix nearby whatever process built it, and the
 * number could not tell a better process from a worse one. The missing piece
 * was a link from each fix PR to the feature PR it actually repairs. Nothing
 * recorded that link, so this module derives it from evidence, and says how
 * strong each piece of evidence is.
 *
 * LINK STRENGTH, strongest first. Each fix links at ONE tier, its strongest:
 *
 *   explicit  The fix names the feature: `#NNNN` (or `ss#NNNN`) in its title,
 *             body or commit messages resolves to a feature PR, to a fix PR
 *             already linked to one, or to an issue a feature PR closed.
 *   blame     `git blame` at the fix's parent: the lines the fix changes were
 *             last written by a feature PR (or by a fix already linked to one).
 *   weak      Same-file overlap: a feature merged within WEAK_WINDOW_DAYS
 *             before the fix touched one of the fix's files, excluding files
 *             so widely shared that overlap with them means nothing.
 *
 * A fix with none of these is UNLINKED and is reported as residue, never
 * dropped. Every rate the report prints carries its denominator.
 *
 * DESIGN RULES, each load-bearing:
 *
 *   - PURE. No network, no git, no clock. Fetching and blaming live in
 *     scripts/rework-measure.ts, so this half is pinned by fixtures in CI.
 *   - KIND IS READ FROM THE PR, NOT INFERRED FROM CONTENT. The conventional
 *     prefix of the title, else of the branch name. Anything else is `other`.
 *   - WEAK IS NEVER MIXED INTO A STRONG NUMBER. Every aggregate is printed
 *     with and without the weak tier.
 *
 * @see scripts/rework/report.ts - aggregation and the printed report
 * @see tests/rework-link.test.ts - the fixture self-test, incl. an unlinked fix
 */

export type PrKind = 'feat' | 'fix' | 'other'
export type Strength = 'explicit' | 'blame' | 'weak'

/** One merged PR, as fetched. Every field is observed, none is inferred. */
export interface PrRecord {
  number: number
  title: string
  headRefName: string
  body: string
  createdAt: string
  mergedAt: string
  files: string[]
  /** Headline + body of each commit on the PR branch, in order. */
  commitMessages: string[]
  /** Issue numbers GitHub records this PR as closing. */
  closesIssues: number[]
  /**
   * PR number -> lines of this PR's changed (old-side) lines that `git blame`
   * at its parent attributes to that PR. Null when blame was not run or the
   * merge commit was not available locally.
   */
  blamed: Record<string, number> | null
}

export interface Snapshot {
  fetchedAt: string
  repo: string
  /** Features merged on or after this date are reported. */
  since: string
  prs: PrRecord[]
}

export interface FixLink {
  fix: number
  strength: Strength | null
  /** Feature PR numbers this fix repairs. Empty when unlinked. */
  features: number[]
}

/** Days before a fix's merge in which a same-file feature counts as weak. */
export const WEAK_WINDOW_DAYS = 14
/** A file touched by more PRs than this is shared; overlap on it is no signal. */
export const SHARED_FILE_PRS = 25
/**
 * PRs that own lines without having written them, so blame or overlap on them
 * says nothing about which feature a fix repairs. Each entry names why. Lines
 * blamed to one are dropped before shares are computed; neither tier links to
 * one. Add an entry only with a measurement, as these were:
 *
 *   3411  ADR 0095 fold: imported the overlay repo, about 209k added lines. Its
 *         history lives in the old repo, so every overlay line blames here.
 *         Before this entry it was the top "feature" in the measure (37 blame
 *         links, 2026-11-09 run), for lines it imported rather than wrote.
 *   2744  One formatter, one line length: about 7k lines rewritten for style.
 */
export const STRUCTURAL_PRS: ReadonlyMap<number, string> = new Map([
  [3411, 'ADR 0095 fold imported the overlay repo; its lines were written elsewhere'],
  [2744, 'repo-wide reformat; owns lines it only re-wrapped'],
])

/** A blamed PR owning at least this share of the fix's lines is linked. */
const BLAME_SHARE = 0.3

const STRENGTH_RANK: Record<Strength, number> = { explicit: 0, blame: 1, weak: 2 }
const DAY_MS = 86_400_000

const FIX_PREFIX = /^(fix|hotfix|revert)\b/i
const FEAT_PREFIX = /^feat\b/i

export function kindOf(pr: Pick<PrRecord, 'title' | 'headRefName'>): PrKind {
  const conventional = /^([a-z]+)(\([^)]*\))?!?:/i.exec(pr.title.trim())
  const source = conventional ? conventional[1] : pr.headRefName.split('/')[0]
  if (FEAT_PREFIX.test(source)) return 'feat'
  if (FIX_PREFIX.test(source)) return 'fix'
  return 'other'
}

/** A commit headline that records a defect caught on the branch, before merge. */
export function isFixCommit(message: string): boolean {
  return /^(fix|fixup!|hotfix)\b/i.test(message.trim())
}

/**
 * `#NNNN` and `ss#NNNN` references in a fix's own words. The squash suffix
 * ` (#self)` and the PR's own number are excluded: a PR naming itself is not
 * evidence about anything else.
 */
export function referencedNumbers(pr: PrRecord): number[] {
  const text = [pr.title.replace(/\s*\(#\d+\)\s*$/, ''), pr.body, ...pr.commitMessages].join('\n')
  const out = new Set<number>()
  for (const m of text.matchAll(/(?:^|[^\w/])(?:ss)?#(\d{3,5})\b/g)) {
    const n = Number(m[1])
    if (n !== pr.number) out.add(n)
  }
  return [...out].sort((a, b) => a - b)
}

/** Files touched by more than SHARED_FILE_PRS PRs in the snapshot. */
export function sharedFiles(prs: PrRecord[]): Set<string> {
  const counts = new Map<string, number>()
  for (const pr of prs) for (const f of pr.files) counts.set(f, (counts.get(f) ?? 0) + 1)
  return new Set([...counts].filter(([, n]) => n > SHARED_FILE_PRS).map(([f]) => f))
}

interface Context {
  byNumber: Map<number, PrRecord>
  kinds: Map<number, PrKind>
  /** issue number -> feature PRs that closed it */
  issueToFeatures: Map<number, number[]>
  shared: Set<string>
  /** fix number -> its link, filled in merge order so later fixes can chain */
  links: Map<number, FixLink>
}

function weaker(a: Strength, b: Strength): Strength {
  return STRENGTH_RANK[a] >= STRENGTH_RANK[b] ? a : b
}

interface Target {
  feature: number
  strength: Strength
}

/**
 * Resolve one PR or issue number to the features it stands for, at `tier`.
 * A feature is itself. A fix stands for the features it was linked to, at the
 * weaker of the two tiers (a chain is only as strong as its weakest link).
 */
function resolve(n: number, tier: Strength, ctx: Context, self: number): Target[] {
  if (n === self || STRUCTURAL_PRS.has(n)) return []
  const kind = ctx.kinds.get(n)
  if (kind === 'feat') return [{ feature: n, strength: tier }]
  if (kind === 'fix') {
    const link = ctx.links.get(n)
    if (!link || link.strength === null) return []
    const strength = weaker(tier, link.strength)
    return link.features.map((feature) => ({ feature, strength }))
  }
  if (kind === undefined && tier === 'explicit') {
    return (ctx.issueToFeatures.get(n) ?? [])
      .filter((f) => f !== self)
      .map((feature) => ({ feature, strength: tier }))
  }
  return []
}

function explicitTargets(fix: PrRecord, ctx: Context): Target[] {
  return referencedNumbers(fix).flatMap((n) => resolve(n, 'explicit', ctx, fix.number))
}

function blameTargets(fix: PrRecord, ctx: Context): Target[] {
  if (!fix.blamed) return []
  const entries = Object.entries(fix.blamed)
    .map(([n, lines]) => ({ n: Number(n), lines }))
    .filter((e) => e.n !== fix.number && e.lines > 0 && !STRUCTURAL_PRS.has(e.n))
  const total = entries.reduce((s, e) => s + e.lines, 0)
  if (total === 0) return []
  const top = Math.max(...entries.map((e) => e.lines))
  return entries
    .filter((e) => e.lines === top || e.lines / total >= BLAME_SHARE)
    .flatMap((e) => resolve(e.n, 'blame', ctx, fix.number))
}

function weakTargets(fix: PrRecord, ctx: Context): Target[] {
  const fixFiles = fix.files.filter((f) => !ctx.shared.has(f))
  const floor = Date.parse(fix.mergedAt) - WEAK_WINDOW_DAYS * DAY_MS
  let best: { pr: PrRecord; overlap: number } | null = null
  for (const pr of ctx.byNumber.values()) {
    if (ctx.kinds.get(pr.number) !== 'feat' || pr.number === fix.number) continue
    if (STRUCTURAL_PRS.has(pr.number)) continue
    const merged = Date.parse(pr.mergedAt)
    if (pr.mergedAt >= fix.mergedAt || merged < floor) continue
    const overlap = fixFiles.filter((f) => pr.files.includes(f)).length
    if (overlap === 0) continue
    const better =
      !best ||
      overlap > best.overlap ||
      (overlap === best.overlap && pr.mergedAt > best.pr.mergedAt)
    if (better) best = { pr, overlap }
  }
  return best ? [{ feature: best.pr.number, strength: 'weak' }] : []
}

/** The strongest tier present wins; its features are the link. */
function strongest(targets: Target[], fix: number): FixLink {
  if (targets.length === 0) return { fix, strength: null, features: [] }
  const best = targets.reduce(
    (a, t) => (STRENGTH_RANK[t.strength] < STRENGTH_RANK[a] ? t.strength : a),
    targets[0].strength
  )
  const features = [...new Set(targets.filter((t) => t.strength === best).map((t) => t.feature))]
  return { fix, strength: best, features: features.sort((a, b) => a - b) }
}

function buildContext(prs: PrRecord[]): Context {
  const byNumber = new Map(prs.map((p) => [p.number, p]))
  const kinds = new Map(prs.map((p) => [p.number, kindOf(p)]))
  const issueToFeatures = new Map<number, number[]>()
  for (const pr of prs) {
    if (kinds.get(pr.number) !== 'feat') continue
    for (const issue of pr.closesIssues) {
      issueToFeatures.set(issue, [...(issueToFeatures.get(issue) ?? []), pr.number])
    }
  }
  return { byNumber, kinds, issueToFeatures, shared: sharedFiles(prs), links: new Map() }
}

/**
 * Link every fix PR in the snapshot, in merge order. Order matters: a fix that
 * repairs an earlier fix inherits that fix's features, so the earlier one must
 * already be linked.
 */
export function linkFixes(prs: PrRecord[]): { links: FixLink[]; kinds: Map<number, PrKind> } {
  const ctx = buildContext(prs)
  const fixes = prs
    .filter((p) => ctx.kinds.get(p.number) === 'fix')
    .sort((a, b) => a.mergedAt.localeCompare(b.mergedAt) || a.number - b.number)
  for (const fix of fixes) {
    let targets = explicitTargets(fix, ctx)
    if (targets.length === 0) targets = blameTargets(fix, ctx)
    if (targets.length === 0) targets = weakTargets(fix, ctx)
    ctx.links.set(fix.number, strongest(targets, fix.number))
  }
  return { links: [...ctx.links.values()], kinds: ctx.kinds }
}

/**
 * One line of `~/.claude/ss-board/built-by.jsonl`, written by the PostToolUse
 * hook `.claude/hooks/bash-log.mjs` when `gh pr create` succeeds. Each flag
 * was read from the session's transcripts at that moment, not from anyone's
 * account of the work. It is the per-PR process record the retrospective
 * found missing: the --sessions map is built after the fact, by hand, outside
 * this repo.
 */
export interface BuiltByRecord {
  pr: number
  repo: string | null
  session_id: string | null
  auto_build: boolean
  wired: boolean
  plan: boolean
  critique: boolean
}

/**
 * Parse built-by.jsonl. A line that is not a JSON object with an integer `pr`
 * is skipped and counted, never cast: a log written by a hook across versions
 * must not take the measure down, and must not hide what it dropped.
 */
export function parseBuiltBy(text: string): { records: BuiltByRecord[]; skipped: number } {
  const records: BuiltByRecord[] = []
  let skipped = 0
  for (const line of text.split('\n')) {
    if (!line.trim()) continue
    let o: unknown
    try {
      o = JSON.parse(line)
    } catch {
      skipped++
      continue
    }
    if (typeof o !== 'object' || o === null || Array.isArray(o)) {
      skipped++
      continue
    }
    const r = o as Record<string, unknown>
    if (typeof r.pr !== 'number' || !Number.isInteger(r.pr)) {
      skipped++
      continue
    }
    records.push({
      pr: r.pr,
      repo: typeof r.repo === 'string' ? r.repo : null,
      session_id: typeof r.session_id === 'string' ? r.session_id : null,
      auto_build: r.auto_build === true,
      wired: r.wired === true,
      plan: r.plan === true,
      critique: r.critique === true,
    })
  }
  return { records, skipped }
}
