# ADR 0083: Authorship model — every output class declares its voice, format, gates, and delivery

- **Status:** Accepted (2026-07-30)
- **Decider:** Captain (this session)
- **Builds on:** ADR 0037 (Operator thesis — no imposed defaults, configurable substrate), ADR 0011 (multi-persona per customer), ADR 0028 (outbound integrity gates: provenance and voice), ADR 0035 (fail-closed, no imposed defaults), ADR 0075 (routine-grid enforcement is compositional)
- **Supersedes:** the sample-transform voice mechanism as the product's voice answer (ADR 0028 §2 remains in force as the gate doctrine; its _implementation_ binding to ingested structural samples is replaced here)
- **Client commitments in scope:** writing in the firm's voice, learned from their own material, corrections carried forward (`venturecrane/engagements`: A&P proposal, letters 07 and 10)

## Context

"Voice" in this repo names two unrelated mechanisms, and the confusion between them cost a full working session and produced a build that fed the weaker one.

**Mechanism A — spec-primed authorship.** An agent reads the customer's writing and produces a _written specification_: named traits with verbatim exemplars, plus precedence rules subordinating voice to accuracy. That spec enters the drafter's context. The model writes with it. This is what the 2026-07-28 drafting prove-out graded: seven of eight traits modulate by audience exactly as specified, zero leakage into court documents, the voiced arm 24% shorter than the control at budget parity, and in one case the voice _protected_ accuracy (the decompose-the-numbers trait declined a false total the control asserted). It is the mechanism behind exhibit E-1, which the firm has seen.

**Mechanism B — sample transform.** Documents are reduced to content-free structural fingerprints (sentence-length distribution, greeting/signoff category, punctuation rhythm), aggregated into a numeric profile, and applied as a post-hoc rewrite of four surface properties: greeting line, signoff line, sentence rhythm, paragraph rhythm. Fully automated, no authoring step.

Three findings settle the choice between them.

1. **B's entire surface is a subset of A's.** Everything B can change, A also controls — and controls with understanding of what the document is doing, rather than by pattern-matching a finished draft. B cannot produce a cold open, decompose a number, or order an argument.
2. **B is broken for the document type customers actually supply.** Measured 2026-07-30 against the rehearsal corpus through the real differ: all five client letters returned `greeting_style=none, signoff_style=none`. The vocabulary has no formal-letter register (`"Dear <first name>,"` matches no pattern; `"Yours,"` / `"Cordially,"` / `"Respectfully,"` match no closer). Two of B's four levers are dead for letters, and the cheap fix is unsafe: the existing first-name template renders `"Hi {name},"`, which would rewrite a firm's letters into a register it does not use. (Issue #2072 documents the defect; this ADR removes the need to fix it.)
3. **Neither mechanism is wired on a seat.** B's transform hook runs but no seat carries samples. A ran only in the prove-out harness, which placed the spec in the system prompt directly; the shared drafting discipline's clause _"when the seat carries an authored firm voice profile, apply it"_ names a condition no code path makes true. The capability the firm was shown is real and unwired — the failure class ADR 0028 was written about, recurring.

Two further gaps surfaced while framing the replacement, and neither is a voice problem:

- **Format is a separate axis from voice, and it is binary where voice is probabilistic.** A model writes _in_ a register and one grades whether it sounds right; typography either complies or does not. The customer's own instructions bear this out — the A&P principal's authored drafting standards (`engagements`: A&P letter 19) are almost entirely format: Times New Roman 12, double-spacing between requests only, all-caps bold underlined item labels, a Definitions section, caption, proof of service, and a CCP §2030.050 declaration whenever interrogatories exceed 35. None of that is voice, and none of it should be produced by a model.
- **The unit of configuration is not "voice."** Voice, format, gates, and delivery all vary by _what is being produced_, and treating any of them as a global setting produces a pile of special cases. The Operator authors at least five categories of text (attorney work product; outbound to outside parties; outbound to clients; everyday replies to firm staff; the internal record), and their requirements differ on every axis.

## Decision

**Every output the Operator produces belongs to an output class. Each output class declares four properties — voice, format, gates, delivery — and each property is authored by the customer or fails closed to the persona's own authored judgment. None of the four has an SMD-chosen default.**

### 1. Two authored voices, both the customer's

- **Persona voice** — the Operator speaking as itself: digests, reminders, escalations, replies to staff, the training notes left on a matter. Authored **with** the customer as part of the persona (ADR 0011): what this employee is, what it is called, how it comes across. A plaintiff PI firm and a med spa hire different employees; different persona, different voice. It requires no customer documents, so it exists from day one.
- **Firm voice** — the customer speaking, with the Operator holding the pen: work product and any correspondence carrying the firm's name, to clients, adjusters, vendors, or opposing counsel. Derived **from** the customer's own documents, read in place. Available once the corpus has been read, during implementation.

Both are per-engagement. **Neither is an SMD asset.** What SMD carries across customers is the discipline beneath any voice (never invent, cite the record, refuse rather than guess, escalate rather than nag), the harness that enforces it, and the method for authoring a persona and distilling a voice. Asserting a house voice on a seat is a tenet-3 violation.

Two further registers exist and are not customer-authored:

- **Court register** — dictated by rule and convention. Court-bound work product is written in it, never in firm marketing voice. "Zero voice leakage into court documents" is therefore a pass, not a shortfall.
- **Records** — a chronology, a lien ledger, a task field. These have a _format_ and no voice at all; nothing should attempt to give them a register.

### 2. Voice carries three axes

Firm base voice → modulated by **audience** (client / adjuster / opposing counsel / internal) → varied by **person**. The person axis holds two distinct settings that must not be conflated:

- **Writing as X** — a demand letter over an attorney's signature sounds like that attorney's variation of the firm voice.
- **Writing to X** — a digest that attorney reads is presented the way they prefer to receive information.

Same person, opposite direction, different property (the first is voice, the second is format).

### 3. Format is authored or persona-judged, never SMD-defaulted

A format slot is **empty** until the customer authors it. Empty means the persona's own judgment produces the shape — itself an authored thing, not a generic default. **Once authored, a format is binding and deterministic, every time.** Bindingness does not vary by document importance: a daily digest whose shape the firm specified is exactly as binding as a discovery set's typography.

What varies is the enforcing machinery, by artifact type:

| Artifact                              | Format is                                                    | Enforced by                               |
| ------------------------------------- | ------------------------------------------------------------ | ----------------------------------------- |
| Email, digest, memo                   | structure: sections, order, item shape, inclusion rules      | template + check                          |
| Letter                                | template: letterhead, RE line, signature block, wrapped body | template + check                          |
| Discovery set, response, court filing | typography and required elements                             | deterministic renderer + mechanical gates |

**Critical format is never produced by the model.** The model fills content into a structure; code owns typography and required elements. This is the design already filed as #2068 (deterministic `.docx` renderer) and already practiced by the drafting lane's ten mechanical gates, several of which are format gates (a §2030.050 declaration past 35 interrogatories is a mechanically checkable condition, not a judgment).

Format has three provenances: **the customer** (their skeletons, templates, letterhead, per-person preferences), **the rules** (CRC, CCP, court requirements), and **convention**. The middle one is legitimately ours to supply and is not an imposed default — it is the law. Voice has no equivalent; nothing there is ours to bring.

### 4. Authoring is by plain speech, and a correction is an edit to the class

A customer authors a property by saying it: _"could this be a table instead of text"_, _"send, wait seven days, send again unless the attorney says don't bother."_ That statement becomes a stored property of the output class — not a preference a model recalls with varying fidelity.

**Consistency is the feature.** An output that honors a requested shape most of the time is worse than one that never claimed to, because the reader stops trusting it and re-reads everything. Format drift on daily-volume output is how the engagement is lost quietly.

This also gives "you correct it once and it stays corrected" a concrete mechanism: a correction is an **edit to that output class's property**, auditable, visible in the portal, surviving restarts, applying identically to every subsequent run.

### 5. The gate re-expresses in terms of the class

ADR 0028's doctrine stands: provenance and voice fidelity are gates on the live outbound path, and a customer must not be configured for autonomous external send while voice is un-gated. Its _implementation_ binding — "evaluates real output against the principal's ingested samples" — is replaced. The gate now asks: **which voice and format does this output class declare, and does this output conform?** That question is answerable for persona-voice output (which B could never gate, having no samples for it) and for format (which B could not see at all).

### 6. Mechanism B is removed

The structural-diff ingest, profile aggregation, and post-hoc transform are retired rather than repaired. Removal follows the "gone means gone" discipline: the layers are git (adapter modules, the bin tooling, tests), the R2 vault sample prefixes, the volume mirror synced at boot, the running plugin, and the seat configuration that references them. Each runtime layer gets a negative probe; the removal is not complete because the diff merged.

The read-in-place bridge (#2071) is **kept and repointed**: name-resolution, connector-side text extraction, refusal-on-ambiguity, and provenance are all required by A's distillation step, which needs the same documents fetched the same way. Only its downstream consumer changes.

## Consequences

**Good.**

- One mechanism instead of two, and it is the one already graded against an adversarial panel.
- Voice becomes answerable for the highest-volume output (staff replies, digests), which B never addressed.
- Format becomes a first-class property with deterministic enforcement, which is what the customer's own instructions actually ask for.
- The privacy posture is stateable per property: the persona voice needs no customer material at all; the firm voice's artifact is a derived specification, whose contents and retention we describe plainly rather than by implication.
- The correction loop and the per-person layer both have a home, closing two gaps that had none.

**Costs, accepted.**

- **Distillation is a human-agent pass, not a script.** Reading a corpus and writing a spec is per-engagement work. This is the guide half of the moat (ADR 0037 tenet 4), and it is a cost we choose knowingly.
- **B's privacy property is lost and must be replaced by description.** B stored only numbers. A stores prose describing how a firm writes, including example constructions. That is a different retention answer and must be stated to the customer in those terms, consistent with the sent record; it is not a copy of their documents and must not be described as one.
- **Every output class must be enumerated and authored.** More surface than "one voice setting," and the work lands at onboarding.

**Risks.**

- **An unauthored class silently falls to persona judgment.** Mitigated by making the empty state visible in the portal rather than invisible: the customer sees which classes they have shaped and which run on the Operator's own judgment.
- **The distillation could overfit** to a small or unrepresentative corpus. Mitigated by the customer approving the spec before it applies — the same posture as every other authored property.

## Acceptance criteria

- [ ] (repo) Output-class registry exists: each class declares voice, format, gates, delivery; unauthored properties are explicit, never defaulted
- [ ] (repo) Persona voice and firm voice are separately authorable; court register and record classes carry no firm voice
- [ ] (repo) A customer statement of format becomes a stored class property; a correction is an edit to that property
- [ ] (repo) Mechanism B removed from git; the read-in-place bridge retained and repointed at distillation
- [ ] (runtime) B absent from every runtime layer it reached: R2 sample prefixes, the volume mirror, the running plugin, seat config — one negative probe per layer, `crane_verify` recorded
- [ ] (runtime) A seat carries an authored firm voice spec and an authored persona voice, and a delivered draft is observed in the declared voice for its class
- [ ] (runtime) A delivered digest is observed honoring a customer-authored format, and honoring it identically on a second run
- [ ] (runtime) The gate blocks or down-ranks an output that does not conform to its class's declared voice, observed on the live path

## Amendment — 2026-08-10: what a declared-but-uninstalled spec costs (ss#2228, ss#2234)

The Decision above says each property is "authored by the customer **or fails closed to
the persona's own authored judgment**." The runtime implemented only the first half of
that sentence for one state, and the second half never arrived.

`shared/spec_gate.py` treated a class that declares `voice_spec: expected` with **no spec
installed** as a hard refusal, on the stated reasoning that "refusing is the entire point
of the declaration." On `pilot-smokeball` the `staff` class was declared during a proving
window (#2094) and the spec never followed. From 2026-08-04 to 08-09 every autonomous
staff send refused with `spec_not_read` — an instruction the model could not follow,
because there was no spec to read. The firm's escalations and digests fell back to
Smokeball matter memos, the Captain's mail stopped, and nothing alerted. Six days.

**A control that can only fail silently and permanently is not a control.** The
declaration was working; what it did was wrong.

**Ruling (Captain, 2026-08-10).** A declared-but-never-installed spec remains a broken
control and is still recorded as one. What it _costs_ now depends on who is waiting:

| Class                                                       | Who is waiting                        | Disposition                                                   |
| ----------------------------------------------------------- | ------------------------------------- | ------------------------------------------------------------- |
| `staff`                                                     | a person inside the firm, on ops mail | **proceed** in the persona's own authored register, and alert |
| `outbound_client` / `outbound_vendor` / `outbound_external` | someone outside the firm              | draft for human review, and alert                             |
| `work_product` / `record`                                   | nobody — it is an artifact            | **still refuse**                                              |

The asymmetry is the point. Persona voice needs no customer corpus and exists from day
one (§1), so falling back to it imposes nothing — it is the ADR's own "fails closed to the
persona's own authored judgment", finally implemented. The firm's voice to the outside
world has no such fallback: the persona's register is the _wrong_ voice there, not a
neutral one. And an artifact blocks no one, so refusing costs nothing.

**Two states are not this, and refuse everywhere:** a spec whose bytes no longer match the
root-recorded digest (tamper must never become an escape hatch by deleting a file), and a
manifest the seat cannot read at all. The second required a new distinction —
`spec_manifest.manifest_state()` — because `load_entries()` returned the same empty result
for "nothing installed" and "cannot look", and treating the latter as the former would let
a lost `SMD_SPEC_DIR` unlock autonomous sending while every health signal read green.

**Consequence worth stating plainly:** for `staff`, `expected` and `none` now produce
identical _send_ behaviour. What the declaration still buys is the alarm — a declared
class with no installed spec raises `spec_control_broken:<class>.<property>` through the
heartbeat, independent of whether the seat happens to send anything.

**The same ruling closes the mirror-image defect.** A declared-but-uninstalled _format_
spec used to yield no violations and silently PASS — the opposite failure direction from
voice, in the same function — and a body the gate could not inspect (`None`, which the
content floor treats as fail-toward-draft) was coerced to `""` and skipped the format
check entirely.

Amended acceptance criteria:

- [ ] (repo) A declared-but-uninstalled spec is distinguishable from an unreadable manifest, and only the former can waive a refusal
- [ ] (repo) `work_product` and `record` still refuse on a broken control; `staff` proceeds; outbound drafts
- [ ] (runtime) A staff-class send reaches its recipient on a seat whose declared staff spec is not installed, observed as the recipient
- [ ] (runtime) The broken control raises an alert that reaches a person, once, and resolves when a spec is installed

## Amendment — 2026-08-19: format provenance realized as the firm's Word template (ss#2448)

Decision 3 said the typography tier is code's, not the model's, and named #2068's
deterministic `.docx` renderer as the design. It is now built, with one refinement to
where the authored format LIVES: **in the firm's own Word template, in the firm's
Document Library in its practice-management system, and nowhere else.** A drafting
skill files a draft through `mcp_smokeball_render_docx_draft` with a `document_class`;
the tool resolves the class template deterministically from the seat's authored
library location (`self_initiation.document_library.{matter_number, folder_name,
templates}`, read off the live customer.yaml by the connector; the model never picks a
template), opens it as the base document (page setup, headers and footers, styles
survive; body cleared), writes the content in using a small contract of named
paragraph styles (`SMD Body`, `SMD Item Label`, `SMD Item Text`, `SMD Heading 1-3`,
`SMD Caption`, `SMD Signature`), and reports what it applied (`formatApplied`). A
template that lacks a named style gets the class's product default applied inline, and
the fallback is named in the delivery note. No firm template authored: the starter, a
Times New Roman 12 base with the named styles defined, self-described in its document
properties, which the establishment turn files into the library for the firm to edit.

Two provenances, equally: the firm's own template or letterhead dropped into the folder
under the class's file name, or the starter we file for them. A style edited in Word
takes effect on the next draft; there is no config publish and no reboot, because
typography is not config. customer.yaml carries the library location and an optional
per-class file-name override only, never a font or a spacing.

The critique that shaped this (three independent reviews, 2026-08-19) removed every
place the renderer would have invented legal content: it numbers nothing, labels
nothing, and inserts no declaration. Item numbers come from the propounded set; the
35-interrogatory rule is an aggregate across the matter and attorney-reserved; a
statute-bound declaration is jurisdiction-specific, and inserting one for a firm in
another state would be fabricated client-facing content. The sentence in Decision 3
that called that declaration "a mechanically checkable condition" stands as a statement
about the CONDITION; the TEXT is the firm's, authored into its skeleton, never the
renderer's. The model writes labels, numerals, caption, signature block, and proof of
service as content, exactly as the skeletons show; the renderer styles them.

`output_classes.<class>.format_spec` (the text-shape assertion set) is unchanged and
stays `none` on the seats that author it so: that system checks the shape of staff mail
and digests; Word format is the drafting lane's renderer, which is what those seats'
authored comment already said owns work-product shape.

Evidence: PR #2449 (renderer, merged e1297712), #2452 (observed wire shape), #2454
(the five drafters deliver through the tool); pilot-smokeball v138 booted on the build
(vfy_01M0DTETJ7SKV5DKJ5YH33T2SG), a class-rendered document filed on a rehearsal matter,
downloaded byte-identical and opened by the Captain (vfy_01M0DTM2EGQZP9FZTM53S17CJ7,
vfy_01M0DW05DHTTK09MP4PRDEXNBC).
