# Logic in code: how to resume (paused 2026-11-04, ~11:10 MST, by the Captain)

Paused so another agent can rework the development process first. Everything below is where the work stands; nothing is lost if this file, the plan beside it and the git branches survive.

## What this is

The 2026-11-04 audit found the model restating, computing or deciding facts code already holds across the Operator's job lanes; the approved plan (`plan.md` here; critique in `critique-devils-advocate.md`) builds six mechanisms with Opus agents in three waves. ADR 0104 records the design. Read `plan.md` first, then `brief-program.md`, then the per-workstream briefs (`brief-*.md`). The briefs refer to the lead's old scratchpad path for themselves; on resume, hand agents THESE paths instead.

## State of the repo (verify with `gh pr list` and `git fetch`; Law 10)

- **Merged to main today:** #3733 (drafting evidence-first core), #3735, #3736, #3734 (the `jobs` rename, ADR 0103: the runner is `operator/runners/jobs/jobs/`).
- **#3741 `logic-in-code/token-sdk` (W0):** OPEN, all 13 checks green, reviewed by the lead (comment on the PR). The SDK resolution gate, observe|enforce mode, `resolve_matter_by_id`. **Merge first** (the classifier refuses agent merges; `publish-audit.sh` line 1 does it, or `gh pr merge 3741 --squash`).
- **#3742 `logic-in-code/fixed-wording` (C):** OPEN, reviewed by the lead; `substrate` check failed on one runner test (`runners/.../tests/test_allowance_decide.py:239`, two phrases pinned in the chronology skill's DELIVER text); the agent was fixing it in the skill text when paused. Check the branch's last commit and re-run checks.
- **A `logic-in-code/connector-values`** and **B `logic-in-code/writes-and-conflict`**: branched from `origin/logic-in-code/token-sdk` (not main); each must `git rebase --onto origin/main origin/logic-in-code/token-sdk` once #3741 is squash-merged. Their worktrees: `.claude/worktrees/agent-a9b8cc8cca1af4e88` (A) and `.claude/worktrees/agent-ad9ad7b51ae95a6d1` (B). B's last report: committed 314a71fe, push pending the hook (its grep test needed one line in trust-balance-nudge, which B was told to add itself). A's last report: signatures settled (see "A's signature changes" below). Their pause replies are appended at the bottom of this file as they arrive.
- **W6 `logic-in-code/litigation-events`:** started minutes before the pause; worktree `.claude/worktrees/agent-a8183a514b666fc86`; pause reply at the bottom.
- **Audit + ADR 0104 branch:** `worktree-audit-model-as-logic` in `.claude/worktrees/audit-model-as-logic`, rebased onto main at a137f20f, two commits (51dac277 audit, 66a45559 ADR 0104), **not pushed** (classifier refused; `publish-audit.sh` pushes and opens the PR). It adds `docs/reviews/model-as-logic-audit-2026-11-04{,-agent-reports}.md`, `docs/adr/0104-logic-in-code-six-mechanisms.md`, index entries, regenerated `docs/handbook/adr-index.md`.

## Merge order

#3741 (W0) -> A -> B (rebased on A; shares register() lines and trust-balance-nudge) -> C (#3742, independent) -> wave 2 lanes (W6, then W7/W8/W9 as they land; W9 imports A's `printed_money`).

## A's signature changes (B's skills depend on them)

`add_medicals_row(matter_id, source_file_id, provider_name, charge, service_start_as_printed, service_end_as_printed, account_number, claimant_index, patient_name, provider_address)`; `add_negotiation_rows` gains `source_file_id` (required with `operator_tab=true`), amounts and dates as printed; `resolve_invoice_matter` gains `date_of_birth_as_printed` and the optional group `sha256, first_page, last_page, message_id` (all four or none); `stage_vendor_invoice` and `file_attachment_pages_to_matter` gain optional `message_id`. file-work-requests does not call `add_medicals_row` (SKILL.md:451 forbids charges there), so B needs no Medicals-charge lines in it. B adds the trust-balance-nudge resolver line itself (A was told not to).

## Not yet started

W7 negotiation-casefile-schemas, W8 chronology-prompt, W9 demand-specials-expiry (wave 2; briefs not yet written, specs are in `plan.md`), wave 3 (W11, W12), and A5 (demand evidence-first digest on #3733's package).

## Proofs still owed (none run; all `(runtime)` rows unmet)

Token observe cycle (one daily cycle on pilot-smokeball and A&P, read the `would_refuse` rows, zero on the post and chronology paths) before any enforce; pilot probes for the post ledger, the conflict check and the fixed wording with `[SMD-PROBE]` artifacts torn down the same session; $0 rehearsals in the A&P ops workspace for the runner lanes against answer keys written first; one capped A&P dry-run per lane after the Captain's yes. Per-stage cost baseline: `ledger.report` over surviving `usage-ledger.jsonl` files in the ops workspace before W8 merges.

## How to resume

1. `/sos`, then read this file and `plan.md`.
2. Run `publish-audit.sh` (Captain `!`) if #3741 and the audit PR are still unpublished; merge in the order above as checks go green.
3. For each unfinished workstream: spawn an Opus agent (`isolation: worktree`, `mode: acceptEdits`) with `brief-program.md` + its brief + "continue from branch <x> at <sha>; read the branch's last wip commit body for done/half-done/next". Check for the existing worktree first (`git worktree list`); a worktree whose branch is pushed can be removed and recreated from the branch.
4. Keep the cap of four agents; wave 2 lanes are disjoint from each other and from wave 1.

## Where this kit lives

Committed in the repo at `docs/plans/logic-in-code/` on branch `worktree-audit-model-as-logic` (the audit + ADR 0104 PR), so any machine has it after `git fetch`. The briefs mention the original session's scratchpad path for `brief-program.md`; on resume, hand agents the repo paths instead. A copy also sits at `~/.claude/plans/logic-in-code/` on the Mac the work was paused on.

## Branch state at 11:19 MST (from `git ls-remote` and `git worktree list`)

- On origin: `logic-in-code/token-sdk` 014fe335 (#3741), `logic-in-code/fixed-wording` 1d93260f (#3742).
- Local only, unpushed (publish-audit.sh step 3 pushes them from their worktrees): `logic-in-code/connector-values` at dd14610b (worktree `agent-a9b8cc8cca1af4e88`), `logic-in-code/writes-and-conflict` at d9431817 (worktree `agent-ad9ad7b51ae95a6d1`), `logic-in-code/fixed-wording` local tip 74cc501e (one commit past the pushed 1d93260f: the runner-test fix; worktree `agent-af4d46e2ae25807ae`). If a push is refused by its pre-push hook (`npm run verify`), the branch stays local to that Mac until the hook's failure is fixed there; `git -C <worktree> log -1` shows the wip commit body with done / half-done / next.
- W6 `litigation-events`: no edits; its worktree was removed at the pause. Start it fresh from `brief-litigation-events.md` plus the design notes below.

## Pause replies from the agents (appended at pause time)

### B writes-and-conflict (FINISHED, committed d9431817 on `logic-in-code/writes-and-conflict`, unpushed, no PR)

The pre-push hook refused the push on a 60 s timeout of `tests/medchron-scrub.test.ts` while two other agents' `npm run verify` loaded the machine; run alone that file passes (417/417). Everything else green: connector 1518, SDK 58, overlay 7434, substrate 5252, pyright 84, ruff clean. Push from the worktree on a quiet machine (publish-audit.sh does it), then open the DRAFT PR with the body `pr-body-writes-and-conflict.md` in this kit (title: "feat(operator): a write on a matter the Operator chose opens only with that matter's resolution, and an outside sender's email moves nothing until the conflict check clears it"), base `logic-in-code/token-sdk` until #3741 merges, then `git rebase --onto origin/main origin/logic-in-code/token-sdk`, and rebase again after A merges (B's edits sit in A's modules' `register()` lines and in trust-balance-nudge). Built: 17 create/render tools gated at their registration lines (`create_task`/`create_event` with `matter_arg("matter_id", optional=True)`: a firm-general task with no matter opens without a token; the five object-matter writes stay ungated and are pinned in conformance; `EXPECTED_RESOLUTION_GATED` filled); `conflict_check` connector READ with the rule table (hit / clear / search_failed, `subject_line` added, a clear counts only when `sender_email` is the turn's verified sender, the one clearance task written by the tool, a second call returns the same task); `shared/conflict_facts.py` + `hermes-smd-trust/conflict_fence.py` + `inbound.turn_origin()` (verdict recorded from the trust plugin's own `on_post_tool_call`; non-roster email senders on Smokeball seats get no write or send until clear, nothing after a hit; webhook turns, roster senders and seats without Smokeball never fenced); 33 skills updated and version-bumped (file-work-requests step 1 = `resolve_invoice_matter`, step 1b re-resolves before each later write, negotiation figures as printed; router step 2 = the one `conflict_check` call, rubric and algorithm rewritten; task-list-keeper memos carry the matter number); `tests/skill-matter-resolution.test.ts` reads the gated set from the conformance pin. Mutations caught: dropping the `resolved_matter_id` clause, dropping the roster exemption. **Behaviour change to verify on the pilot:** formal service emailed straight in by an OUTSIDE sender is now fenced (a hit leaves a clearance task instead of a capture); roster-forwarded service still skips the check. **Proofs owed:** a `[SMD-PROBE]` outside sender on pilot gets exactly one clearance task and no draft; Smokeball's `ContactId` filter on `/matters` returns a matter for someone attached only as other-side counsel or adjuster (opposing-counsel and carrier hits depend on it, untested off-seat); one observe-mode daily cycle of `would_refuse` rows before enforce.

### W6 litigation-events (no edits; branch exists locally, nothing committed or pushed)

Worktree `.claude/worktrees/agent-a8183a514b666fc86`, branch `logic-in-code/litigation-events` from a137f20f. Test venv at `/tmp/litigation-events/venv` (Python 3.12; 3.11 cannot install `requirements/jobs.txt`; CI uses 3.13); baseline 3,444 passed, 11 skipped, 10 errors in `test_records_*` until `./connectors/_contract` is installed (CI installs it). Design notes to carry into the build: (1) removing the model's `status` strands Dropped, UIM respondent, firm's client, service rejected and appeared-no-answer, so the schema needs dated events `dropped`, `service_rejected`, `appeared`, `default_set_aside` and a defendant `role` (UIM respondent, firm's client); planned precedence: role, dismissed, settled, dropped, default+answer = Unclear, default, answered, appeared, served, rejected, out for service, not served. (2) Keep the stored status shape with `status_from`; `defstatus.reconcile` applies only to seeded statuses; a note contradicting a derived status still holds under the note rule. (3) A verify/audit verdict that overturns a status is logged as unmatched; an "unclear" verdict still marks the defendant Unclear. (4) `read.py` is 547 lines, over the 500 ceiling: split state conversion and verdict code into new modules under `litigation/`. (5) Discovery12 `to_result` needs `today` passed in; `batch._RESPONSE_RANK` and `carry._stale` keep the "not yet due" string because code still writes it.
