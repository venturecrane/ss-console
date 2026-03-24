# /edit-article - Editorial Review Agent

This command runs an article through two parallel editor agents, applies blocking fixes directly, and reports what changed. Advisory issues are reported but not auto-fixed.

## Arguments

```
/edit-article <path>
```

- `path` - path to the article markdown file (required)

Parse the argument: if `$ARGUMENTS` is empty, scan `~/dev/vc-web/src/content/articles/` for files with `draft: true` in their YAML frontmatter. List them and ask the user to pick one using AskUserQuestion. If no drafts found, tell the user: "No draft articles found. Provide a path: `/edit-article <path>`"

If `$ARGUMENTS` is provided, use it as the article path.

## Pre-flight

Before spawning agents, do these checks in order:

1. **Terminology doc**: Resolve `~/dev/vc-web/docs/content/terminology.md`. Read it. If it doesn't exist, stop: "Terminology doc not found at ~/dev/vc-web/docs/content/terminology.md. Cannot run editorial review."
2. **Venture registry**: Read `~/dev/crane-console/config/ventures.json`. If missing, stop: "Venture registry not found."
3. **Article file**: Read the file at `path`. If it doesn't exist, stop: "Article not found at {path}."
4. **Display**: Extract the `title` from the article's YAML frontmatter. Display: `Editing: {title}` and proceed immediately. Do NOT ask for confirmation.

Store file paths for agent delegation:

- Terminology doc path: `~/dev/vc-web/docs/content/terminology.md`
- Venture registry path: `~/dev/crane-console/config/ventures.json`

Store the full content of the article as `ARTICLE_TEXT` (unique per invocation, must be embedded).

Build a list of **stealth ventures** - any venture where `portfolio.showInPortfolio` is `false`.

Extract **venture-name tags** from the article's frontmatter `tags` field. Recognized venture tags: `kid-expenses`, `durgan-field-guide`, `silicon-crane`, `draft-crane`. Store the matched tags as `ARTICLE_VENTURE_TAGS` (or "None" if no venture tags found).

## Editor Agents (2, launched in parallel)

Launch both agents **in a single message** using the Task tool (`subagent_type: general-purpose`, `model: "sonnet"`).

**CRITICAL**: Both Task tool calls MUST be in a single message to run in true parallel.

---

### Agent 1: Style & Compliance Editor

Prompt:

```
You are the Style & Compliance Editor. You check articles against the terminology doc and anonymization rules before publish.

## Source Documents

Read these files using the Read tool before starting your review:
- **Terminology Doc (source of truth):** ~/dev/vc-web/docs/content/terminology.md
- **Venture Registry:** ~/dev/crane-console/config/ventures.json

## Stealth Ventures (must not appear at all)

{list of ventures with showInPortfolio: false, with their names and codes}

## Article Venture Tags

{ARTICLE_VENTURE_TAGS}

## Article Under Review

{ARTICLE_TEXT}

## Instructions

Read the article line by line. Check every line against the rules below. Report findings with exact quoted text and line numbers.

### BLOCKING checks (must fix before publish)

**Genericization violations - always blocking (regardless of tags):**
- Any `crane-*` pattern EXCEPT "Venture Crane" (e.g., crane-context, crane-mcp, crane-watch, crane-relay are all internal names that must not appear in published articles)
- Real org names: "venturecrane" in prose (OK in `sources` frontmatter URLs)
- Real venture codes used as identifiers: vc, ke, sc, dfg, dc (OK in `sources` frontmatter)
- Specific venture counts: "5 ventures", "six ventures", or any specific number of ventures (these go stale)
- Legal entity names
- Stealth venture names or identifiable details (ventures listed as stealth above)

**Venture name genericization - tag-dependent (see Article Venture Tags above):**
- If the article IS tagged with a venture name (e.g., tags include `kid-expenses`), that venture's proper name ("Kid Expenses") is ALLOWED in prose. Do not flag it.
- Other public venture names in a tagged article are ADVISORY - report under "### Advisory", suggest genericizing for focus.
- If the article has NO venture-name tags, ALL public venture names are ADVISORY - report under "### Advisory", suggest genericizing for readability.
- Stealth ventures are ALWAYS blocking regardless of tags.

**Terminology violations** - per the terminology doc:
- "product factory" instead of "development lab"
- "SQLite" alone without "D1" (should be "D1" or "D1/SQLite" on first reference)
- "secrets manager" alone without naming "Infisical" on first reference
- Any other violations of the canonical name table in the terminology doc

**Manufactured experience** - flag these patterns, then evaluate context:
- Patterns: "we discovered", "we learned", "we realized", "we felt", "we believed", "surprised", "it struck us", "it dawned on", "After X years", "In my experience", "Having spent", "I noticed", "I decided", "I wanted"
- NOT automatic blockers. For each match, evaluate: does the sentence attribute a subjective human experience the agent couldn't have had?
  - FINE: "We learned from the build logs that usage dropped 40%" (citing evidence)
  - BLOCKING: "We learned that simplicity matters" (manufacturing wisdom)
  - FINE: "We discovered the worker was timing out after checking the error logs" (citing debugging)
  - BLOCKING: "We discovered that less is more" (manufacturing insight)

**Founder-voice fabrication** - any sentence that puts words in the founder's mouth or manufactures a personal anecdote

### ADVISORY checks (should fix)

- Public venture names per the tag-dependent rules above (suggest genericizing for focus where no matching tag exists)
- Em dashes (should be hyphens)
- "I" in articles (only "we" or third person per terminology doc)
- Throat-clearing openers ("In this article, we will...", "Today we're going to...")
- Marketing language (superlatives, hype, "revolutionary", "game-changing", etc.)
- Register mismatch (article should be analytical/explanatory, not terse build-log voice)

### Output Format

Start with `## Style & Compliance Editor`

Then:

```

### Blocking (must fix before publish)

1. Line X: "{exact quoted text}" - {rule violated}. Fix: "{exact replacement text}"

### Advisory (should fix)

1. Line X: "{exact quoted text}" - {issue}. Fix: "{exact replacement text}"

### Clean

- {What was checked and passed}

```

If a section has no findings, write "None" under the heading.

CONSTRAINTS:
- Quote the EXACT text from the article. Do not paraphrase.
- Include line numbers. Count from line 1 of the raw file (including frontmatter).
- Every issue MUST include a Fix with the EXACT replacement text that can be used in a find-and-replace operation. The old text and new text must be copy-pasteable.
- Do NOT write files. Return your report as your final response message.
```

---

### Agent 2: Fact Checker

Prompt:

```
You are the Fact Checker. You cross-reference verifiable claims in articles against real sources.

## Article Under Review

{ARTICLE_TEXT}

## Instructions

You have access to tools. Use them to verify claims in the article against real sources. Work through the verification checklist below IN ORDER.

### Verification Checklist

**1. Venture claims**
Read the venture registry at ~/dev/crane-console/config/ventures.json. Compare to any count, name, or capability claim in the article. Flag mismatches.

**2. Number verification**
For any token count, file count, line count, percentage, or other specific number in the article: search build logs at ~/dev/vc-web/src/content/logs/*.md for verification. Flag numbers that don't match.

**3. Status claims**
For anything described as a "current limitation", "not yet", "doesn't yet", "future work", or similar: search build logs and the codebase for evidence it's been resolved. Flag solved problems presented as current limitations.

**4. Feature claims**
For anything described as working or shipped: verify the component exists. Check for the worker, endpoint, config file, or package - but do NOT deep-read source code. A quick existence check is sufficient.

**5. Cross-article consistency**
Read other articles at ~/dev/vc-web/src/content/articles/*.md. Flag contradictions between the article under review and other published articles.

### Scope Constraint

Do NOT read arbitrary source code files to verify technical claims. Stick to the checklist above. Use these sources ONLY:
- ~/dev/crane-console/config/ventures.json (venture registry)
- ~/dev/vc-web/src/content/logs/*.md (build logs)
- ~/dev/vc-web/src/content/articles/*.md (other articles)
- crane_notes MCP tool (enterprise context)
- crane_status MCP tool (current issue state)
- Glob/Grep to check if a file, worker, or endpoint EXISTS (not to read implementation)

If a claim cannot be verified from these sources, flag it as "UNVERIFIED - requires manual confirmation" rather than guessing.

### Classification

**BLOCKING (must fix before publish):**
- Outdated claims: solved problem presented as current limitation
- Wrong numbers: counts/percentages that don't match build logs
- Aspirational-as-shipped: features described as working that are designed-but-not-deployed (aspirational content is fine if clearly labeled as future/planned)

**ADVISORY (should fix):**
- Architecture descriptions that don't match current code structure
- Cross-article contradictions
- Claims you couldn't verify (flag as UNVERIFIED)

### Output Format

Start with `## Fact Checker`

Then:

```

### Blocking (must fix before publish)

1. Line X: "{exact quoted text}" - {what's wrong}. Source: {where you checked}. Fix: "{exact replacement text}"

### Advisory (should fix)

1. Line X: "{exact quoted text}" - {issue}. Source: {where you checked}. Fix: "{exact replacement text}"

### Clean

- {What was checked and passed, with source references}

```

If a section has no findings, write "None" under the heading.

CONSTRAINTS:
- Quote the EXACT text from the article. Do not paraphrase.
- Include line numbers. Count from line 1 of the raw file (including frontmatter).
- Every finding MUST cite the source you checked against.
- Every issue MUST include a Fix with the EXACT replacement text that can be used in a find-and-replace operation. The old text and new text must be copy-pasteable.
- Do NOT write files. Return your report as your final response message.
```

---

## Apply Fixes

Wait for both agents to complete. Then:

### Step 1: Apply blocking fixes

Re-read the article file (it may have changed since pre-flight). For each blocking issue from both editors:

1. Use the Edit tool to find the exact quoted text and replace it with the suggested fix
2. If the quoted text can't be found (line numbers shifted, text was already fixed, etc.), skip it and note it in the report
3. Deduplicate - if both editors flagged the same text, apply the fix once

### Step 2: Apply advisory fixes

For advisory issues that have clear, mechanical fixes (em dashes, grammar errors, wrong terminology), apply them the same way. Skip advisory issues that require judgment calls or significant rewriting - list those in the report for human review.

### Step 3: Report

After applying fixes, present:

```
## Editorial Report: {article title}

### Fixed: {count}
{list of fixes applied, with before/after quotes}

### Requires Human Review: {count}
{advisory issues that weren't auto-fixed because they need judgment}

### Clean Checks
{what passed across both editors}
```

**If nothing was fixed and nothing needs review**: "Editorial review complete. No issues found."

**If fixes were applied**: End with "Applied {N} fix(es). Re-run `/edit-article` to verify." Do NOT automatically re-run - let the user decide.

---

## Notes

- **Auto-fix**: This command fixes blocking issues and mechanical advisory issues directly in the article file.
- **Human review**: Advisory issues requiring judgment (rewriting sections, tone adjustments, architectural descriptions) are reported but not auto-fixed.
- **Re-run to verify**: After fixes, run `/edit-article` again to confirm issues are resolved.
- **Agent type**: Both editor agents use `subagent_type: general-purpose`, `model: "sonnet"` via the Task tool.
- **Parallelism**: Both agents launch in a single message for true parallel execution.
- **No rounds**: Single pass. Re-invoke after fixes to verify.
- **Terminology doc is the source of truth**: The Style & Compliance Editor checks against it. If the terminology doc is wrong, fix it there - not in the article.
