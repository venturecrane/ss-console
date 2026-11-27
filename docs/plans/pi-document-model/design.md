# PI discovery documents: one model, one reader, one writer

Status: design, awaiting the Captain's approval of slice 1. Written 2026-11-26 from a design pass over origin/main `e50de3a32`. Paths are under `operator/runners/jobs/jobs/drafting/` unless given in full.

## Why

Every PI firm produces the same family of discovery documents with different formatting, style and content retrieval. Today the Word file is flattened to markdown and each consumer re-finds structure with its own regex: nine separate structure readers (Premises P1-P9). On 2026-11-26 the filed-set reviser failed four $0 rehearsals on the first firm's real papers for 16 layout reasons (unnumbered definitions under a bold title, a term with the comma inside curly quotes, a declaration stating the count four times with per-reason subset lists, a signature table after the last request, "No."/"Nos." agreement, written-out vs range lists, the "X or XS" plural form, capitals inside definitions). Once patched, the content matched the firm's hand version exactly. The 16 failures are one pattern, not 16 bugs.

## 1. First-principles model

Every PI discovery paper is the same ordered regions: caption, title, identification block (propounding party, responding party, set number), front (preliminary statement, general objections, definitions, instructions), numbered items, closing (dated line, signature), attachments (declaration for additional discovery, verification, index of exhibits, proof of service), attorney notes. Only the item's shape differs: a request (sets) or request plus objections, answer and cites (responses).

| Varies by                   | What                                                                                                                                                                                                       |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| (a) Vertical / jurisdiction | Type list, item label words, required regions per type, the 35 limit and which types it binds, declaration statute and its reasons, DISC-001 text, verification rule, which regions are attachments        |
| (b) Firm style              | Where each region sits and how it is marked in Word, numbered or unnumbered definitions, term quoting and plural form, list style, "No."/"Nos.", signature form, fixed wording (frame, phrase book), paper |
| (c) Matter                  | Parties, court, case number, set number, served requests, facts, exhibits, prior sets                                                                                                                      |
| (d) The ask                 | Type(s), target party, operations, counts, the attorney's words                                                                                                                                            |

## 2. Model, reader, writer

- **Model** (`paper/model.py`, dataclasses): `Paper{type, caption, title, ident, front, definitions[Definition{term, plural, text}], instructions, items[Item], closing, declaration, verification, index, service, notes, blocks}`; `Item{number, label, parts, request, objections, answer, cites, parent}`; `Declaration{count_mentions, reason_lists[{reason, numbers}]}`. Every field holds the ids of the Word blocks it came from; every body block belongs to exactly one field.
- **Reader** (`paper/read.py`): docx to `Block{text, bold, caps, style, numbered, in_table, align}` to `Paper`, driven by a style profile. No markdown in between.
- **Style profile** (`paper/profile.py`, one JSON per firm per type, pinned in job inputs like the phrase book), learned by code at $0 from the firm's finished documents: per region the learner tries a closed list of generic detectors (heading style, bold-only paragraph, caps line, table row, label word plus number) and keeps the one satisfying the invariants on every corpus document, then records measured values (`definitions.numbered`, `term_quote`, `plural_form`, `list_style`, `signature`, `declaration.count_mentions`).
- **Invariants**, also the fail-closed test at read time: every block assigned to one region; items 1..n with no gap; declaration counts equal the item count; each reason list a subset of the items; `write(read(doc))` is the same XML (`revise/xmlproof`). A miss returns `Unread(region, block, why)`; the job holds for SMD (`layout_unread`) and nothing is filed. The fix is adding the document to the corpus and relearning, never a regex patch.
- **Writer** (`paper/write.py`): in place for anything filed (wraps `revise/inplace.py`, a new block is a clone of its neighbour); fresh for first drafts (wraps `firm_frame.apply` plus `render.render(base=)`). Number lists, "No./Nos.", verb agreement and capitals come from the profile.

## 3. Keep, wrap, retire

- **Keep:** `revise/inplace.py`, `revise/xmlproof.py`, `revise/layout.py`; `library_setup/frame_make.py`, `frame_facts.py`, `frame_lines.py`, `styles_make.py` (already the harvest of fixed wording from a filed paper; becomes the profile's wording half); `forms/catalog.py`, `forms/policy.py`, `forms/render_body.py`; `rulebook/book.py`, `counts.py`, `lane_proof.py`, `replay/`, the `Oracle` protocol.
- **Wrap (algorithm stays, input becomes the model):** `set_items.renumber/remap`; `propounded.add/cut/follow_ups/check_plan/scope`; `propounded_decl._carry/_compress/_verb`; `propounded_define.term_ok/define/capitalize`; `revise/markers.settle`, `revise/index_fix.recompute`, `challenge.py`.
- **Retire (layout detection):** `set_items._label`, `responses._HEAD/_head`, `served._SERVED_ITEM`, `sets._DEFS/_region`, `propounded_define._DEF_LINE/_BOLD_DEFS/_BOLD_END`, `propounded._CLOSING/_label_style/decl_block`, `exhibit_index.read_responses`, `amended.our_responses`, `firm_frame.regions/ID_LINE/CLOSING_START/SERVICE`.

## 4. Lanes as model operations

| Lane                 | Operations                                                      | Model returns                                                   | Code decides                                                        |
| -------------------- | --------------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------- |
| First-draft set      | new, add_items, define, attach_declaration                      | item and definition texts                                       | type, count, numbering, declaration, caps, frame                    |
| First-draft response | new, restate(served), answer(n), object(n, ground), build_index | facts with cites per item, candidate grounds                    | restated text, objection wording (phrase book), index, verification |
| FROG response        | same, from the DISC-001 catalog                                 | values per subpart                                              | everything else (as today)                                          |
| Amended              | subset(challenged), answer(n)                                   | challenged numbers (checked against the letter), new answers    | which numbers, title, carry-over                                    |
| Revise               | define, add, follow_up, cut, reword(n)                          | the plan (checked verbatim against the email), texts, a ranking | scope, numbering, citations across files, declaration lists         |
| Meet-and-confer memo | read three papers, join by number                               | one assessment paragraph per item                               | position, layout                                                    |

A catalog (`paper/catalog.py`) holds one recipe per type: regions required, operations allowed, proof key (per type, `document-drafter.<type>.<lane>`; today per class).

## 5. Onboarding the next firm

Harvest per type from 3 to 5 filed .docx files: profile (learner), frame (`frame_make`), phrase-book candidates (objection sentences recurring across papers), attorney blocks. $0 proof per type: read each past filed paper, rebuild it from the recipe with the filed item values as a recorded oracle, write it, compare region by region. This proves structure, firm wording and layout; model-written content is proven by recorded replay on saved requests and watched on the first real request.

## 6. Slices (each proven at $0 on the first firm's saved papers)

1. Model and reader, shadow only: on every saved paper the new reader's items, terms and declaration equal today's readers, every block assigned. Retires nothing.
2. Profile learner and the first firm's profile: the 16 layout reasons appear as profile values; a deliberately altered paper returns Unread. Retires definition and declaration detection regex.
3. Writer in place plus propounded revise operations: the four rehearsals match the firm's hand versions. Retires `_label_style`, `decl_block`, `propounded_define` detection.
4. Response revise and amended on the model. Retires `responses._head`, `exhibit_index.read_responses`, `amended.our_responses`.
5. Catalog, per-type proof keys, first-draft set. Retires `sets._DEFS`, `firm_frame.regions`.
6. First-draft responses and the memo.
7. Harvest command and corrections: an attorney correction writes to the profile or phrase book and reruns that type's corpus proof.

Risks to test first, as inputs: items numbered by Word auto-numbering (the number is not in the text); a signature in a text box; two templates of one type in a firm's corpus; tracked changes in a filed file; a declaration filed as its own file; an RFP index as a table; pleading line numbers inside pasted text; types the first-draft census finds that the catalog lacks.

## Scoreboard (same rows every check-in)

| Type                        | Read (slice 1-2) | Revise (3-4) | First draft (5-6) |
| --------------------------- | ---------------- | ------------ | ----------------- |
| Our special interrogatories |                  |              |                   |
| Our requests for production |                  |              |                   |
| Our requests for admission  |                  |              |                   |
| SROG responses              |                  |              |                   |
| FROG responses              |                  |              |                   |
| RFP responses with index    |                  |              |                   |
| RFA responses               |                  |              |                   |
| Amended responses           |                  |              |                   |
| Meet-and-confer memo        |                  |              |                   |

## Premises

- P1 `revise/prior.py:83-110`: docx to markdown blocks; base for `revise/set_units.py:76`, `revise/propounded_run.py:54`, `revise/propounded_rehearse.py:67`.
- P2 `set_items.py:41` label regex; `:233` renumber.
- P3 `revise/responses.py:52,85` heading regex; `:271` read.
- P4 `served.py:146,196` a third item regex.
- P5 `sets.py:155,175-192` Definitions only under a `#` heading; signature by `Dated:`.
- P6 `revise/propounded_define.py:32-37,55` a second Definitions reader.
- P7 `revise/propounded.py:429-441` closing and declaration by regex; `:303` `_label_style`.
- P8 `exhibit_index.py:45-49,81`; `amended.py:108`: two more response readers.
- P9 `firm_frame.py:150-163` front and closing regions by regex; `:267` apply.
- P10 `revise/inplace.py:245` new block clones a neighbour; `:526` prove.
- P11 `revise/propounded.py:444-457` Oracle; `:167` check_plan.
- P12 `revise/propounded_decl.py:28-36,77-83,106,158` list forms and agreement.
- P13 `library_setup/frame_make.py:363` make_frame; `frame_facts.py:226` case_fact.
- P14 `forms/render_body.py:118`; `forms/policy.py:189`.
- P15 `lane_proof.py:85-100` proof keys by class.
- P16 `firm.py:17-70` drafting-firm.yaml schema.
- P17 `operator/verticals/law-firm/addons/pi/`: the PI layer holds references only, no discovery rules.
- P18 `operator/workspace_broker/drafting_route.py:119,193`; `drafting_ledger.py:104` five classes, no per-type catalog.
- P19 `revise/propounded_rehearse.py:57`.
- P20 `revise/propounded.py:58-60` the 35-limit and add-able types are constants in code.
