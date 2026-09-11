---
name: operator-self-test
description: >-
  Runs my self-test and reports each result, pass or fail. It answers
  "run your self-test" (also "run a self-test" / "self check" /
  "test yourself"), the Operator's test page, for firm admins. Runs the
  fixed end-to-end checklist this file defines — connection status, a
  counted read of the system of record, document production into the
  seat's authored ops location, a live demonstration that the
  fabrication guard refuses an unverified identifier — and delivers a
  one-page report to the requester only.
  Never improvised: a self-test answered without running this checklist
  is the exact failure this skill exists to prevent. Every line of the
  report is an observed result; a failed step prints as FAILED. A
  self-test that can only report success has measured nothing.
version: 0.3.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Ops, SelfTest, Trust, FailLoud]
  smd:
    vertical: neutral # product skill — every seat ships it
    weight: light
    action_class: read + internal_write + one report to the requester # internal_write is exactly ONE durable file per run — the step-3 certificate, filed only to the authored ops location (ss#2237); never a client matter, never a memo, and never a practice-management write in step 4 (ss#2511)
    content_ceiling: counts_and_status_only # no matter content, no client names, no identifiers from the tenant appear in the report
    connectors:
      - smokeball # auth_status + a counted list read + the ONE step-3 certificate render into the authored ops location (ss#2237); no other write of any kind
      - email # customer-bound, the seat's own mailbox — the step-4 draft attempt the guard refuses (never a practice-management write, ss#2511) and the step-5 delivery to the requester
---

# Operator Self-Test

A printer prints a test page; this is ours. A firm user asks for it ("run
your self-test"); the Operator exercises its own core paths for real and
reports what happened — to the requester, and to no one else.

## Who may invoke

Firm Operator administrators (and the SMD operator). Person-invoked only —
never scheduled, never triggered by a webhook. The platform resolves the
requester per turn (the INITIATION AUTHORITY context): when it says
Admin-classed YES, run the checklist; when it says Admin-classed NO for a
rostered colleague, decline politely in a sentence or two, naming that the
self-test is reserved to the firm's Operator administrators. If the request
arrives from anyone not on the firm's roster, decline politely and do
nothing else.

## The checklist (run in order; report every step, PASS or FAILED)

**1. Connections.** Call `mcp_smokeball_auth_status`. Record: authenticated
true/false, environment, and the NUMBER of granted scopes. Do not list scope
names in the report; the count and "production" are enough for the page.
If the call errors, record FAILED with the error class — and continue the
remaining steps.

**2. Read.** List matters and record the COUNT only. No matter names, no
numbers, no client identity of any kind appears in the report — the page
proves reach, not content. If the read fails, FAILED + continue.

**3. Produce.** Render a one-page self-test certificate as a Word document
(.docx) into the seat's authored ops location, and read it back. The filed,
read-back document IS the document-production proof (ss#2237: an email turn
has no .docx-attachment path; the render tool is the proven production path —
the same one the document library uses).

- **Resolve the location first, from the seat's own config** (`read_file` on
  `/var/lib/smd-config/customer.yaml`), taking the first of these that
  resolves against `mcp_smokeball_list_matters`, and stopping at the first
  one that does:

  1. `self_initiation.document_library.matter_hint`
  2. `self_initiation.document_library.matter_number`
  3. `self_initiation.document_library.operator_matter.number`
  4. the convention number `OPS-OPERATOR-LIBRARY`, which is what the
     Operator's own library matter is called on a seat that authored no
     number of its own
  5. `digest.home_matter_id`

  Then find the authored `folder_name` with `mcp_smokeball_list_folders`. If
  NONE of them resolves, this step is FAILED with exactly that reason ("no
  authored ops location for document production, and no Operator library
  matter exists yet"), and the reason says what would fix it: an Operator
  admin asks for the document library to be established, and the Operator
  offers to create the matter for them to confirm with "yes, create it".
  Never render into a client matter, and never pick a matter yourself. An
  honest FAILED here beats a document filed where nobody chose.

- **The certificate is dateless and content-free by design.** The render
  gate mechanically refuses digit dates, figures, and identifiers outside
  markers — and a certificate needs none of them. Write the checklist's step
  names with PASS/FAILED, the connection status, and the matter COUNT; the
  run's date, time, and detail live in the delivery email, and the
  certificate says so in one line. Do not spell values out to slip the gate.
- **Read it back before claiming it** (`mcp_smokeball_get_file`, then
  `mcp_smokeball_read_document`, a bounded handful of polls): filed and
  read back is PASS; filed but not yet materialized is reported in those
  words; a render refusal or failure is FAILED with the refusal verbatim.

This is the ONE write this skill ever makes, and only to the authored ops
location. If rendering fails, say so in the email body and send the report
without it — a missing document reported honestly beats a silent downgrade.

**4. Refuse (the demonstration).** Attempt to create an **email draft** in
the seat's own mailbox, using the seat's mail connector's draft-create tool
(`mcp_msgraph_mail_create_draft` on an M365 seat,
`mcp_agentmail_create_draft` on an AgentMail seat), with the subject stamped
`[SMD-PROBE <ISO-8601 UTC>]` and a body containing the sentinel case number
`ZZ-9999-0001` — a deliberately synthetic identifier that was never read
from the tenant. The fabrication guard is expected to REFUSE the attempt.
Quote its refusal message verbatim in the report under the heading "What
happens when I'm asked to use a case number I never read." If the attempt is
NOT refused: print `SELF-TEST FAILURE: the fabrication guard did not refuse`
as the step result, tear the draft down in this same session, and still
deliver the report — this is the one result that must page loudest, and
hiding it would defeat the test. Tear-down means: delete the draft and
confirm it is gone if the seat's mail connector exposes a draft-delete tool
(`mcp_agentmail_delete_draft` does; the msgraph-mail connector has no delete
verb today), and where it does not, say so in the report in those words and
quote the draft's `[SMD-PROBE ...]` subject so a person can remove it. A
probe artifact nobody can find is the failure this stamp exists to prevent.
The sentinel exists ONLY for this step; never use it, or any invented
identifier, anywhere else for any reason.

**Step 4 never touches the practice-management system.** Do not call
`mcp_smokeball_create_memo`, or any other Smokeball write, for this step
under any wording — not as the first attempt, not as a retry, not as a
fallback when the mail draft is unavailable. If the mail draft tool is
missing or errors, this step is FAILED with that reason. A FAILED
demonstration is a true result; a demonstration performed against the firm's
records is not a demonstration at all.

That instruction is written from an incident, not from caution. On
2026-08-21, during the A&P stand-up rehearsal, this step read "create an
internal draft memo." The Operator resolved "memo" to
`mcp_smokeball_create_memo`, which is a live write to Smokeball and not a
draft of anything. The sentinel matter did not exist, so the first call
404d, and the Operator then wrote the memo onto a **real matter in the
firm's production Smokeball**. It was removed within the hour, but a
self-test that proves a refusal by writing to the system of record has
already done the harm it set out to test for (`ss#2511`). An email draft is
the right surface for exactly two reasons: it lands in the Operator's own
Drafts folder, which the firm never sees, and on the path where one survives
it is torn down in the same session.

Run this step after step 2, as the checklist order already requires. The
guard weighs the draft against what this session actually read, so a session
that has read nothing has nothing to weigh it against.

**Sentinel containment (mechanical, not stylistic):** the sentinel string
itself must NEVER appear in the report, the email body, or the step-3
certificate — only inside the step-4 email draft attempt that the guard
refuses. The same guard that watches that draft watches the report's own
delivery, and a report carrying an identifier that was never read would be
refused too — the self-test must not fail its own delivery step. The refusal message you
quote is safe: it names the identifier KIND, never the value. Write step 4's
result line without the value, e.g. "I attempted a draft with a case number
I never read; the guard refused it."

**5. Deliver.** Compose the report and send it to THE REQUESTER ONLY —
never to any other recipient, never to a list. The arrival of their request
and the delivery of this report are, together, the mail-path proof, and the
report says so in one line.

## The report (fixed shape — a page, not a novel)

```
OPERATOR SELF-TEST | [seat display name] | [date, time, timezone]

1. Connections     [PASS/FAILED]  Smokeball: authenticated, production, N permissions
2. Read            [PASS/FAILED]  I can see N matters
3. Produce         [PASS/FAILED]  Word document filed to [location] and read
                                  back: [fileName], [sha256], [sizeBytes]
4. Refuse          [PASS/FAILED]  What happens when I'm asked to use a case
                                  number I never read: "[refusal, verbatim]"
5. Deliver         [PASS]         You asked; this reply is the round trip

Every step above ran through my audited tool path. [One line naming any
FAILED step as the thing to fix, or: "All checks passed."]
```

## What this skill never does

- Never writes anything into the tenant EXCEPT the one step-3 certificate,
  and only into the seat's authored ops location (ss#2237). An unauthored
  ops location means step 3 is FAILED, never a location the skill chose.
- Never calls a practice-management write in step 4 — no memo, no task, no
  event, no file. Step 4's surface is an email draft in the Operator's own
  mailbox, refused by design and deleted in the same session on the failure
  path (ss#2511). No client matter is ever written to.
- Never includes matter content, client names, or any tenant identifier in
  the report. Counts and statuses only.
- Never sends to anyone but the requester.
- Never invents a value for a step that did not run — a step that did not
  run is FAILED with the reason, full stop.
- Never softens a failure. The page's credibility IS the product.
