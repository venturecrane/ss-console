## Why

The 2026-11-04 model-as-logic audit found two places on the firm's post-intake and keying path where the model restated a fact a tool already held, and nothing in code caught a wrong value:

1. **The post record was typed.** `record_post_batch` took each letter's `placed_on` (the matter number it was filed on), its `issue` and its `candidates` from the model, typed from memory of the turn. The connector checked only page spans, so a misremembered matter number, or a Medicals row that never landed recorded as landed, reached the office manager's daily post list as fact. The filing ledgers it checked against are in-process and forgotten on restart; `add_medicals_row` and `add_negotiation_rows` recorded nothing at all.
2. **Figures and dates were converted by the model.** Every money tool refused `$`, commas and a split cents box, so the model turned "$1,250.00" or a CMS-1500's "8300 00" into "1250.00" itself (the 2026-11-03 runner defect read a split box at 100x). Dates had to arrive as ISO, so "10/01/2026" was converted by the model. And nothing checked that the figure written was printed on the document it came from. Separately, `get_expenses` told the model to page and total the ledger itself, and `trust-balance-nudge` compared a balance against a floor no seat authors.

Facts this relies on (probed 2026-11-04): `letter_pages.py:311-424` (FILED_PAGES / SCANNED_PAGES / FILED_DOCS, in-process), `post_journal.py:117-199,225` (model-typed fields; span checks only), `_sdk/.../server.py:166-173` (the wrapper translates an exception after the body), `medicals_layout.py:13,104`, `vendor_invoice.py:244,262`, `layout_sections.py:42,172` (strict parsers), the runner's `howell.money` contract and cases (#3729), `cited_facts.py:160,177` (whole-document re-read), `server.py:1824,1854` (balances and one-page expenses), `smokeball_connector/server.py` at its 890-line module-size baseline.

## What changed

**Mechanism 1: outcome ledger; the post record is derived, never typed.**

- `outcome_ledger.py` (new): on disk under `$SMD_POST_JOURNAL_DIR/outcomes/<sha256>-<sha256(message_id)[:16]>.jsonl`, 0600, one whole-line append under a lock, `RETAIN_DAYS`. Rows `{ts, tool, sha256, message_id, first_page, last_page, matter_id, matter_number, status, detail, returned_id, candidates}`. `recording()` writes in `finally`, so a tool that raises leaves a row with the exception's class as `status`. An unwritable ledger never fails the act; the record then derives `not_attempted`, which lists the letter for a person.
- Writers: `file_attachment_pages_to_matter` (`filed`, matter id and the number its resolution named), `stage_vendor_invoice` (`staged` / `figure_not_saved` / `possible_duplicate` / `refused`), `add_medicals_row` (`written` / `figure_not_saved` / `tab_not_written`, keyed from the filed bill's FILED_DOCS record), `add_negotiation_rows` with `operator_tab` (`written` / `figure_not_saved` / `possible_duplicate` / `offer_not_written`), and `resolve_invoice_matter` with the new optional group `sha256, first_page, last_page, message_id` (all four or none; a partial group is `refused` and nothing is searched; the verdict row carries the candidates' matter numbers). The filing and the stage gain an optional `message_id`.
- `post_outcomes.py` (new) holds the derivation table, in code and stated in the skill: filed + the kind's act landed is `""`; the act missing is `tab_not_written` / `expense_not_staged` / `offer_not_written` with "no attempt recorded"; failed is that issue (or the row's own `figure_not_saved` / `possible_duplicate`) with the tool's reason; held with a verdict is `ambiguous` / `one_candidate` / `no_match` / `search_failed` with the verdict's candidates, or `filing_refused` after a `unique`; nothing at all is the new `not_attempted`. A letter whose pages straddle a filed document, or pages staged as a vendor's bill recorded as another kind, is refused.
- `record_post_batch` takes only `first_page, last_page, sender, kind, detail` per letter. A letter passed with `placed_on`, `issue` or `candidates` is refused and the refusal names what they derive to. `kind` stays the one model input (the module docstrings say so). `_partition_problem` and `_filing_problem` are kept; the result gains `letters_recorded`.
- FILED_DOCS records carry the bundle `sha256`, `message_id`, the resolution's matter number and the pages' text (read for nothing: the cached reading or the text layer, else none).
- Daily list `pre_run.py`: `not_attempted` in the vocabulary (pinned equal to the connector's) with its own sentence; nothing else changed.

**Mechanism 3: as printed in, parsed in code, confirmed in the source.**

- `printed_money.py` (new): `money(raw)` is `howell.money`'s contract (`$`, commas, split cents box, parentheses and leading-minus negatives; other inner whitespace is None), one stricter rule: a comma only as a thousands separator ("1,25" is None). `positive_money` for charges. `howell.py` is untouched (wave 2 re-points it).
- `printed_dates.py` (new): `date_from_printed(raw, *, two_digit_year=True)`: ISO passes through, a written-out date ("October 1, 2026") reads, otherwise exactly one `printed_date_candidates` reading or None; a date of birth passes `two_digit_year=False` so "04/12/88" is never guessed.
- `parse_amount`, `parse_charge`, `parse_row` read through them; tool docstrings say "exactly as printed". `invoice_date` and Negotiation dates read as printed. `resolve_invoice_matter` gains `date_of_birth_as_printed` (ISO params kept for code callers; an unreadable one is listed in `ignored`). `add_medicals_row` takes `service_start_as_printed` / `service_end_as_printed`; one that is not one date is refused naming the string.
- `cited_facts.confirm_in_pages(client, matter_id, file_id, first, last, value, labels)` plus `confirm_in_text` / `printed_in`: values compare as values (a figure on the line's figures, a split box as one figure, so "125,000.00" never confirms 1,250.00 and "8300 45" never confirms 8300; a date on the line's dates), beside a label or anywhere; an unreadable source is `unreadable`, never "not printed".
- Source checks: `stage_vendor_invoice` refuses unless the amount is printed beside a total / amount / balance / due label on the invoice's own pages (the bundle span for a bill in the post, the text layer for an emailed PDF), checked before the pages are claimed; `add_medicals_row` the same for the charge and both dates over the filed bill (its held text, else the matter's copy); `add_negotiation_rows` with `operator_tab=true` requires `source_file_id` and confirms every amount (beside an offer label) and date. A person-dictated row (no `operator_tab`) needs no document.
- `money_reads.py` (new, registered from `attachment_tools` because `server.py` is at its ratchet): `get_expense_totals(matter_id)` (READ) reads the whole ledger, Decimal totals, finalized / unfinalized, `by_cost_type`, `listingComplete`, `deletedExcluded`; no total for an incomplete ledger or an unreadable amount. `get_matter_balances` moved here whole (server.py shrank, module-size baseline regenerated) and returns `low_trust`, `floor_usd`, `shortfall_usd` against `personas[].skills[trust-balance-nudge].settings.trust_floor_usd`; absent is `low_trust: null, reason: "no floor authored"`; two personas disagreeing is no floor. `expense_ledger.read_whole_ledger_counted` added.
- Validators: `settings.trust_floor_usd` accepted only on `trust-balance-nudge`, positive number, in both `bootstrap/validate.py` and `sections-persona-skills.ts`, with three parity fixtures and the content hash re-pinned on both sides. `_template/presets/pi-law-firm/customer.yaml` carries the commented key. No seat's customer.yaml is changed.
- Overlay: `get_expense_totals` is READ in `shared/action_classes.py`, a tenant-source read in `shared/provenance.py`, and a firm-record read in the fence-completeness list. `provenance._MONEY_KEY_SUFFIXES` gains `_usd`, so a reply quoting `floor_usd` / `shortfall_usd` is traceable to the read.

**Skills** (version bumps): `combined-post-intake` 0.2.0 (step 3 passes the group and `date_of_birth_as_printed`; steps 4, 4a, 4b, 4d pass `message_id`, figures and dates as printed, `source_file_id`; step 5 drops the three fields and carries the kind -> act table; step 6 records under her reply's id), `vendor-invoice-intake` 0.3.0 (dates and amount as printed, the group, `message_id`), `trust-balance-nudge` 0.2.0 (acts on `low_trust`, does nothing on null "no floor authored"). The resolver line before `create_memo` is left to the writes-and-conflict PR, as agreed.

**Docs**: handbook connector inventory regenerated; `verticals/law-firm/smokeball-surface.md` updated.

Out of scope and untouched: every `register()` line and `requires_resolution` (writes-and-conflict), the broker, any runner file, doctrine.

## Tests

- Connector `operator/connectors/smokeball/tests`: 1650 passed, 2 skipped (was 1488 + 2 skipped on the token-sdk base). New: `test_outcome_ledger.py` (35), `test_printed_money.py`, `test_printed_dates.py` (incl. the confirm-in-source cases: printed, missing, split cents box, comma, beside no label, unreadable), `test_money_reads.py`; updated medicals, layout, vendor-invoice, letter, post-journal, attachment-source and conformance tests.
- Overlay `operator/hermes-smd-overlay/tests`: 7421 passed, 41 skipped. New validator tests, the computed-money provenance test.
- Daily list `operator/skills/post-intake-daily-list/tests`: 10 passed (new: every issue has its own sentence).
- Substrate `operator/tests safety_substrate bin/tests`: 1821 passed, 1 skipped. Function-size ratchet green, pyright ratchet holding at 84, every noqa reasoned.
- `ruff check` and `ruff format --check` clean (operator root config and overlay).
- `npm run verify`: VERIFY_RESULT

Mutations run, each caught:

1. `outcome_ledger.recording` without the `finally` (append only on success): `test_a_filing_that_raises_still_leaves_a_row` and `test_a_medicals_row_is_keyed_by_the_bill_it_came_from` fail.
2. `cited_facts.confirm_in_text` returning `printed` for any text (a figure absent from the source accepted): 7 tests fail across medicals (charge not printed, beside no label, date not printed), vendor invoice (amount not printed, beside no label) and Negotiation (amount and date not printed).

## Seat artifacts

- Jobs bundle and image artifact paths (`.github/operator-bundle-paths.txt`, `.github/operator-artifact-paths.txt`): `operator/connectors/smokeball/**`, `operator/skills/**` (three skills and the daily-list pre-run).
- Image: `operator/hermes-smd-overlay/**` (validator, action map, provenance suffix).
- Release order: the overlay's READ entry for `get_expense_totals` ships in the image; until it is on a seat the new read is unclassified and refused (fail-closed). The skills' new arguments need the connector bundle on the seat before the skills; a skill passing `message_id` to an old connector is refused by argument validation, so release the bundle first.

## Acceptance criteria

| AC                                                                                                                                                                    | Status | Evidence                                                                                               |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------ |
| Each keying act writes its own outcome row keyed by bundle and message, 0600, including on a raise                                                                    | met    | `test_outcome_ledger.py`; mutation 1                                                                   |
| `record_post_batch` derives `placed_on`, `issue`, `candidates` per the table, incl. "no attempt recorded" and `not_attempted`                                         | met    | `test_outcome_ledger.py::test_the_derivation_table`, `test_post_journal.py`                            |
| A letter passed with a derived field is refused, naming the derived values                                                                                            | met    | `test_post_journal.py::test_a_letter_passed_with_a_derived_field_is_refused_naming_the_derived_values` |
| Money and dates are taken as printed and parsed in code (howell contract)                                                                                             | met    | `test_printed_money.py`, `test_printed_dates.py`, tool tests                                           |
| A figure or date not printed on its source is refused, nothing written                                                                                                | met    | medicals, vendor-invoice, layout tests; mutation 2                                                     |
| `get_expense_totals` totals the whole ledger; no partial total                                                                                                        | met    | `test_money_reads.py`                                                                                  |
| `get_matter_balances` decides `low_trust` against the authored floor; unauthored is null "no floor authored"                                                          | met    | `test_money_reads.py`; validator parity fixtures                                                       |
| (runtime) On pilot-smokeball, a combined post bundle run end to end records a batch whose `placed_on` / `issue` match the tools' outcome rows read back from the seat | unmet  | proved by the lead on seats                                                                            |
| (runtime) On pilot-smokeball, a medical bill whose charge is passed off by a digit is refused "not printed" and nothing reaches the Medicals tab                      | unmet  | proved by the lead on seats                                                                            |
| (runtime) `get_matter_balances` on A&P returns `low_trust: null, reason: "no floor authored"` and the nudge drafts nothing                                            | unmet  | proved by the lead on seats                                                                            |

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01WxHZSoHD5KPopoAr9CpPSF
