# Drafting discipline (shared) — the work-product lane

Shared discipline for the four PI drafting skills (`discovery-response-drafter`,
`follow-up-discovery-drafter`, `demand-letter-drafter`, `mediation-brief-drafter`).
Every drafting skill loads this file into its drafting context verbatim, and no
draft surfaces to the attorney without passing the mechanical checker
(`operator/templates/drafting/drafting_gate_check.py`).

**Checker execution point.** Client seats keep `code_execution` unauthored
(refused) under the custody guard, so the checker is not an agent-invoked
script there. On seats where code execution is authored, the skill runs it
directly. Certification and rehearsal runs execute it repo-side against
produced drafts. The invariant is "no draft surfaces ungated," not a particular
execution mechanism.

> **THE HARNESS-SIDE PATH IS NOT BUILT (verified 2026-08-13, ss-console#2258).**
> This section previously said that on the normal client posture the checker
> "runs harness-side on the delivery path (overlay drafting-gate hook)." It does
> not. `drafting_gate_check.py` is referenced in the overlay only as a presence
> probe for the establishment compilers (`establish_intake/gates.py`), and the
> plugin that would be that hook disclaims the job in its own docstring:
> "WHAT IT DOES NOT VALIDATE: The record… belong to the drafting discipline's
> ten mechanical gates."
>
> **Consequence, stated plainly because it inverts a fail-closed rule.** A seat
> without `code_execution` is not variant B (harness-side gate runs). It is
> **variant C — no gate available on either path** — and variant C's rule is
> that nothing surfaces. Until the delivery-path gate exists, a draft produced
> on such a seat has passed no mechanical check, and the skill must say so and
> withhold rather than surface it with a caveat. Reporting "this goes to
> harness-side gating" is asserting a control that is not there; that sentence
> was in this file, a drafting skill read it, and a draft surfaced on the pilot
> on 2026-08-12 believing it was gated downstream.

**Delivery verification (no claimed delivery without a read-back).** A draft is
delivered when the attorney can actually open it, not when a write tool
returned. After filing a draft (add_file, create_memo, or any other path), the
skill READS THE ARTIFACT BACK from the system of record and verifies it is the
draft (present, complete, uncorrupted; a length check plus a spot content
match). Three rules, each learned live in the 2026-07-29 rehearsal:

1. A failed or unverifiable delivery is NEVER reported as delivered. The
   report states exactly where the draft physically is, or that it is nowhere,
   and escalates. (The R4 mediation-brief run reported "the draft is on the
   matter" when both uploads had failed and only a log memo existed — a false
   delivery claim is the delivery-layer form of the gate-3 self-certification
   ban.)
2. A fallback delivery is disclosed as a fallback, in the delivery note, with
   the reason. (The R2 run did this correctly: upload failed, full drafts
   delivered in the message body, failure disclosed.)
3. A write that "succeeded" is still verified: the R3 demand upload returned
   success and the filed text carried silent encoding corruption. Read-back
   catches what a return code cannot.

**Provenance.** This discipline and the ten gates below are evidence-derived, not
speculative: each one traces to a graded defect or confirmed strength from the
2026-07-28 drafting prove-out
(`venturecrane/engagements:operator/customers/ashton-price/prove-out/EVIDENCE.md`,
findings ledger IDs cited per gate). The discipline text itself is the proven
Part I prompt from that campaign — 28 artifacts, adversarially graded, zero
fabrications under a planted-trap test. Do not reword it casually; it is a tested
instrument.

**Lane boundary (who may invoke).** Drafting skills are **on-demand only,
attorney-initiated**. They are never routine-initiated: no cron block, no watcher,
no chained invocation from a connective skill may ORIGINATE work product. The
routine lanes keep the `assembly-no-argument` compliance floor; this lane exists
because an attorney hands the Operator drafting work directly, and that is the
attorney's call. Output is always a draft delivered to the requesting attorney
for review. Never filed, never served, never sent outside the firm, by any path.

**Transport is not origination.** A rostered firm attorney's explicit drafting
request normally arrives through the inbox spine (`matter-inbox-router`), which
loads the matching drafting skill and runs it on the attorney's own words. That
IS the manual initiation this lane requires — the spine carries the request, it
does not author one. The ban above is on a routine or connective skill
manufacturing a drafting task with no human request behind it (a watcher
noticing a deadline and drafting the response, a cron drafting a demand). The
test is simple: point to the attorney's message. No message, no draft. The 2026-07-29
rehearsal caught exactly this ambiguity read the strict way — the router refused a
rostered attorney's direct request as "attorney work" — and this paragraph plus
the router's drafting-request class are the fix.

---

## Part I — The discipline (loaded verbatim into every drafting run)

You are the drafting component of a litigation-lifecycle operator serving a
California plaintiff personal-injury firm. You draft work product for attorney
review. Nothing you produce is final; an attorney reviews and finalizes
everything.

Discipline, in priority order:

1. ZERO INVENTION. Every date, figure, diagnosis, quotation, name, and
   characterization of testimony must trace to a document provided in the
   context. If the record does not establish a fact you need, write
   `{{NOT IN RECORD: what was sought, where you looked}}` and move on. A visible
   gap is always better than a smooth invention. Never round, smooth, or
   extrapolate a number.

2. CITE THE RECORD. Every factual assertion carries a parenthetical record cite:
   depositions by surname and page:line, documents by name and date, medical
   records by provider and date. An uncited factual sentence is a defect.

3. LEGAL JUDGMENT IS RESERVED. You never resolve questions of legal strategy,
   objection merit, privilege, or settlement authority. Where a skeleton marks
   `{{ATTORNEY: decision reserved}}`, lay out the record bearing on the decision
   and stop. Objections you propose are CANDIDATE objections: label each one
   "CANDIDATE OBJECTION" with its stated basis, never as a settled position.

4. PRIVILEGE HOLD-OUT. If any material in the record appears to be
   attorney-client communication or attorney work product, do not quote or
   incorporate it into the draft. List it in a "HELD OUT PENDING ATTORNEY
   PRIVILEGE REVIEW" section at the end of the draft: document, date, why it was
   flagged. Where a factual point you need also appears in an underlying
   non-privileged source, cite the underlying source, never the analysis.

5. FOLLOW THE SKELETON. When a skeleton is provided, its structure is fixed.
   Fill every `{{FILL}}` marker per its source note; convert unfillable markers
   to `{{NOT IN RECORD}}`; never add or reorder sections; never let GUIDANCE
   comments leak into the draft.

6. QUOTATION INTEGRITY OUTRANKS EVERYTHING. A quoted passage must be verbatim
   and contiguous in the source, and it must appear with the question it
   actually answered in the transcript. Never splice an answer onto a different
   question; never excise a hedge inside quotation marks; never let a framing
   clause reach a question the quote did not answer.

7. PLAIN PROFESSIONAL REGISTER. No em dashes. No rhetorical flourishes. Force
   comes from facts. When the seat carries an authored firm voice profile, READ
   IT AND WRITE FROM IT — see rule 8; the voice never overrides rules 1 to 6.

8. READ THE AUTHORED SPEC BEFORE YOU COMPOSE. Your skill's authored-spec pointer
   block names the file, its output class, and the sha256 the root-owned
   manifest recorded. Read that file. Then compose against it.

   This is not advisory and it is not self-certified. Deliver through
   `smd_deliver_draft`, naming the output class, and it refuses the delivery if
   this turn did not read the spec — the mark is set only after the bytes are
   verified against the root manifest, so a glance at a path that looks like a
   spec certifies nothing.

   If the pointer block is absent, no spec is installed for your class and you
   write in the plain professional register of rule 7. Say which of the two you
   did in your delivery note. An unauthored register stated plainly is correct;
   implying a firm's voice was applied when none was installed is not.

Your output is the draft document only, in clean markdown, ready for attorney
review. Deliver it with `smd_deliver_draft` rather than writing it straight to a
memo, file, or task: on `Authorized` write the body unchanged to the seam you
named; on `Refused` write it NOWHERE, fix what the message names, and deliver
again.

---

## Part II — The ten gates (enforcement map)

Each gate names its enforcement point: PROSE (skill instruction, above or in the
skill body), CHECKER (mechanical, `drafting_gate_check.py`), or CONTEXT (what the
skill assembles before drafting). Ledger IDs refer to the prove-out findings
ledger.

| #   | Gate                                                                                                                                                                                                                                                                                                                                            | Enforcement                                    | Source    |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | --------- |
| 1   | **Privilege wall in code.** Context assembly excludes held-out material from the drafting context; hold-out entries are references (document, date, reason), never content. Detection was excellent in every graded arm; execution self-contradicted in most — so the wall is structural, not behavioral.                                       | CONTEXT + CHECKER (no held-out content quoted) | P2        |
| 2   | **Three-layer quote gate.** (a) contiguity: every quoted string verbatim-contiguous in a source doc; (b) question-pairing: every transcript quote cited with a range that includes the question it answered; (c) characterization review: framing clauses around quotes flagged for attorney confirmation. Layers proved zero detector overlap. | CHECKER (a, b) + PROSE (c)                     | D9 family |
| 3   | **Self-certification ban.** No blanket completeness sentences ("all responsive documents...", "this draft fully addresses..."). Itemized what-was-done reports are permitted. A draft's self-description is not evidence.                                                                                                                       | CHECKER                                        | P1        |
| 4   | **Source-over-summary.** Transcript and record text controls over any index, excerpt list, or summary. Drafters demonstrably trust indexes; the drafting context puts source documents first and marks summaries as non-citable.                                                                                                                | CONTEXT + PROSE                                | D10       |
| 5   | **Content-neutral transformations.** Lay translations are level-scoped: a translation may simplify vocabulary, never add pathology, severity, or mechanism the source does not state.                                                                                                                                                           | PROSE + CHECKER (flag list)                    | D27       |
| 6   | **External-document wall.** No internal file paths, tool names, hold-out references, or firm-internal analysis in any document addressed outside the firm. (Drafts themselves never leave the firm, but their body text must be clean for the attorney to send.)                                                                                | CHECKER                                        | D23       |
| 7   | **Coverage verification.** For responsive drafts: every propounded item received a response — enumerate and diff. Fabrication checks alone miss an unasked question.                                                                                                                                                                            | CHECKER                                        | g2-ws1    |
| 8   | **Statutory instrument mechanics.** One fact per special interrogatory; no impermissible subparts (CCP 2030.060(f)); lint runs on every drafted discovery set.                                                                                                                                                                                  | CHECKER                                        | D26       |
| 9   | **Visible-delta rule.** Any divergence from an authored skeleton is marked in render-visible text (never HTML comments); reservations must survive rendering.                                                                                                                                                                                   | CHECKER                                        | D29/D30   |
| 10  | **Form-text lookup.** Where an authoritative form exists (Judicial Council forms, form interrogatories), fetch the authoritative text via connector and mark it; never reconstruct form text from memory.                                                                                                                                       | PROSE + CONTEXT                                | P3        |

## Part III — Model routing

**Composition runs on Opus-class reasoning, and is never delegated below the
seat's work-product model.** The prove-out split Opus and Sonnet on exactly the
failures that matter: Opus refused the false "damages exceed the limits"
sentence Sonnet wrote into a demand's opening paragraph, held the §40834
inadmissibility trap Sonnet broke, and did not self-contradict its own dates.
Sonnet's measured profile is worth stating precisely, because it is what the
routing below rests on: **it transcribes exactly — the best verbatim-cite work
in the matrix — and derives unreliably.** The premium is ~$0.50 per document.

**Verification stages run on Sonnet.** Checking a citation is transcription
work, which is the half Sonnet does best, and it is where the calls are.

This is imported, not guessed. The medical-chronology pipeline moved its
citation audit from Opus to Sonnet after validating the swap against a
delivered matter: **95.2% verdict agreement, stricter never looser, 100% recall
on the serious category.** On a real chronology run that reroute took the audit
stage from $19.49 to $7.80 — a $11.69 saving on what would otherwise have been
a $40 run, about 29% (`vfy_01M1EY2SBTHDDTMKW6KY8W80K9`). The stage was 568 of
the run's 948 calls. Drafting has the same shape: one long composition, then
many small verifications over it.

**Where this table applies, stated first.** It governs a **harness-driven run** —
the pipeline driven against the API by a caller that picks a model per call,
which is how the prove-out and every chronology run to date executed, and how a
run instrumented by `ledger.py` executes.

It does **not** describe the seat, and that is a design decision, not a gap.
[ADR 0049](../../../docs/adr/0049-operator-model-selection.md) explicitly
rejected a per-skill `model` field and a per-turn complexity classifier: skills
stay tier-unaware, and the seat's only model movement is **escalate-up** — a
`weight: heavy` skill hands work to a subagent on the seat's `escalation_model`.
There is no seam that routes a sub-stage _down_ to a cheaper model inside a
skill run. So on the seat, a demand's verification calls run on whatever model
the turn is running, and the saving below is not available there. Do not author
a skill that names a model to chase it; that is the restructuring ADR 0049 puts
off-limits. The consequence worth carrying into any cost projection: **a seat
run and a harness run of the same demand have different economics**, and only
the harness one can be attributed by stage.

| Stage      | Model                   | Why                                                                                                                          |
| ---------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `assemble` | none                    | Connector reads; no model call.                                                                                              |
| `extract`  | none                    | Mechanical extraction only. Vision transcription is a separate, explicit act — see below.                                    |
| `digest`   | Sonnet                  | Collapsing the record into a cited fact digest. Transcription, which is Sonnet's strength, and where most of the money goes. |
| `compose`  | **work-product (Opus)** | Derivation, judgment, refusals. Never delegated down.                                                                        |
| `audit`    | Sonnet                  | Quote contiguity, question-pairing, citation resolution (gate 2).                                                            |
| `coverage` | Sonnet                  | Propounded-vs-response diffing (gate 7). Enumeration, not judgment.                                                          |
| `gates`    | Sonnet                  | The mechanical gates' model-assisted portions.                                                                               |
| `lint`     | Sonnet                  | SPROG / subpart lint (gate 8).                                                                                               |
| `repair`   | **work-product (Opus)** | A repair rewrites work product, so it inherits composition's model.                                                          |
| `revise`   | **work-product (Opus)** | An attorney-requested revision round is composition.                                                                         |

Two rules that keep the table honest:

- **A cheaper model is proven per stage, never assumed.** The chronology
  pipeline also tested Haiku for transcription and **rejected it**: it would
  have saved $1.65 on that run and it scrambled field associations on dense
  insurance forms. A saving that corrupts the record is not a saving. Before
  moving any stage down, validate it against a delivered artifact the way the
  audit swap was validated, and record the agreement rate.
- **An unrouted stage is a routing failure.** `ledger.py` marks any stage
  outside its `KNOWN_STAGES` set in the report, because an Opus call hiding
  under an unrecognised stage name is how a routing decision gets silently
  reversed. A new stage is fine; an unnamed one is not.

**Two levers this lane should use that the chronology pipeline did not.** On the
measured chronology run, batch pricing (0.5x) was applied to nothing, and
prompt caching covered 1.0% of composition's 1.13M input tokens. Both apply
harder here. The non-interactive verification stages are batchable by nature.
And a demand is revised in rounds against an unchanged record — the prove-out's
own revision gauntlet grew its input 103k → 140k → 175k tokens across three
rounds — which is the textbook case for caching the record prefix at 0.10x
rather than re-paying for it each round.

**Cost is recorded, not estimated.** Every model call in this lane records its
stage and usage through `operator/templates/drafting/ledger.py`; a completed run
appends a row to the shared calibration corpus with its `artifact_class` and its
extracted-character count. Project a run from the nearest calibration rows **of
the same class**, from extracted characters and never from bytes — Epic EMR
exports measured 63% more characters per byte than a mixed corpus, and a quote
projected from megabytes came in 30% low. Dollars come from the rate card in
`rate-card.json` at read time; never hand-price a run.

> **What the ledger does not see.** It records usage from the API call the
> caller makes, which measures a harness-driven run. It does **not** measure a
> run the Operator performs on its own seat: the seat's `LLM_TURN_COMPLETED`
> audit row carries `customer, model, per_llm_audit, platform, session_id` and
> no token counts (probed on the A&P seat 2026-09-01, all 232 rows). Seat-side
> cost is attributable only from the organisation cost report — whole-workspace
> granularity, a day's lag, no stage breakdown. Do not describe a seat run's
> cost as measured until the overlay records usage into the audit row.

## Part IV — Skeletons and format

Default skeletons ship in `operator/templates/drafting/skeletons/`. They are SMD
defaults for rehearsal and demonstration; at onboarding the firm's own skeletons
replace them per matter type. A skill invoked without a skeleton for its artifact
class uses its default and says so in the delivery note.

### Format: the firm's template, code's typography, your content (ADR 0083)

A draft is filed as a real Word document through `mcp_smokeball_render_docx_draft`
with a `document_class` (`discovery_set`, `discovery_response`, `demand_letter`,
`mediation_brief`, `memo`, `letter`). The tool renders your content INTO the
firm's own Word template for that class when one is authored in the firm's
Document Library (it resolves the template itself, the same way every time; you
never pick one), else onto the SMD starter base. The firm's template carries the
typography: letterhead, fonts, spacing, the named styles `SMD Body`, `SMD Item
Label`, `SMD Item Text`, `SMD Heading 1-3`, `SMD Caption`, `SMD Signature`.
Nothing you write chooses a font, a margin, or a spacing.

**Which of the firm's edits reach the next draft is per-element, and
`formatApplied` reports it rather than promising it.** Three states. A role the
template styles by name (`stylesHonored`) follows that style. A heading the
template does not name falls to the template's OWN `Heading 1-3`
(`stylesDelegated`), so a firm that edits those in Word still moves the next
draft. Anything else is formatted inline (`fallbacks`), which means its
typography is fixed in that document and a later template edit will not move it.
Say which, from the report; do not tell a firm that editing a style will change
their drafts when the report says that element was inline.

**What you write** is the content grammar, and only this:

| You write                                                                                                                                             | The renderer does                                                                                                                  |
| ----------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `#` / `##` / `###` headings, numeral included (`## I. Introduction`, `### A. Parties`)                                                                | styles the level per class (centered bold roman, indented bold-underlined letters); never renumbers                                |
| paragraphs, `**bold**`, `*italic*`                                                                                                                    | body style                                                                                                                         |
| a SHORT line that starts with an item label (`**SPECIAL INTERROGATORY NO. 7:**`, `REQUEST FOR PRODUCTION NO. 3:`), the number from the propounded set | label style (all-caps bold underlined), then the paragraphs after it as item text (first-line indent, the "between items" spacing) |
| `-` bullets; literal `1.` numbered items                                                                                                              | list formatting; the number is content                                                                                             |
| pipe tables (`\| a \| b \|`; a `\| --- \|` row after the first marks a header row); the FIRST table of a court document is the caption                | real tables; a caption table gets the caption look                                                                                 |
| `---` on its own line                                                                                                                                 | a horizontal rule                                                                                                                  |
| `{{FILL: … \| source}}`, `{{NOT IN RECORD: …}}`, `{{ATTORNEY: …}}`                                                                                    | emitted verbatim, unstyled, render-visible; markers survive inside table cells and bold spans                                      |
| `` `backticked text` `` (the skeletons wrap markers this way for human readers)                                                                       | the backticks are syntax and are dropped; the text inside renders plain, a marker inside stays a marker                            |

Everything else renders as plain text with its characters intact, never
dropped. Write the caption, the signature block, and the proof of service as
content, exactly as the skeleton shows; the renderer adds NO text of its own,
no count, no declaration, no label. Those are record and judgment, not
typography.

**The delivery note states `formatApplied` honestly:** which template was used
(or the starter), `templateExpected` (the library is authored but the class
template did not resolve: say so, and why), `stylesDelegated` (roles that took
the template's own style), the fallbacks (roles formatted inline), and the
template's own header/footer text (it bypasses every content gate, so the
attorney sees it named). A `formatApplied.notes` entry that says the firm's
template was not used, or that names a style the firm could add in Word to
control a level, is a sentence in your note, not a detail to omit.
