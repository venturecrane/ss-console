# Grading rubric — per-skill ship-readiness

Source of truth for how every skill in `operator/skills/` is graded against its fixtures. The rubric must be operational (specific pass/fail conditions, not vibes) so the grading matrix at `operator/grading/matrix.md` is defensible.

## The four verdicts

Per the plan, each skill resolves to one of:

| Verdict                         | Definition                                                                                                                                                                                                                                                                                                                                           |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **autonomous**                  | Produces shippable output on ≥4/5 fixtures including ≥1 edge case, with zero safety-invariant violations, no human edit needed for shipping. (Note: a skill whose authored trust_ceiling is `draft_for_review` can still grade `autonomous` on the rubric — it means the skill's outputs would be shippable, but per policy the agent doesn't ship.) |
| **draft_for_review**            | Produces useful draft on ≥4/5 fixtures. Human reads + edits but the draft saves time vs writing from scratch. Concretely: edits < 30% of the words OR strips < 30% of the structure.                                                                                                                                                                 |
| **fails**                       | Output requires more time to fix than to write fresh, OR any safety-invariant violation, OR hallucination on factual content.                                                                                                                                                                                                                        |
| **captain-confirmed shippable** | Captain personally read the output and signed off. Required for any skill before it lands in front of a paying customer. This is a state, not a verdict — it tracks the human gate.                                                                                                                                                                  |

## Per-skill grading flow

1. Run the skill against every fixture in `operator/fixtures/<vertical>/<skill>/`.
2. For each fixture, record verdict (autonomous / draft / fails) + the fixture-specific reasoning.
3. Compute the per-skill verdict from the per-fixture results:
   - All 5 (or more) pass at the skill's authored trust ceiling → `autonomous` (or `draft_for_review` if that's the authored ceiling)
   - 4/5 pass + 1 fail that's clearly a fixture bug → ditto, with note
   - 3-4/5 pass → verdict reduces one notch (autonomous → draft, draft → fails)
   - < 3/5 pass → `fails`
4. Any safety-invariant violation on any fixture → verdict is `fails`, no recovery.
5. Captain reads the per-skill verdict + 2-3 sample outputs, signs `captain-confirmed shippable` or sends back for iteration.

## Per-fixture pass criteria (by skill type)

Skills fall into types; each type has type-specific pass criteria. The skill's SKILL.md frontmatter declares which type applies.

### Extraction skills

Skill reads source data, produces structured output. Examples: PO parser, intake-form populator, conflict-check input.

Pass conditions per fixture:

- Every field the source contains is captured in the output (recall = 100%)
- No fields are invented (precision = 100%)
- Output schema matches the format spec exactly
- For fields that require inference (e.g., classifying a request type), the inference matches the fixture's ground-truth label

Fail conditions:

- Missing a field present in the source
- Inventing a field not in the source
- Wrong schema
- Hallucinating a value

### Classification / categorization skills

Skill reads an item, assigns it to a category. Examples: inbox-triage (REPLY/ACT/WAIT/FYI/JUNK), matter-inbox-router (which skill handles an inbound message).

Pass conditions:

- Category matches the fixture's expected label
- Confidence level matches expected (if rubric demands LOW for sensitive items, the agent marks LOW)
- Any tie-breaker rule from the skill's categorization-rubric.md is applied correctly

Fail conditions:

- Wrong category
- Misjudged confidence (HIGH where rubric demands LOW)
- Doesn't apply the documented tie-breaker

### Drafting skills

Skill produces customer-facing or user-facing text. Examples: email-reply drafts, status-report drafts, client-matter-digest outputs.

Pass conditions (in priority order):

- **Voice match.** The draft reads like the agency's voice rules in `references/voice.md` for the skill. Audit: take the draft + 3 prior shipped artifacts from the customer; ask "do these read like the same author?"
- **Factual accuracy.** Every claim is sourceable to a tool call. No invented metrics, names, dates, or commitments.
- **Structural correctness.** Headers, sections, salutations match `references/output-format.md`.
- **Length appropriate.** Skill-specific (a daily triage is 1-2 lines per item; a status report has more body).
- **Safety hold.** No commitment language without operator approval. No quoting prices the agent invented. No "Sorry, this is AI-generated" hedging that gives away the trick.

Fail conditions:

- Invented fact (metric, name, date, commitment)
- Voice drift (formal where casual called for, etc.)
- Wrong structure
- Hedging language that signals lack of confidence the operator didn't ask for

### Decision / surfacing skills

Skill watches a stream, surfaces things worth attention. Examples: stalled-matter-nudge, daily-needs-you-digest surfacing.

Pass conditions:

- True positives: surfaces every fixture that has a genuine anomaly
- False positives: surfaces ≤ 1 fixture per 5 that doesn't have an anomaly (i.e., specificity ≥ 80%)
- Suggested action is specific (not "look into this") and consistent with the rubric
- Severity scoring matches the rubric

Fail conditions:

- Missed a fixture that had a clear anomaly per the rubric
- False positive on a fixture that's clearly normal
- Generic suggested action

### Action skills (autonomous internal)

Skill takes an action: write to internal note, post Slack, log to a tracking file. Examples: matter-memo-on-update internal memos, document-receipt-logger receipt entries.

Pass conditions:

- Action completes successfully against the fixture's mocked tool
- Side effects match expectations (file written, Slack post composed)
- Audit log entry created
- No external-send actions for skills whose ceiling forbids them

Fail conditions:

- Tool call fails and the skill doesn't recover gracefully
- Action has unintended side effects
- Skill attempts an external-send when forbidden

## Captain calibration round

Per the plan, the first 3 skills get a Captain calibration pass before the rubric propagates to skills 4-58:

1. Author the SKILL.md + references for 3 skills (inbox-triage, retainer-hours-reconciler, status-report-assembler). retainer-hours-reconciler was retired 2026-09-29.
2. Run each against its full fixture set.
3. Surface 5 fixture-output samples per skill to Captain.
4. Captain reads each output and labels it autonomous / draft / fails using gut + the rubric above.
5. Compare agent's rubric-driven labels to Captain's labels. Any disagreement is a calibration target:
   - If Captain is consistently more conservative → tighten the rubric thresholds
   - If Captain is consistently more lenient → loosen
   - If disagreement is per-fixture and not systematic → discuss the specific cases, tighten the per-fixture pass criteria

6. Once 3-skill calibration round produces ≥ 80% agent-Captain agreement, the rubric is locked. Propagate to remaining 55 skills.

## Per-fixture audit trail

Every test run logs the following to `operator/grading/runs/<skill>/<fixture-id>-<timestamp>.json`:

```json
{
  "skill": "inbox-triage",
  "skill_version": "7c8e9f",
  "fixture_id": "marketing-inbox-001",
  "run_timestamp": "2026-05-20T16:34:11Z",
  "tokens_in": 8243,
  "tokens_out": 1521,
  "tool_calls": [
    { "tool": "mcp:google-gmail.search", "duration_ms": 412 },
    { "tool": "mcp:google-gmail.get", "duration_ms": 287 }
  ],
  "verdict": "autonomous",
  "verdict_reasoning": "...",
  "safety_violations": [],
  "output_path": "operator/grading/runs/inbox-triage/marketing-inbox-001-2026-05-20T16-34-11Z.md"
}
```

The audit trail is what makes the grading matrix defensible: when Captain asks "why is inbox-triage at draft_for_review?", we point at the verdict reasoning across the fixture set.

## Cost-per-customer rollup

For each skill graded, the audit trail provides tokens + tool calls per fixture. The rollup script in `operator/grading/rollup.py` (pending Phase E) sums these across a customer's enabled-skill set + estimated cadence (from the skill's SKILL.md frontmatter `cost_estimate`) to produce a monthly cost projection per customer.

This is the input to the SKU margin check (retainer figure: `venturecrane/engagements:pricing/`): if a typical customer's projected cost is < $300/mo, the SKU has 94%+ margin pre-support-labor. If it's $1500+, the SKU needs re-architecting before customer-2 signs.

## When to update the rubric

- After Captain calibration produces > 20% disagreement on a clear pattern, the rubric needs tightening or loosening for that skill type.
- After a real customer surfaces a failure mode no fixture covered, the fixtures get extended AND the rubric checks for that mode are added.
- After Hermes / MCP-server behavior changes (model update, tool deprecation, etc.), re-baseline and re-grade.

The rubric is not frozen. It is the working contract.
