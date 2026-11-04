Stacked on #3741 (W0 `token-sdk`); base is `logic-in-code/token-sdk` until that merges, then this rebases onto main (`git rebase --onto origin/main origin/logic-in-code/token-sdk`). Rebase after A (`connector-values`) merges as well: the register lines sit in A's modules.

## Why

The 2026-11-04 model-as-logic audit, mechanisms 2 and 5. Two places where code held the answer and the model decided it:

- 17 Smokeball create and render tools took a bare `matter_id`, so a model that settled on a matter in its own head could write on it (the 2026-09-18 wrong-matter defect, on every lane but two).
- The router's conflict step was prose: the model chose whether to search, what counted as adverse, and wrote the clearance task's words itself. Nothing in code knew a check had been owed.

## What changed

**Mechanism 2: the gate declared on every write that targets a matter the model chose.**

- `requires_resolution=matter_arg()` at the registration line of: `create_folder`, `add_file`, `file_attachment_to_matter`, `render_docx_template`, `render_docx_draft`, `create_memo` (server.py decorators), `add_workbook`, `add_negotiation_rows`, `add_medicals_row`, `add_medicals_provider`, `render_firm_form_letter`, `render_sr1`, `render_sr19`, `render_funding_case_eval`, `prepare_records_order` (one import line plus the one argument at each module's `register()`; no tool body touched).
- `create_task` and `create_event` take `matter_arg("matter_id", optional=True)`, a small SDK addition: their matter is optional by signature (firm-general tasks exist), and a call that names no matter has no matter to misfile on, so it opens with no claim and no row. A call that names a matter is gated exactly as the strict ones; a resolver that raises is never an open door.
- Not gated, pinned in conformance: `update_task`, `update_event`, `update_memo`, `delete_file`, `place_records_order` (their matter is the object's). `stage_vendor_invoice` and `file_attachment_pages_to_matter` keep their in-body checks.
- `EXPECTED_RESOLUTION_GATED` filled (17), `EXPECTED_OPTIONAL_MATTER` pinned, conformance schema assertion covers all 17.
- **Skills.** 36 skills name a gated write today (`grep -lE '\b(create_task|create_event|create_folder|add_file|file_attachment_to_matter|render_docx_template|render_docx_draft|create_memo|add_workbook|add_negotiation_rows|add_medicals_row|add_medicals_provider|render_firm_form_letter|render_sr1|render_sr19|render_funding_case_eval|prepare_records_order|stage_vendor_invoice|file_attachment_pages_to_matter)\b' operator/skills/*/SKILL.md`); the brief's 35 omitted vendor-invoice-intake. A owns 3 (combined-post-intake, vendor-invoice-intake, trust-balance-nudge); this PR updates the other **33**, each with a version bump, plus ONE sentence in trust-balance-nudge (by the lead's call, so this PR is self-consistent; A does not add it): "Before the memo, call `resolve_matter_by_id` ... and pass its `matter_resolution` to `create_memo`." 31 gain a "Matter writes open with a resolution" section (resolve_matter_by_id with the id and number ONE read returned, immediately before each write; single use; anything but `unique` writes nothing). `file-work-requests` step 1 is now `resolve_invoice_matter` on her own words (a client's name alone never resolves), plus step 1b (a fresh `resolve_matter_by_id` per later write), and step 2k takes negotiation amounts and dates exactly as printed (per A's signature change). `matter-inbox-router`'s email filing passes the verdict's `matter_resolution`.
- `task-list-keeper` memos now carry the snapshot's `matter_number`, the casework validator accepts it, and the casework finish note tells the turn to resolve each memo's matter before `create_memo`.
- **Grep-driven test** `tests/skill-matter-resolution.test.ts`: reads the gated set from the connector's own `EXPECTED_RESOLUTION_GATED` pin (no second list) and fails any SKILL.md that names a gated write and neither resolver. Passes on this branch.

**Mechanism 5: the conflict check is a tool whose recorded result the trust gate enforces.**

- New connector read `conflict_check(sender_name, sender_email, subject_line, named_parties, resolved_matter_id)` (`conflict_check.py`), the rule table as written: `hit` iff the sender or a named party is a contact in an adverse role (other side; a counsel relationship or an insurance relationship on an other-side role) on an OPEN matter that is not `resolved_matter_id`; `clear` otherwise; `search_failed` on any tenant fault or a request it cannot search completely (no sender name, more than 10 parties, more open matters than it reads), never clear. On `hit` it writes the ONE clearance task itself: subject `Conflict clearance: inbound from <sender> re <subject line> (held, no reply sent)`, plain ASCII, due next business day on the firm's clock, on the resolved matter else firm-general, owned by that matter's responsible staff (else the first hit matter's), note composed from Smokeball's own contact names and roles, never message-body text. A second call for the same message returns the same task (in-process; and found again on the matter's open tasks after a restart). Classified READ in manifest, `action_classes.py`, the firm-record-read list and the provenance seeding list.
- `subject_line` was added to the briefed signature: the rubric's task subject needs it.
- Overlay: `shared/conflict_facts.py` (per session and turn message id; `hit` sticky; a `clear` counts only when its `sender_email` is the turn's verified sender and its `subject_line` the verified subject, when the provider gave one). `hermes-smd-trust/conflict_fence.py`: in an EMAIL turn on a seat that authors `mcp:smokeball`, from a sender not on the roster, every non-read call is refused until a clear is recorded ("Run conflict_check first; nothing is written or sent until it returns clear"), and everything after a hit. Recorded from hermes-smd-trust's own `on_post_tool_call` (the plugin that enforces it), so no hermes-smd-reply file is touched (C owns those). `shared/inbound.py` gains `turn_origin()`: the origin of the message id this turn's trusted prompt named, so a session handed several emails is still fenced on the current one.
- **The router's class does not reach the overlay.** `grep -rln -i "new_matter\|new-matter-intake\|new_client_inquiry\|intake_class\|route_class" operator/hermes-smd-overlay/plugins operator/hermes-smd-overlay/shared` finds only `message_structure.py` (a DECISION_CARD shape map) and a comment in `matter_binding.py`; no code path carries the router's classification. So the fence keys on what code knows: non-roster email senders only, scoped to Smokeball seats. Never fenced: webhook and handoff turns, roster senders (the daily post bundle, vendor invoices and forwarded served papers), seats without Smokeball. Fail closed: an email turn whose sender is not in the verified index, or an unreadable customer.yaml.
- Router skill: step 2 is the one `conflict_check` call and relays its verdict; the halt no longer creates its own task; rubric and algorithm describe the tool and the rule table; "the task exists" stays the eval. One consequence stated in the skill: formal service forwarded by a rostered colleague skips the check (the caller's carve-out); the same papers emailed straight in by an outside sender are fenced like any outside mail, so a hit leaves a clearance task rather than a capture nobody saw.
- Docs: connectors README, smokeball-surface.md, the handbook connector inventory (regenerated, 68 tools).

## Tests

- Connector: 1518 passed, 1 skipped (new: `test_conflict_check.py` 20, `test_gated_registrations.py` 7 through the real served surface in enforce mode, conformance pins).
- SDK: 58 passed (new: optional resolver opens absent / claims present / a raising resolver refuses).
- Overlay: 7434 passed, 41 skipped (new: `test_conflict_fence.py` 23, casework memo number).
- Substrate set (the CI pytest line): 5252 passed, 2 skipped; pyright ratchet holding at 84; ruff check and format clean across `operator/`.
- `npm run verify`: green (the pre-push hook). `medchron-scrub` timed out once under parallel load and passes alone.
- Mutations, each run once: dropping the `resolved_matter_id` clause (`hits = conflicts`) fails `test_already_resolved_to_that_matter_is_clear`; dropping the fence's roster exemption fails 6 tests (`test_a_colleague_on_the_roster_is_never_fenced_on_the_daily_paths` x6).

## Seat artifacts

- Image: `operator/hermes-smd-overlay/**` (trust fence, conflict_facts, inbound, escalation casework), `operator/connectors/_sdk/**`.
- Skills bundle (`.github/operator-artifact-paths.txt` `operator/skills/**`): 33 SKILL.md, router references, task-list-keeper review.py.
- Jobs bundle (`.github/operator-bundle-paths.txt` `operator/connectors/smokeball/**`, `operator/connectors/_sdk/**`): the registrations and conflict_check.

## Acceptance criteria

| AC                                                                                                                                                                                | Status | Evidence                                |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ | --------------------------------------- |
| The 17 create and render tools declare `requires_resolution`; the gated set is pinned in conformance and every schema requires `matter_resolution`                                | met    | `test_conformance.py`                   |
| A firm-general `create_task` / `create_event` opens with no resolution; one naming a matter is gated                                                                              | met    | SDK + `test_gated_registrations.py`     |
| Every skill naming a gated write names a resolver                                                                                                                                 | met    | `tests/skill-matter-resolution.test.ts` |
| `conflict_check` follows the rule table; one clearance task per message in the rubric's shape                                                                                     | met    | `test_conflict_check.py`                |
| The fence refuses writes and sends without a recorded clear, holds everything on hit, never fires on webhook turns or roster senders                                              | met    | `test_conflict_fence.py`                |
| (runtime) On pilot-smokeball, an `[SMD-PROBE]` outside sender adverse on a probe matter gets no draft and exactly one clearance task, torn down the same session                  | UNMET  | lead, on seat                           |
| (runtime) The ContactId matter listing returns a matter for a contact attached only as an other-side counsel or adjuster relationship (the premise opposing-counsel hits rest on) | UNMET  | lead, on seat                           |
| (runtime) One observe-mode daily cycle on pilot and A&P shows the `would_refuse` rows per lane before enforce                                                                     | UNMET  | lead, on seat                           |

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01WxHZSoHD5KPopoAr9CpPSF
