---
title: Logic in code - the six mechanisms that keep a fact code holds from being retyped by the model
date: 2026-11-04
status: accepted
captain: Scott Durgan
related-adr: docs/adr/0050-operator-task-execution-framework.md, docs/adr/0083-authorship-model-output-classes.md, docs/adr/0086-matter-identity-send-binding.md, docs/adr/0087-chronology-package-content-control.md, docs/adr/0100-one-operator-core-and-authored-capabilities.md, docs/adr/0102-matter-files-on-a-staff-reply.md
enforced_by:
  - operator/connectors/smokeball/tests/test_conformance.py
  - operator/connectors/smokeball/tests/test_matter_resolution.py
  - operator/connectors/smokeball/tests/test_post_journal.py
  - operator/connectors/smokeball/tests/test_vendor_invoice.py
  - operator/connectors/smokeball/tests/test_medicals_tools.py
  - operator/workspace_broker/tests/test_held_wording.py
  - operator/workspace_broker/tests/test_held_wording_parity.py
  - operator/hermes-smd-overlay/tests/test_customer_config.py
  - docs/reviews/model-as-logic-audit-2026-11-04.md
---

# ADR 0104: Logic in code, the six mechanisms

- **Status:** Accepted (2026-11-04)
- **Decider:** Captain (the ask, 2026-11-04: "on more than one occasion we've discovered after the fact that some job solution designs relied on the model to handle the logic layer where it would have been better in many ways (cost, accuracy, time) to use code for the logic"; then "design a solution for all of this and start building"); mechanisms by the agent
- **Builds on:** ADR 0050 (execution classes), ADR 0083 (format is code's, the model fills content), ADR 0086 (matter identity binds at a seam code owns), ADR 0087 (the runner's gates are the content control), ADR 0100 (capabilities and the jobs bundle), ADR 0102 amendments 3 and 4 (the records reply's facts are written by code; a records request is a queued lane)
- **Record of the audit:** `docs/reviews/model-as-logic-audit-2026-11-04.md`, with the five lane reports beside it

## Context

Four fixes in one week each closed the symptom just seen: #3684 (a defendant's status read "Served, no answer in file" beside a note saying a default was entered), #3729 (a split dollars-and-cents box read at 100x), #3730 and #3732 (a transcript's line numbers spliced into a quotation), and the 11-02 records reply that retyped the finder's page ranges and got them wrong. The audit of 2026-11-04 read every job lane, the Smokeball connector and the in-turn skills the client seat enables, and found the same design flaw in four shapes:

1. **The model restates a fact code holds, and the restatement ships.** The post-intake record's `issue`, `placed_on` and `candidates` (the office manager's one daily email renders from them, and code checks only that filed pages were filed); the chronology's INDEX and FILES-SEEN blocks (exhibit titles, Diagnostic Highlights and the firm-facing Limitations line are built from the model's own counts); the delivered replies of every job lane but records; the casefile header.
2. **The model does arithmetic or date math.** The demand's specials total and its expiry weekday ("[INSERT weekday, date 30 days after the letter date]"), treatment-gap day counts, discovery "not yet due", a casefile statute inside 120 days, net to client, ledger totals, the trust-floor comparison.
3. **The model parses money and dates at the connector boundary.** Tools ask for `"1250.00"` and `YYYY-MM-DD` instead of the printed string; the connector's own parsers refuse `$`, commas and a split cents box, so the model converts and a misread is baked in before code sees it; the re-fetched bytes are never compared to the figure.
4. **The model asserts a status instead of reporting events.** A defendant's `status` is an enum with no source field while dismissal, settlement and default have no dated field at all; a negotiation offer whose letter does not name the plaintiff is written to the firm's row as "Joint, all plaintiffs."

The lanes are not "the model decides everything". The doorway, caps, round limits, drop policies, chunking, retries, sorting, exhibit numbering, document selection, courtdates, row placement and dedup are code already. What remained was narrower, recurred across lanes, and in each case one lane already had the fix the others lacked: the records lane renders its reply facts in code (ADR 0102 amendment 3), the vendor-invoice lane requires a resolution token (`resolution_token.py`, after the 2026-09-18 misfiling), the drafting lane parses printed money (`howell.money`), the update lane audits ICD codes as claims (`update/icd.py`), the broker refuses other words on a held litigation or casefile reply (`held_wording.py`, after job 01M5MWGB on 2026-10-24). Each of those was the second instance of a general thing built as a local fix (ADR 0103 §5).

## Decision

Six mechanisms, each built once and applied across every lane. A model call returns a value a script acts on; it never restates a fact code holds, never does the arithmetic, never parses a figure or a date code can parse, never asserts a conclusion code can derive from the events, and never composes the words that carry a fact to the firm.

### 1. Outcome ledger: the post record is derived, never typed

Every keying act on a scanned bundle (filing a letter's pages, staging a vendor invoice, adding a Medicals row, adding negotiation rows, resolving a letter's matter) writes its own outcome row to an on-disk ledger keyed by the bundle's sha256 AND the message id (so a re-sent PDF or a re-run of the same bundle never merges with a prior run), written in a `finally` so a tool that raises records its exception class as the outcome. `record_post_batch` takes from the model only `first_page`, `last_page`, `sender`, `kind` and `detail`; `placed_on`, `issue` and `candidates` are derived: the filing row gives the matter; the act the letter's `kind` requires (a medical bill needs a Medicals row, a vendor bill a staged expense, a settlement offer a negotiation row) that is missing or failed gives `tab_not_written` / `expense_not_staged` / `offer_not_written`, with "no attempt recorded" when it was never tried; no filing and a resolve verdict gives that NOT_PLACED issue with the verdict's candidates; neither gives `not_attempted`, listed for a person. A letter submitted with any of the three derived fields is refused and the refusal names the derived values. `kind` is the one residual model input, and it is a judgment (what the letter is), not a fact the system holds.

### 2. Resolution token on every write that targets a matter the model chose

The SDK's `ConnectorServer.tool()` takes `requires_resolution`, a per-tool resolver that names the target matter from the call's arguments. The registered MCP wrapper shows `matter_resolution` in the tool's schema as required, verifies the token against that matter before dispatch and consumes it after a successful write, exactly as `stage_vendor_invoice` does today; the original Python function is untouched, so the runner and the tests keep the plain contract. Nineteen create and render tools are gated. `update_task`, `update_event`, `update_memo`, `delete_file` and `place_records_order` are not, because their matter is the object's and the model picks no matter. A webhook-given matter mints through `resolve_matter_by_id(matter_id, matter_number)`, which requires the live number to equal what the model read (two facts, the rule `resolve_matter` already applies). The gate ships in `observe` mode (a `would_refuse` audit row, then dispatch) for one daily cycle on the pilot and the client seat before `enforce`, because 35 skills name a connector write tool and a refusing write on the daily post bundle is a client-visible outage; the mode is `matter_resolution_mode` in the seat's connector block and the validator refuses any other value.

### 3. As printed in, parsed in code, confirmed in the source

Money and dates cross the tool boundary as the printed string. One parser each, in the connector (the shared library of ADR 0100): `printed_money.money` carries `howell.money`'s contract ($, commas, parentheses, the split cents box; any other inner space is None, never a guess) and the drafting lane imports it from here; `printed_dates.date_from_printed` sits over `printed_date_candidates` and refuses an ambiguous string. A figure must be printed in the document it came from: the vendor amount beside a total or balance label in the re-fetched text, the Medicals charge and dates in the filed page span, a negotiation amount read from a document in that document (`cited_facts.confirm_in_pages`). Totals over a ledger are a read tool that pages to the end and sums in Decimal; the trust floor is an authored scalar the connector compares, `null` with "no floor authored" when the firm has not said, so the nudge does nothing rather than guess.

### 4. Fixed wording, rendered by the broker at bind time

A job's reply words, held or delivered, are rendered by code when the model binds the reply: from the ledger row's own columns and the broker's own reads at that moment (the allowance, credits, the files on the row), with zero slots, so a figure is rendered by code or its sentence is omitted. The bind returns the texts; the send refuses any other words before the one-reply claim, so a refused send leaves the reply unspent and the redraft carries the exact text. This generalises the held-wording check of 2026-10-24 from two lanes' held rows to every job lane's held and delivered rows, and to the negotiation notices and SMD notes whose text was already code's but unenforced. The records lane, where no model is in the reply at all, remains the stronger shape for a lane with no in-turn work.

### 5. Conflict check is a tool whose result the trust gate enforces

The gateway cannot read the practice-management system at dispatch (`shared/matter_binding.py`), so the check is a connector tool the model calls, and the trust plugin enforces its recorded result. The rule table: `hit` iff the sender or a named party is a contact in an adverse role (other side, opposing counsel, their carrier) on an OPEN matter AND the inbound was not already resolved to that matter by the router, which is to say a new relationship; `clear` otherwise; `search_failed` on any tenant fault, never read as clear; formal service of a captioned document is the caller's carve-out and the tool is not called. On `hit` the tool creates the one clearance task in the rubric's fixed shape, composed in code from sender, subject line and candidates, and returns its id; a second call in the turn returns the same task. The fence is on the acts the halt exists to stop: in a turn whose inbound is an email from a sender not on the roster, or one the router classified as a new-matter or new-client inquiry, an internal write or a send with no recorded `clear` is refused, and a recorded `hit` refuses both. Webhook and handoff turns have no sender and are never fenced; the firm's own forwarded post, invoices and served documents run on resolved matters and are never fenced.

### 6. Events in, status out; prompts ask only for judgment

The model reports dated, cited events; a rule table derives the status. A litigation defendant carries `served`, `answered`, `dismissed`, `settled`, `default_entered` and `sent_for_service` as dated, sourced fields; `defstatus.derive` sets the status (dismissed over settled over a default-and-answer conflict, which is Unclear, over default, over answered, over served with no answer, over out for service, over not served); the gates require a source on every event; a verify or audit verdict addresses a defendant by an index code built, and an unmatched verdict is logged, never dropped. Discovery "not yet due" is `due >= today` with no response, computed. A negotiation offer's plaintiff is `joint`, an index, or `unstated`, and unstated is held, never written as joint. The casefile's header facts and statute date come from the inventory and the layout, the model cites a document only where code holds nothing, and one statute window lives in code. Every prompt loses what code computes: the chronology's INDEX restatement, its FILES-SEEN counts and its two unread blocks, the "oldest first" and dedup rules code re-does, the update mode's re-emission of unchanged values; tool schemas replace regex over markdown. The demand lane's specials table, total, the Demand table's medical line and the expiry line are placed by code from the Medicals tab and the billing documents (`howell.extract`/`build`), and every dollar figure in the letter must equal a table or billing figure or the draft is refused internally.

## The proof ladder, per lane

Nothing runs first on the client seat. Unit tests on synthetic shapes with a mutation per rule; then a $0 rehearsal on the real inputs in the client's ops workspace against an answer key written before the change (`litigate --rehearse --read-limit 0`, `casefile --rehearse`, `rehearse <job_dir>`, the records lane's `expected.json` shape); then pilot-smokeball, the only non-client seat with the connector bound, with `[SMD-PROBE]` artifacts torn down the same session; then one capped dry-run on the client seat after the Captain's yes. The demand and drafting lanes have no $0 seat replay; that gap is named, and their first run is a lane-proof on the pilot. Per-stage dollars survive 72 hours in a job's `usage-ledger.jsonl`; the chronology prompt change is priced by `ledger.report` over the surviving ledgers before and after.

### Amendment, 2026-11-05: every refusal ships off, per seat

By the resume, a merge reached the client seat in ring 1 before the pilot in ring 2, with no person between, and staging has no Smokeball connector. So the token's observe-first rule now covers every mechanism that can refuse or change what a firm sees: `logic_in_code` in the seat's `customer.yaml` (`printed_source_check`, `post_record_derivation`, `conflict_fence`, `fixed_wording`, and since 2026-11-06 `wording_rules`, whether the job lanes follow a firm's confirmed wording rules, and since 2026-11-07 `library_setup`, whether a firm's "set up our ..." request is answered from the firm's own documents), each `observe` when absent and `enforce` when unreadable. Code ships before the skills that use it, in separate PRs, accepting the old call and the new one. The pilot authors `enforce` first; a client seat only on the Captain's yes. Detail: `docs/plans/logic-in-code/resume-2026-11-05.md`.

### Amendment, 2026-11-06: measured on the real work, the program shrinks to what earned it

The six mechanisms came from reading code. On 11-06 the A&P ops workspace read the seat's own records for 2026-10-07 to 2026-11-06. It was read-only and cost $0 (task-20261106T035743-11795, with the row-level key in task-20261106T045106-1606's input). It counted about 60 non-drafting failures, about 35 of which reached the firm. It also counted, against each mechanism, the real failures that mechanism would have prevented:

- 1 outcome ledger: 0
- 2 resolution token: 0
- 3 as printed: 1 partial
- 4 fixed wording: about 7
- 5 conflict fence: 0
- 6 events in, status out: about 5

Most of the volume maps to no mechanism. That includes:

- litigation values and dates wrong through ordinary code defects
- jobs dying silently (process kills, disk, token lapse, lost wakes)
- in-turn chat replies stating false things

A person stepped in on 7 of 7 litigation jobs and 8 of 9 chronology jobs.

So:

- Mechanisms 4 and 6 stay.
- The merged code for 1 and 3 (#3752) stays in observe, where it changes nothing a firm sees.
- 2 and 5 are not built further. #3753, #3797, #3792 and #3800 were closed with their branches kept.

The program's measure is now per lane: a $0 replay of the lane's last real jobs finishes with no step a person had to take that code could remove. Each fix is plain code proven against that lane's answer key before it merges. A switch is used only where a fix would change what a firm sees in a way it has not asked for. A held, failed or dead job's notice to the firm is one shared renderer in `operator/workspace_broker/fixed_wording.py` (#3816), which every lane joins. Litigation goes first and chronology second.

## Build order

Wave 1 (connector, broker, overlay, skills; no runner file): the SDK token flag and mode first; then, in parallel and on disjoint files, the outcome ledger with the as-printed parsers and source confirmation; the write-tool declarations with the 32 skills, the conflict tool and the trust fence; the broker's fixed wording with the status tools and the DELIVER skill sections. Wave 2 (the runner, after the `jobs` rename of ADR 0103 lands): litigation events, negotiation and casefile schemas, the chronology prompt and review, the demand specials and expiry. Wave 3: the in-turn computed values (confirm-by dates, net to client, `reply_block` everywhere, the router's deterministic tells) and the schema-for-regex trims. Not here: the demand digest's evidence-first treatment, built on the drafting lane's package (#3733) after it is proven there.

## Consequences

- A wrong matter number, figure, date, status or count can no longer reach the firm through a restatement: it is copied by code or the sentence is not written.
- The connector's write surface has one gate instead of two tools' private checks; a new write tool is gated by one argument at registration.
- Every lane's reply is testable by reading the row, and the parity test pins the skill's text to the renderer, so a skill edit cannot drift from what the broker will accept.
- Prompts shrink on the most expensive calls (the chronology's compose at up to 128k output tokens per chunk), and the saving is measured, not asserted.
- Cost accepted: an observe cycle before enforce on the token; a renderer per lane in the broker; a connector read per inbound from an unknown sender.
- Residual model inputs, named: the letter's `kind` in post intake; which sections a request's words match in the page finder (ADR 0102); the free-text reason for a file a chunk drew nothing from; the judgment calls the lanes exist for (transcribe a page, draft a sentence from facts, grade a claim against its page).

## Rejected

- **Enforce the token on day one.** 35 skills write on matters; an untested refusal on the daily post path is an outage on the only live seat. Observe first, with every would-be refusal read back.
- **A reply-time conflict fence.** A non-roster sender's reply is already held before send; the acts a conflict must stop are the writes.
- **`reply_text` written by the runner at delivery.** It goes stale when a credit lands or another job delivers first, and the broker would then refuse the true figures; the broker renders at bind time from the row and its own reads.
- **A dollar lint that needs the cited-source check.** Legitimate narrative figures exist outside the specials table; the computable rule (every figure equals a table or billing figure) ships now and the cited-source clause waits for the evidence-first digest.
- **Gating `update_*` tools.** The model picks no matter there; a token would add a forgeable-looking ceremony and break the gate-authored escalation writes.

## Acceptance criteria

- [ ] (repo) The SDK gates a registered tool by `requires_resolution`; the schema shows `matter_resolution` as required; observe and enforce modes behave as stated; `resolve_matter_by_id` mints on two matching facts only
- [ ] (repo) `record_post_batch` derives `placed_on`, `issue` and `candidates` from the outcome ledger and refuses model-typed values; a letter whose required act was never attempted reads `no attempt recorded`
- [ ] (repo) Every connector money and date parser accepts the printed form; a vendor amount, a Medicals charge and a negotiation amount not printed in their source are refused
- [ ] (repo) The broker renders and enforces reply wording for every job kind, held or delivered, with zero slots; the parity test pins each skill's DELIVER text
- [ ] (repo) `conflict_check` applies the rule table; the trust fence refuses writes and sends with no recorded clear and everything on a hit, and never fires on webhook turns or the firm's own forwarded post
- [ ] (runtime) Observe mode on the pilot and the client seat reads zero would-be refusals on the daily post and chronology paths before enforce; on the pilot a bare `render_firm_form_letter` is refused and a tokened one files
- [ ] (runtime) A probe post bundle on the pilot yields one outcome row per act, a derived record, and a daily list rendered from it; a comma-typed vendor amount stages at the printed figure
- [ ] (runtime) A probe inbound from a non-roster sender naming an adverse party yields a hit, exactly one clearance task and no write or send; a roster-forwarded carrier letter yields clear
- [ ] (runtime) A delivered lane-proof twin's bind returns the broker's `reply_text`; other words are refused; the exact text sends
- [ ] (runtime) Litigation, casefile and chronology $0 rehearsals in the client ops workspace match their answer keys; one capped dry-run per lane on the client seat after the Captain's yes
