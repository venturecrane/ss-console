# The five agent reports behind the 2026-11-04 model-as-logic audit, verbatim

Companion to [model-as-logic-audit-2026-11-04.md](./model-as-logic-audit-2026-11-04.md). Each report was produced by a read-only audit agent at `origin/main` 2afdb2ab and is reproduced unedited, with its top heading demoted one level.


---

<!-- report: chronology -->

## Chronology lane audit: logic carried by the model

Paths below are relative to `operator/runners/medchron/medchron/`.

There are 16 model call sites. Code already owns most of the control flow:
- chunking and splitting
- the retry and model-tier ladders
- sorting
- exhibit numbering
- merge routing and its falsifier
- the audit rounds

None of the 15 decide, gate, timeline or coverage modules makes a model call (`gates/*`, `*_decide.py`, `decisions.py`, `decision_rules.py`, `timeline.py`, `covered.py`, `dos_check.py`). They are code only.

### Call sites

| file:line | What the model is asked | Kind | Trusted or re-derived | Recommended move |
|---|---|---|---|---|
| `stages/classify.py:207` (`prompts/classify-system.md`) | Label scanned page images ORDER/REQUEST/AUTH/CERT/INDEX/RECORD/BLANK | JUDGMENT | Re-derived. Control pages ride in every batch and a failed control untrusts every label (`classify.py:293-312`). A label outside the set is ignored (`NONRECORD_LABELS`, :308) | Clean. The loose `X = LABEL` regex (:231) is acceptable given the controls. |
| `stages/scope.py:295` (`prompts/filter-system.md`) | MATERIAL or OMIT for each pre-incident encounter | JUDGMENT | Code splits pre and post by date (:287-288) and keeps anything unclassified (:318). It also builds the count and date span of the omitted-visits note (:320-323) | Clean. |
| `stages/summarize.py:108` (`prompts/summarize-system.md`) | Write the Prior Medical History, one citation per sentence | JUDGMENT, plus copying citations | Page-set containment is re-derived (`beyond_source`, :56), with one retry. The heading is set in code (`with_heading`, :97) | Containment is checked per set, not per sentence, so a citation re-pointed to the wrong sentence passes. Years and dates the summary writes in prose are never checked against the source entries. |
| `stages/update_collect.py:362` (same prompt) | The same, for an update | JUDGMENT, plus formatting | The model must emit the heading itself. A miss is exit 1 with no retry (:373-385), and the heading is then thrown away (`head, _, text = block.partition("\n")`) | Use `summarize.with_heading` and the one-retry ladder. |
| `stages/condense.py:98` (`prompts/condense-system.md`) | Shorten a routine entry, keeping the header lines and every citation exactly | JUDGMENT, plus copying | Re-derived by `accept` (:67): no new citation, header lines unchanged, result shorter | Strip the two header lines in code and re-attach them after the call. Add a check that every number and date in the output also appears in the input; today only citations are checked. The prompt asks to drop "repeated history already stated in earlier entries" (`condense-system.md:12`), but the call sends only this one entry (:102). |
| `stages/compose.py:337` / `:489` (`prompts/map-system.md`), ENTRIES block | Compose dated entries from record text | JUDGMENT | Every claim is audited downstream | Clean. "chronological within this chunk" (prompt :45) only costs tokens; code re-sorts. |
| same, citation grammar `(FILE: <name>, p. x)` (prompt :21) | Retype each file's long name inside every citation | LOGIC (matching an id against a closed list) | Resolved fuzzily. When several files share a prefix, `max(cands, key=commonprefix)` picks one (`assemble.py:88-92`). A wrong pick is caught only later at the audit, where the claim is repaired or dropped | Give each file a short token in the chunk header (`F3`) and have the model cite `(F3, p. 12)`; code maps it back. |
| same, INDEX block (prompt :47-48) | One row per entry: date, provider, ICD codes, file | LOGIC (date, provider, file: a restatement of the entry) plus JUDGMENT (the codes) | **Trusted.** INDEX rows decide whose name titles an exhibit (`group.py:141-175`). Diagnostic Highlights take their ICD codes straight from the INDEX and never audit them (`build_doc.py:101-127`) | Take date, provider and file from the parsed entries. Keep only the ICD codes, keyed to their entry, and audit them as claims the way the update lane does (`update/icd.py`). |
| same, BILLING-DATES and CONFLICTS blocks (prompt :50-54) | List billed service dates; list conflicts and referenced-but-absent studies | JUDGMENT (extraction) | **No consumer.** They are written to `billing_dates.md` and `conflicts.md` (`assemble.py:99-110`) and no code anywhere in `medchron/` reads them (grep). The date-of-service check reads the billing stage instead (`dos_check.py`) | Drop both blocks from the prompt and from `compose.BLOCKS` (:64). Or wire CONFLICTS into the review, if conflicts are meant to reach the firm. |
| same, FILES-SEEN block (prompt :57) | Echo every file header verbatim, with `entries: N`, `billing-dates: N`, or `nothing extractable: <reason>` | LOGIC (the echo and both counts) plus JUDGMENT (the reason) | **Trusted for coverage.** `entries: 0` becomes the firm-facing line "read in full; it contains no dated medical encounter" (`coverage.py:128,256`, printed by `limitations.py:363-370`). `billing-dates: N` explains a file using the model's own count (:113-117). A free-text reason settles the gate (:111). A header the model rewrites matches nothing (`SEEN_LINE`, :78) and holds the run | Count entries per file from the parsed ENTRIES citations, and billing dates from the parsed BILLING-DATES lines. Ask the model only for a reason for each file it drew nothing from, keyed by the file token. |
| `stages/repair.py:49` (`prompts/map-system.md`) | The map prompt again, on halves of a truncated chunk | Same as compose | Splitting and the narrowing to unread pages are code | Same moves as compose. |
| `stages/merge_model.py:28` (`prompts/merge-system.md`) | Arrange the routed clusters under headings and mark disagreements | Mostly JUDGMENT. "Use the fullest provider wording" and "chronological" are LOGIC | Citations, paragraph count and entry count are re-checked (`merge_falsify.check`). The chosen provider line is not | Code sets both header lines (the code-merge path already picks the fullest wording); the model returns the body only. |
| `stages/billing.py:174` / `:256` (`SCHEMA_NOTE`, :45) | Transcribe a bill: doc_type, provider, patient, printed totals, line items, date_first and date_last | JUDGMENT, except date_first/date_last, which are a min/max over service dates | Amounts are re-derived (`billing_chart._amount`). **date_first/date_last are trusted:** unmatched provider rows take their first and last dates from them alone (`billing_chart.py:305,348`). JSON is parsed after stripping code fences (:142), with no tool schema | Compute first and last from the `line_items` dates when items exist. Use a tool schema. Separate defect: `page_first` is read (`dos_check.py:111`, `billing_chart.py:290`) but the schema never asks for it, so it is always None. |
| `stages/vision.py:287` | Transcribe one page, with a required first line `HANDWRITING: yes\|no` | JUDGMENT | A reply without the field defaults to handwritten (:66) | Clean. |
| `stages/review.py:477` calling `proof/review.py:141` (`prompts/review-system.md`) | Adversarial review on checks 1 to 5, ending with a VERDICT line of counts | Checks 1-3 JUDGMENT. Check 4 is mostly LOGIC for tables code built. Check 5 duplicates code | Counts are recomputed from the parsed rows (`proof/review.py:162-164`); VERDICT is only a completion marker. Check numbers are parsed by a loose regex on a table cell (:50). **A check 4 MUST-FIX deletes a summary line with no code check** (`review_policy.py:469-476`) | See priority 1. Remove from check 5 what `code_findings` already does: before birth, after today, missing exhibit (`review.py:263-293`). |
| `stages/update_review.py:185` (`prompts/update-review-system.md`, `prompts/update-finish-system.md`) | Review the added entries against page images; the finisher returns JSON edits | JUDGMENT | Well guarded: grounding, `check_drop`, `check_provider`, a redate needs the date printed on its page (`update/review_repair.py:332-340`), and every new sentence is audited. Duplicates are decided in code (`update/duplicates.py`). `complete: true` is the model vouching for its own completeness (`review_repair.py:213`), and the JSON comes back as free text | Move to a tool schema. In the finisher, build `(Exhibit E - p. P)` from the `exhibit`/`page` fields instead of asking the model to type the citation a second time. |
| `audit/verify.py:183` / `:241` | Does the cited page support the claim | JUDGMENT | Tool schema with an enforced verdict set. Code computes whether support was found only on uncited pages (`text_verdict`, :228) | Clean. |
| `audit/repair.py:168` | Weaken or remove an unsupported claim | JUDGMENT | Citation equality, a size cap, and loss of any value the audit did not name are all re-checked (`_rejection`, `value_check`) | Clean. A small saving: strip the citation before the call and re-attach it. |

### Five highest-value items (accuracy risk first, then cost)

#### 1. Review check 4 can delete summary rows that code built from the entries, and Prior Medical History sentences that by design have no entries behind them (accuracy)

**The tables are built by code.**
- `stages/build_doc.py:7`: "Tables are computed FROM THE ENTRIES in code, never by a model, so they cannot disagree with the body."
- `stages/review_tables.py` already recomputes them with build_doc's own functions.

**The model's finding deletes rows anyway.**
- The model is asked to find them: `prompts/review-system.md:15` lists "A SUMMARY WITH NO ENTRY BEHIND IT ... timeline row, diagnostic highlight or prior history line ... MUST-FIX."
- `stages/review_policy.py:469-476` runs `delete_supported` for any model check 4 MUST-FIX whose quote matches exactly one summary line. Code never checks whether the row really lacks support.
- Recomputation then honours the deletion. `review_tables.py:14`: a provider row removed earlier by the review "is not brought back".

**The reviewer may not have the evidence in view.**
- The review reads at most 60 entries per part (`review.py:89`, `REVIEW_PART_MAX_ENTRIES = 60`).
- A table row whose entries sit in another part has no support the model can see.

**Prior Medical History sentences are exposed by design.**
- Outside `itemize_all`, the body holds only post-incident entries (`summarize.py:181`: `body = sorted(entries, key=_iso) if itemize_all else post`).
- So every history sentence is a line "no entry in the chronology supports".
- `summary_lines` includes the history block (`review_policy.py:162`: "the Prior Medical History block at their head").
- I have not seen this happen live; I'm inferring it from the code.

**Move:**
- Decide check 4 in code for Timeline and Diagnostic Highlights rows.
- Check history sentences against the pre-incident entries the summary was built from (they are on disk), not against the body.
- Keep the model only for the free-text judgment.

#### 2. The map INDEX is trusted for exhibit titles and is the unaudited source of Diagnostic Highlights (accuracy)

**The request.** `prompts/map-system.md:48` asks for one line per entry: `YYYY-MM-DD | <provider> | <ICD codes as recorded> | <file name>`. That repeats the date, provider and file already in the entry itself.

**Exhibit titles.** `stages/group.py:141-147`: "The INDEX is the source ... Citation attribution is the fallback only; it never outvotes an INDEX row." Exhibit names come from `lane_name` over these rows (:235).

**Diagnostic Highlights.**
- `stages/build_doc.py:101-127` (`icd_codes`) takes each code, its first-diagnosed date and its provider from INDEX rows.
- The only check is that the INDEX date equals some entry's date.
- The update lane fixed exactly this gap. `update/icd.py:3-5`: "a code the new entries recorded first went straight into the table from the compose pass's INDEX row: never checked against its page." The full build still does it.

**Move:**
- Take date, provider and file from the parsed ENTRIES, which `assemble` and `group` already parse.
- Shrink INDEX to `entry -> ICD codes`.
- Turn the full build's codes into audited "Diagnosis code X recorded on DATE" claims, the way the update lane does.

#### 3. FILES-SEEN self-reported counts decide coverage and write firm-facing text (accuracy)

**The request.** `prompts/map-system.md:57`: "every === FILE: header in this chunk verbatim, each with `entries: N`, `billing-dates: N`, or `nothing extractable: <one-line reason>`."

**`entries: 0` writes Limitations text.**
- `stages/coverage.py:256`: `why = NO_ENCOUNTER if counts.get(name) == 0`.
- `NO_ENCOUNTER` (:128) is "read in full; it contains no dated medical encounter".
- That line goes into the firm's Records Reviewed and Limitations (`stages/limitations.py:363-370`).

**`billing-dates: N` explains a file with the model's own count.** `coverage.py:113-117`: "evidenced billing dates only ({bills.group(1)}); the billing chart carries them".

**A free-text reason settles the coverage gate.** `coverage.py:111-112`: "nothing extractable:" becomes `"composer: " + reason`.

**Code already distrusts the count elsewhere.** It overrides it only for cited files. `coverage.py:248`: "whatever its FILES-SEEN count says (a composer miscount, ...)".

**A rewritten header holds the run.** A header the model rewrites fails `SEEN_LINE` (:78), and the file is then never accounted for.

**Move:**
- Count entries per file in code from the ENTRIES citations (`assemble.CITE`).
- Count billing dates in code from the BILLING-DATES lines.
- Ask the model only for a reason for each zero-entry file, keyed by the code-assigned file token.

#### 4. The update's prior-history step stops the job over a heading the model was asked to write (reliability and cost)

**The full build does this in code.** `stages/summarize.py:20`: "The heading is set here in code (row 10), so build_doc never refuses on its wording." It also retries once on a containment miss (:120-133).

**The update path refuses instead.**
- `stages/update_collect.py:373-385` builds a rejection reason from `"does not open with its heading" if not B.PMH_HEAD.match(head)` and calls `sr.log(f"REJECTED: the added prior history {why}")`.
- It then returns exit 1. There is no retry, and a containment miss is also a hard exit.
- The heading is checked and then discarded: `head, _, text = block.partition("\n")`, and only `text` is kept.

**Move:** call `summarize.with_heading`, and reuse `summarize._summary`'s one-retry, told-what-reached-too-far ladder.

#### 5. Map output that no code reads, on the most expensive call (cost)

**The most expensive call.** Composition runs at up to 128k output tokens per chunk (`stages/compose.py:521`, `compose_max_tokens`).

**Two blocks have no reader.**
- BILLING-DATES (`prompts/map-system.md:50-51`) and CONFLICTS / REFERENCED-BUT-ABSENT (:53-54) are written to disk by `stages/assemble.py:99-110` (`billing_dates.md`, `conflicts.md`).
- They are read by no code in `medchron/` (grepped for `billing_dates`, `conflicts.md` and both block names; the only hits are the writer, the BLOCKS shape check and docstrings).
- Their only role is the presence check in `well_formed` (`compose.py:64-69`).

**The INDEX repeats every entry's date, provider and file.** See item 2.

**Move:**
- Remove both blocks and shrink INDEX to codes. That removes output from every chunk of every matter.
- Update `compose.BLOCKS` and `record_unread`'s template (`compose.py:246`) to match.
- If the conflicts list was meant to reach the firm, it currently does not; that is a decision to wire it in or delete it.

### Smaller items

- **`page_first` is never produced.** `stages/billing.py`'s `SCHEMA_NOTE` has no `page_first`, but `dos_check.py:111` and `billing_chart.py:290` read `c.get("page_first")`. It is always None, so an itemless bill's dates and an inherited-provider log row carry no page.
- **Condense is asked for something it cannot see.** The prompt rule about history "already stated in earlier entries" (`condense-system.md:12`) needs cross-entry context, but the call sends only one entry (`condense.py:102`).
- **The merge model picks the provider line.** "Use the fullest version" (`merge-system.md:11`) is trusted for routed clusters; the falsifier counts entries, citations and paragraphs, not the header line. Code could set the header.
- **Model-reported completion.** The review's VERDICT counts and the update review's `complete: true` are completion markers only. Counts are recomputed (`proof/review.py:162`), and the findings JSON is parsed leniently at the last split (`review_repair.py:212`). Low risk; a tool schema would make it strict.


---

<!-- report: litigation -->

## Audit: model-carried logic in the LITIGATION, NEGOTIATION and CASEFILE lanes

Paths below are relative to `operator/runners/medchron/medchron/` unless they start with `prompts/`. Casefile prompts were read from `venturecrane/engagements` `origin/main:operator/customers/ashton-price/casefile/prompts/{contract,reader,auditor}.md`, read-only.

### Already code-owned (no finding)

- `litigation/deadlines/` and `litigation/reassign/` make no model call: "No model is called: `cents` is 0 by construction" (`reassign/run.py:5`, `deadlines/run.py:5`).
- `litigation_lane.py`, `litigation_close.py`, `negotiation_lane.py` and `casefile_lane.py` make no model call; their reply text is composed in code.
- Litigation pass count, the plan's read groups, audit scope (`parity.compare`, `passes.py:194-204`) and settlement-hit selection (`passes.py:180-187`, MAX_HITS) are code.
- Litigation integrity flags are composed in code (`run.py:346-348`).
- Litigation calendar court dates are code with no model (`courtdates.py`).
- Discovery12 paper selection, hash dedup and chunking (`discovery12/run.py:140-151`, `batch.chunk`) and the cross-chunk merge (`batch.py:144-200`) are code.
- Negotiation document selection (`select.py`), the email sent date (`extract.sent_date`), row placement, duplicate detection (`rows.py`, `match_existing`), notices and the tab summary (`notice.py`, `summary.py`) are code.
- Casefile statute and court ranking (`rank.py:77-140`), low limits (contract rule 7), the closure pre-refusal of money and statute tasks (`audit.py:91-100`), Medicals charge-in-text (`validate.py:233-244`) and appointment dedup and today filter (`propose.py:156-220`) are code.

### Model call sites

| file:line | what the model is asked to do | JUDGMENT / LOGIC | trusted or re-derived | recommended move |
|---|---|---|---|---|
| `litigation/runbase.py:172` (OCR_PROMPT `litigation/extract.py:48`) | Transcribe one page image | JUDGMENT | n/a | none |
| `negotiation/run.py:184` (OCR_PROMPT `negotiation/text.py:35`) | Transcribe one page image | JUDGMENT | n/a | none |
| `litigation/tools.py:325` `run_loop` (read, verify, audit) | Decide which documents to open beyond the code-chosen candidates; search for contradicting evidence before any negative (`read.py:158-162`) | JUDGMENT, part LOGIC | Only settlement words are scanned in code and enforced (`gates.rule_settlement`). Answers, dismissals, defaults and proofs of service rely on the model remembering to search | Code-scan opened texts with the `defstatus._PATTERNS` and service words; audit must resolve each hit for every no-answer or not-served defendant, as it does for settlement hits |
| `litigation/tools.py:395-405` `_json_answer` | Fallback: an answer written as text is parsed from the first `{` to the last `}` | parsing | Loose. Also reused by `casefile/audit.py:127` | Remove the fallback; only the tool input is accepted |
| `litigation/read.py:58` defendant `status` | Pick 1 of 14 statuses (`vocab.py:36-50`) | LOGIC for Served-no-answer / Answered / Default / Dismissed / Settled / Not served (they follow from dated events); JUDGMENT for Rejected / UIM / Dropped / Appeared / Firm client | TRUSTED. The schema gives status no `doc` / source. Gates check only: answered without a date, answer date with a no-answer status, served date with not served, and note contradiction (`gates.py:184-198`) | Model returns each event (served, answered, default, dismissal, settlement), dated and cited; code derives the status by rule table |
| `litigation/read.py:181` | "A next court date must be the next one AFTER today" | LOGIC (min of dates ≥ today) | Partly re-derived: the gate refuses only a past date on an active case (`gates.py:178`); courtdates merges the calendar (`courtdates.py:242`). Code never knows whether an earlier upcoming date in the papers was skipped. The prompt says AFTER today; the gate allows today | Model lists every court date it finds, with its event and doc; code picks the earliest ≥ today |
| `litigation/read.py:178-179` | sent_for_service: "if the papers went out more than once, record the most recent request" | LOGIC (max date) | Trusted | Model lists each request; code takes the max |
| `litigation/read.py:119-127,422-442` verdict `path` | Echo `defendants[<exact name>].field` as a free string | LOGIC (match against a closed list) | Matched by lowercase, whitespace-normalised equality (`_find_defendant`, `read.py:415-419`); a miss is `continue` with no log (`read.py:441-442`) | Make the path an enum built by code (defendant index); log every unmatched verdict |
| `litigation/read.py:328` | (code) an invalid `firm_role` is defaulted to `"plaintiff"` | code default | A fabricated value | Map to Unclear and flag it |
| `litigation/update.py:39-43` TAIL | "copy every current value exactly (same date, same doc) unless an arrived document changes it" | LOGIC (merge) | Re-derived by `update.guard` and `passes.reguard` | Model returns changes only; code merges (cost) |
| `litigation/read.py:173`: flags, notes, `case_status.detail` | Plain-sentence prose | JUDGMENT | Printed to the firm (`book.py:278,283`); `leak_scan` checks vocabulary only (`gates.py:41-57`) | Keep; optionally check any date quoted in a flag against the structured values |
| `litigation/read.py:142` case_status enum | Case status from the papers and emails | JUDGMENT | Source required (`gates.py:146`); settlement scan enforced | none |
| `litigation/discovery12/run.py:231` + `sections.py:34,104-116` response `"not yet due"` | Decide whether a response is not yet due | LOGIC (due date vs today) | TRUSTED on a fresh read. The only `due < today` check is `carry._stale` (`carry.py:109-122`), used for reuse of a saved record | Model returns yes / no / unclear; code sets "not yet due" iff no response and due ≥ today |
| `discovery12/sections.py:104-114` section assignment | Sort each set into 1 of 12 sections | mostly LOGIC from the set's title | Trusted, though code has a classifier `complete.kind_of` (`complete.py:50-67`) | Cross-check the model's section against `kind_of(item)`; flag disagreements |
| `discovery12/sections.py:108` "One entry per set or event" | Dedup | LOGIC | Re-derived across chunks (`batch.merge`, `batch.py:188-200`) | none (code already owns it) |
| `discovery12/sections.py:110-111` `due` | "the response due date as the papers or a stipulation state it" | JUDGMENT (read) | Date-in-text gate (`sections.findings`) | Optional: code computes the CCP due date from served date + method and flags differences |
| `negotiation/extract.py:218` events `kind` | Classify demand / offer / 998 / tender / acceptance / ... | JUDGMENT | Second review (`offer_check.py` check 4) | none |
| `negotiation/extract.py:106` | "A carrier repeating an offer already in the current rows (same amount) is kind 'other'" | LOGIC (dedup against the rows given) | OVERRIDES code: `other` is in SKIP_KINDS and dropped (`rows.py:41,353`) before `match_existing` (`rows.py:263-266`) can return `possible_duplicate` and tell the firm | Remove the rule; code's `match_existing` decides |
| `negotiation/extract.py:101` `amount_confirmed` | "true only when that exact figure is printed in this document" | LOGIC (string search) | Re-derived by `offer_check.code_reasons` (money_in_sources). But `rows._place` uses the model's flag (`rows.py:285`), so a `false` withholds an amount code verified | Set `confirmed` from the code check; the review's check 2 owns "refers to this offer" |
| `negotiation/extract.py:107` `plaintiff_index` | "null when the offer is joint (all plaintiffs) or the document does not say" | schema conflation | Code treats every null as joint and writes "Joint, all plaintiffs." on the first tab (`rows.py:372-374,149,191`) | Enum: `joint` / `<index>` / `unstated`; unstated is held or asked, never labelled joint |
| `negotiation/extract.py:100` "oldest first" | Order events | LOGIC | Re-sorted in code (`rows.py:366` `sorted(events, key=_dated)`) | Drop from the prompt |
| `negotiation/extract.py` `author_side` / `addressed_to` | Who wrote the document and to whom | JUDGMENT | Gated by `rows.admissible` and review check 1 | none |
| `negotiation/offer_check.py:147` → `proof/review.py:119` | Adversarial review returning a markdown table plus `VERDICT: n MUST-FIX` | JUDGMENT | Parsed by regex (`proof/review.py:48,84-102`); a `\|` inside a quoted cite shifts cells; the row is found by `_ROW_CITE` in the cite (`offer_check.py:161-163`). The VERDICT count is only a completion marker; counts are recomputed (`review.save`) | Use structured output (json_schema, as `extract.py:225` already does) |
| `casefile/read.py:167` record header: number, matter_id, clients, matter_type, status, opened, responsible, assisting | Copy facts from the pack header (`pack.py:186-197`) | LOGIC (copy) | Only `number` is validated (`validate.py:117`). `book.py:319-325` prints the model's `clients`, `matter_type` and `status`; `audit.py:80` gives the auditor the model's `clients` for rule (b), "names this matter's client" | Render and audit from `inv` (`inventory.py:135`); drop these fields from the record |
| `casefile/read.py:167` `statute_date` | Produce the statute date | mostly LOGIC (layout value or statute-reminder task due dates are code-held) | TRUSTED. No source field, no ISO check in `validate.py`. Drives `rank._statute` priority "Pull today" (`rank.py:77-87`) and prints on the print-out (`book.py:324`) | Code derives it from layout or reminder tasks; the model supplies it only with a cited document; code cross-checks and flags a disagreement |
| contract.md:97, reader.md:34-35 | "a statute inside 120 days … is a time_sensitive finding" | LOGIC (date arithmetic) | Duplicates `rank.py` with different windows (60/180, `firm.py:100-101`) | Remove from the prompt; code owns it |
| contract.md:56, auditor.md:12 | Proof document "dated on or after the task's creation" | mostly LOGIC | Trusted. Code passes `task_created` and `saved` (`audit.py:73,83`) but never compares them | Code flags proof saved before task creation; the model judges only the printed date |
| reader.md:16 | "Completed tasks: check all of them if fewer than 15; otherwise the opening-workflow ones" | LOGIC (count and select) | Trusted; `validate.py` does not check coverage of `done_tasks_checked` | Code lists the done tasks to check; the validator requires a row for each |
| contract.md:122-124,157 `headline` | One or two sentences; the sample composes counts and elapsed time | JUDGMENT that composes facts | Trusted, printed first (`book.py:332`) | Code composes the count clause from `facilities`; the model writes the qualitative part only |
| contract.md:65-71 statute reminders and money/court words | Never `mark_complete` | LOGIC | Re-derived (`validate.py:150-160`, `audit.py:91-100`) | none (code already owns it) |
| contract.md:137-141 appointments, one per provider and date; upcoming | Dedup and date filter | LOGIC | Re-derived (`propose.py:156-220`) | Drop from the prompt (cost) |
| contract.md:109 "Only a letter missing at most one fact is filed" | Count | LOGIC | Not used; letters are only reported (`book.py:68-82`) | Remove from the contract, or enforce in code if filing is ever built |
| `casefile/audit.py:115` | Refute proposed closures | JUDGMENT | Code pre-refutes; a missing verdict becomes unsure | none |

### The 5 highest-value items (accuracy risk first, then cost)

#### 1. A litigation defendant's status is an unsourced model assertion that the firm sees

- Schema, `litigation/read.py:58`: `"status": {"type": "string", "enum": list(vocab.DEFENDANT_STATUSES)}`. There is no `doc` field. Dated fields carry `**_REF`.
- The gates check sources on dates (`gates.py:175-176`, "date without source") and on case status only (`gates.py:146`, "case status without source"). Nothing requires a source for "Dismissed", "Settled" or "Default entered".
- These are DEFENDANT_DONE (`vocab.py:52`), which takes the defendant off the needs-action list.
- Not gated at all: "Served, no answer in file" with no served date.
- #3684 was a symptom in a rule table that patched this from the outside.
- Move: the model reports cited, dated events; code derives the status.

#### 2. Discovery "not yet due" is a date comparison the model makes and nothing checks on a fresh read

- `discovery12/sections.py:34`: `RESPONSES = ("yes", "no", "unclear", "not yet due")`.
- The only check is in `carry._stale` (`discovery12/carry.py:109-122`): "A 'not yet due' answer whose due date has passed or is unknown". It runs only when deciding whether a saved breakdown can be reused.
- `sections.findings` (`sections.py:241-261`) checks only that the date is in the cited text.
- An overdue, unanswered set can print as "not yet due".
- Move: code computes it from `due.date` and today.

#### 3. Negotiation writes "Joint, all plaintiffs." into the firm's Smokeball row when the document simply did not say

- Prompt, `negotiation/extract.py:107`: "null when the offer is joint (all plaintiffs) or the document does not say."
- Code, `negotiation/rows.py:372-374`: `target: int = pidx if ... else host` and `"joint": multi and not own`.
- Then `rows.py:149` writes `txt = "Joint, all plaintiffs. " + txt` into the row note.
- On a multi-plaintiff file, an offer to one plaintiff whose letter does not name them is recorded as joint, on the first plaintiff's tab. This is fabricated content in a client record.
- Move: a three-way enum; unstated is held.

#### 4. Casefile: the statute date, the highest-stakes date on a PI file, is an unchecked model copy that drives priority

- `casefile/rank.py:78`: `d = _date(rec.get("statute_date"))` feeds "Pull today" (`rank.py:83-85`).
- `casefile/book.py:324`: `f"statute {e(long_date(rec.get('statute_date')))}"`.
- `validate.py` has no rule on `statute_date`, and the record carries no source for it.
- Duplicated arithmetic: reader.md:35, "a statute date already past or inside 120 days is a time_sensitive finding", while `rank.py` uses 60/180 days.
- Same class: `casefile/audit.py:80`, `"clients": record.get("clients") or []`, gives the auditor the first reader's copy of the client names to verify rule (b) against, when `inv["clients"]` (`inventory.py:130-135`) holds the real list. `book.py:319-325` prints the model's clients, matter_type and status.
- Move: code supplies header facts and the statute date; the model cites a document only where code has none.

#### 5. Verdicts silently dropped on a name mismatch, and the model's dedup pre-empts code's (accuracy; also cost)

- `litigation/read.py:440-442`:
  ```
  target = _find_defendant(result, name)
  if target is None:
      continue
  ```
  `_find_defendant` compares `" ".join(name.lower().split())`, so "Acme Corp." vs "Acme Corp" loses a verify or audit overturn with no log line. The code's own fuzzy matcher, `identity.same_person`, is not used here.
- `negotiation/extract.py:106`: "A carrier repeating an offer already in the current rows (same amount) is kind 'other'". Since `other` is in SKIP_KINDS (`rows.py:41`), the event never reaches `match_existing` (`rows.py:263-266`), whose `possible_duplicate` would have told the firm to check the letter. A held figure on a new date disappears.
- Cost items in the same spirit:
  - Update mode re-emits every value for code to re-guard (`update.py:40`).
  - "oldest first" is re-sorted by code (`extract.py:100` vs `rows.py:366`).
  - The model selects done tasks by count (reader.md:16).
  - The model re-emits header fields on every casefile read.


---

<!-- report: demand -->

## Demand lane audit: places where the model carries logic code should own

Scope: `operator/runners/medchron/medchron/demand/*.py`, `demand_lane.py`, `note_by_smd.py`, `note_lane.py`.
`quotefix.py` was not re-audited.
Firm prompts read from `~/dev/engagements/operator/customers/ashton-price/demand/prompts/`, the skeleton from `skeleton-demand-house-ap.md` in the same directory.

### Summary

There are three accuracy-critical places where the model carries logic, and code already has, or nearly has, what it needs to own each one:

1. The model writes the record digest. Every later stage, the auditor included, treats that digest as the only record.
2. The model computes the medical specials and their total.
3. The model computes the demand's expiry date and weekday.

The drafting lane already has a code-owned billing table (`drafting/howell.py`). The demand lane does not use it.

Most of the demand lane's control flow is already in code: which documents get read, the privilege wall, the coverage checks, the pre-suit or litigation choice, the signer, the repair limit and the retry limits. The one exception is item 5 below.

### Model call sites

| file:line | What the model is asked to do | Kind | Trusted or re-derived (where) | Recommended move |
|---|---|---|---|---|
| `demand/transcribe.py:87` | Transcribe one page image whose text layer could not be read (shared prompt from `stages/vision.py`). | Judgment | Trusted. Page markers and the machine-read label are added by code (`transcribe.py:41-57`). | Keep. |
| `demand/summarize.py:109` (prompt `digest-demand.md`) | Turn about 120K characters of the record into a cited digest: classify each document, pull dates, providers, figures and quotations, write page cites, flag privilege candidates, list every file seen. | Mostly judgment, but the page cites, the figures index and the files-seen list are logic | Trusted. The only completeness check is that the text `## FILES-SEEN` appears (`summarize.py:28,135`). Nothing compares that list with the file headers code wrote into the chunk (`summarize.py:47,56`). Page cites are never checked against the page markers. | Code checks the files-seen list against the headers it built, and a missing file is a re-ask. Better: the digest becomes evidence tied to code-known page spans (the PR #3733 shape). |
| `demand/summarize.py:171` (prompt `condense.md`) | Shrink the digests to a length budget while keeping every cite (rule 1), de-duplicate encounters (rule 4), merge the index blocks (rule 6). | Dedup and preservation are logic | Trusted. Nothing checks that the cites survived. | Code compares the set of cites before and after and refuses a loss. Code does the dedup of encounters with the same document and page. |
| `demand/draft.py:270-301` → `demand/gapaudit.py:267` (prompt `gap-audit.md`) | One table row per missing record, bill or treatment gap. Section D: every interval over 45 days, with "the two dates, the number of days". | Missing records: judgment. Section D dates and day counts: logic. | Item numbers, grouping and the readiness summary are code (`gapaudit.py:182-219`). Day counts, the 45-day comparison and every "Where" cite are trusted. Batches come from the model's own digest headings (`gapaudit.py:92-108`), so a document the digest dropped gets no batch. | Code computes Section D from visit dates. Batch from `extracted.jsonl`, not the digest. |
| `demand/draft.py:332` (compose: discipline, skeleton, voice, `compose-demand.md`, brief) | Write the whole letter. Re-decide the four coverage questions; a failure makes the model print the coverage-report line, which ends drafting (`run.py:533`). Pick accident-related charges by comparing service dates to the date of loss. Add up the specials. Compute the expiry date and weekday. Choose ICD-10 codes. Compute lost earnings. Propose the demand figure. | Advocacy and the demand figure: judgment. Coverage re-decision: control flow. Date filter, totals, expiry date, weekday, lost earnings: logic. | The front-matter client and date of loss are checked by code (`crosscheck.py:50-73`). Totals are re-checked only by model passes plus a set-membership check (see the `review.py` row). The expiry date, weekday, claim number, insured and ICD codes are never checked by code. | Code renders the specials table, its total and the expiry line. The model uses tokens for figures and dates (the PR #3733 pattern). |
| `demand/draft.py:393` (prompt `audit-demand.md`) | Per section, give each claim one verdict (SUPPORTED, DRIFTS, INVENTED, ARITHMETIC) by checking it against the digest, including checking the arithmetic. | Mostly judgment; the arithmetic check is logic | Counting is code (`draft.py:194-203`, the higher of the model's tally and its finding rows). Only INVENTED and ARITHMETIC block (`draft.py:120,127-129`). DRIFTS, which the prompt defines as "a different date, a different figure", does not block and ships with a note (`run.py:347`). The audit checks against the digest, not the record. | Code checks figures and dates. Make a DRIFTS row on a figure or date blocking, or settle it in code. |
| `demand/draft.py:446` (prompt `repair-demand.md`) | Rewrite the flagged sections, including "Every ARITHMETIC finding: recompute from the digest figures shown". | Arithmetic recompute: logic | The loop limit is code (`run.py:73,320-331`, MAX_REPAIRS=2). The stop decision comes from the model audit's tallies. The repaired arithmetic is re-audited only by the model. | Goes away once code owns the totals. |
| `demand/finalpass.py:45,79-80,277-319` (no call; acts on the audit text) | — | — | A regex over the model's own detail text ("correct", "checks out") decides whether an ARITHMETIC finding is dropped. The claim is located by 40% word overlap (`finalpass.py:47,184-204`). | Code recomputes the arithmetic instead of reading the model's wording. |
| `demand/review_stage.py:107` → `demand/review.py:445` (`DEMAND_PROMPT`, `review.py:61-82`) | A separate model reviews the filed files. Its check 3, "WRONG ARITHMETIC: a total that is not the sum of its rows ... MUST-FIX", is a hold. | Check 3: logic. The rest: judgment. | Code checks run first, but weakly (next row). | Remove check 3 once code owns the sums. |
| `demand/review.py:222-244,306-319`, `demand/review_stage.py:83` (code) | — | — | A dollar figure is accepted if it appears anywhere among the record, the digest, the attorney's request, any bare number (`proof/checks.py:112-116`), or a column sum of the letter's own table. A miss is only should-fix; a date miss is only a note. The digest and the request count as sources even though the audit prompt says the request is not one. Figures are checked as members of a set, never tied to the provider or line they belong to. | Drop the digest and the request from the sources. Check each figure against the line it belongs to. |
| `demand/review_stage.py:74` (`COVERAGE_PROMPT`, `review.py:84-103`) | Decide whether each quoted source really establishes a coverage failure. | Judgment | — | Keep. |
| `demand/vendor.py:28-50` (code over model rows) | — | — | Provider names come from the model's free text, de-duplicated only by exact lowercase match, then looked up in the vendor directory. | Minor: normalize names, or key on the provider code already reads from the Medicals tab (`facts.py:32-44`). |
| `demand_lane.py:220-257`, `note_lane.py:40-48`, `note_by_smd.py` | No model calls in the runner. The wake carries only code-written fields. In the note lane's send turn, the model copies SMD's recorded words "word for word" (`operator/hermes-smd-overlay/plugins/hermes-smd-medchron/note.py:11-14`). | Copying | I did not confirm that a code check compares the sent body to the recorded text. | Make the broker send the recorded text, not words the model re-typed. |

No model call decides which documents or pages are read. `pull.select` (`pull.py:52-67`) and `preflight.extract_one` (`preflight.py:71-105`) are code. The coverage checks are code (`premise.py:259-323`), and so are the choice of format and signer (`run.py:568-599`).

### The five highest-value items

#### 1. The digest is the sole record, and nothing checks it against the source

- The digest prompt (`digest-demand.md`) says: "Your digest is the ONLY thing the drafter will see".
- The audit's system block is the prompt, the skeleton, the matter fields and "## THE RECORD DIGEST" (`draft.py:340-349`). Its verdicts are against the digest. The prompt says "SUPPORTED: the digest states it".
- The repair and the review see only the digest. The review also counts the digest as a source for its figure check (`review_stage.py:83`).
- So a figure, date or page cite the digest got wrong comes back SUPPORTED at every later stage.
- Completeness is decided by whether the marker text appears: `if TRUNCATION_MARK in text: final[stem] = text` (`summarize.py:135-136`). Code wrote every file header itself (`summarize.py:47,56`).
- This is the same class of flaw the drafting rework fixes, and it sits upstream of everything. The demand lane needs the evidence-first treatment here: facts tied to code-known page spans, and cites written by code.

#### 2. The specials and their total are model arithmetic, and code's own data is never given to compose

- `compose-demand.md:19` says "Show every sum with its components" and "dated on or after the date of loss". Skeleton slot: "| Total | [INSERT $ total of the rows above] |" (`skeleton-demand-house-ap.md:124`).
- `facts.py:32-44` reads the Medicals tab per provider, amount and service date. That data reaches only the preflight comparison (`preflight.py:137-158`) and the review's figure set (`review.py:228-234`), never compose (`run.py:287-300`).
- The review accepts a total that equals the sum of the letter's own rows (`review.py:235-243`), so wrong row figures with a matching total pass.
- `drafting/howell.py` already does this in code. Its docstring: "The table is computed here, in code, and the compose prompt RECEIVES it; the model never adds a column."
- Reusing it for the demand's specials is the biggest single fix.

#### 3. A wrong date or figure that the audit grades DRIFTS ships

- `audit-demand.md:9` defines DRIFTS as: "a different date, a different figure, a different attribution, a cite pointing at the wrong document".
- `draft.py:120`: `BLOCKING = ("INVENTED", "ARITHMETIC")`. DRIFTS rows go to the attorney notes as "The file supports this only in part" (`run.py:347`).
- Whether a misstated figure stops the job therefore depends on which label the model picked. Code-owned figures and dates make the label irrelevant.

#### 4. The expiry date and weekday are computed by the model and checked by no one

- The skeleton says "[INSERT weekday, date 30 days after the letter date]" (`skeleton-demand-house-ap.md:58`), and the Craig skeleton has the same at `:237`.
- The format check matches only the shape `(\w+day, )?\w+ \d{1,2}, \d{4}` (`format_check.py:100-101`).
- The review flags a date only if it is before the drafting date (`review.py:315-316`), and an expiry date is always after it.
- Code already holds the frozen letter date (`run.py:166-176`). Render the expiry line in code. A wrong weekday on a time-limited demand is a defect in the demand's own terms.
- The same applies to the drafting-date slot: code supplies the date and the model copies it.

#### 5. Arithmetic findings are dropped by keyword, and compose can override the coverage decision code already made

- `finalpass.py:45`: `_CLEARED = re.compile(r"\b(correct(ly)?|checks out|confirmed|verified)\b")`. At `finalpass.py:280-282`, a matching ARITHMETIC row is "left as written". Fix: code recomputes the arithmetic.
- `run.py:533`: `if v1.lstrip().startswith(draft.COVERAGE_SENTINEL): return self._coverage(...)`. This runs after code's coverage check already passed (`run.py:562-563`), driven by the instruction "IF ANY GATE FAILS, DO NOT DRAFT" (`compose-demand.md:5`).
- Fix: compose returns a value. Either drop its re-decision, or make it a structured objection that code weighs against `premise.json` before anything is filed.

### What already has the right shape, and what's missing

Already code-owned:

- reading the matter record (`facts.py`) and the client and date-of-loss check (`crosscheck.py`);
- document selection and the privilege wall (`pull.py`, `preflight.py`);
- the coverage checks and the pre-suit or litigation choice (`premise.py`);
- the quote check (`draft.py:151-167`, `gate.py`);
- gap-audit numbering and readiness (`gapaudit.py:182-219`);
- the signature, and every loop limit.

Missing:

- an evidence index under the digest;
- code-rendered specials and their total (`drafting/howell.py`);
- code-rendered expiry, drafting date and gap-audit day counts;
- a crosscheck widened to cover the insured, the claim number and the carrier against `facts.insurer` (today it checks only the client and the date of loss);
- a check that each ICD-10 code appears in the record;
- review sources limited to the record only, with each figure bound to its line;
- a code check that the files-seen list matches the chunk's headers, and that the condense step kept every cite.

I could not inspect the firm's `drafting-discipline.md` and voice files in full. All six firm prompts were read in full.


---

<!-- report: connector -->

## Smokeball connector + SDK: model-carried logic audit

Scope: operator/connectors/smokeball/smokeball_connector/*.py, operator/connectors/_sdk/**, operator/connectors/_contract/**. All paths below are relative to operator/connectors/smokeball/smokeball_connector/ unless prefixed.

### Direct model call sites

There are only two. Nothing in `_sdk/` or `_contract/` calls a model; the only Anthropic HTTP calls are in vision.py and page_finder.py.

| file:line | what the model is asked | J/L | trusted / re-derived | move |
|---|---|---|---|---|
| vision.py:156-190 (INSTRUCTION, IMAGE_INSTRUCTION), :282-320 build_request; callers letter_tools.py:180, extract.py:262, extract.py:287 | Verbatim transcription of one page or one photo. Return `[no legible content]` for a blank page. | JUDGMENT | Clean. `[p.N]` is composed in code (`_compose` :632). The model never sees a page number. All pages succeed or the document fails (:349-360). The sentinel is checked in code (`_is_legible` :626). No tools are sent. | none |
| page_finder_prompts.py:24-57 (INDEX) | Per page: sections, headings, document type, dates "as printed", provider, running header | JUDGMENT (reading a scan) | Facts are composed in code (ADR 0102 am. 3-4) | none |
| page_finder_prompts.py:62-80 (MATCH) | Filter by provider against a closed section list (:63). With several same-date sections, prefer the clinical one over canceled/no-show by an explicit rule (:66). Use a section's later dates (:69). Pick "closest" by nearest dates (:73, :75). Pick related candidates. | LOGIC once the request is parsed (provider/date filter, rule classification, nearest-date); JUDGMENT for related candidates | Ids are validated and pointers composed in code (page_finder_match.py:104 `compose`). The selection itself is trusted. Dates are compared by the model on raw printed strings. | The model parses the request into {provider, date/range, type}. Code normalises section dates (printed_facts), filters by provider and date, applies the canceled-vs-clinical rule on document_type, and computes nearest-date. The model keeps related candidates and ambiguous requests. |

### Tool descriptions that hand logic to the Hermes agent

| # | file:line | what the model is asked to do | J or L | trusted-as-is or re-derived | recommended move |
|---|---|---|---|---|---|
| 1 | vendor_invoice_tools.py:184-185; vendor_invoice.py:262-308 (`parse_amount`, `parse_invoice`) | Turn the invoice's printed amount into "1250.00" (no $, no commas). Turn invoice_date into ISO. | L (number + date parsing) | TRUSTED. Only the shape is validated. The bytes are re-fetched at `_fetch_for_staging` vendor_invoice.py:438, but the amount is never compared to the text. | Re-extract the text layer (or the cached page range for a bundle) and refuse unless the figure appears near a total label. Accept the amount and date as printed and parse them in code. |
| 2 | medicals_tools.py:174-178; medicals_layout.py:104 `parse_charge`, :116-129 `_problem` | Locate the bill's total charge and normalise it to "4345.16". Convert service dates to ISO. | L for the parsing; J for locating the total | TRUSTED. FILED_DOCS (letter_pages.py:384) holds the source range, but its text is never checked. | Confirm the charge and dates in that page range's text, cited_facts-style beside a "Total"/"Balance" label. Pass dates as printed and parse them in code. |
| 3 | layout_tools.py:355-360 (`add_negotiation_rows`) | Normalise demand/offer amounts and dates. Choose which demand row an offer answers ("pass `row` (0 to 9)", :359). | L | Row placement (`_plan` :228) and duplicate detection are code. Figures and the choice of answered row are TRUSTED. | For `operator_tab=true` (a figure read from a document), confirm it against the source text. Code proposes the answered row (the latest demand row with an empty offer); the model confirms. |
| 4 | vendor_invoice_tools.py:93-94, :103-106; matter_resolution.py:666-674 | Convert date_of_loss and date_of_birth to ISO. The as-printed path exists only for a garbled date_of_loss. | L (date parsing) | Matching is code (clean). The ISO conversion is TRUSTED. | Invert it: always pass dates as printed and let code parse them (`printed_facts.printed_date_candidates` exists). Add the same path for date_of_birth. |
| 5 | post_journal.py:242-251 (docstring); `_letter` :128-169; `_partition_problem` :172; `_filing_problem` :183-199 | After the run, restate per letter: placed_on (matter number), issue (tab_not_written, expense_not_staged, ...), candidates, page_count, received_at. | L (restating tool results code produced) | PARTLY re-derived. The page partition and filed-vs-not (against FILED_PAGES) are checked. placed_on is NOT checked against the matter the range was filed on. issue, candidates and page_count are not checked against tool results. | Each tool journals its own outcome keyed by (sha256, range): file_attachment_pages_to_matter, add_medicals_row, stage_vendor_invoice, add_negotiation_rows, resolve_invoice_matter. The model supplies only sender and kind. The office manager reads this list. |
| 6 | letter_tools.py:272-277 | Drive windowed reads (1-15, 16-30, ...) and stitch letters across window edges | L (iteration control) | Trusted | Acceptable, since partitioning is judgment. Options: overlap windows by one page, or accept boundaries per window and stitch in code. |
| 7 | letter_tools.py:363-367 (`document_kind`) | Classify medical vs letter, which chooses the folder | J | Trusted | Fine. Note that post_journal `kind` (:245) is the same judgment entered a second time; capture it once. |
| 8 | server.py:1865-1866 (`get_expenses`) | "a caller totalling a ledger must page the same way": the model pages and sums money | L (paging + arithmetic) | TRUSTED | `expense_ledger.read_whole_ledger` already exists. Expose a whole-ledger read with Decimal totals computed in code. |
| 9 | server.py:1830-1831 (`get_matter_balances`) | Compare availableBalance against the firm's authored floor (the low-trust flag) | L (numeric comparison) | TRUSTED. No code reads a floor; grep for floor/low_trust/low-trust returns no relevant hit. | Code reads the authored floor and returns `low_trust: bool` plus the floor used. |
| 10 | server.py:1072-1076; skills/medical-chronology-maintainer/SKILL.md:427-428 | Page get_files_on_matter to the end, subtract covered_document_ids, filter medical records by name or folder | L (paging + set difference); J (medical filter) | Trusted. `listingComplete` is only a warning. | The broker or connector returns the delta (find_files_on_matter already reads every page). The model keeps the medical/non-medical judgment. Likely overlaps the chronology audit. |
| 11 | sr19_form.py:150-152 | "If the matter already names the other driver's insurer, do not call it" | L (a check against the record) | Prose only. The code reads the parties (:112) but never refuses. | Refuse in code when the other side carries an insurer contact. |
| 12 | sr1_form.py:313-314 | Do not call when the client was a passenger | J/L | Prose only | Refuse in code when a matter role or field records passenger. Otherwise this stays judgment. |
| 13 | server.py:927-928 (`create_event`) | Pass time_zone: "Use the firm's authored zone" | L | Trusted | Fill it from customer.yaml. Refuse a value that differs from the authored zone. |
| 14 | server.py:838-841 (`update_task`) | Pass staff_id, which the model sources from the matter's personResponsibleStaffId | L | Trusted | Code reads the matter and fills it. |
| 15 | form_letters.py:497-499 (`unfilled` "tell the person who asked exactly those"), :513-514 (`card_values_to_check`); the left_for_client / left_for_firm / left_for_signer / not_in_file outputs of sr1, sr19 and funding_case_eval; the status outcomes of medicals, vendor and negotiation | Write the staff reply from facts code returned | L (prose built from code-held facts) | Trusted | Generalise `reply_block` (already in police_request.py:204 and form_emails.py:114) to every filing tool. |
| 16 | gap_orders.py:403-408 (NEXT_STEP); records_orders.py:416, :454-456 | Run a multi-step script: pass `order` unchanged, "Say each `picked` line first", say merged, name after_this_order, list would_order, ask the question once | L (control flow + prose from code facts) | The order round-trip is re-derived (`order_ref` records_orders.py:105, checked at records_place.py:85). The prose is trusted. | Code returns a reply_block. The order is held server-side behind a ref rather than as a JSON round-trip. |
| 17 | event_delete.py:209-215 | Pass `events` unchanged; loop "prepare again" past the 50 cap | L | Re-derived: each event is re-read and checked for subject/date before deletion (`_mismatch` :252) | Low priority. Each batch already needs an administrator's yes. |
| 18 | server.py:1453, :1461; :1681, :1689 (render_docx_template / render_docx_draft) | Write heading numerals and item labels/numbers "from the propounded set" | L (sequence numbering, matching against a closed list) | TRUSTED | Code numbers headings. Discovery item numbers come from the parsed propounded set. Likely overlaps the litigation audit. |
| 19 | cited_facts.py:1-25, `confirm` :176 (used by form_letters.py:380, sr1_form.py:239, sr19_form.py:137, police_request.py:164) | Locate the card or license file and the value; pass {value, file_id} | J (which file); the value step is redundant | RE-DERIVED: the value must sit beside its fixed label in the re-read transcription. The cost is redundancy, not accuracy. | Given only file_id, code extracts the label-adjacent value itself. This is clearest for the SAME_LINE crash-card fields. |
| 20 | Every write that takes a bare matter_id: server.py create_task :789, create_memo :1799, render_docx_* :1385/:1617; layout_tools add_negotiation_rows; medicals_provider add_medicals_provider; form_letters render_firm_form_letter; sr1/sr19 | Choose the matter from list_matters / get_contacts output | L (matching against a closed list) when the request names a client or number | Only the guard against another matter's number in the text (`_verify_matter_reference` server.py:496). No resolution token. | Extend the resolve_matter token (resolution_token.py) to dictated-request writes, as the post and invoice lanes already require. |

Outside the connector but enabled by it: skills/matter-memo-on-update/SKILL.md:63-64 has the model build the dedup key `op-mmou:<matterId>:<ticks>` and search memo bodies for it (L, trusted). memo_tools.py:46-50 already labels memo machine markers as DEBT that belongs in a ledger.

### Top 5

1. **The vendor expense amount is trusted without a source check.**
   - Evidence: vendor_invoice_tools.py:184-185 asks for "THIS invoice's charges as a string with at most two decimals". `parse_invoice` (vendor_invoice.py:285) checks shape only, although the bytes are re-fetched at :438.
   - Harm: a model misread or reformat becomes a staged expense at the wrong amount.
2. **The Medicals charge and dates have the same flaw.**
   - Evidence: medicals_tools.py:174-176 reads "this bill's total charges ... never a figure you added up, rounded or converted". The rule exists only in prose, and the filed range is in FILED_DOCS but its text is never consulted.
   - Harm: demands are built from this tab.
3. **The post journal restates outcomes code already holds.**
   - Evidence: post_journal.py:245-246 reads "``placed_on`` (the matter number it was filed on, from that letter's own resolve)". `_filing_problem` (:183) checks pages, never the matter.
   - Harm: a wrong matter number can reach the office manager's daily list.
4. **Money paging, totalling and comparison are left to the model.**
   - Evidence: server.py:1866 reads "a caller totalling a ledger must page the same way". server.py:1831 reads "The low-trust flag is availableBalance vs the firm's authored floor".
   - Harm: neither is computed in code anywhere.
5. **Dates are parsed by the model before matter resolution.**
   - Evidence: vendor_invoice_tools.py:94 reads "``date_of_birth`` (both ISO, YYYY-MM-DD)". The code parser `printed_date_candidates` runs only as a fallback for a garbled date_of_loss (matter_resolution.py:671-674).
   - Harm: an ambiguous US date the model converts wrongly drops a corroborating fact and turns a `unique` verdict into a question.

### Already clean (evidence)

- **vision.py:** structural provenance (`_compose` :632), the completeness gate (:349-360), no tools in the request (`build_request` :282).
- **Workbook totals are computed in code:** workbook_tools.py `add_workbook` docstring, "THE SUM IS COMPUTED HERE, in code ... never add a column of figures yourself".
- **Task and event dates are computed in code:** list_tasks returns `localDueDate` (server.py docstring :70-73 of list_tasks); list_events returns `localDate`/`localTime` ("Never convert startTime from UTC yourself").
- **create_event** appends the responsible attorney in code (server.py:947-952 region, docstring "The tool reads the matter and appends its personResponsible").
- **update_task / update_event** merge the vendor's full-replace PUT in code (event_update.py; server.py:836-838).
- **Matter matching and token minting are code:** matter_resolution.py:654 `resolve_matter`; a single fact never matches (:640).
- **Expense dedup reads the whole ledger in code:** vendor_invoice.py:509 `_preflight` uses `read_whole_ledger` and `classify_against_ledger`.
- **Vendor subject and description are composed in code:** vendor_invoice.py:319 `compose_entry`; finalized is always false.
- **Medicals tab choice, patient-to-claimant match, provider dedup and the note are code:** medicals_tools.py:262-302 (`_select_tab`, `_claimant_of`); module docstring "The row's note is COMPOSED HERE".
- **Negotiation row placement and duplicate detection are code:** layout_tools.py:228 `_plan`; layout_sections.py:214 `match_existing`.
- **Records-order integrity:** records_orders.py:105 `order_ref`, verified at records_place.py:85.
- **Event deletion:** each event is re-read and matched before delete (event_delete.py:252, :317).
- **Form letters, SR1, SR19 and funding fill every value from the record in code.** funding_case_eval takes only `matter_id` (funding_case_eval.py:284).
- **add_file:** `content_text` removes model-composed base64 (server.py add_file docstring, #2055).
- **_sdk/:** registration, manifest and conformance only; no prompts and no model calls (operator_connector_sdk/server.py, manifest.py, conformance.py).
- **_contract/records_contract/facts.py:** the reply facts are rendered by code. The model's own words are guarded by `unstated()` (:442), which refuses a page or count token the facts do not state. Residual: the model still writes the prose around the facts, guarded by regex. Acceptable.


---

<!-- report: in-turn -->

## In-turn skills audit: logic the model carries that code should own

Read at origin 2afdb2ab. Live-seat priority is from `operator/customers/ashton-price/customer.yaml`:
- inbox-triage, email-reply and intake-to-system-sync are NOT bound on A&P.
- The live crons are statute-watch, post-intake-daily-list, negotiation-watch, connector-auth-check and operator-help.
- Every in-turn job below is therefore person-initiated through the matter-inbox-router.

The chronology, litigation and case-file rows overlap the other auditors and are kept brief.

Paths are relative to `/Users/scottdurgan/dev/ss-console/operator/` unless they start with `docs/`.

### Table

| # | skill or file:line | What the model is asked to do | LOGIC or JUDGMENT | Re-derived by code (where), or trusted | Recommended move |
|---|---|---|---|---|---|
| 1 | `skills/matter-inbox-router/SKILL.md:117` | Run the conflict cross-check itself (`get_contacts` + `list_matters` on the sender and named parties) and decide whether to HALT and create the clearance task | LOGIC | Trusted. No overlay code runs or verifies a conflict check; a search finds only a comment at `hermes-smd-overlay/shared/matter_binding.py:385` | Code: a conflict probe at dispatch whose verdict is injected above the delimiter, with the clearance task created by code |
| 2 | router `:118`, `:57-84` | Pick one of about 25 classes. Several tells are deterministic: roster membership; "PDF with an empty or boilerplate body" (`:146`); an SMD-domain admin (`:139`); "the thread has a HELD chronology job" (`:142`); "`records_job_submit` is among your tools" (`:160`) | Mixed: about half LOGIC | Trusted, apart from the INITIATION AUTHORITY context and the attachment notice (`hermes-smd-overlay/shared/inbound.py:384-475`) | Code: the webhook router computes the deterministic tells and injects a candidate class; the model judges only the residue |
| 3 | router `:120-125` (`steps_to_run`) | Read a routine's SKILL.md and run a whole `### Job` section (trial binder, witness list) inside the turn | Multi-stage job in a turn | Dormant on A&P: no `case_manager.date_prep` is authored | Move to a lane, the same shape as records |
| 4 | router `:110` (Phase 1) | Run an `execute_code` poll on the retired `crane_gmail.py` (the skill says "Rework pending") | Dead path | Not reached: the seat is webhook-only | Delete it, or put it in code |
| 5 | `skills/combined-post-intake/SKILL.md:170-180` | Loop 15-page windows up to `pageCount` and carry a letter across a window boundary | LOGIC (the loop and the number of passes) | The tool caches the reads; nothing caps calls or spend per turn | Move to a lane |
| 6 | combined-post-intake `:233-249` | Partition the pages into letters | JUDGMENT | `record_post_batch` checks coverage only (`connectors/smokeball/smokeball_connector/post_journal.py:172`) | Keep the model call, but have it return a partition that a script validates and acts on |
| 7 | combined-post-intake `:497-525` | Map each tool status to the closed `issue` vocabulary, and type `placed_on` and `candidates` | LOGIC | Partly re-derived. `post_journal.py:183-199` checks only placed versus not placed against FILED_PAGES. PLACED_CHECK issues, `placed_on` and `candidates` are trusted; `post_journal.py:137-158` validates their shape only | Code: the keying tools record their own outcomes by sha256 + pages, and the record derives `issue` and `placed_on` from them |
| 8 | combined-post-intake `:545-583` | Compose a line per letter and the reconciling count ("13 pages, 5 letters, 3 filed...") | LOGIC | Count and labels trusted; dollar figures go through the money gate | On A&P this reply is silent because the daily list is authored (`:538`), so the record (row 7) is what Christa sees. The record should render the reply too |
| 9 | combined-post-intake `:400-403`, `:435-452`; `skills/vendor-invoice-intake/SKILL.md:206-218` | Re-read `get_expenses` or the Medicals tab and retype the figure into the reply | LOGIC | The money gate checks membership in the session's set of read figures (`hermes-smd-overlay/shared/outbound_gate.py:126-138`). It does not bind a figure to its matter, so matter B's figure on matter A's line passes in a multi-matter bundle | The tool returns the read-back figure already bound to its line |
| 10 | vendor-invoice-intake `:141-149` | Flag a statement, a credit, a non-USD invoice, several invoices in one PDF, or two figures for one invoice | Mostly LOGIC | Trusted | Code checks inside `stage_vendor_invoice` (amount sign, currency, invoice count) |
| 11 | `skills/file-work-requests/SKILL.md:114-127` | Resolve the matter by hand: `list_matters`, keep the exact number match, "preferring Open matters". Then WRITE: letters filed, Medicals rows, Negotiation rows, a task closed | LOGIC | Trusted. `render_firm_form_letter` takes `matter_id` with no resolution token, unlike `stage_vendor_invoice` (vendor-invoice-intake `:163-166`) | Require the `resolve_invoice_matter` token on every write tool |
| 12 | file-work-requests `:152-161`, `:200-202`, `:278-282`, `:410-416` | Choose the insurance card, dec page and authorization files by filename tokens ("auth" and "employ"), and close "the one open task whose subject names it" | LOGIC | Cited values are re-confirmed by `cited_facts` (`connectors/smokeball/smokeball_connector/form_letters.py:641`). File choice and the task match are trusted | Tool-side lookups that return the candidate or a refusal |
| 13 | `skills/discovery-served-watch/SKILL.md:170-176`, `:210-217`, `:222-231` | Dedup by scanning memos for the fileId; match the caption to a matter with `list_matters`; compute a `due_date` one to two business days out | LOGIC | `due_date` is scanned (`hermes-smd-overlay/plugins/hermes-smd-trust/outbound.py:352`) and the only ambient dates are today and yesterday (`outbound.py:617-628`). So a computed confirm-by date is refused, which means a redraft loop. I inferred this from the code and did not observe it | Use `resolve_invoice_matter(case_number)`; the tool computes the confirm-by date; dedup in code |
| 14 | `skills/medical-records-chaser/SKILL.md:182-198` | outstanding = roster minus "confidently received" (judged by filename); compute a confirm-by date; compose the external chase | LOGIC + JUDGMENT | `days_past_confirm_by` comes from code (`:207`). The rest is trusted, and the date is refused as in row 13 | A pre_run computes what is outstanding (the records-order-chase shape) |
| 15 | `skills/settlement-statement-feeder/SKILL.md:161-177`, `:133-139` | Sum the liens and subtract fee and costs into "net to client"; compare the trust balance with the gross | LOGIC (money arithmetic) | The money gate refuses any figure not read this session (`outbound_gate.py:279-314`). I did not confirm that `render_docx_draft`'s `draft_markdown` reaches that marker scan | The tool computes the net and the in-trust check |
| 16 | `skills/matter-status-responder/SKILL.md:48-52` | Decide whether the requester is the client (the privilege check); pick the "next step"; compose a CLIENT-facing status | JUDGMENT + LOGIC | Dates go through the identifier gate; the privilege check is trusted | Code: match the sender to the matter's client contact |
| 17 | `skills/matter-status-digest/SKILL.md:54-67`; `skills/stalled-matter-nudge/SKILL.md:48-52` | Run an `execute_code` Phase 1, which customer seats refuse (`skills/daily-needs-you-digest/SKILL.md:152-157`), then work out recency and bands by hand | LOGIC | Trusted | A pre_run, like statute-watch |
| 18 | `skills/daily-needs-you-digest/SKILL.md:176-190` | Read every open matter in one turn, then apply the bands, the ages and the ordering | LOGIC, multi-stage | The escalation ledger is code; the bands are trusted. The cron is off on A&P | A class-N pre_run |
| 19 | `skills/litigation-status/SKILL.md:286-340`; `skills/case-file-manager/SKILL.md:153-170`; `skills/medical-chronology-maintainer/SKILL.md:617-682` | Compose the delivered email from counts, with exact labels, the kinds-to-phrase table, and "consumed is `<m>`, never the total" | LOGIC | Held wording is enforced (`workspace_broker/reply_binding.py:573-590`, `workspace_broker/held_wording.py`). Delivered wording is not checked | The runner returns `reply_text`, as the records lane does (`runners/medchron/medchron/records/run.py:469`), and the bind refuses any other words |
| 20 | medical-chronology-maintainer `:278-336`, `:427-446` | Resolve the matter by dual probe; hunt the date of birth in at most 4 documents and "stop once two agree"; take the delta as the listing minus the covered ids; decide which files are "medical" (named ids override the runner's folder rules) | LOGIC | `requested_by` is overridden by code (`hermes-smd-overlay/plugins/hermes-smd-medchron/__init__.py:95-103`); the rest is trusted | The chronology auditor's lane |
| 21 | `skills/inbox-triage/SKILL.md` Phase 2 step 2 (not on A&P) | Pick the From address by the reply-identity rule | LOGIC | The broker refuses an unauthored From | Code picks the identity |

### Already thin, or already code (with evidence)

- matter-records REQUEST mode (`skills/matter-records/SKILL.md:76-96`): it relays the tool's `say` sentence word for word, or sends no reply when `say` is `none`.
- litigation-status and case-file-manager REQUEST modes: the submit tools take the requester and the words from the email; the reply is a fixed acknowledgement.
- Escalation acknowledgements go through `reply_verdicts` (router `:121`): code writes the verdict rows and renders the confirmation, which the model relays verbatim.
- statute-watch and records-order-chase: `skills/records-order-chase/SKILL.md:44-70` says the pre-run "does the whole job in code (execution class N, ADR 0050)".
- post-intake-daily-list: rendered by a pre-run with no model; the `post_journal.py` docstring says which lines are listed "from that vocabulary, never by the model's wording".
- Records lane facts: `hermes-smd-overlay/shared/matter_file_facts.py:167-186` calls `records_contract.facts.render`. This is the reference fix shape.

### Top 5

#### 1. The post-intake record is Christa's only notice on A&P, and the model writes its key fields (accuracy, live seat)

`skills/combined-post-intake/SKILL.md:538`: "Do NOT reply to this bundle. Everything that needs the sender is on that one email a day."

- Her daily list is rendered from the model's `issue`, `placed_on` and `candidates`.
- `post_journal.py:183-199` checks only that a letter recorded as placed has filed pages: "Placed letters must be filed pages; held ones must not."
- So a filed medical bill whose Medicals row failed, recorded with `issue` empty, drops off her list.
- And a mistyped `placed_on` reaches her as "needs calendaring" on the wrong matter number.

**Move:** the keying tools write their own outcomes to the journal, keyed by sha256 + pages, and `record_post_batch` derives `issue` and `placed_on` from them. Then the whole bundle (windows, resolves, filings, keying) becomes a queued lane, the shape the records request just left.

#### 2. Writes on a matter the model resolved by hand (accuracy, live seat)

`skills/file-work-requests/SKILL.md:120-121`: "keep the matters whose client is that person ... preferring Open matters."

- The letters, the Medicals and Negotiation rows, and the task completions that follow carry no resolution token.
- Vendor invoices already require one: "`stage_vendor_invoice` refuses without the `matter_resolution` a `unique` verdict returns" (vendor-invoice-intake `:164-165`).
- discovery-served-watch `:210-217` matches the caption with `list_matters` in the same way.

**Move:** every matter write takes the `resolve_invoice_matter` token.

#### 3. The model writes the delivered-job replies (accuracy, plus redraft loops)

`skills/litigation-status/SKILL.md` carries the exact count labels and explains why: "a shortened label reads as a different measure".

- The broker checks wording only on held jobs. `workspace_broker/reply_binding.py:568`: `if v.kind not in held_wording.KIND_LANE or not v.row or v.row.get("state") != "held":`
- Delivered litigation, case-file and chronology replies go out in the model's own words, with nothing checking their counts or labels.

**Move:** the runner renders the reply text (`runners/medchron/medchron/records/run.py:469` is the template), and `reply_bind` refuses any other words.

#### 4. The conflict halt is decided by the model alone (accuracy, safety)

`skills/matter-inbox-router/SKILL.md:117`: "Run the conflict cross-check FIRST. Before any routing, a read-only `get_contacts` + `list_matters` name/entity check".

- No overlay code performs or verifies it.
- The clearance task's shape is also left to the model.

**Move:** a code probe at dispatch whose result is injected, with the clearance task created by code.

#### 5. Skills ask for computed values the gates refuse (cost, redraft loops, output that can never land)

- discovery-served-watch `:222-225`: "set `dueDateOnly` to a near-term administrative 'confirm-by' date (1-2 business days out)". Against it, `outbound.py:617-624` treats only "UTC today and yesterday" as ambient: "Anything further out (a computed deadline, a hearing date) must come from a read".
- medical-records-chaser `:195-197` asks for the same kind of confirm-by date.
- settlement-statement-feeder `:165-168` lays out "gross recovery - attorney fee - case costs - (sum of lien figures) = net to client". Against it, `outbound_gate.py:303-310` refuses a figure that is "Not traceable to anything read this session ... Re-read the source record that carries the figure, or remove it."

**Move:** the tool computes the date or the net and returns it.
