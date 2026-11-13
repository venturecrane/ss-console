---
title: The Demo Seat
section: product
order: 8
owner: captain
review_every: 90
last_reviewed: "2026-11-10"
summary: SMD's demo Operator, working for the fictional firm Brannock & Ferreira LLP on fictional matters. What it is, how a representative gets onto it, the demo by act, what never to show, how its tenant is reseeded, and what is still to be done outside the repository.
sources:
  - label: The demo seat's configuration
    href: operator/customers/pilot-smokeball/customer.yaml
  - label: Its seat declaration (purpose demo)
    href: operator/customers/pilot-smokeball/seat.yaml
  - label: The tests that hold the demo seat true
    href: tests/demo-seat.test.ts
  - label: The talk track's demo moments
    href: docs/collateral/operator-law-talk-track.md
  - label: What is running for a client today and what is set up per client
    href: operator/skills/product-guide/references/built-versus-planned.md
  - label: Invite-code enrollment (ADR 0106, amended for demo seats)
    href: docs/adr/0106-invite-code-enrollment-for-guide-seats.md
  - label: Seat purposes (ADR 0107)
    href: docs/adr/0107-seat-purpose-and-the-twin-gate.md
  - label: The fictional firm's approved voice
    href: operator/customers/pilot-smokeball/seed/voice/spec/work_product.voice.md
  - label: The tenant uploader
    href: operator/bin/seed-staging-matter.py
---

## What it is

The demo seat is a real Operator, running the same image, skills and gates a paying firm's
seat runs, set up for a firm that does not exist: **Brannock & Ferreira LLP**, a fictional
California personal-injury firm. Its matters live in SMD's own Smokeball staging tenant and
every person, provider and number in them is invented. A sales representative emails it in
front of a prospect, and every reply comes from the product itself. Nothing is staged and
nothing is a client's.

It is the seat `pilot-smokeball` (Fly app `hermes-pilot-smokeball`), declared
`purpose: demo` in its `seat.yaml` and `seat.kind: demo` in its `customer.yaml`
(ADR 0107). It gates no release: a client's changes are rehearsed on that client's release
twin, never here. The slug keeps its old name for now (see the follow-ups).

What the seat is set up to do, and why each choice:

- **It answers SMD's administrators and the representatives SMD invites, nobody else.** A
  prospect is never on its list, so a prospect who emails it gets no reply. The
  representative drives the demo.
- **It never sends outside the firm.** A letter to opposing counsel, an adjuster or a court is
  kept as a draft for a person. That is the demo's refusal act, and it is the posture a real
  firm starts with.
- **It spends nothing unasked.** No skill on it queues a job (no chronology run, no demand, no
  drafting job), no Smokeball change wakes it, and its one schedule is an hourly check that
  the Smokeball connection is alive, which calls no model. A daily cost cap stops it if
  something runs away.
- **It runs the production models** (the same pair the paying client's seat runs), so a
  prospect sees the product as a firm gets it.
- **One meeting never leaks into another.** The runtime's own working memory is off, because
  several representatives share the seat. A preference a person states is kept for that
  person only.

## How a representative gets onto it

The same way as onto the product guide: an invite code (ADR 0106, amended 2026-11-10 for demo
seats).

1. An SMD administrator emails the demo seat "I need a code for <the representative's
   name>". The seat emails the code back in that thread. The administrator passes it to the
   representative.
2. The representative emails the demo seat from their own address with the code anywhere in
   the message. The seat checks the code before any model sees the message, welcomes them by
   name, and tells SMD. From then on the seat answers them like a member of the firm's staff,
   never as an administrator.

A code works for one person by default and expires in 14 days if unused. SMD removes a
representative with `sender_enrollment_cli remove <address>` on the seat. Someone who is not
an administrator and asks for a code is told only that access comes from SMD Services.

## The demo, act by act

Use one of the three fictional record matters (2026-PI-102 Chen, 2026-PI-103 Ramirez,
2026-PI-104 Whitfield; see [Sample Medical Records and Chronologies](/admin/playbook/sample-medical-records)).
Each act is one email from the representative. Every reply is an ordinary model turn; none of
them starts a paid job.

| Act | The representative emails | What it shows | Skill it uses |
|---|---|---|---|
| 1. Meet it | "Introduce yourself and tell me what you can see." | It knows the firm's system and its own setup | `operator-introduce` |
| 2. The employee manual | "How do we work together?" | The firm's rules on screen: what it does on its own and what it brings to a person first | `operator-help` |
| 3. A matter question | "Where are we on 2026-PI-102?" then "What is due on 2026-PI-103?" | It works from the firm's own file | `matter-status-responder`, `deadline-and-sol-tracker` (routed by `matter-inbox-router`) |
| 4. A document | "On 2026-PI-103, read the first sick visit note and tell me what it says about the bite." | It reads the file where it lives and cites what it found | `matter-document-review` |
| 5. The refusal to send | "On 2026-PI-102, write to the defendant's insurance adjuster asking for the store's video of the fall, and send it." | It drafts in the firm's approved voice and does not send: a person sends it | any letter to an outside party; a meet-and-confer letter uses `meet-and-confer-drafter` |
| 6. A preference that holds | "When you answer me, lead with the open items." Then ask act 3 again. | The answer follows the preference without being told twice | the learned-preference lane |
| 7. The deliverable | Show the sample chronology package for a matter | The exact format a client receives, every entry cited to its page | none (shown, not produced) |

Notes for each act:

- **Act 4:** point it at one short document, never "review all the records". The skill is the
  one heavy skill on the seat and reads with the larger model; a whole record set is a long,
  costly turn.
- **Act 5:** say plainly that a firm's own voice is set up with the firm during
  implementation and approved by them before it applies. This firm's voice was written down
  from its own documents and approved the same way, which is what the prospect is seeing. If
  the voice has not been installed on the seat yet (follow-up 1 below), the draft is in a
  plain professional register; do not claim it is the firm's.
- **Act 6:** the preference belongs to the person who stated it. It holds for that
  representative in later meetings too; another representative does not inherit it.
- **Act 7:** present it as a sample made from fictional records, in exactly the form a firm
  receives. A real chronology runs for hours, so it is never produced live. Open a citation
  and the exhibit page it points at. Never present it as work delivered to a firm, and never
  say the Operator read these records: the reading was supplied by an answer key, not
  performed. The package files come from SMD (the build in
  [Sample Medical Records and Chronologies](/admin/playbook/sample-medical-records)); the seat
  does not attach files to its email replies.

## What not to show

Describe anything the Operator can be set up to do, the way the site and the product guide
describe it. Demonstrate live only what this seat is set up to do, so the demo never stalls.
The guide's "running for a client today" labels
(`operator/skills/product-guide/references/built-versus-planned.md`) say what a client has now
if a prospect asks. In particular:

- **Never send to an outside party live from this seat.** On this seat every outside send is
  a draft; acting on its own for outside sends is something SMD sets up per client.
- **Do not ask it to build a chronology, draft a demand or run any job.** Those are queued
  jobs that run for a long time and cost money; none is set up on this seat, and the
  request would wait unanswered while SMD is paged.
- **Do not demonstrate chat, voice notes, the AI assistant connector, the client level dial,
  Google Workspace or code execution on this seat.** None is set up here. If a prospect asks,
  describe it as something SMD sets up for the client.
- **Voice:** the Operator drafts in the firm's voice, written per kind of document from the
  firm's own examples, and sharpens as the firm corrects drafts. This seat has no firm voice
  set up, so describe it rather than demonstrate it.
- **Never quote a time.** No turnaround, no response time, no implementation length.
- **Never use a real client's name, matter or result.** The seat holds none, and the demo
  never refers to one.

## Reseeding the tenant

The fictional matters live in SMD's Smokeball staging tenant, a runtime layer nothing in git
rebuilds by itself. If the tenant is reset, or a matter's records need replacing:

1. Build the record sets (no tenant touched, no model called), from `operator/` in the jobs
   runner's environment:
   `python -m jobs.medchron.sample customers/pilot-smokeball/seed/records/*.yaml --out customers/pilot-smokeball/seed/records/out`
2. Upload each matter's PDFs **on the seat**, where the tenant's token lives:
   `seed-staging-matter.py <slug> --records out/<slug>/records` (the file's header shows how
   to run it on the seat with `SMD_DIRECT_WRITE_ACTOR` set). It refuses a folder that is not
   exactly the spec's set, and it is idempotent by document name.

This is a seat act done deliberately, with a negative probe of anything it replaces. Never
reseed during a scheduled demo.

## Follow-ups that need an outside act

None of these is done by the repository change that set the seat up; each is somebody's act
on a live system.

1. **Install the fictional firm's voice on the seat.** The configuration declares the voice
   for the firm's documents and its letters to outside parties; the seat reports a broken
   control for each until the spec is in its vault object. Install the approved card for both
   classes with `operator/bin/publish-output-class-spec.py --slug pilot-smokeball --class work_product`
   and again with `--class outbound_external`, each with
   `--body operator/customers/pilot-smokeball/seed/voice/spec/work_product.voice.md` (an R2
   write; the script refuses any seat that is not a demo or proving seat).
2. **Release the configuration to the seat.** The merged `customer.yaml` is published by CI;
   what applies live and what needs a reprovision is
   [Changing a Client's Operator](/admin/playbook/changing-a-clients-operator). The skill set,
   the schedule and the memory switches take effect on the next boot. Probe after: the
   seat's heartbeat reports the new release, its schedule lists only the auth check, and
   `sender_enrollment` appears on its heartbeat.
3. **Retire what the old configuration left on the live systems.** The seat used to run
   routines this configuration no longer has: its cron store, its Smokeball `matter.updated`
   webhook subscription (the reconciler never deletes one) and the probe addresses on its old
   roster are runtime state. Each gets a negative probe, as root `CLAUDE.md` ("Gone means
   gone") requires.
4. **A Microsoft 365 mailbox.** The seat's mailbox is AgentMail today. A paying firm's
   Operator writes from a mailbox in the firm's own Microsoft 365 tenant, so the demo should
   too: a user in an SMD lab tenant, connected the way a client's is. Creating that user is an
   outside act.
5. **Optional: a slug that says what it is.** Renaming `pilot-smokeball` means a new Fly app
   (a seat's app is its identity, ADR 0036): provision the new slug, move the Smokeball
   connection and the enrollment store, reseed, then retire the old app with a negative probe
   of every layer. Until then the slug keeps its old name.
6. **Run every act once before the first prospect sees it.** Each skill above pages SMD the
   first time it is used on this seat (the lane-proof gate's first-use notice; nothing is
   held), and the first run is where a setup gap shows. Each act is an ordinary model turn.
7. **Enroll the first representatives.** Each needs an administrator's code (above).
