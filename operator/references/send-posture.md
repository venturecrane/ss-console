# Send posture — the one source (recipient-aware, configurable, fail-closed)

This is the single authoritative statement of how the Operator decides whether to
**send**, **draft**, or **refuse** an outbound message. Every skill and every
doctrine doc defers here instead of restating it. If you are tempted to write in a
skill body that the agent only ever drafts, or that every external send is held for
review, stop — that universal framing is the retired doctrine (see the history below),
and it is what re-created the "nothing ever sends" regression every time it was
scrubbed. State the skill's _authored_ ceiling and point here.

Anchors: [ADR 0025](../../docs/adr/0025-configurable-autonomy-per-action-class.md)
(configurable per action class), [ADR 0035](../../docs/adr/0035-no-imposed-entitlement-defaults.md)
(no imposed default — unauthored is fail-closed), [ADR 0031](../../docs/adr/0031-content-sensitivity-send-floor.md)
(content-sensitivity floor), [ADR 0055](../../docs/adr/0055-operator-is-an-employee.md)
(the roster; the Operator is an employee), and the entitlement template
`operator/templates/ENTITLEMENTS.template.md` (the worked, customer-authored model).

## Two dials

- **Ceiling** — how far the Operator may go on its own, per action class:
  **autonomous** (acts) → **draft_for_review** (prepares; a named human sends) →
  **refused** (won't act). Authored in `customer.yaml`
  `personas[].entitlements.exposure`.
- **Reach** — _who/what_ it may touch: the reply/authorization roster
  (`scope.inbound_allow_from`), which inbox folders it sees, which connectors are
  wired.

## The recipient axis (this is the fix)

A send is not one thing. "Email the firm's own attorney that a deadline is slipping"
and "email a client a document" are different trust decisions, and the taxonomy now
says so. Every outbound send resolves to one of two action classes by **recipient**:

| Recipient                                        | Action class             | Recommended default                                                          | Why                                                                              |
| ------------------------------------------------ | ------------------------ | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| On the human-authored roster (own staff)         | `external_send_internal` | **autonomous, recipient-locked**                                             | a coordinator that needs a click to answer staff isn't a coordinator             |
| Anyone else (client / opposing / court / vendor) | `external_send`          | **draft_for_review**, graduating per recipient-class as the customer chooses | client- and tribunal-bound mail under a human while a new engagement earns trust |

The recipient is classified in code (the overlay's `shared/recipient_classifier.py`) with strict
matching. The roster is **human-authored OUTBOUND authorization** — never grown from
inbound correspondents. An **unresolvable** recipient is routed OUTSIDE (draft),
never internal; a send is never promoted to autonomous on an unknown recipient.

## The non-negotiable floors (these are not the ceiling)

These narrow an autonomous send; they never widen a draft:

- **Fail-closed when unauthored (ADR 0035).** A class with no authored exposure is
  **refused** — no send, no draft. `external_send_internal` autonomy is _authored_,
  not a default: an unconfigured seat sends nothing.
- **Vertical floor (ADR 0025 / 0022).** A pack may pin a class no customer can raise
  — reserved for a genuinely regulation-compelled constraint. No vertical currently
  declares one: the law-firm `external_send` draft floor was removed 2026-07
  ([ADR 0073](../../docs/adr/0073-remove-law-external-send-floor.md)) — outside-send
  is the firm's authored dial, and the recommended (not imposed) starting posture
  for a new engagement is `draft_for_review`, graduating as the customer chooses.
- **Content-sensitivity floor (ADR 0031).** Money / contract / scope / legal content
  bound **outside** drops an autonomous send to a draft. An internal staff alert is
  deliberately not content-floored — carrying that context to a colleague is its job.
- **Taint gate (ADR 0035 / 0027).** A turn that ingested untrusted inbound content
  cannot fire an autonomous send (internal or outside) — read and draft only.
- **Sticky stop (invariant #4).** A "stop / don't act" pins every class to
  draft/refuse until cleared.

## What a skill writes

State the skill's authored ceiling for each recipient class it sends to, and defer
here for the rest. A law skill that emails clients: "outside sends follow the
authored `external_send` ceiling; internal alerts to rostered staff follow the
authored `external_send_internal` ceiling — see `operator/references/send-posture.md`."
Do not restate the floors, and never re-introduce the retired universal framing.

## History — why the universal framing is banned

Invariant #2 was once a universal no-autonomous-send rule (hold every send for
review). That was reformed by ADR 0025 (configurable per action class) and
ADR 0035 (no imposed default), then given a recipient axis by the
`external_send_internal` split. The reform reached the enforcement code but kept
regrowing from doc/skill prose and a recipient-blind class — the code drafted every
send, internal notifications included, and each scrub decayed. The single source
here, the recipient-aware code, and the CI guard that fails the build on the retired
wording are what make the removal hold. Do not re-open it.
