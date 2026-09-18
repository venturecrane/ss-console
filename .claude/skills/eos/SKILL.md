# /eos - End of Session Handoff

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "eos")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

The agent closes or blocks, then summarizes and saves. The default is not "defer" — the default is "complete." /eos halts until every session-originated loose end is either completed or blocked with an externally-verifiable reason. Only then does it synthesize and save the handoff. The Session Close-Out Audit (Step 2) is the gate.

## Usage

```
/eos
```

## Execution Steps

### 1. Gather Session Context

The agent has full conversation history. Additionally, gather:

```bash
# Get repo info
REPO=$(git remote get-url origin | sed -E 's/.*github\.com[:\/]([^\/]+\/[^\/]+)(\.git)?$/\1/')

# Get commits from this session (last 24 hours or since last handoff)
git log --oneline --since="24 hours ago" --author="$(git config user.name)"

# Get any PRs created/updated today
gh pr list --author @me --state all --json number,title,state,updatedAt --jq '.[] | select(.updatedAt | startswith("'$(date +%Y-%m-%d)'"))'

# Get issues worked on (from commits or conversation)
gh issue list --state all --json number,title,state,updatedAt --jq '.[] | select(.updatedAt | startswith("'$(date +%Y-%m-%d)'"))'
```

### 2. Session Close-Out Audit

Before synthesizing the handoff, run the mechanical audit below. This is the only way to avoid both (a) leaving unmerged in-session work to rot, and (b) fabricating loose ends that weren't actually part of this session's goals.

#### Detection checklist

Checks A-F are objective. Do NOT substitute judgment.

**A. Uncommitted session-originated changes.**
Run `git status --porcelain`. For each output line, classify as session-originated by consulting the session transcript's `Edit` and `Write` tool calls. A file is **high-confidence session-originated** iff its path appears in at least one `Edit` or `Write` call this session. Files dirty but NOT in the tool-call history are **low-confidence** (could be pre-existing working tree state or a parallel-agent edit).

- **High-confidence files** surface in the close-out prompt automatically.
- **Low-confidence files** surface under a separate "verify ownership" line and require per-file confirmation before any completion action runs.

If the tool-call history is unavailable (long session, compaction), downgrade ALL dirty files to low-confidence and require per-file confirmation.

**B. Unpushed commits on the current branch.**

```bash
git log @{u}..HEAD --oneline 2>/dev/null || git log --oneline -10
```

If the current branch has an upstream with commits above it, OR the branch has no upstream and carries commits from this session, flag it.

**C. Unpushed commits on other branches touched this session.**
For every branch named in the session transcript's `git checkout`, `git switch`, or `git branch` calls, run the Check B query. Report each that has unpushed commits.

**D. Session-authored PRs — Ship Gate.**

```bash
gh pr list --author @me --state open \
  --json number,title,mergeStateStatus,statusCheckRollup,reviewDecision,isDraft \
  --jq '.[] | select(.isDraft == false)'
```

Do NOT filter out `BEHIND`/`DIRTY`/`BLOCKED` merge states in the query — a session PR that fell behind main is still session work; dropping it from the audit lets it rot silently.

Classify each PR by `mergeStateStatus` first, then the `(required-checks, reviewDecision)` pair:

- **Needs-update** — `mergeStateStatus == "BEHIND"`: run `gh pr update-branch <N>`, wait for checks to re-run, then reclassify. `mergeStateStatus == "DIRTY"`: merge conflicts — surface in the close-out prompt; never auto-resolve conflicts.
- **Merge-ready** — all required checks `SUCCESS`, `reviewDecision == "APPROVED"` (or no approval required on this repo), no unresolved review threads.
- **Review-ready** — all required checks `SUCCESS`, awaiting review.
- **CI-failing** — at least one required check `conclusion == "FAILURE"`. Surfaced via Check E.

**Ship Gate policy.** Session-authored PRs represent work the session was responsible for finishing. Sessions must end with PRs merged or explicitly blocked — incomplete work does not slide. The Ship Gate enforces that:

- Merge-ready → /eos runs `gh pr merge <N> --squash --delete-branch` as the completion action.
- Review-ready → /eos pauses and prompts the Captain (see Completion Actions) for merge, named reviewer, or external blocker. No silent deferral.
- Explicitly blocked → allowed only with an externally-verifiable reason. "Captain to review," "next session will merge," and "will decide later" are rejected — those are deferrals, not blockers.

This is the one place /eos merges. /ship still forbids auto-merge on direct invocation; /eos earns its narrow merge path by gating on green+approved and by being the last gate before handoff.

**E. Session-authored PRs with explicit FAILURE on required checks.**
Filter Check D's query to PRs with `statusCheckRollup` containing at least one `conclusion == "FAILURE"` on a required check. PENDING/STALE/NEUTRAL/SKIPPED states do NOT count as failures for this check - surface them separately as "CI unresolved" in the handoff without marking them as loose ends requiring action.

**F. Memory/doc edits that reference unshipped PRs.**
For each memory or doc file edited this session (identify via the transcript's `Edit`/`Write` tool calls), extract PR references with:

```bash
grep -oE '\b(PR |pull/|#)([0-9]+)\b' <file>
```

For each match, verify with `gh pr view <N> --json state` - if the command returns an issue (not a PR) it errors out harmlessly and the match is discarded. For each PR number where `state != MERGED`, inspect the file's surrounding text. If the text claims post-merge state (phrases like "merged", "landed", "in production", "resolved by"), flag the file.

**G. Open issues asserted as resolved during this session.**

Scan the session transcript for phrases matching any of:

- `verified( as)? resolved`
- `confirmed (remediated|fixed|closed|gone)`
- `(all|both) .* (violations|findings) confirmed (gone|remediated)`
- `fix in code AND tested`

For each match, extract any `#\d+` issue numbers within ±200 characters. For each extracted number, run `gh issue view <N> --json state`. If `state == OPEN`, surface the issue.

**Completion action:** `gh issue close <N> --comment "Verified resolved during session {session_id}. See handoff."` Deferral disallowed — an in-session verification that isn't carried through to closure is the same incomplete-work pattern Check D catches on PRs.

**H. Session diagnostic notes must cite matching memory.**

If the session transcript contains failure-narrative keywords ("silent failure", "root cause unknown", "mysterious", "didn't work as expected", "recovered via", "bug-hunt", "investigate next"), extract the surrounding context. For each match, grep `MEMORY.md` for matching memory filenames (by topic keywords: "worktree" → `feedback_parallel_agents_worktrees.md`, "MCP" → `feedback_mcp_debugging.md`, etc.).

For each match, the handoff's diagnostic/retro section MUST cite the memory by filename. A bare "investigate next session" or "root cause unknown" without citing a matching memory entry is rejected — re-synthesize the section with the citation. If no memory matches after an honest check, add the assertion "no matching memory found (candidate for new feedback memory)" so the gate is explicit.

**Defensive note on `gh` JSON schema.** Fields like `mergeStateStatus` and `statusCheckRollup` have stable names but nested shape changes across `gh` minor versions. Wrap each jq call at the shell level: unexpected output formats should log a warning like `[eos:check-X] gh output unexpected, skipping this check` and proceed to the next check. Do NOT crash the skill on schema drift.

#### Anti-Fabrication Gate

Every item you are about to list as a loose end must pass this gate.

**Gate:** Is this item one of:

1. A high-confidence uncommitted file from Check A (in the session's tool-call history), OR
2. A low-confidence uncommitted file from Check A that the Captain confirmed as session-originated, OR
3. Unpushed commits from Check B or C, OR
4. An open session-authored PR from Check D (Ship Gate: merge-ready, review-ready, or explicitly blocked), OR
5. An open session-authored PR from Check E (failing required checks), OR
6. A memory/doc file from Check F that references an unshipped PR with post-merge prose, OR
7. An open issue from Check G that the session asserted as resolved, OR
8. A diagnostic note from Check H that needs memory citation, OR
9. A specific item the Captain requested **via an explicit chat message with a verb and subject** that has not been completed. (NOT an aside summarized from context. NOT an inference. A literal user message.)

If NO to all nine → do not list it. Do not hedge. Do not qualify. Kill it.

**Specifically forbidden items** (these are fabrications, never list them):

- Refactors, test additions, or cleanups "noticed in passing"
- Documentation/comment/naming improvements the Captain did not request
- Adjacent bugs unrelated to this session's goals
- "Nice-to-have" improvements to code that shipped this session
- Work explicitly deferred earlier in the session (the decision already happened)
- Anything prefixed with "we should also…" or "it would be nice to…"
- Fleet-wide or time-distant concerns surfaced for completeness
- Reassurances dressed up as open items ("the CI gate is still alive" is a result, not a loose end)

If the audit finds nothing that passes the gate, skip directly to Step 3. Do not fabricate a list. Saying "we're done" is the correct output.

#### Deduplication with /own-it

If `/own-it` was invoked earlier in this session and resolved items via its rule-4 closure checklist, those items do NOT re-surface here. Consult the transcript; items already marked as resolved earlier are excluded.

#### Close-Out Prompt (only if items exist)

If **>4 items pass the gate**, do NOT auto-split. Surface the count and halt with:

> Session close-out audit found N items - this session wasn't actually closable in one flow. Invoke `/own-it` and address them individually, or pick the highest-priority items to resolve now.

Do not proceed to a bulk prompt that hides items behind a scroll.

If **≤4 items pass the gate**, present them in ONE consolidated prompt using `AskUserQuestion`:

- Question: "Session close-out: N items to resolve before handoff. Complete now?"
- Header: `Close-out` (≤12 chars)
- Options:
  - **"Complete all"** — execute the obvious completion for each item per the Completion Actions table below. After actions, re-run Checks A–H; items that are still in scope roll back to a second prompt.
  - **"Complete selected"** — present per-item options in a second `AskUserQuestion`.
  - **"Declare external blocker"** — each item left requires a one-line reason naming an external party or system that prevents completion (vendor response pending, Captain directive required, approver identified by name, etc.). Reasons matching `(?i)(Captain to review|next session|later|will decide|I'll review)` are rejected with: "That's not an external blocker — it's a deferral. Name the actual blocker, or complete the item." Reprompt once; if the second reason also fails, default to "Complete selected."

"Leave for next session" is **not** an available option. Session-originated loose ends complete or block with a named blocker — they do not slide.

#### Completion Actions (per check)

| Check                         | Action                                                                                                                                  | Notes                                                                                                                                                                                     |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A (high-confidence)           | `git add <specific file>` + commit with a message derived from file path + session context                                              | Stage ONLY the specific files from the tool-call history. **Never** `git add -A` or `git add .` — this would sweep parallel-agent state.                                                  |
| A (low-confidence, confirmed) | Same as above, per confirmed file                                                                                                       | Confirmation is per-file; partial confirmations are allowed.                                                                                                                              |
| A (`/code-review` report)     | Prompt: `(a) commit with prior-report convention`, `(b) rm the file`. No third option.                                                  | Prior reports in `docs/reviews/` are committed. Match convention or discard. Deferral disallowed — the report cannot sit untracked across session boundaries.                             |
| B                             | `git push` (or `git push -u origin <branch>` if no upstream)                                                                            | Do not force-push. If push is rejected (non-fast-forward), roll back to the prompt — do not attempt to resolve.                                                                           |
| C                             | `git switch <branch> && git push && git switch -`                                                                                       | Restore original branch on completion.                                                                                                                                                    |
| D (needs-update, BEHIND)      | `gh pr update-branch <N>`, wait for checks to re-run, then re-run Check D and reclassify.                                               | If update-branch fails on conflicts, treat as DIRTY.                                                                                                                                      |
| D (needs-update, DIRTY)       | Surface the conflicting files; prompt Captain: `(a) resolve now (breaks into interactive)`, `(b) declare external blocker`.             | Never auto-resolve merge conflicts at `/eos`.                                                                                                                                             |
| D (merge-ready)               | Run `gh pr merge <N> --squash --delete-branch`. On success, note the merge in the handoff "Accomplished" section.                       | This is the only merge `/eos` performs. Gated on: green required checks, approved (or no approval required), no unresolved review threads. Any gate missing → fall to D (review-ready).   |
| D (review-ready)              | Pause `/eos` and prompt Captain: `(a) approve+merge now`, `(b) name the reviewer and block`, `(c) declare external blocker`.            | No "leave for next session" option. If Captain absent (auto mode), /eos blocks with machine reason `"blocked: pending Captain merge authorization for #<N>"` so the deferral is explicit. |
| E                             | Surface the failing check names + URL                                                                                                   | Do NOT attempt to fix. Ask whether to investigate now (breaks out of `/eos` into interactive debug) or block with the failure noted as the external-blocker reason.                       |
| F                             | Update the file to reflect actual state (e.g., "merged in PR #N" → "proposed in open PR #N")                                            | Use `Edit` with precise `old_string`/`new_string`; do not rewrite the file.                                                                                                               |
| G                             | `gh issue close <N> --comment "Verified resolved during session {session_id}. See handoff."`                                            | Deferral disallowed. In-session verification that isn't carried through to closure is the same incomplete-work pattern Check D catches on PRs.                                            |
| H                             | Rewrite the handoff diagnostic section to cite the matching memory filename, or add "no matching memory found (candidate for new one)". | Forces the check to run. "Root cause unknown" with no memory check is rejected.                                                                                                           |

#### Post-Action Verification

After completion actions run, re-run Checks A–H. Any check that still returns items means the action failed or was partial — surface those items in a second prompt with the completion result attached. Do not mark items as resolved in the handoff if the check still flags them.

#### Blocked-Item Age Tracking

"Leave for next session" is never an option (see Close-Out Prompt) — the only way an item carries forward is as a declared external blocker. Each blocked item is recorded in the handoff's "Loose ends captured at /eos" section with:

- Item description (path, branch, PR number)
- `first_seen: YYYY-MM-DD` (today if new; copied from prior handoff if this item already appeared)
- `age: N` sessions (1 if new; incremented if matched to a prior handoff's item via path/branch/PR number)
- `reason:` the externally-verifiable blocker the Captain declared

On the next `/eos`, match by identifier (path/branch/PR number) against the prior session's "Loose ends captured at /eos" list (fetched via `crane_context` or the prior handoff record). Increment `age` for matches.

**When `age >= 3`, the blocker must re-justify itself.** The Captain must choose:

- Re-affirm the blocker with current evidence that it is still external and still live (a stale reason cannot roll forward), OR
- Kill the item (remove from the handoff entirely — speculative work gets killed, not re-filed).

This converts filing pressure into closure pressure: blocked items age visibly and must re-earn their place every session past the third.

**Critical:** the audit produces at most ONE close-out prompt (plus a post-action verification prompt if some completions failed, plus an age≥3 escalation prompt if that case hits). Do not follow up with "anything else?" - that phrasing is the exact invitation to fabricate that this audit exists to prevent.

### 3. Synthesize Handoff (Agent Task)

#### 3a. Self-Review Bias Caveat

If `/code-review` was invoked in this session (check transcript for the skill invocation) AND the review graded code that Claude wrote in a prior or current session, any grade-change claim in "Accomplished" carries the caveat:

> (self-review — subject to bias; treat as signal, not measurement)

This is a one-line boilerplate. Do not omit. Do not reword. Do not soften to "may be biased." The caveat exists because we run review and authorship on the same model; a neutral framing misrepresents the evidence.

Using conversation history and gathered context, the agent generates a summary covering:

**Accomplished:** What was completed this session

- Issues closed/progressed
- PRs created/merged
- Problems solved
- Code changes made

**In Progress:** Unfinished work - write as pickup instructions for an agent with NO conversation history

- Include specific file paths, function names, and branch names
- State exactly where you stopped: "Function X is partially implemented in file Y"
- List what remains as numbered steps
- Include any items from Step 2's audit that the Captain declared blocked by an external party or system, under a subheading **"Loose ends captured at /eos"** with exact file paths, branch names, PR numbers, `first_seen`, `age`, and `reason` (the externally-verifiable blocker name). Do not paraphrase or compress. The next session's `/eos` reads this to increment age. Items that completed via action do NOT appear here.

**Blocked:** Items needing attention

- Blockers encountered
- Questions for PM
- Decisions needed
- External dependencies
- **Unwired seams:** any producer→consumer seam this session touched but did not observe working on the real runtime. Per the Definition of Done ("Done means wired", `crane_doc('global', 'team-workflow.md')`), these are NOT done — name the seam and use `status:blocked` rather than implying completion. The EOS verify-coverage gate (Layer 4c) enforces this mechanically when `status=done`; see `docs/instructions/eos-gate.md`.

**Next Session:** Recommended focus - write as an ordered action plan

- "1. Open src/foo.ts and complete the retryWithBackoff() function"
- "2. Run npm test - expect 2 new tests to pass"
- "3. Create PR for issue #123"

**Anti-Fabrication rule (also applies to this section).** The same gate from Step 2 governs what goes into "In Progress," "Blocked," and "Next Session." If an item does not pass the Step 2 gate, it does not belong in the synthesized handoff. Write "(none)" for empty sections. Do not pad.

### 4. Display and Save (Auto-Save)

Display the generated handoff, then **immediately save it to D1 without asking for confirmation.** Do not prompt the user with "Save to D1?" or any yes/no question. Just show the summary and save.

### 5. End Work Day

Handled automatically: `crane_handoff` closes the work day server-side when the final handoff is saved (any call without `final: false`). No separate call — there is no agent-exposed work-day tool.

### 6. Save Handoff via MCP

Call the `crane_handoff` MCP tool with:

- `summary`: The synthesized handoff text
- `status`: One of `in_progress`, `blocked`, or `done` (infer from context)
- `issue_number`: If a primary issue was being worked on

This writes to D1 via the Crane Context API. The next session's SOD will read it.

**Important:** When status is `in_progress`, the full summary is shown to the next session's SOD briefing. Write "In Progress" and "Next Session" as if giving instructions to another developer who has zero context from this conversation.

**Status invariants (enforced before save):**

- `done` — Step 2 found nothing. No unmerged session PRs, no uncommitted session files, no open-but-verified-resolved issues, no uncited failure diagnostics. If any of those exist, `done` is rejected.
- `in_progress` — work genuinely carries into the next session (in-flight branch, research thread, design spike). Not a synonym for "PRs opened but not merged" — that is a Ship Gate violation, not in-progress work.
- `blocked` — at least one Check item carries into the next session with an externally-verifiable blocker reason. The reason must name the blocker.

#### Cross-venture sessions

If work was done across multiple ventures this session (e.g., started in dc-console then switched to crane-console), write a separate handoff for each venture:

1. Identify all ventures that had meaningful work this session (commits, PRs, code changes, issue progress).
2. For each venture, call `crane_handoff` with a `venture` parameter set to the venture code. This overrides auto-detection so you can write handoffs for ventures other than the current repo.
3. Each handoff summary should cover only the work relevant to that venture.

Example for a session that touched both `dc` and `vc`:

```
crane_handoff(summary: "Rebuilt AI assist panels...", status: "done", venture: "dc")
crane_handoff(summary: "Added /ship skill, command sync...", status: "done", venture: "vc")
```

### 7. Report Completion

```
Handoff saved to D1. Next session will see this via crane_sos.
```

## Key Principle

**The agent summarizes. The agent saves. No confirmation needed — with one targeted exception: if Step 2's audit surfaces genuine unshipped work, the skill asks ONE consolidated close-out question before proceeding. Otherwise, auto-save proceeds silently.**

The agent has full session context - every command run, every file edited, every conversation turn. It should synthesize this into a coherent handoff without asking the user to remember or summarize anything. The Session Close-Out Audit is the only place `/eos` produces a prompt, and only when objective detection checks find loose ends that pass the anti-fabrication gate.

## Related

- `/own-it` rule 4 — mid-session closure checklist; `/eos` inherits the same discipline at session end.
- Principles enforced here: pad is worse than done (no manufactured loose ends); speculative work gets killed, not filed; never `git add -A` under parallel agents.
