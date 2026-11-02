---
title: Large outbound files over Microsoft Graph - a verified draft, sent in one broker call
date: 2026-11-02
status: accepted
captain: Scott Durgan
related-adr: docs/adr/0089-staff-send-as-on-approval.md, docs/adr/0086-matter-identity-send-binding.md
enforced_by:
  - operator/workspace_broker/tests/test_msgraph_pdf_attachments.py
  - operator/workspace_broker/tests/test_attachment_rows_and_graph_golden.py
---

# ADR 0101: Large outbound files over Microsoft Graph

## Context

A firm on Microsoft 365 asked for records that are scans: one is 6.67 MB, 35
pages. The broker's Graph send verb carried files only inline in `sendMail`.
Microsoft documents that path as under 3 MB per file and a request of about 4
MB, and sends 3 MB to 150 MB through an upload session on a draft
(learn.microsoft.com/graph/outlook-large-attachments, fetched 2026-11-02).

A draft and an upload session need `Mail.ReadWrite`. Under the two-app fence
(overlay#280) the broker's send app holds `Mail.Send` only. The read app holds
`Mail.ReadWrite`, and the agent holds that same credential for its own mail
tools. So the broker can build a draft only with a credential the agent also
holds.

## Decision

The lead's decision, 2026-11-02:

1. **Caps on a Graph send.** At most 3 files, at most 1 workbook (512 KiB), PDFs
   up to 25 MiB each, 25 MiB in total. The validator is the same one the
   AgentMail verb runs (`agentmail_attachments.validated`), with Graph's sizes.
2. **Two wire paths, chosen by size.** If the files fit one inline request (2.5
   MiB in total, and the whole serialized request is at most 4,000,000 bytes),
   they go inline in `sendMail` as before. Anything larger goes through
   `msgraph_upload.send_via_draft`.
3. **The draft is verified before it is sent (Option A).** In one broker call,
   with nothing else awaited in between, the broker:
   - creates the draft on the read app;
   - attaches the files (an upload session for anything 3 MiB or larger);
   - re-reads the draft;
   - sends it on the send app (`POST /messages/{id}/send`).

   The re-read downloads every file and compares its name, length and sha256,
   then compares the recipients (to, cc, bcc), the subject and the body hash,
   and finally lists the attachment ids again. Graph cannot change an attachment
   in place, so an unchanged id list means the bytes the broker read are still
   on the draft. If anything differs, the broker refuses the send, deletes the
   draft and writes the refusal row. The draft id goes on the row and never back
   to the agent.
4. **Large files travel by spool handle.** A file too large for the broker's
   request frame arrives as `{name, content_type, handle, sha256}`. The broker
   reads the bytes from its own spool (`attachment_intake.read_spooled`), which
   the agent cannot write, re-hashes them, and refuses on a mismatch.
5. **What a failure means.** Any failure before `/send` is a definite "not
   sent". A 4xx from `/send` is Graph's own refusal: it is reported as a plain
   refusal with Graph's status and error code, and the broker does not guess the
   tenant's size limit. A 5xx, 408, 429 or timeout on `/send` is an unknown
   outcome, and the draft is left for the reconciler.
6. **Send-as messages carry inline files only.** A send-as never builds a draft
   under a staff From.

## Consequences

**The residual race, stated rather than hidden.** One round trip remains between
the last verifying read and Graph acting on `/send`. In that window, anything
holding the agent's read credential could PATCH the draft. It would need the
draft id, which was minted seconds earlier and is never returned to the agent.

**Option B closes the race.** The firm's Microsoft 365 administrator grants the
send app `Mail.ReadWrite`. Then the credential that touches the draft is one the
agent never holds. That is a client tenant act. It is on the A&P ask list, and
this path does not wait for it.

The text-layer rule for image-only scans belongs to the overlay's gate, not to
this ADR.

## Verification

- Broker unit tests against a modelled mailbox in
  `operator/workspace_broker/tests/test_msgraph_pdf_attachments.py`:
  - tamper cases for each field, each refused and the draft deleted;
  - an upload url on another host, refused before any bytes move;
  - a 413 on send, reported as a plain refusal;
  - a 503 on send, an unknown outcome with the draft left;
  - spool-handle mismatch, absence and malformed cases.
- The inline workbook send stays byte-identical to the golden.
- Runtime proof on a seat is a separate act: a real draft path send on the test
  tenant, recorded with `crane_verify`.
