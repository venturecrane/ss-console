# ADR 0089: The Operator sends as a staff member, only on that staff member's emailed approval of the exact draft

Status: Accepted (Captain decision 2026-09-21)

Related: ADR 0031 (content-sensitivity send floor, amended here), ADR 0056 (unauthored is refused), ADR 0071 (the confirm ceiling, amended here), ADR 0072 (recipient-aware send), ADR 0075 (typed outbound roster), ADR 0078 (client-custody email), `operator/workspace_broker/send_as_acts.py`, `docs/security/operator-threat-model.md`

## Context

A law-firm client asked the Operator to work a paralegal's case files: read the tasks created when each file was opened, cross off what the file proves was done, and handle what was not. Much of "what was not done" is a letter or email to someone outside the firm: an insurer, a medical provider, an employer.

The Operator could only leave that mail as a draft in its own mailbox, which no staff member works from. So the work stopped at the one step the firm most needed. The Captain directed the frictionless path: the Operator prepares the email, the person it should come from approves or corrects it by replying, and it goes out from that person.

Two facts shaped the design, both probed before anything was built:

1. **Exchange "Send As" works through the Operator's existing send app.** Graph `sendMail` on the Operator's own mailbox with `from` set to a staff member sends as that person, once the tenant grants the mailbox Send As on them. No access-policy widening is needed, and the Operator gets no read access to the staff member's mail (`vfy_01M33RQ2H17A43CQ7777GKDNZ0`, test tenant).
2. **The existing safety gates were placed for a different send.** The taint gate refuses every send class on a turn that read outside material, before any approval is considered. The content floor withholds "legal" wording even from an approved send. The matter and identifier gates key on what the session read. A staff member's approval arrives on a new session that read nothing. As built, every real letter would have been refused. The critique pass found this against the code before it was built.

## Decision

**A new send class, `external_send_as_staff`, reachable only at the `confirm` ceiling. The Operator may put a staff member's name on outside mail only when that staff member approves the exact draft by email. The workspace broker owns the approval row, the approval email, the decision, and the one transmit that sets a From.**

### 1. Who may be sent as, and who may approve

`scope.staff_send_as: [{address, name}]` names the staff members. The entries must be exact person addresses already reachable under the seat's counterparty surface. The person named is the only one whose reply can send in their name. An administrator may cancel a staff draft but can never send one as somebody else. A draft may be requested only by that staff member or by an administrator.

### 2. A proposal is a draft

A send-as proposal may be made on a tainted turn. Nothing leaves until a person approves the exact text, and reading a document or an insurer's email is how real letters get written. What the taint gate guards against is an unreviewed send, and this lane has none. The approval email says when the draft was prepared after reading outside material, and from what.

### 3. The gates run where the provenance is

The fabrication gate, matter gate, and identifier filter run at propose time, on the proposing session, which holds the read provenance. The broker refuses a proposal whose gate pass is not all true. At approval, only session-independent facts are re-checked: the digest, expiry, approver, and whether the row is unconsumed.

### 4. An approved send-as is exempt from the content floor (amends ADR 0031)

ADR 0031's floor exists so that money, contract, and legal wording has a person review it before it leaves. A send-as approval is exactly that review, by the person whose name is on it. Re-applying the floor would withhold the approved letter, and it would do so for exactly the words a law firm's letters contain.

### 5. Email approval is approver-bound, not token-bound (amends ADR 0071)

ADR 0071 section 6 sketched email approval as a signed single-use token. This lane binds the approval to four things instead:

- the verified intra-tenant sender, authenticated by the firm's own Exchange Online Protection on an M365 seat;
- an exact match to the row's approver;
- a digest over the stored message, so inbound text can alter nothing that is sent;
- a forgery guard. The Operator's mailbox holds Send As on the approver, so an answer that mailbox sent itself (its Sent Items hold it) is refused, and a failed check refuses too.

The tag word is `draft` (`[draft 1a2b3c4d]`), distinct from `[act x]`, so neither lane can answer for the other.

### 6. What the approval email shows, and what the draft may carry

The approval email lists every To and Cc, marking any address on no roster; the subject; the full body with link targets as written; and the three reply forms (`send`, `change: ...`, `cancel`). The draft carries no Bcc and no caller-chosen Reply-To. Reply-To is set by code to the staff member and the Operator's mailbox, so the Operator can see the answer and tell the staff member it arrived.

### 7. Sent exactly once, and never retried

The broker consumes the row atomically with the transmit and records DISPATCHED or FAILED. A transport failure has an unknown outcome, so a retry could deliver twice. The broker never retries, and it tells the approver.

### 8. Outside mail from the Operator's own mailbox is unchanged

`external_send` keeps its authored ceiling (`draft_for_review` on the law-firm seat, and the letter's "a person sends it" cap). This ADR opens nothing for mail that goes out under the Operator's own name.

## Consequences

- **A client firm's admin grants Send As on each staff member the Operator may send for.** It is one command per person. It gives the Operator no access to their mailbox.
- **An approved row authorizes its recipients.** The outside party need not be pre-rostered: a person looked at the addresses and said send. `domain_blocks` still refuses. This is a deliberate widening of the broker's "only people the config names" fence (ss#2258), and it is bounded by a human approval of the specific addresses.
- **Rogue-path risk, stated.** With Send As granted, any credential that can call `sendMail` on the Operator's mailbox can send as staff. The guards:
  - the two-app split, so the agent never holds the send app;
  - a boot-smoke probe asking Microsoft whether the agent's own read app holds `Mail.Send`, which fails boot on a send-as seat if it does;
  - every send-as leaving an identity-joinable audit row, so `operator/bin/reconcile-sends.py` flags any message in Sent Items that did not come through the broker.
- **Deferred, stated rather than silent:**
  - "change: ... and send", because it sends text the approver has not seen; a firm-authored opt-in, later;
  - attachments, such as the signed authorization on a records request: the next change on this lane, with the attachment hash inside the digest;
  - real outside delivery, which the SMD test tenant cannot do (Microsoft 5.7.708 on a new tenant); proven on the first client seat.

## Verification

Proven on smd-staging against the smdopslab test tenant, all inside the tenant. Each runtime step is recorded with `crane_verify`.

1. A staff stand-in asks the Operator for a draft.
2. The approval email arrives.
3. `change:` produces a revised draft.
4. `send` delivers it from the staff member with both Reply-To addresses.
5. The outside party's reply reaches the Operator's mailbox and the staff member is told.
6. A tainted proposal, made after reading outside mail, reaches approval.
7. A letter with floor words sends after approval.
8. Negative probes: an administrator cannot send, a replayed or expired answer sends nothing, a `from` off the roster is refused, a caller's Bcc is absent, and the read app holds no `Mail.Send`.
