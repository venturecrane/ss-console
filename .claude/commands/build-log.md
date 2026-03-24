# /build-log - Draft a Build Log Entry

Draft a short operational update (200-1,000 words) about what shipped, broke, or changed. The founder provides a topic and the agent drafts around it.

## Usage

```
/build-log "Shipped the new webhook classifier and decommissioned the old monolith"
/build-log --weekly          # synthesize from last 7 days of handoffs/git
/build-log --weekly --days 14  # custom lookback window
```

## Arguments

Parse `$ARGUMENTS`:

- If it starts with `--weekly`, enter **weekly mode**. Check for `--days N` to set the lookback window (default: 7 days).
- Otherwise, treat the entire argument string as the **topic**. Strip surrounding quotes if present.
- If `$ARGUMENTS` is empty, stop: "Provide a topic: `/build-log \"what happened\"`"

## Pre-flight

1. **Terminology doc**: Verify `~/dev/vc-web/docs/content/terminology.md` exists. If missing, stop: "Terminology doc not found."
2. **Venture registry**: Verify `~/dev/crane-console/config/ventures.json` exists. If missing, stop: "Venture registry not found."
3. **Recent logs**: Read up to 3 most recent files in `~/dev/vc-web/src/content/logs/` for voice consistency.

Read the terminology doc and venture registry on demand when drafting (not pre-loaded into prompt).

Build a list of **stealth ventures** - any venture where `portfolio.showInPortfolio` is `false`. These must not appear in the draft by name, code, or description.

---

## Topic Mode (default)

When the founder provides a topic string:

### 1. Gather lightweight context

- Read the most recent handoff via `crane_context` MCP tool for the current venture (if available)
- The 3 recent logs from pre-flight are sufficient for voice matching

### 2. Draft the entry

Write a 200-1,000 word build log entry following these rules:

**Structure**: Flexible. No mandatory headings. The content dictates the structure. Common patterns from existing logs include "What We Did" / "What Surprised Us" sections, but these are not required. If nothing genuinely surprising happened, do not manufacture a surprise.

**Voice**: First-person plural ("we") by default. Match the tone of recent logs - operational, direct, honest.

**Genericization** (from terminology doc, Published Content section):

- Replace `crane-*` internal names with functional descriptions (crane-context -> "the context API", crane-mcp -> "the MCP server", etc.)
- Replace venture codes with generic codes (vc -> alpha, ke -> beta, etc.)
- Replace "venturecrane" org references with omission or "example-org"
- Replace specific venture counts with "multiple ventures" or "several projects"
- Exception: "Venture Crane" in prose is fine (it's the company name on the published site)
- Exception: External tool names are real (Claude Code, D1, Infisical, Tailscale, etc.)

**Venture names** (three-tier system from terminology doc):

- If the topic is about a specific public venture, use the real venture name in prose and tag the log with the venture-name tag (e.g., `kid-expenses`). The real name adds credibility and connects to the portfolio.
- If the topic is NOT about a specific venture, genericize public venture names as before ("Project Alpha", etc.) or use "another project" / "a different venture".

**Stealth filtering**: Ventures with `showInPortfolio: false` in the registry must not appear at all - not even in genericized form. If the topic involves a stealth venture, draft around it or stop and tell the Captain: "This topic involves a stealth venture. Revise the topic or confirm you want to proceed."

**Style rules** (from terminology doc):

- No em dashes (use hyphens)
- No marketing language
- No throat-clearing openers
- Real numbers, real tool names, real configs
- Include setbacks or honest limitations when they exist - but only when they actually exist

### 3. Present and save

Display the draft to the Captain. Ask:

```
Save as draft? (y/n)
```

If yes:

1. Generate a slug from the title (lowercase, hyphens, no special chars)
2. Write to `~/dev/vc-web/src/content/logs/YYYY-MM-DD-slug.md` with today's date
3. Frontmatter: `title`, `date` (today), `tags` (infer 1-3 from content; if the log is about a specific public venture, include the venture-name tag, e.g., `kid-expenses`), `draft: true`
4. Report: "Saved draft to {path}. Run `/edit-log {path}` before publishing."

---

## Weekly Mode (`--weekly`)

When invoked with `--weekly`:

### 1. Gather signals across all ventures

For each venture in the registry:

**Handoffs** (primary signal):

```bash
# Query crane-context for recent handoffs per venture
```

Use the `crane_context` MCP tool if available, or `crane_notes` to search for recent handoff-tagged notes.

**Git activity** (supporting signal):

```bash
# Commits in the lookback window
git log --oneline --since="{N} days ago" --all

# Merged PRs
gh pr list --state merged --json title,mergedAt --jq '.[] | select(.mergedAt >= "'$(date -v-{N}d +%Y-%m-%d)'")'

# Closed issues
gh issue list --state closed --json title,closedAt --jq '.[] | select(.closedAt >= "'$(date -v-{N}d +%Y-%m-%d)'")'
```

**VCMS notes** (supplementary):
Use `crane_notes` to search for recent notes across ventures.

### 2. Weight and filter

- Handoff summaries carry the most weight. They contain narrative context.
- Git metadata (commit messages, PR titles) is supporting evidence, not primary material. Do not construct narrative from commit messages alone.
- If a venture has no handoffs in the lookback period, note it contributed no narrative signal. Do not scrape commits for padding.

### 3. Quality gate

Evaluate the gathered material. If it contains **no genuine setback, surprise, or non-trivial decision**, output:

```
INSUFFICIENT MATERIAL - The last {N} days produced no narrative-worthy signal. Handoff coverage was sparse and commit messages don't carry enough context for a quality log. Try /build-log "topic" with a specific topic instead.
```

Stop. Do not draft.

### 4. Draft

If the quality gate passes, draft using the same rules as topic mode (genericization, stealth filtering, style rules, 200-1,000 words).

### 5. Present and save

Same as topic mode step 3.

---

## Notes

- **Topic mode is the 80% case.** The founder knows what happened. The bottleneck is drafting, not signal gathering.
- **Weekly mode is fragile.** It depends on `/eod` handoffs existing. Sparse handoffs plus raw commit messages produce mediocre drafts. The quality gate prevents publishing low-quality filler.
- **All logs save as `draft: true`.** Publishing requires `/edit-log` review and Captain approval.
- **Stealth ventures are never exposed.** The registry's `showInPortfolio` field is the source of truth.
