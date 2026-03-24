# /content-scan - Content Candidate Triage

Read-only triage tool that scans all ventures for publishable content candidates. Produces a ranked list of article candidates, promotion candidates, and build log gaps. Does NOT draft anything - that's `/build-log`'s job.

## Usage

```
/content-scan              # Default: 7-day lookback
/content-scan --days 14    # Custom lookback
/content-scan --save       # Also save results to VCMS
```

## Arguments

Parse `$ARGUMENTS`:

- If `--days N` is present, set `LOOKBACK_DAYS` to N. Default: 7.
- If `--save` is present, set `SAVE_TO_VCMS` to true. Default: false.
- If `$ARGUMENTS` is empty, use defaults (7 days, no save).

---

## Step 1: Pre-flight

### 1a. Environment check

Verify `CRANE_CONTEXT_KEY` is set in the environment. If not, stop: "CRANE_CONTEXT_KEY not set. Launch with `crane vc`."

### 1b. API health check

Ping crane-context health endpoint. If not `200`, stop: "crane-context API unreachable. Cannot run content scan without handoff data." Do not degrade to git-only mode - handoffs are the primary signal.

### 1c. Load venture registry

Read `~/dev/crane-console/config/ventures.json`. Store as `VENTURE_REGISTRY`.

Build a list of **active ventures** - entries with a non-empty `repos` array. Skip ventures with `"repos": []`.

### 1d. Load content index

Scan `~/dev/vc-web/src/content/` for published content:

- **Articles**: Glob `~/dev/vc-web/src/content/articles/*.md`. Read frontmatter (title, date, tags) from each file.
- **Build logs**: Glob `~/dev/vc-web/src/content/logs/*.md`. Read frontmatter (title, date, tags, draft) from each file. Exclude files where `draft: true`.

Store these as `ARTICLE_INDEX` and `LOG_INDEX`.

For each venture, count published articles where the venture's name or code appears in tags. Store as `ARTICLE_COUNTS` (e.g., `{ ke: 0, dfg: 0, sc: 0, dc: 0, vc: 12 }`).

---

## Step 2: Short-circuit check

Search VCMS for the most recent `content-scan` note:

```
crane_notes(tag: "content-scan", limit: 1)
```

If a note exists, extract its `created_at` timestamp as `LAST_SCAN_DATE`.

Query handoffs created since the last scan. Check if any exist (count > 0).

If **zero** ventures have new handoffs since the last scan:

1. Output: "No new signals since last scan ({LAST_SCAN_DATE}). Skipping."
2. Record cadence: `crane_schedule(action: "complete", name: "content-scan", result: "skipped", summary: "No new handoffs since last scan", completed_by: "crane-mcp")`
3. Stop.

---

## Step 3: Gather signals per venture

### 3a. Handoffs (primary signal)

Fetch all handoffs within the lookback window. Group by `venture` field. For each venture, store the `summary` and `created_at` from each handoff.

**Note**: Handoffs created from crane-console sessions carry `venture=vc` even when the work targets another venture. Scan each handoff's `summary` text for venture names and codes (dc, ke, sc, dfg) and cross-reference mentions to associate handoffs with the correct venture when the `venture` field doesn't match.

### 3b. Git activity (metadata only)

Iterate each active venture in the registry. For each venture, for each repo in its `repos` array:

```bash
# Merged PRs in the lookback window
gh pr list --repo {ORG}/{REPO} --state merged --json number,title,mergedAt \
  --jq '.[] | select(.mergedAt >= "'$(date -v-{LOOKBACK_DAYS}d +%Y-%m-%d)'")'

# Closed issues in the lookback window
gh issue list --repo {ORG}/{REPO} --state closed --json number,title,closedAt \
  --jq '.[] | select(.closedAt >= "'$(date -v-{LOOKBACK_DAYS}d +%Y-%m-%d)'")'
```

Store PR titles and counts per venture-repo.

**Selective PR body fetching**: For PRs whose titles contain any of these keywords (case-insensitive): `redesign`, `migrate`, `remove`, `replace`, `decision`, `tradeoff`, `rewrite`, `new` - fetch the full PR body:

```bash
gh pr view {NUMBER} --repo {ORG}/{REPO} --json body --jq '.body'
```

**Cap at 5 body fetches per repo.** After 5, stop fetching bodies even if more titles match.

### 3c. Record signal counts

For each venture, record:

- Number of handoffs in window
- Number of merged PRs in window
- Number of PR bodies fetched

---

## Step 4: Classify candidates

Evaluate gathered signals using bucket-based assessment. Do not assign numeric scores.

### Article candidates

**Gating question**: Does the material have handoff narrative AND a decision or surprise worth generalizing to readers outside Venture Crane?

For each venture with handoffs in the window, assess:

- **High confidence**: Handoff explicitly describes a design decision, tradeoff, architectural choice, or surprising outcome. The topic generalizes beyond the specific venture.
- **Medium confidence**: Handoff describes substantive work with some narrative depth, but the generalizable angle is less obvious. May need editorial shaping.

Produce a one-line headline and a "Why" rationale for each candidate.

### Promotion candidates (weekly only)

**Skip this section entirely if `LOOKBACK_DAYS` < 7.**

**Gating question**: Does an existing build log read like a draft article worth promoting?

Filter `LOG_INDEX` to logs that are:

- Older than 14 days (published_date + 14 < today)
- Longer than 400 words (read the file to count)

For qualifying logs, assess:

- **High confidence**: Log has a substantive "What Surprised Us" section (or equivalent narrative depth) AND no existing article covers the same topic for the same venture.
- **Medium confidence**: Log has some narrative depth but the article angle needs more development.

### Log candidates

**Gating question**: Did something ship (merged PR with handoff narrative) with no matching build log?

For each venture with merged PRs in the window:

- Check if the venture has any build log published within the lookback window (any log in `LOG_INDEX` with a matching venture tag and a date within the window).
- If the venture has merged PRs AND handoff narrative in the window but NO log published in the window, flag it as a log gap.

### Suppression rules (never surface as candidates)

- Git-only activity with no handoff narrative (PRs exist but no handoffs mention the work)
- Routine operational work: dependency updates, config tweaks, linting fixes, formatting changes, CI adjustments
- Topics already covered by a published article with the same venture context (check `ARTICLE_INDEX` for matching venture tags and similar topics)

### Coverage boost

If a venture has zero published articles (`ARTICLE_COUNTS[code] == 0`), boost its candidates one confidence level:

- Medium -> High
- Candidates that would otherwise be borderline -> Medium

This integrates the gap signal into the ranking. Do not present it as a separate analysis.

---

## Step 5: Display output

Format and display the results:

```
CONTENT SCAN - {TODAY} - Last {LOOKBACK_DAYS} days
================================================================================

ARTICLE CANDIDATES
--------------------------------------------------------------------
HIGH  {CODE}  {Headline}
              Why: {rationale}
              Source: handoff {date}
              Note: {venture name} has zero published articles (coverage boost)

MED   {CODE}  {Headline}
              Why: {rationale}
              Source: handoff {date}

PROMOTION CANDIDATES (weekly)
--------------------------------------------------------------------
HIGH  {CODE}  {log-slug} ({date})
              Why: {word count} words, substantive surprise section, no article on topic
              Action: draft article via /build-log

LOG CANDIDATES
--------------------------------------------------------------------
      {CODE}  {description} - merged PR, handoff exists, no log
              Source: PR #{number} merged {date}

COVERAGE GAPS (ventures with zero published articles)
  {comma-separated list of venture names}

SIGNAL HEALTH
  {code}  handoffs: {N}   git: {N} PRs     {status}
  ...
================================================================================
```

**Status labels for signal health**:

- `OK` - at least 1 handoff exists
- `low activity` - handoffs exist but sparse (1 handoff, 0 PRs)
- `no signal` - zero handoffs and zero PRs

**Section omission rules**:

- If there are no article candidates, omit the ARTICLE CANDIDATES section entirely. Do not show an empty section.
- If there are no promotion candidates (or lookback < 7), omit the PROMOTION CANDIDATES section.
- If there are no log candidates, omit the LOG CANDIDATES section.
- If all ventures have at least one article, omit the COVERAGE GAPS line.
- SIGNAL HEALTH always displays.

If there are zero candidates across all sections, display:

```
CONTENT SCAN - {TODAY} - Last {LOOKBACK_DAYS} days
================================================================================

No publishable candidates found.

SIGNAL HEALTH
  {code}  handoffs: {N}   git: {N} PRs     {status}
  ...
================================================================================
```

---

## Step 6: Save to VCMS

If `SAVE_TO_VCMS` is true (from `--save` flag), save automatically.

If `SAVE_TO_VCMS` is false, ask: "Save results to VCMS? (y/n)"

If saving:

```
crane_note(
  action: "create",
  title: "Content Scan - {TODAY}",
  content: "{full output text from Step 5}",
  tags: ["content-scan"],
  venture: null
)
```

The note is global (no venture), tagged `content-scan` so the short-circuit check in Step 2 can find it.

---

## Step 7: Record cadence

After completing the scan, record in the Cadence Engine:

```
crane_schedule(
  action: "complete",
  name: "content-scan",
  result: "{result}",
  summary: "{summary}",
  completed_by: "crane-mcp"
)
```

**Result enum**:

- `success` - API healthy, at least one article-grade candidate found (High or Medium)
- `warning` - API healthy but zero article-grade candidates (only log gaps or no candidates at all), OR partial API failures during signal gathering
- `skipped` - short-circuited in Step 2 (already recorded there; do not record again)

**Summary examples**:

- "3 article candidates (1 high, 2 med), 1 log gap, 2 promotions"
- "No article candidates. 1 log gap across 5 ventures"
- "Partial failure: ke handoffs unreachable. 1 candidate from vc"

---

## Notes

- **This is a triage tool, not a drafting tool.** It identifies what to write about. Use `/build-log` to draft.
- **Handoffs are the primary signal.** Git activity confirms something shipped but never justifies a candidate alone.
- **No VCMS queries during signal gathering.** VCMS is only checked in the short-circuit (Step 2) and optionally for borderline disambiguation. This saves 5+ MCP calls per run.
- **Promotion scanning is weekly.** Gated behind `LOOKBACK_DAYS >= 7`. Logs don't change daily.
- **Coverage boost is integrated, not separate.** Ventures with zero articles get +1 confidence level on their candidates rather than appearing in a standalone gap analysis.
- **Fail fast on API failure.** If crane-context is down, stop. Git-only data produces noise, not signal.
- **Stealth ventures** (where `portfolio.showInPortfolio` is `false`) are skipped automatically because they have repos but should not produce public content candidates. If a stealth venture's work generates a candidate, omit the venture name and flag it: "Candidate from internal venture - discuss with Captain before proceeding."
