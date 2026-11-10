/**
 * Rework measure: per-feature rows, monthly aggregates, the residue, and the
 * printed report. Pure, like scripts/rework/link.ts.
 *
 * PRE-MERGE vs POST-MERGE. A defect found before the feature merged costs a
 * branch commit; one found after costs a fix PR, a merge-queue cycle and often
 * a release. So both are counted, separately:
 *
 *   pre-merge   commits on the feature's own branch whose headline starts
 *               `fix`/`fixup!`/`hotfix`, plus linked fix PRs merged BEFORE the
 *               feature merged (a fix landed for a feature still in flight).
 *   post-merge  linked fix PRs merged after the feature merged.
 *
 * First-fix lag is days from the feature's merge to its first post-merge fix,
 * strong tiers only and then with the weak tier, so the two never blend.
 */

import {
  isFixCommit,
  linkFixes,
  STRUCTURAL_PRS,
  type BuiltByRecord,
  type FixLink,
  type PrRecord,
  type Snapshot,
} from './link'

/** The process label for one PR, from its built-by records (any `true` wins). */
export function builtByLabel(records: BuiltByRecord[]): string {
  return records.some((r) => r.auto_build) ? 'auto-build' : 'other'
}

/**
 * Feature PR number -> process label. A PR with a built-by record (written by
 * the hook when the PR was opened) is labelled from it; a PR without one falls
 * back to the --sessions map; a PR in neither is left out, and measure() calls
 * it `unmapped`. Records for another repo are ignored: PR numbers are per repo.
 * Returns null when there is no source at all, so the session table is not
 * printed full of `unmapped`.
 */
export function processLabels(
  builtBy: BuiltByRecord[] | null,
  sessions: Map<number, string> | null,
  repo: string
): Map<number, string> | null {
  if (builtBy === null && sessions === null) return null
  const out = new Map<number, string>(sessions ?? [])
  const byPr = new Map<number, BuiltByRecord[]>()
  for (const r of builtBy ?? []) {
    if (r.repo !== null && r.repo !== repo) continue
    byPr.set(r.pr, [...(byPr.get(r.pr) ?? []), r])
  }
  for (const [pr, records] of byPr) out.set(pr, builtByLabel(records))
  return out
}

export interface FeatureRow {
  number: number
  title: string
  mergedAt: string
  month: string
  session: string
  post: { explicit: number; blame: number; weak: number }
  pre: { fixPrs: number; fixCommits: number }
  lagDaysStrong: number | null
  lagDaysAll: number | null
}

export interface MonthAggregate {
  key: string
  features: number
  withStrongFix: number
  withAnyFix: number
  strongFixes: number
  allFixes: number
  withPreMerge: number
  medianLagStrong: number | null
  medianLagAll: number | null
}

export interface Residue {
  fixes: number
  explicit: number
  blame: number
  weak: number
  unlinked: number
  /** Linked, but only to features merged before `since` (not reported rows). */
  toEarlierFeatures: number
  blameUnavailable: number
  otherKindPrs: number
  totalPrs: number
}

export interface ReworkMeasure {
  since: string
  fetchedAt: string
  rows: FeatureRow[]
  byMonth: MonthAggregate[]
  bySession: MonthAggregate[] | null
  residue: Residue
  links: FixLink[]
}

const DAY_MS = 86_400_000

function median(values: number[]): number | null {
  if (values.length === 0) return null
  const s = [...values].sort((a, b) => a - b)
  const mid = Math.floor(s.length / 2)
  const m = s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2
  return Math.round(m * 10) / 10
}

function lagDays(from: string, to: string): number {
  return Math.round(((Date.parse(to) - Date.parse(from)) / DAY_MS) * 10) / 10
}

function featureRow(
  feat: PrRecord,
  fixes: { fix: PrRecord; link: FixLink }[],
  session: string
): FeatureRow {
  const post = { explicit: 0, blame: 0, weak: 0 }
  let preFixPrs = 0
  const strongLags: number[] = []
  const allLags: number[] = []
  for (const { fix, link } of fixes) {
    if (link.strength === null) continue
    if (fix.mergedAt < feat.mergedAt) {
      preFixPrs++
      continue
    }
    post[link.strength]++
    const lag = lagDays(feat.mergedAt, fix.mergedAt)
    allLags.push(lag)
    if (link.strength !== 'weak') strongLags.push(lag)
  }
  return {
    number: feat.number,
    title: feat.title,
    mergedAt: feat.mergedAt,
    month: feat.mergedAt.slice(0, 7),
    session,
    post,
    pre: { fixPrs: preFixPrs, fixCommits: feat.commitMessages.filter(isFixCommit).length },
    lagDaysStrong: strongLags.length ? Math.min(...strongLags) : null,
    lagDaysAll: allLags.length ? Math.min(...allLags) : null,
  }
}

export function aggregate(rows: FeatureRow[], keyOf: (r: FeatureRow) => string): MonthAggregate[] {
  const groups = new Map<string, FeatureRow[]>()
  for (const r of rows) groups.set(keyOf(r), [...(groups.get(keyOf(r)) ?? []), r])
  return [...groups]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, rs]) => {
      const strong = (r: FeatureRow) => r.post.explicit + r.post.blame
      return {
        key,
        features: rs.length,
        withStrongFix: rs.filter((r) => strong(r) > 0).length,
        withAnyFix: rs.filter((r) => strong(r) + r.post.weak > 0).length,
        strongFixes: rs.reduce((s, r) => s + strong(r), 0),
        allFixes: rs.reduce((s, r) => s + strong(r) + r.post.weak, 0),
        withPreMerge: rs.filter((r) => r.pre.fixPrs + r.pre.fixCommits > 0).length,
        medianLagStrong: median(
          rs.flatMap((r) => (r.lagDaysStrong === null ? [] : [r.lagDaysStrong]))
        ),
        medianLagAll: median(rs.flatMap((r) => (r.lagDaysAll === null ? [] : [r.lagDaysAll]))),
      }
    })
}

function residueOf(
  snapshot: Snapshot,
  links: FixLink[],
  kinds: Map<number, string>,
  reported: Set<number>
): Residue {
  const byNumber = new Map(snapshot.prs.map((p) => [p.number, p]))
  const inWindow = snapshot.prs.filter((p) => p.mergedAt >= snapshot.since)
  const windowLinks = links.filter((l) => (byNumber.get(l.fix)?.mergedAt ?? '') >= snapshot.since)
  const count = (s: FixLink['strength']) => windowLinks.filter((l) => l.strength === s).length
  return {
    fixes: windowLinks.length,
    explicit: count('explicit'),
    blame: count('blame'),
    weak: count('weak'),
    unlinked: count(null),
    toEarlierFeatures: windowLinks.filter(
      (l) => l.strength !== null && !l.features.some((f) => reported.has(f))
    ).length,
    blameUnavailable: windowLinks.filter((l) => byNumber.get(l.fix)?.blamed === null).length,
    otherKindPrs: inWindow.filter((p) => kinds.get(p.number) === 'other').length,
    totalPrs: inWindow.length,
  }
}

/**
 * The whole measure. `sessions` is an optional map from feature PR number to
 * a process label (e.g. `auto-build` / `other`), from processLabels(): the
 * built-by record first, the --sessions map second; features absent from it
 * are `unmapped`.
 */
export function measure(snapshot: Snapshot, sessions: Map<number, string> | null): ReworkMeasure {
  const { links, kinds } = linkFixes(snapshot.prs)
  const byNumber = new Map(snapshot.prs.map((p) => [p.number, p]))
  const fixesByFeature = new Map<number, { fix: PrRecord; link: FixLink }[]>()
  for (const link of links) {
    const fix = byNumber.get(link.fix)
    if (!fix) continue
    for (const f of link.features) {
      fixesByFeature.set(f, [...(fixesByFeature.get(f) ?? []), { fix, link }])
    }
  }
  const rows = snapshot.prs
    .filter((p) => kinds.get(p.number) === 'feat' && p.mergedAt >= snapshot.since)
    .filter((p) => !STRUCTURAL_PRS.has(p.number))
    .sort((a, b) => a.mergedAt.localeCompare(b.mergedAt))
    .map((p) =>
      featureRow(p, fixesByFeature.get(p.number) ?? [], sessions?.get(p.number) ?? 'unmapped')
    )
  const reported = new Set(rows.map((r) => r.number))
  return {
    since: snapshot.since,
    fetchedAt: snapshot.fetchedAt,
    rows,
    byMonth: aggregate(rows, (r) => r.month),
    bySession: sessions ? aggregate(rows, (r) => r.session) : null,
    residue: residueOf(snapshot, links, kinds, reported),
    links,
  }
}

const pct = (n: number, d: number) => (d === 0 ? 'n/a' : `${Math.round((n / d) * 100)}%`)
const days = (v: number | null) => (v === null ? '-' : `${v}d`)

function table(title: string, aggs: MonthAggregate[]): string[] {
  const out = [
    title,
    '',
    '| group | features | >=1 fix, strong | >=1 fix, incl. weak | fixes strong / incl. weak | median first-fix lag strong / incl. weak | caught pre-merge |',
    '| --- | --- | --- | --- | --- | --- | --- |',
  ]
  for (const a of aggs) {
    out.push(
      `| ${a.key} | ${a.features} | ${a.withStrongFix}/${a.features} (${pct(a.withStrongFix, a.features)}) | ${a.withAnyFix}/${a.features} (${pct(a.withAnyFix, a.features)}) | ${a.strongFixes} / ${a.allFixes} | ${days(a.medianLagStrong)} / ${days(a.medianLagAll)} | ${a.withPreMerge}/${a.features} |`
    )
  }
  return [...out, '']
}

export function renderReport(m: ReworkMeasure): string {
  const r = m.residue
  const lines = [
    `# Rework measure: features merged since ${m.since} (fetched ${m.fetchedAt})`,
    '',
    'Strong = explicit #ref or git blame. Weak = same-file overlap within the window.',
    '"caught pre-merge" = fix commits on the feature branch, or a linked fix PR merged before it.',
    '',
    ...table('## By month of feature merge', m.byMonth),
    ...(m.bySession ? table('## By session type', m.bySession) : []),
    '## Fix PRs merged in the window, by link',
    '',
    `- fix PRs: ${r.fixes} (of ${r.totalPrs} merged PRs; ${r.otherKindPrs} were neither feat nor fix)`,
    `- explicit: ${r.explicit}/${r.fixes} (${pct(r.explicit, r.fixes)})`,
    `- blame: ${r.blame}/${r.fixes} (${pct(r.blame, r.fixes)})`,
    `- weak: ${r.weak}/${r.fixes} (${pct(r.weak, r.fixes)})`,
    `- unlinked (residue): ${r.unlinked}/${r.fixes} (${pct(r.unlinked, r.fixes)})`,
    `- linked only to features merged before ${m.since}: ${r.toEarlierFeatures}/${r.fixes}`,
    `- blame unavailable (merge commit not local): ${r.blameUnavailable}/${r.fixes}`,
  ]
  return lines.join('\n')
}
