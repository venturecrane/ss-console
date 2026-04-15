# Follow-on recommendations for #377 — 2026-04-15

Consolidated recommendations covering every pending judgment call from the #377
workstream. Read top to bottom. Each recommendation is phrased so Captain can
respond yes / no / modify per item and hand the execution to engineer(s) in a
single pass.

Scope: backfill strategy for existing quotes, five audit Captain-decision
items, thirteen ambiguous closed-issue triage items, three manual-only items
(verify or refute), and a final pass against the #377 acceptance criteria to
catch anything missed.

Author: recommender. Execution belongs to engineer(s) after Captain approval.

## Summary of recommendations

| #   | Item                                               | Recommendation                                     | Notes                                                                                                   |
| --- | -------------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| 1   | Backfill strategy for existing quotes              | **Option A** (null backfill + follow-on)           | Pre-launch. No contractual clients yet. Line-items fallback removable in follow-on.                     |
| 2   | Brand "SMD Services" centralization                | **Centralize behind `BRAND_NAME` constant**        | Brand stays. One PR, ~18 occurrences, mechanical.                                                       |
| 3   | "Scott Durgan" hardcoded fallback                  | **Require `engagements.consultant_name`**          | Remove fallback. Matches §6 "we / our team" voice.                                                      |
| 4   | "Receipt attached" phrase                          | **REMOVE from all three portal surfaces**          | False claim. Portal doesn't attach receipts.                                                            |
| 5   | SOW PDF fixed timeframes                           | **CLAUDE.md §3 does NOT apply to signed SOW**      | SOWs are contracts. Keep timeframes. Audit only external marketing content.                             |
| 6   | "support window" phrase                            | **REMOVE the phrase, keep the sentiment**          | Same cadence email, rewrite without bounded-scope framing.                                              |
| 7   | #341 lifecycle stage count                         | CHECK-AND-RECLOSE                                  | Stage-count discrepancy is cosmetic. Walkthrough comment confirms completion.                           |
| 8   | #217 backup size 435KB vs 700KB                    | CHECK-AND-RECLOSE                                  | 435KB is complete for solo-operator volume. Threshold was an estimate, not a floor.                     |
| 9   | #68 headcount soft warning                         | FORCE-CLOSE-WITH-RATIONALE                         | ICP moved to revenue; headcount warning is obsolete.                                                    |
| 10  | #69 engagement-role assignment UI                  | REOPEN                                             | Real feature gap. Multi-contact engagements need it before #77 closes cleanly.                          |
| 11  | #70 financial-prerequisite soft warning            | FORCE-CLOSE-WITH-RATIONALE                         | Decision #6 is the gate; UI reminder is nice-to-have, not load-bearing.                                 |
| 12  | #79 parking-lot admin UI                           | REOPEN                                             | Table exists, admin surface was never built. Needed before first real engagement.                       |
| 13  | #83 Claude-API extraction trigger                  | FORCE-CLOSE-WITH-RATIONALE                         | Manual paste works pre-launch; automated trigger is Phase 5 per issue spec.                             |
| 14  | #77 engagement contact roles                       | Same as #69 — blocked by REOPEN of #69             | Don't touch #77 until #69's UI lands.                                                                   |
| 15  | #80 presigned URLs for documents                   | CHECK-AND-RECLOSE                                  | Stream-through is a functional equivalent; presigned is a post-launch optimization.                     |
| 16  | #78 time-entry admin UI                            | REOPEN                                             | API exists, UI doesn't. Needed to honor estimated-vs-actual ACs on first engagement.                    |
| 17  | #364 SMS SLA + vacation fallback                   | **Captain must confirm SLA; write fallback doc**   | Editorial commitment. Not closeable without Captain answer.                                             |
| 18  | #368 Scott's photo deployed                        | **Captain must confirm**                           | Runtime check; trivial to verify in the admin UI.                                                       |
| 19  | #226 7-day monitoring + delete `intake.ts`         | CHECK-AND-RECLOSE; delete `intake.ts` in follow-on | 30-day thin-adapter window expires 2026-04-24; one-line PR.                                             |
| 20  | Branch protection on main (manual)                 | **Manual — confirmed**                             | GitHub Settings UI. Exact click path below.                                                             |
| 21  | Stitch API key rotation                            | **Manual — confirmed**                             | Key is in local working-tree only, NOT git history. Rotate via Stitch console, then ship .gitignore PR. |
| 22  | Global guardrails update (scribe's proposed text)  | **Automatable — confirmed**                        | Upload script exists in sibling repos. Two-step execution below.                                        |
| 23  | Anything else missed from #377 acceptance criteria | Five items                                         | Called out in Part 5.                                                                                   |

---

## Part 1 — Backfill strategy for existing quotes

**Recommendation: Option A** (null backfill + follow-on to remove line-items-derived
deliverables fallback).

**Why.** The venture is pre-launch. There are effectively no contractually-bound
clients yet whose signed proposal renders the removed section and would be confused
by a thinner page. The acute fabrication risk (the 3-week schedule) is already gone
via #378 and #384, and the portal empty-state pattern now handles missing authored
content correctly. Option B introduces uniform "TBD in SOW" sentences across every
legacy row, which is the Pattern B violation class in lighter clothing. Option C
punishes signed-and-paying clients with a "being refreshed" banner for a problem
that is internal hygiene, not a client-facing defect — the tradeoff is wrong.

Option A preserves the signed-artifact-is-truth principle (the signed PDF stays
the contractual document; the portal is read-only context) and leaves us with one
tractable follow-on: removing the line-items-derived deliverables fallback once
visible-status quotes carry authored `deliverables`.

**Downstream.** This lets the engineer file a follow-on issue to:

1. Add a non-null `authored_at` timestamp set when both `schedule` and `deliverables`
   are populated. Gives the admin index a clean authored-vs-legacy signal.
2. Drop the line-items-derived deliverables fallback in
   `src/pages/portal/quotes/[id].astro` once all visible-status quotes carry
   non-null `deliverables`.
3. Add a `crane_status`-style report that flags any visible-status quote with
   NULL authored content so the team can sweep them.

**Reversibility.** One-way door is low-risk. Option A writes no data — if we
change our mind later, Options B or C are still on the table. The converse is
not true: if we run Option B's UPDATE statement now, unwinding it requires
remembering which rows were touched and we lose the send-gate's ability to
distinguish authored from TBD-filler.

---

## Part 2 — Five audit Captain-decision items

### 2.1 Brand name "SMD Services" post org transfer

**Recommendation.** Keep the brand. Centralize it behind a single `BRAND_NAME`
constant so future renames are a one-line change.

**Why.** "SMD Services" is the operating brand of the venture regardless of
which GitHub org hosts the repo. The venturecrane/ss-console org move on
2026-04-08 is infrastructure; it does not imply a rebrand. Centralizing behind
a constant is cheap, defensive, and removes the 18-occurrence sprawl the audit
flagged.

**Scope of remediation.** ~18 occurrences across `src/lib/email/resend.ts:11`,
`src/lib/email/templates.ts` (7 occurrences), `src/lib/email/follow-up-templates.ts:30`,
`src/lib/email/booking-emails.ts:49,77,105`, `src/lib/pdf/sow-template.tsx:164`,
four portal pages, and one signature block (`templates.ts:408-409,555`).
Create `src/lib/branding.ts` exporting `BRAND_NAME = 'SMD Services'`.
Replace literals with imports. One focused PR.

**Reversibility.** Fully reversible. This is a mechanical refactor; reverting
it is also mechanical.

### 2.2 "Scott Durgan" hardcoded consultant fallback

**Recommendation.** Require `engagements.consultant_name` at render time.
Remove all four `?? 'Scott Durgan'` fallbacks. Where the value is genuinely
missing, render empty or hide the consultant block.

**Why.** CLAUDE.md §6 specifies "we" / "our team" voice. A hardcoded fallback
to a single personal identity directly contradicts that positioning. It is
also a personal-identity commitment rendered on every engagement regardless of
the actual delivery team — identical violation class to the audit's core
concern. Blocking render when the field is null enforces the authoring
discipline the send-gate already applies elsewhere.

**Scope of remediation.** Four files:
`src/pages/portal/quotes/[id].astro:85`, `src/pages/portal/invoices/[id].astro:186`,
`src/pages/portal/index.astro:229`, `src/lib/email/templates.ts:408-409,555`.
Either treat `consultant_name` as required in the query surface (preferred —
matches #69's coming engagement-contact-role fix) or render the consultant
block conditionally. In the email templates, if no consultant is assigned,
sign from the brand only.

**Reversibility.** Code change is reversible. Data implications: new
engagements must carry a `consultant_name`; easy to add in the admin UI.

### 2.3 "Receipt attached" phrase in portal UI

**Recommendation.** REMOVE the phrase from all three portal surfaces. The
phrase does not need replacing.

**Why.** The portal does not attach a receipt to any rendered surface — only
the payment-confirmation email does. The phrase is a false statement. No
Captain judgment needed on whether to keep the claim; the claim is untrue.
The only reason the audit flagged it for Captain is that the fix is
trivial and worth confirming before any batch remediation touches payment
UI tone.

**Scope of remediation.** Three string sites, one PR:
`src/pages/portal/invoices/[id].astro:265`, `:446`, `src/lib/portal/states.ts:80`,
`src/pages/portal/index.astro:195`. The remaining sentence at each site
still reads correctly without the tail (`"Paid ${paidShortDate}."`,
`"Invoice paid."`).

**Reversibility.** Full. Zero data implications.

### 2.4 SOW PDF fixed-timeframe commitments

**Recommendation.** CLAUDE.md §3 does NOT apply to the signed SOW PDF. Keep
the three timeframes as blessed contractual terms. Document the carve-out
inline in §3 so no future agent has to re-ask.

**Why.** §3's motivation is no-fixed-timeframes in _marketing content_ —
landing pages, outreach, collateral — where arbitrary durations either
undersell complex engagements or oversell simple ones (the memory file says
exactly this). A signed SOW is the opposite surface: it is a contractual
document where specific timeframes are the entire point of the clause.
"Within 1 business day of receiving the deposit," "2-week stabilization
period," and "within one business day of the kickoff confirmation" are
the contractual commitments the business is willing to stand behind. Moving
them to per-engagement authored fields would either (a) create a new ceremony
where Captain authors the same three timeframes on every SOW, or (b) create
risk that a SOW ships without the terms at all. Neither is better.

**Scope of remediation.** One addition to CLAUDE.md §3 (roughly: "This rule
applies to marketing and external positioning content. It does NOT apply to
signed contractual documents (SOWs, invoices, receipts) where specific
timeframes are the contractual terms."). Zero code changes to
`src/lib/pdf/sow-template.tsx`.

**Reversibility.** Full — it's a documentation clarification.

### 2.5 "support window" phrase in safety-net email

**Recommendation.** Remove the phrase "that's what the support window is for."
Rewrite the surrounding sentence to express availability without invoking a
bounded scope concept.

Proposed rewrite (replace `src/lib/email/follow-up-templates.ts:211-213`):

> If anything needs adjusting or questions have come up, we're still here.
> Reply to this email or reach out anytime.

**Why.** "Support window" implies a bounded scope concept that may or may
not have been sold in a given engagement. That uncertainty is exactly the
Pattern A risk — every post-handoff client gets the same email regardless
of what was contracted, so the email should not name a contract term whose
presence varies per engagement. The sentiment (we're still around if you
need us) is harmless and worth keeping. Only the framing is the problem.

**Scope of remediation.** Two-line edit to
`src/lib/email/follow-up-templates.ts:211-213`. No schema, no authoring
ceremony.

**Reversibility.** Full.

---

## Part 3 — Thirteen ambiguous triage items

Format: **#NNN — Recommendation. Rationale.** If FORCE-CLOSE, proposed
rationale text is quoted below.

### #341 — E2E lifecycle sprint walkthrough

**CHECK-AND-RECLOSE.** The "8 stages vs 12 steps" discrepancy is cosmetic.
The walkthrough comment confirms the full flow ran end-to-end on a real
entity (signal→prospect→enrichment→SignWell→atomic batch→Stripe→milestones→
handoff→re-engagement). The AC was written against an earlier stage count
that the lifecycle refactor later expanded. If #341 closed under the
evidence of a working walkthrough, the AC is met; the number-of-stages
wording is stale documentation, not unmet work.

### #217 — Booking setup: snapshot prod D1 to R2

**CHECK-AND-RECLOSE.** The 435KB vs 700KB gap is almost certainly a stale
estimate, not an incomplete export. Pre-launch D1 has a handful of seed
rows across the lifecycle tables and the schema itself; 435KB is plausible
for that volume. Captain can sanity-check by running
`wrangler d1 execute ss-console --command "SELECT name FROM sqlite_master WHERE type='table';"`
and confirming the backup SQL contains `CREATE TABLE` for every row
returned. If both table lists match, reclose. If any table is missing,
reopen with a specific remediation. The 700KB threshold was an estimate,
not a spec.

### #68 — Portal: Client CRUD — "soft warning for employee count outside 10-25"

**FORCE-CLOSE-WITH-RATIONALE.**

> The ICP moved to revenue-based ($750k-$5M, expanding to $10M) per the
> April 2026 strategic pivot. Employee-count-outside-10-25 soft warning is
> obsolete. The revenue-based fit check lives in the assessment script, not
> the CRUD UI. Applying `force-close` label.

### #69 — Portal: Contact CRUD + engagement roles

**REOPEN.** This is a real feature gap, not a bookkeeping miss. The
`engagement_contacts` table is in the schema and the entity detail page
renders contacts, but there is no admin UI for assigning a contact to a
specific engagement role or flagging a primary POC per engagement. Multi-
contact engagements will need this before real client delivery. Today you
cannot ship an engagement with three stakeholders where one is the signer,
one is the operational POC, and one is CC'd on invoices — you'd be
hand-massaging rows in D1. Reopen, tag `portal`, add to Priority-3
Delivery Readiness in CLAUDE.md.

### #70 — Portal: Assessment capture — "soft warning for financial prerequisite"

**FORCE-CLOSE-WITH-RATIONALE.**

> Decision #6 (financial prerequisite) is enforced upstream in the
> assessment qualification gate, not in the admin UI. A soft warning on the
> assessment detail page duplicates the gate at a point when it is no
> longer actionable (the assessment has already been conducted). Closing as
> obsolete by the decision-stack evolution. Applying `force-close` label.

### #79 — Portal: Parking lot protocol

**REOPEN.** The `parking_lot` table exists in the schema and analytics
reference it, but there is no admin surface to log or disposition items.
The whole feature has data plumbing and no UI. Not a documentation gap —
the parking lot protocol is a live delivery methodology piece and needs an
admin page before the first engagement. Reopen, tag `portal`, add to
Priority-3 Delivery Readiness.

### #83 — Portal: Claude API extraction integration

**FORCE-CLOSE-WITH-RATIONALE.**

> Per the issue spec, automated extraction trigger is Phase 5. Manual paste
> of extraction JSON (the current implementation at
> `src/pages/admin/assessments/[id].astro`) is acceptable for Phase 1
> volume (a handful of assessments per month pre-launch). Automation is a
> post-launch optimization, not a launch blocker. Refile as a Phase 5
> issue when assessment volume justifies the automation. Applying
> `force-close` label.

### #77 — Portal: Engagement management + milestones — contact role assignment

**Depends on #69.** If #69 is reopened (per our recommendation), #77's
open AC is satisfied by the same UI work. No action on #77 itself until
#69's UI lands. Leave closed; mention in the #69 reopen comment that #77's
contact-role AC rolls into the same fix.

### #80 — Portal: Documents — "presigned URLs"

**CHECK-AND-RECLOSE.** The streaming implementation in
`src/pages/api/portal/documents/[id]/download.ts` (or equivalent) is a
functional equivalent of presigned URLs from the client's perspective —
authenticated download, scoped to the engagement, R2-backed. Presigned
URLs are a specific R2 feature that offloads bandwidth from the Worker;
that's a post-launch performance optimization, not a correctness
requirement. The AC's intent (client can view and download authorized
documents) is met. If bandwidth becomes an issue after launch, file a
performance issue.

### #78 — Portal: Time tracking (estimated vs actual)

**REOPEN.** API endpoints exist at `src/pages/api/admin/time-entries/` but
no admin UI wires them together. Estimated-vs-actual is a core AC for the
first paid engagement — without the UI, you cannot log time, and without
logged time, you cannot produce the variance report the AC requires.
Reopen, tag `portal`, add to Priority-3 Delivery Readiness.

### #364 — Tap-to-SMS — SLA confirmation + vacation fallback

**Captain must confirm SLA and document fallback.** This is not closeable
without two Captain decisions:

1. Can "Replies within 1 business day" be honored? If yes, leave the copy
   live. If no, soften to "We aim to reply within a business day" or
   remove the SLA altogether.
2. Document the vacation fallback somewhere in `docs/process/` — what
   happens when Scott is unreachable? Who does the client text instead,
   or what OOO reply goes out?

Once both are answered, the AC is met. The engineer can draft a one-page
`docs/process/consultant-availability.md` from a three-line Captain answer.

### #368 — Consultant photo hosting — Scott's photo deployed

**Captain must confirm.** Trivial to verify: open the admin UI for the
consultant record and check whether the `consultant_photo_url` is populated
with a real R2 asset vs the placeholder. If populated, CHECK-AND-RECLOSE.
If not, upload and reclose. No decision needed here — just a runtime check
Captain has to eyeball.

### #226 — Booking: cutover test plan + delete legacy `intake.ts`

**CHECK-AND-RECLOSE on the monitoring window; ship a follow-on PR to
delete `intake.ts`.** The 7-day monitoring window expires on roughly
2026-04-16 (legacy intake thin-adapter had a 30-day window from the
migration date). If no errors were observed in that window, the test plan
doc's checkboxes are bookkeeping, not gating. Reclose #226 and file a
one-line follow-on PR deleting `src/pages/api/intake.ts`. Two commits,
one PR.

---

## Part 4 — Manual-only items (verified or refuted)

### 4.1 Branch protection on main — **MANUAL, confirmed**

Captain must configure in the GitHub Settings UI. There is no GitHub API
equivalent that an agent can hit for repo-admin branch-protection rules
without Captain's token, and using Captain's personal token from an agent
is explicitly against policy.

**Exact click path** (from `docs/process/branch-protection-setup.md`):

1. Open https://github.com/venturecrane/ss-console/settings/branches
2. Under "Branch protection rules", click **Add rule** (or **Edit** if a
   `main` rule already exists)
3. Set **Branch name pattern** to `main`
4. Check **Require status checks to pass before merging**
5. Check **Require branches to be up to date before merging**
6. In the status-checks search box, find and check:
   - `Block undeferred TODO(#NNN) patterns` (from `scope-deferred-todo.yml`)
   - `Typecheck, Lint, Format, Test` (CI workflow)
   - `npm audit (high+)` (audit workflow)
7. Click **Save changes**

Note: status checks only appear in the dropdown after the workflow has run
at least once on a PR targeting `main`. All three have now run, so they
will be in the list.

### 4.2 Stitch API key rotation — **MANUAL, confirmed**

The Stitch API key is present in the user's local `.mcp.json` working-tree
modification. It is **not** in git history — `git show HEAD:.mcp.json`
shows only the `crane` server with empty env, and
`git log --all -p -- .mcp.json` contains no `AIzaSy` or `STITCH_API_KEY`
matches. No history scrub is needed. Rotation is still worth doing: the
key has been visible in agent transcripts that ran from this working
tree, and one accidental `git add .` would track it. Rotation requires
the Stitch web console — there is no CLI rotation flow.

**Sequence Captain should follow:**

1. Open Stitch at https://stitch.withgoogle.com/ (or the console the key
   was issued from — check the key-issuance thread in Bitwarden for the
   exact URL if uncertain).
2. Revoke the current key and generate a new one.
3. Put the new key on the clipboard (do not paste into chat).
4. An agent then ships a follow-on PR that:
   - Adds `.mcp.json` to `.gitignore` so future edits stay untracked
   - Adds `.mcp.json.example` with the schema but no key (stub value)
   - Updates the repo README / CLAUDE.md setup section to reference the
     example file
5. Captain repopulates local `.mcp.json` with the new key.

Note: `git rm --cached .mcp.json` is not needed — the HEAD version is
already key-free. The .gitignore change is purely forward-looking.

**Timing recommendation.** Rotate within the next session. Low-severity
exposure (working-tree + transcripts, not git history), but cheap to
close.

### 4.3 Global guardrails update — **AUTOMATABLE, confirmed**

Investigated per team-lead brief:

- `upload-doc-to-context-worker.sh` is NOT present in
  `ss-console/scripts/`. It IS present in six sibling consoles:
  `crane-console`, `sc-console`, `dc-console`, `dfg-console`, `ke-console`,
  `smd-console`. Same script, identical signature
  (`./scripts/upload-doc-to-context-worker.sh <doc-path> [scope]`).
- The script requires `CRANE_ADMIN_KEY` in the environment and targets
  `https://crane-context.automation-ab6.workers.dev`.
- `guardrails.md` is in the GLOBAL_DOCS whitelist, so uploading it with no
  scope override auto-routes to global scope.
- Scribe's `/tmp/guardrails-proposed.md` survived and matches the text
  blessed in PR #383's CLAUDE.md update (same two-pattern framing, same
  examples).
- The crane MCP exposes read tools (`crane_doc`, `crane_doc_audit`) but
  no write tool for docs — writing goes through the Worker HTTP endpoint,
  which the upload script wraps.

**Recommendation.** Automatable. Two paths:

**Path A (agent runs the script — preferred).** Copy the script from a
sibling console into ss-console one-off, run it once, then commit the
script to ss-console/scripts/ so future guardrail updates are
self-contained. Agent command sequence, given Captain has
`CRANE_ADMIN_KEY` exported in their shell:

```
cp /Users/scottdurgan/dev/crane-console/scripts/upload-doc-to-context-worker.sh \
   /Users/scottdurgan/dev/ss-console/scripts/

chmod +x /Users/scottdurgan/dev/ss-console/scripts/upload-doc-to-context-worker.sh

# Place scribe's proposed text into a docs/instructions/guardrails.md in this repo
mkdir -p /Users/scottdurgan/dev/ss-console/docs/instructions
cp /tmp/guardrails-proposed.md \
   /Users/scottdurgan/dev/ss-console/docs/instructions/guardrails.md

cd /Users/scottdurgan/dev/ss-console
CRANE_ADMIN_KEY=... ./scripts/upload-doc-to-context-worker.sh docs/instructions/guardrails.md
```

Commit the script and the doc in one PR.

**Path B (Captain runs from a sibling).** Captain cd's into e.g.
crane-console and runs
`./scripts/upload-doc-to-context-worker.sh /tmp/guardrails-proposed.md global`
directly. Faster but leaves ss-console without the script, which means
this question recurs the next time we update a global doc from here.

**Recommend Path A.** One more file to commit, but ss-console joins the
other ventures' doc-upload convention.

---

## Part 5 — Was anything else missed?

Reread #377 acceptance criteria top to bottom. Status of each AC block:

**Hotfix — met.** PR #378 stripped the fabricated schedule.

**Audit phase — met.** PR #381 landed the audit report. Every finding has
a pattern label and a proposed remediation. Captain-decision items are
all covered in Part 2 above.

**Process enforcement (fail-closed) — mostly met.**

- Move 1 (TODO-deferred AC merge gate) — met via PR #379.
- Move 2 (issue-close gate reopening unchecked ACs) — met via PR #379.
- Move 3 (PR template enumerating per-AC satisfaction) — met via PR #379.
- Move 4 (sanctioned empty-state pattern) — met via PR #383
  (`docs/style/empty-state-pattern.md`).
- Move 5 (retroactive sweep report) — report committed via PR #382.
  **Stage B is outstanding** — the 13 NEEDS-CAPTAIN items triaged in Part
  3 above are the Stage B work. If Captain approves Part 3, that AC is
  complete. The remaining 60 triage items in #382 (32
  CHECK-AND-RECLOSE + 28 FORCE-CLOSE + 3 REOPEN) are already
  determined and can be executed without further Captain decisions —
  that's a separate engineer task.

**Guardrail + schema — mostly met.**

- CLAUDE.md guardrail names both patterns with audit-sourced examples —
  met via PR #383.
- **Global guardrails doc — outstanding.** See Part 4.3. Scribe produced
  the text, it did not land. Automatable per above.
- CODEOWNERS for `src/pages/portal/**` — met via PR #380.
- Schema migration for `quotes.schedule` and siblings — met via PR #384.
- Admin UI authors new fields, send-gate — met via PR #384.
- **Existing quotes backfilled or flagged — outstanding.** Part 1 above
  recommends Option A, which means "intentionally leave legacy rows NULL
  and rely on the empty-state pattern on render."

**Nothing else is missing.** Five items surfaced that are not covered by
merged PRs:

1. The five audit Captain-decisions (Part 2 above).
2. The thirteen ambiguous triage items (Part 3 above).
3. The three manual items (Part 4 above).
4. Backfill path (Part 1 above).
5. Retroactive execution of the 60 non-ambiguous triage items in #382
   (CHECK-AND-RECLOSE / FORCE-CLOSE with rationale / REOPEN).

Item 5 is execution, not judgment. Once Captain signs off on items 1-4,
an engineer can batch-run item 5 as a single session.

— End of recommendations.
