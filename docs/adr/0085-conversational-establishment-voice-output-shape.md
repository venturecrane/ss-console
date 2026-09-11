# ADR 0085: Voice and output shape are established conversationally, by Operator admins, through the Operator itself

- **Status:** Accepted (2026-08-02)
- **Decider:** Captain (this session's dialogue; the model below was stated by the Captain and repeated back for confirmation)
- **Builds on:** ADR 0083 (authorship model — output classes, two voices, format as a peer axis), ADR 0057 (Claude-connector access model — authored allowlist, grant-as-gate), ADR 0055 (the Operator is an employee; the roster), ADR 0037 (Operator thesis — configurable substrate, no imposed defaults), ADR 0078 (channel coupling / client-custody email)
- **Amends:** ADR 0083's registry-location clause (portal as the _editing_ surface for specs) and the operational reading of its §4. ADR 0083's model — output classes, voice/format as class properties, the vault, the root-owned applier, the gates — stands unchanged. What changes is **who performs the authoring act, and where**.

## Context

ADR 0083 §4 defines authoring as plain speech: _"A customer authors a property by saying it."_ The first implementation wave (#2089, #2096, #2123) landed the storage, the gates, and a portal form — and in doing so quietly inverted the model: the portal textarea became the authoring **entry**, and conversation was reduced to a witness channel (correction capture, #2091). The 2026-08-02 review session surfaced the inversion through the base scenario the product is sold on:

> An attorney emails the Operator: pull these files from this matter, draft the document we always produce. Review, iterate by reply, save the final back to the system of record.

An AI employee whose firm-level standards can only be shaped through an administrative web form is not the employee that scenario describes. The client was sold a remote worker: you teach it the way you teach an employee — by pointing at the work and saying how it should be done.

Two clarifications from the same session also bind here:

- **An unauthored output shape never blocks the base scenario.** The spec gate binds only where a class _declares_ `voice_spec`/`format_spec: expected` (`shared/spec_gate.py`, binding condition 2); undeclared means the persona's authored judgment produces the shape (ADR 0083 §3). The spec-before-declare sequencing rule (#2094) is what keeps "declared but missing" — the one state that would halt a client-visible path — from ever being configured on a customer seat.
- **"Ready for client use" does not require a client.** Readiness is every client-performed act performed by us in the client's role, on a proving seat, through the identical path. The client's first real use validates magnitude (does the voice delight), never mechanism.

## Decision

**The client establishes and maintains both the firm's voice and the firm's output shapes by instructing the Operator directly, through the channels they already use with it. Establishment authority belongs to Operator admins. It takes effect immediately. Every user gets a personal customization layer on top. The portal shows what has been established; it is not where establishing happens.**

### 1. The Operator is the establishment instrument

During implementation, and again whenever the firm's practice evolves, an Operator admin instructs the Operator — by email, through the Claude connector, or any later channel — to review a named set of content and establish or update from it:

- **Voice:** _"Review the letters on these matters and use them to establish the firm's voice."_ The Operator reads the content in place (the #2071 bridge; the letter-07 commitment that the firm never assembles or hands over documents), runs the distillation discipline (ADR 0083; the compiler suite of #2134/#2135/#2138/#2140), and installs the result as the voice property of the relevant output classes.
- **Output shape:** _"Review these examples of [the thing we produce] and establish its shape."_ Same motion; the derived result is the format property — prose for the model plus declarative assertions for the checker (#2090's design line).

Establishment is a **repeatable act**, not a setup ceremony. "Update the voice" is the same verb later that "establish the voice" is on day one.

### 2. Establishment authority: the Operator-admin allow list

Firm-level establishment is restricted, for now, to **Operator admins** — the role today called Named Administrator (Chris and Christa for A&P; the sent agreements use "Named Administrators," so any client-facing rename stays consistent with the signed paper).

The mechanism is the third instance of an established shape: an **authored allow list in `customer.yaml`**, beside the roster (`scope.inbound_allow_from`, ADR 0055) and the connector principals (`mcp_connector.access[]`, ADR 0057 §3). Email-address-shaped identities; changed through a PR, because who speaks for the firm is commitment-shaped. The seat reads it to classify an instruction as admin-classed; every channel consults the same list.

### 3. Effect is immediate — the restriction is the safety

An admin's establishment instruction takes effect on completion, with no confirmation beat. The authority boundary (§2), the instruction's provenance requirements (§5), and the compiler gates (§4) are the controls; a second approval step is not.

### 4. Instructed authorship supersedes witness-never-author, for establishment only

The correction-capture invariant (#2091 / overlay#214: the agent records, never applies) was designed against a world where any statement could reach a spec. Under this ADR the Operator **does** author — but only:

- on an **admin-classed** instruction (§2), on an **untainted, sender-attributed** turn (§5),
- from **client-designated content**, read in place,
- through a **mediated write path** that verifies that provenance server-side (the broker-verb pattern; the agent's own uid still cannot write the vault or the root-owned spec tree directly),
- with the **distillation compilers as write gates**: the leak check (no client prose retained), the digit invariant (no asserted numbers), and the self-test (no `block` rule the firm's own writing violates) run before any result is installed. The compiler discipline is precisely what makes agent-derived specs trustworthy enough for §3's immediacy.

Correction capture is unchanged for everyone else: a non-admin statement is captured as `proposed`; **promotion authority is the same Operator-admin role**, exercisable conversationally ("apply Sarah's correction") or from the portal's review view.

**Amendment, 2026-08-21 (Captain decision; ss-console#2529).** The clause above assumes the instruction arrives as designated content. A firm also establishes by talking: an admin writes one sentence about how a kind of output should read, and any person writes one about their own work. That sentence has no corpus, and every compiler named above refuses an empty one, so the write gates cannot apply to it. Rather than refuse the act, this ADR names a second, narrower authorization for it: **an admin's confirmed one-sentence adjustment installs on authority, attribution, an untainted turn, and a readback, not on the compilers.** Personal preferences (§6) install on the same footing, with the person's own identity as the authority.

**The readback is the control.** The Operator states the rule back in a canonical block rendered server-side, carrying a short tag; the person answers; only then does it commit, and the committed bytes are the bytes from that block, taken from the stored proposal and never from the confirming request. A request that carries a different sentence is refused, not substituted. What replaces four compiler gates is therefore not nothing: it is a human confirming a specific sentence, and a mechanism in which that confirmation is checkable rather than asserted.

**What is given up, stated plainly.** A confirmed adjustment is not checked for retained client prose, for asserted numbers, or against the firm's own writing. The bound on the damage is that it is one sentence, capped in length and in count per property, attributed to a named person on both ends (who instructed it, who applied it), rendered into the spec file where the firm can read it, superseded by any later contradicting sentence, and reversible by re-establishing the property from documents. Corpus-fed establishment keeps all four gates unchanged; nothing here weakens that path.

### 5. Channel trust rides the mailbox custody model

Admin authority is an email identity, so sender attribution matters per channel:

- **Claude connector:** Clerk-authenticated per request behind the grant table (ADR 0057). Strongest attribution; nothing further needed.
- **Firm-custody M365 mail (A&P):** the instruction arrives intra-tenant; the firm's own Exchange Online Protection authenticates the sender (SPF/DKIM/DMARC + intra-org spoof detection) before the message reaches the inbox the Operator reads via Graph. Tenant authentication suffices for immediacy. Optional belt-and-braces, non-gating: record the message's authentication headers in the audit row of each admin-classed instruction. Two limits, stated so they are not over-trusted: tenant anti-spoofing addresses _outside_ forgery, not an insider sending as themselves (that is what §2's list is for), and it assumes the firm has not loosened its own anti-spoof defaults — one onboarding line, not a probe of ours.
- **AgentMail-custody seats (the general product):** what SPF/DKIM/DMARC verdicts AgentMail exposes on inbound mail is **unverified and is a named probe** (see build seams). If strong: same immediacy. If weak: admin-classed instructions on that channel get a one-reply possession check (the Operator replies to the rostered admin address; the reply confirms) — mailbox possession, ADR 0057's own identity primitive, applied only to the spoofable channel and only to firm-level acts.

### 6. The per-person layer is first-class and open to every user

Any individual in the client organization customizes the voice — and, where needed, the output shape — for their own work, by telling the Operator. Per-person preferences (the #2067 artifact, generalized from one attorney to every user) are the floor for work produced _for_ that person; the firm layer is the floor beneath that. Personal customization needs no admin: the person's own rostered identity is the authority over their own preferences.

### 7. The portal is a window, not a door

The portal's role contracts to **visibility and audit**: which classes exist, what has been established for each (described plainly, per ADR 0083's retention posture), by whom and when, the proposed-corrections review queue, and the provenance trail. The Advanced page's spec-authoring form (#2089/#2096/#2123) is superseded as the primary experience; whether a residual portal edit path survives is an implementation choice for the re-scope issue, not a requirement.

Nothing else about ADR 0083's registry moves: declarations (`output_classes:`) stay in `customer.yaml` behind PRs; spec content stays in the vault; the applier, the manifest trust split, the read mark, and the gates are unchanged.

## Consequences

**Good.**

- The establishment experience _is_ the product experience — teaching your employee by talking to it — and it is the same motion on day one and in year three.
- The admin allow list reuses a twice-proven config shape; no new authorization concept.
- The compiler suite gains its production caller: the gates built for a human-run runbook become the write gates of an Operator-run skill (#2141's assembly is now that skill's spine).
- The portal work already shipped is not wasted: the storage path, server-side hashing, merge-preserve, and read-back verification all remain the write seam the mediated path lands on; the form demotes, the plumbing stays.

**Costs, accepted.**

- The witness-never-author line moves, and the security argument must be carried by provenance + mediation + compiler gates instead of by a blanket prohibition. The mediated path is therefore load-bearing and must be built to the same standard as the broker verbs.
- Seat-side admin identity is new surface that must exist before any establishment path ships.
- Distillation quality becomes runtime behavior on client instruction, not a supervised runbook pass. The self-test's demotion mechanism (a rule the firm's own writing violates auto-demotes and names the documents) becomes the honesty of the Operator's _reply_ rather than of a presented report.

**Risks.**

- **A bad establishment run degrades every subsequent output of that class.** Mitigations: the compiler gates refuse the worst failure modes mechanically; establishment is versioned in the vault with the prior spec retained (fail-static applier already keeps the previous tree; the write path must keep the previous object recoverable); and re-establishment is the same cheap conversational act.
- **Insider misuse of an authenticated non-admin identity** is handled by §2; insider misuse _by_ an admin is the firm governing its own admins — their call, not a product gate (ADR 0057 §4's posture).

## Acceptance criteria

- [ ] (repo) `customer.yaml` carries an authored Operator-admin allow list; the seat classifies instructions against it; changing it is a PR
- [ ] (repo) A mediated establishment path exists for voice and for shape: provenance-verified server-side, compiler gates run before install, previous spec recoverable
- [ ] (repo) Correction promotion is exercisable by an admin conversationally, not only from the portal
- [ ] (repo) The portal renders established properties, provenance, and the corrections queue read-only; the authoring form is demoted per §7
- [ ] (repo) The AgentMail inbound authentication-verdict probe is run and its result recorded; the possession-check fallback is built only if the verdict is weak
- [ ] (runtime) On a proving seat: an allow-listed admin, by email, points the Operator at a document set; the Operator establishes the voice; the spec is installed root-owned; the next draft of that class is composed against it — no portal touch, no SMD deploy
- [ ] (runtime) The same motion updates an existing voice, and the prior spec is recoverable
- [ ] (runtime) A non-admin's identical instruction is refused with a reply naming who can do it, and is captured as a proposed correction where applicable
- [ ] (runtime) A user states a personal preference; work produced for that user honors it; firm-level output is unchanged

## Amendment, 2026-08-22: three categories of change, and who decides each (Captain decision; ss-console#2546)

The 2026-08-21 amendment above named a second authorization: an admin's confirmed one-sentence adjustment installs on authority, attribution, an untainted turn, and a readback. Building it exposed a question the ADR had not answered. A person who is not an admin can also state a firm-level sentence, and what happened next was that the Operator recorded it and said an admin could apply it by replying "apply that". No admin was told. A decline was silent, a lapse was silent, and a request about a routine got a sentence with nothing behind it.

The gap was not in the mechanism. It was that "who decides" had only two values, admin and not-admin, and a firm has three kinds of change.

| Kind           | Example                                                 | Who decides                                         | How                                                                                      |
| -------------- | ------------------------------------------------------- | --------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Personal       | "open tasks first, for me"                              | the person                                          | readback, yes                                                                            |
| Firm standards | voice, document shape and templates, letter tone        | any Operator admin                                  | readback, yes; a non-admin's request goes to `rule_requests_to` for "apply that" or "no" |
| Operations     | routines, schedules, channels, memory, autonomy, on/off | SMD on request; admins keep pause/off in the portal | fixed reply; request passed to SMD by email                                              |

Two things follow from the table, and both are deliberate.

**Operations stay with SMD for now.** A routine is a schedule that initiates work on its own, and changing one changes what the Operator does when nobody is watching. That is a configuration act with a reviewed diff behind it, not a sentence. So the Operator's answer to "start sending me a digest every Monday" is that SMD makes those changes, and the request actually reaches SMD rather than being absorbed by a polite reply. What an admin keeps unilaterally is the direction that only ever reduces exposure: pause, and off, from the portal. The self-managed end of this spectrum, where an admin approves a routine change by email the way they approve a rule, is a later decision and is not taken here.

**Authority and traffic are separated.** Every Operator admin may apply a firm rule; that is §2 and it does not move. What ss-console#2546 adds is `scope.rule_requests_to`, an authored subset of `scope.admins` naming who is EMAILED when a non-admin asks for one. The distinction exists because the two lists answer different questions. Authority asks who may speak for the firm, and the firm's answer is its Named Administrators. Traffic asks whose inbox rings, and a firm with a litigating partner and an office manager on the same list does not want the partner paged every time a paralegal asks for a different sign-off. Before this key, the only way to spare him that was to take his authority away.

The routing list is a subset by validation, not by convention. An address on it that is not an admin would be a person asked to answer a question they have no power to answer, and the broker's own recipient fence would refuse the send anyway, so the request would reach nobody and nothing would say so. Empty is fail-closed in the honest direction: no admin is emailed, and the Operator says that rather than claiming somebody was asked.

The loop the routing closes is the point of the whole amendment. A rule stated by somebody who is not an admin is recorded, read back, and emailed to every address on `rule_requests_to` with the tag and "reply 'apply that' or 'no'". An admin's "apply that" commits it and the person who asked is told it is in effect. An admin's "no" declines it, once, and the person who asked is told it was declined. Nobody answering inside seven days lapses it, and the person who asked is told that too. An admin who is not named for requests receives nothing at any point. Personal preferences under §6 are untouched: a person's own identity is the authority over their own layer, and nothing about their own preference is routed to anybody.

Two bounds worth stating plainly. The seven-day window applies to a rule only; an act proposed for confirmation keeps the twenty-four hours it was authorized under, because widening it would widen a commitment nobody widened. And a decline is an explicit act by a second person: the requester cannot decline their own rule, since leaving it unconfirmed is how they withdraw it.

### Acceptance criteria (this amendment)

- [ ] (repo) `customer.yaml` carries `scope.rule_requests_to`, validated as a subset of `scope.admins`, parsed by the seat, and authored on both the client seat and the proving seat
- [ ] (repo) A non-admin's firm rule is emailed to every `rule_requests_to` address with the readback and the two answers; an admin not named receives nothing
- [ ] (repo) An admin's "no" declines the proposal once, and the person who asked is told the outcome on apply and on decline
- [ ] (repo) A rule nobody answers lapses at seven days and the person who asked is told; an act still expires at twenty-four hours
- [ ] (repo) An operations request gets the fixed reply and reaches SMD by email
- [ ] (runtime) On a proving seat, all four legs are observed end to end from the seat's own mailbox

## Amendment, 2026-08-23: an operations request comes back answered (Captain decision; ss-console#2546, reopened)

The 2026-08-22 amendment above put operations — routines, schedules, channels, memory, autonomy, on/off — with SMD, and said the request "actually reaches SMD rather than being absorbed by a polite reply". Half of that shipped. An email reaches `team@smd.services`; nothing comes back. SMD's answer never reaches the person who asked, and the Operator's reply at request time narrates a routine that does not exist ("Once it is live, the digest will arrive every Monday"), which is the same promise-of-future-behavior the rest of this ADR spends its length refusing to make.

**The loop closes.** An operations request is now recorded as a row, tagged, answered, and reported, in the same shape a firm rule already is:

- The request is recorded when it is made, and it carries an `[ops XXXX]` tag — eight hex characters, the same shape as `[rule XXXX]` and `[act XXXX]` and deliberately a different word, because the person answering it is deciding something different.
- SMD is emailed with the tag in the subject.
- SMD answers by replying with the tag and either `done` or `no, <reason>`. A reply that says neither leaves the row open and gets one automated ask for those words; it is asked once, because a per-turn re-ask is how a nudge becomes a mail loop.
- The person who asked receives exactly one email: SMD set this up, SMD declined this with the reason SMD wrote, or the request lapsed unanswered after seven days. The reason is **quoted, never paraphrased** — an Operator composing its own account of somebody else's refusal would be inventing client-facing content.
- When the seat cannot get the request out of the building at all, the row is withdrawn and nothing is sent, because nothing was ever asked and the person already heard that in the refusal they got in the same turn.

**Who may answer.** `scope.ops_reply_from`, authored per seat, person addresses at an SMD domain. The grant is exactly one act: resolving a request the Operator itself raised, identified by its tag, whose whole effect is one templated notice to the person who asked. It is not inbound trust. None of these addresses goes on `scope.inbound_allow_from` — `team@smd.services` in particular stays off it, pinned by a test — so mail from one of them that quotes no tag is as untrusted as any other mail, cannot untaint a turn, and cannot instruct the seat.

**The tag is the capability, and that is the accepted risk, stated plainly.** No seat receives an SPF or DKIM verdict on inbound mail (§5 above), so a forged `From: team@smd.services` is exactly as available as a forged `From: scott@smd.services`; naming one rather than the other buys nothing. What bounds the exposure is not the sender but the effect: the most a forged answer can do is send one person at the firm a templated notice about a request they themselves made. Nothing is configured, nothing is installed, and no routine changes — operations changes remain a reviewed diff made by SMD, which is what the 2026-08-22 amendment decided and this one does not touch.

**An operations request is never confirmable, and that is enforced three times.** It is not a rule: nobody at the firm can say yes to it, because it was never theirs to decide. So a submit naming one is refused by name, an administrator's decline is refused by name, and the broker's `consume` — the write that turns a row into something the firm committed — refuses the kind in SQL. Three refusals rather than one, because "the firm accidentally installed a routine change by saying yes" is the failure this shape could plausibly produce.

**Seven days, matching a rule.** The request is emailed to a person at SMD who may be with a client all day, and a request that dies overnight is a request the firm never had. An act still expires at twenty-four hours; widening that would widen a commitment nobody widened.

### Acceptance criteria (this amendment)

- [ ] (repo) `customer.yaml` carries `scope.ops_reply_from`, validated as person addresses at an SMD domain, authored on both the client seat and the proving seat, and absent from `inbound_allow_from`
- [ ] (repo) An operations request is recorded with an `[ops XXXX]` tag; a reply from an authored address quoting that tag resolves it as done or declined; a reply from any other address does not
- [ ] (repo) The person who asked is told once — set up, declined with SMD's quoted reason, or lapsed at seven days — and a withdrawn request tells them nothing
- [ ] (repo) An operations request cannot be committed by a confirmation, declined by a firm administrator, or consumed by the broker
- [ ] (repo) The Operator's reply at request time does not describe what a not-yet-existing routine will do
- [ ] (runtime) On a proving seat, all four legs are observed end to end from the seat's own mailbox: set up, declined with a reason, lapsed, and a tagged reply from an address that is not authored doing nothing
