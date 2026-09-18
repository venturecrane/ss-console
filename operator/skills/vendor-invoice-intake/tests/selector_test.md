# Selector Test: vendor-invoice-intake

Blind cross-skill selector simulation: does the router EXECUTE this skill for a
rostered sender's vendor invoice, and NOT for its near neighbors?

## Synthetic queries (should select)

> A rostered office manager forwards an email from a records copy service with
> one PDF attached, "Invoice INV-2026-001", and writes nothing of her own.

> A rostered paralegal sends: "Two court reporter bills for the Reyes matter,
> attached."

## Expected selection

`vendor-invoice-intake`, executed in the same turn by the router
(`read_file` on `/app/skills/vendor-invoice-intake/SKILL.md`). The rostered
sender's act of sending or forwarding IS the request, even with zero words of
her own.

## Collisions (each pinned: which class wins, and why)

- **Payment / trust / retainer** (`trust-balance-nudge`). "Did my payment go
  through" is a client paying the firm. A vendor's bill to the firm, forwarded
  by staff, is this skill. Tell: who issued the document. A vendor invoice wins
  even when the forward says "please pay this": the pay ask is declined in the
  reply line, and the invoice is still staged unfinalized.
- **Document received** (surface only). A vendor bill attached to a rostered
  sender's message is this skill, not a document to surface. A non-invoice
  document (a medical record, a letter) stays document-received.
- **Served-document intake** (`discovery-served-watch`). Formal service of a
  captioned litigation document is never an invoice, even when the serving
  firm's cover letter mentions fees. Service wins.
- **Non-roster sender.** A vendor emailing its own invoice straight to the
  Operator is NOT this skill: nothing is written. The router surfaces it for a
  person (no-reply / surface).
- **Chronology package request** (`medical-chronology-maintainer`). A provider's
  bill for records is an invoice; a request to build a chronology is not.
- **General / operational.** "Did you get the Acme invoice I sent yesterday?" is
  a question, answered directly; it stages nothing new.

## Result

Pending the first blind selector run on the pilot seat. The five runtime probes
(clean match, no match, two candidates, duplicate resend, scanned PDF) plus one
real forward are the rehearsal set; record the verified date here when they run.
