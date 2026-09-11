# ADR 0086: Matter identity binds at the send-scan seam, via provenance pairs

Status: Accepted (2026-08-09; unverifiable posture measured and ratified by Captain 2026-08-19 — see §Checking)
Issue: [#2167](https://github.com/venturecrane/ss-console/issues/2167)
Related: ADR 0072 (recipient classes), ADR 0075 (typed outbound roster), ADR 0080 (control pattern), audit `docs/audits/operator-output-provenance-2026-07-31.md` §3.3, #2115 (the deferral this resolves), #2128 (pair provenance)

## Context

No control anywhere prevents one matter's content from reaching another matter's recipient. The provenance audit (§3.3) proved the gap is structural, not procedural:

- `classify_recipient(recipient, roster, *, from_tainted)` takes no matter argument — the classifier resolves roster class, never matter membership. Dial and identity are computed from disjoint inputs.
- `_SEND_SCAN_KEYS` has no identity key — the send scan inspects content shapes, not whose content it is.
- The identifier gate (blocking as of overlay#226) verifies that identifiers in a body were read this session; it does not verify they belong to the same matter as the recipient.
- 23 send audit rows since 07-14 carry zero `matter_id` — the outbound path has no matter identity at all.

#2115 deferred exactly this ("requires matter identity on AgentMail/msgraph tool signatures — a design decision") after closing the internal-write half: a connector write naming a matter other than the one it lands on now refuses. The Captain re-raised the outbound half on 2026-08-02: "how do we know it won't mix data from two different matters? or send a matter to the wrong attorney or paralegal?"

The current mitigation on the pilot engagement is a disposition, not a control: every outbound is `draft_for_review`, so a human reads every send. Graduating any routine to autonomous removes it. The audit's thesis applies verbatim: a control that is passing by disposition is not a control.

## Decision

**Matter identity binds at the send-scan seam, expressed as provenance pairs — the same mechanism that binds (matter, date), extended to (matter, recipient).**

Mispairing a recipient with a matter is the same defect shape as mispairing a date with a matter: two values, both individually legitimate, associated wrongly. The pair-provenance machinery (#2127/#2128) already models this: associations are seeded per record from connector reads and checked at the gate, with `has_pairs` gating so an unseeded register never judges.

### Seeding

`(matterNumber, recipient-email)` pairs seed from connector reads that carry both a matter binding and party contact data — `get_roles_on_matter` and `get_relationships_on_matter` are the canonical sources ("who is on this matter"). Both are READ-class and already flow through `provenance.record_read`; the seeding extension rides `_record_associations`' per-record walk. Where the vendor payload does not land the (matter, email) join in one record, the connector attaches it — the same fix shape as #2115's `_attach_matter_ref` (connector-resolved joins, fail-safe: an unresolved ref supplies nothing rather than something wrong).

The trust model is deliberate: matter membership is **connector-derived, session-scoped evidence**, held in the provenance register. It never enters the outbound roster, which stays human-authored outbound _authorization_ (ADR 0072's security posture is unchanged — email-in never becomes rostered, and membership evidence never widens authorization). Authorization says "this class of recipient may receive autonomous sends"; membership says "this specific recipient belongs to the matter this content is about." Both must hold.

### Checking

At `check_outbound_send` (and the draft gate for completeness), the body's own matter identifiers — the CASE_NUMBER hits the identifier gate already extracts — are the send's declared matter identity. The check: **every external recipient must pair with every matter number asserted in the body**, with class-scoped exemptions:

- `INTERNAL` recipients: exempt (staff work across matters).
- `records_vendor` (typed roster, ADR 0075): exempt — a records vendor serves many matters; the class is the authorization boundary.
- `client` and `OUTSIDE`: must pair. Matter-scoping the client is the point — the firm's client on matter A must not receive matter B's content.

### Unverifiable: allow under the routine's posture (ratified 2026-08-19)

As proposed, this section read "unverifiable → refuse." The implementation shipped the other rule — unverifiable reads as _unresolved_ and passes to the routine's authored posture — first as a documented deviation, then measured, then ratified by the Captain on 2026-08-19. The rule now is:

- **Provably wrong → refuse.** A recipient the session's evidence pairs with a _different_ matter than the body asserts is withheld, always. This is the incident class the control exists for, and it holds on 100% of matters.
- **Unprovable → the routine's posture decides.** No party-set evidence means no verdict — the send falls through to whatever exposure level the routine has earned (day one: `draft_for_review`, a person in front of it).

Why refuse-mode was foreclosed by measurement, not preference: the production census of the first client's full book (3,874 matters, `vfy_01M0BT2TF3GDFT3FZDBYZAVMNX`) found only 8.9% of open matters (51/574) and 39.4% of Pending matters carry a party set this control can close — firms record parties for conflicts and captions, and fill in an email only when someone needed to email that person. Refuse-on-unverifiable would have refused ~91% of active-matter outbound: a guarantee for a sliver of the book at the price of the product's graduation path. The rig's 8-of-9-open ceiling was representative, not a fixture artifact.

This does not contradict the playbook's safety lines (`docs/runbooks/operator/implementation-playbook.md` §safety-lines); it composes with them:

- "Nothing goes to an outside party or a tribunal without a person in front of it" is **never on the client's dial** — so the OUTSIDE class (including the medical providers at the center of the first client's Pending mandate, who attach as roles and are structurally unprovable by this control) is permanently human-fronted regardless of this ADR.
- "Refuses rather than guesses" governs acting on _asserted_ membership: the gate never manufactures a verdict from absence. Absence routes to a person; only proof routes around one.

**Graduation evidence (required before any routine loosens past `draft_for_review` on matter-scoped outbound):** (a) contact-axis coverage measured on that routine's real traffic — how often the recipient-side direction (ss#2264) actually closes; (b) a re-census after the client's committed client-email gap-fill (A&P letter 27, item 4 — the client filling missing emails directly grows the provable population); (c) if the routine addresses roles-attached parties (providers), a roles-axis census first — the party-list census does not cover them.

A skill that sends matter-scoped mail must still read the matter's roles first; the gate enforces what the skill contract instructs.

### Rejected alternatives

- **A matter argument on the classifier.** Breaks a byte-identical, hash-pinned two-repo twin for a concern that isn't classification, and imports connector-derived data into a module whose security posture is "human-authored roster only." Classification answers _what class is this recipient_; membership is provenance.
- **The entitlement dial.** Foreclosed by audit §3.3: dial and identity are computed from disjoint inputs, and "lowering entitlement dials is not a mitigation for this defect class."
- **Skill-contract binding.** Non-preventive: no Hermes hook carries an active-skill identifier, and an agent-supplied skill name cannot be trusted as an entitlement input (ADR 0075 honesty boundary #1). Skill contracts instruct; only the gate enforces.
- **Vendor MCP signature changes** (a required `matter_id` on send tools). AgentMail's tool signatures are not ours to change, and a declared-matter argument is agent-supplied — the same trust problem as skill names. The body's own identifiers are harder to launder than a declared argument.

## Accepted gaps (named, not hidden)

1. **Identifier-bearing content only.** A send carrying matter-A prose with no matter identifiers asserts no matter identity and passes this control. The identifier gate's refuse mode narrows the space (content that names identifiers is checked; content that names none leaks less), but narrative cross-contamination is out of scope — it has no deterministic predicate.
2. **Dormant until seeded.** Like all pair provenance, the check activates per session only after a qualifying read. `has_pairs`-gated: an unseeded register never judges. Activation is shared with #2128's seeding work.
3. **Session-scoped.** The register dies with the session. Cross-session membership (a durable matter→party map) is named future work — `identifier_filter`'s own docstring anticipates seeding from matter metadata beyond this-session reads.
4. **False-positive bar inherited from #2128:** the correct-pairing control ranks above the true positive. A send to a recipient correctly paired with the body's matter must pass; the kill-test pair below is the merge gate, and a gate that flags correct sends is worse than no gate.

## Acceptance criteria (build phase, filed as follow-on issues on acceptance)

- [x] (repo, connector) Field-shape confirmation: `get_roles_on_matter` / `get_relationships_on_matter` payloads carry (matterNumber, email) in one record; connector enrichment added where they don't — the payload does NOT carry the join; enrichment shipped in ss#2396
- [x] (repo, overlay + substrate) `(matter, recipient)` pair seeding + send-gate check, both filter copies in lockstep, class exemptions per this ADR — overlay#271 + ss#2396 (the checking half lives in `matter_gate.py`)
- [ ] (repo) Unit false-control + mutation companion (Law 12)
- [x] (runtime) Kill-test pair on a product seat: cross-matter send REFUSES and correct-pairing send PASSES — crane_verify both; the pass direction is the authorizing row — `vfy_01M0B1X4CR4K1M04RTPK1MDMS8` (shadow-firm run d5916657c670-green, both directions on one armed run)

## Verification

Done means: on a live product seat, a send composed with matter A's identifiers addressed to matter B's client refuses with an audit row, and the same content addressed to matter A's client (roles read this session) sends clean. Both observations recorded via crane_verify with the seam named.
