# Operator Implementation Playbook (internal)

The step-by-step method an SMD **implementation specialist** follows, working
with the **build agent**, to stand up an Operator for a new client — from signed
engagement to steady operation. Vertical-neutral: no client, industry, or system
named. Each client instance (`operator/customers/<slug>/IMPLEMENTATION-PLAN.md`)
is this playbook applied to one client; the client-facing plan is a curated
projection of it.

## Who does what

| Role                                | Does                                                                                                                                                                                                                                              |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Implementation specialist** (you) | Gathers the information, runs every client-facing step, records the decisions, and gates each action. You direct the build agent and verify its output; you do not need to know how it does the work.                                             |
| **Build agent**                     | The behind-the-scenes technical work: assembling your worksheets into the client's configuration, building connectors and skills, standing up the isolated Operator, running the rehearsal. You tell it what to build and check what it produces. |
| **Client**                          | Corrects our picture of their work, sets how far the Operator may go, authorizes the connections, and is the judge of the Operator's work on their own account.                                                                                   |

## How this playbook is built

It follows the standard enterprise-implementation lifecycle — **Prepare →
Explore → Realize → Deploy → Run** — the same phase-gate model system integrators
use, adapted for a governed AI employee. Each phase ends at a **gate**: a
deliverable and a check that must pass before the next phase starts.

Two things run through every phase because we are implementing a worker, not
software:

- **Governance** — what the Operator is allowed to do, and the safety lines that
  are never negotiable. Set in Explore, verified at every later gate. (See _The
  safety lines_ at the end.)
- **Adoption** — earning the client's trust to let the Operator do more. It is
  the spine of Deploy and Run. (See _Earning delegation_ at the end.)

## Two rules that never bend

1. **Gate on proof, not calendar.** A step is done when you have seen it work,
   not when a date arrives. There are no durations in this playbook. A stand-up
   can take an afternoon or run over months; the pace is the client's, set mostly
   by how much has to be built in Realize.
2. **The client's data stays in the client's Operator and their own systems.** We
   prove the Operator on a rehearsal setup we build and own, where we can
   legitimately judge it because we authored every case. On the client's real
   work, **the client is the judge** — it is their ground truth, not ours.

---

## Phase 1 — Prepare

_Turn a signed engagement into a running project with the client aligned._

**Steps**

1. **Take the handoff.** From the signed scope and proposal, record: the work the
   Operator will support, the systems named, and the people named. Create the
   client record.
2. **Identify the roles.** Who owns the decisions (the principal), who is the
   day-to-day contact, and who administers each system (their IT or system
   owner). You will need the administrators by Deploy; find them now.
3. **Schedule the working session(s)** — the Explore workshops. Remote is the
   default; in person only if it genuinely fits.
4. **Send the prep list.** Tell the client the decisions they will make and the
   materials to bring: samples of their own letters and templates (for voice),
   who administers each system, and which work they want the Operator to start
   with. Where a system needs their administrator to create something new, that
   ask belongs on this list, not on stand-up day — the Operator's own mailbox in
   the client's Microsoft 365 tenant is the common one, and it needs **two** app
   registrations, not one (give their IT
   [ms-graph-azure-ad-setup.md](ms-graph-azure-ad-setup.md), "Client-custody
   app-only registrations"). Provisioning refuses the seat if only one exists, so
   discovering it late costs a day.

**Gate:** scope recorded, roles identified, working session scheduled, prep list
sent.

---

## Phase 2 — Explore (fit-to-standard)

_Correct our picture of how the client works, and turn it into the authored
configuration. This is the heart of the bespoke work._

**Steps**

1. **Run the fit-to-standard workshop.** Walk our picture of how the client's
   work moves; the client corrects it. Capture the **process inventory** — for
   each recurring process: the trigger (an event or a schedule), the steps, the
   systems each step touches, and the point where a human still decides.
   Everything downstream is built from this inventory.

2. **Set the dial (governance).** Walk the entitlement surface with the client.
   For each kind of work and each system, the client sets **how far** the
   Operator goes (handles it / prepares it for a person / does not touch it) and
   **who** it may reach. Recommend a default for each, record their choice, and
   leave nothing unset — unset means refused. Tell them plainly which safety
   lines are not on the dial (see the end). Record in `ENTITLEMENTS`.

3. **Author the persona (the Operator's own identity).**
   - _Collect_ (in the session): does the client want to name the Operator or
     keep it neutral ("the Operator")? Its title and role, tone words that fit
     their culture, and which people it serves. Write these on the persona
     worksheet.
   - _Record_: into the client's configuration — the persona's name, title, tone,
     and the skills it may run. One persona to start.
   - _Include_: at stand-up the build agent turns this into the Operator's
     identity file. You read it back to confirm it sounds like what the client
     asked for, before rehearsal.

4. **Collect the voice (how its work-product reads).** This is not the persona —
   persona is who the Operator is; voice is how the client's outgoing work reads.
   - _Collect_: a set of the client's own real, human-written letters and
     templates, grouped by who they are written to (client / counterparty /
     expert / internal) — a few per group. Confirm they are the client's own
     writing, not earlier AI drafts.
   - _Put_: nowhere as samples. The documents are read where they are and
     distilled into a written voice specification for each kind of output
     (ADR 0083). There is no sample library.
   - _Include_: the specification is the client's authored voice for that kind
     of output, installed on the seat by the spec applier. Where a kind of
     output has no declared voice specification, a send the Operator would
     have made on its own to an outside party becomes a draft for review. The
     voice changes when the specification is re-authored; nothing relearns it
     from sent mail. (The sample-fingerprint mechanism this step once
     described was removed on 2026-11-07.)

5. **Decide the connections.** For each system in the process inventory, record
   in the **connection register**: is there a ready connector, or does one need
   building? What does connecting require — a simple sign-in, or approval from
   their IT or system administrator? Does the system's vendor need to approve our
   app first? **If so, start that request now** — it has lead time and is often
   the longest wait in the whole implementation.

6. **Resolve the open decisions the client owns.** Anything the process inventory
   left open (how a particular tool is used, how work is divided with a tool they
   already run) — capture the answer or park it explicitly with an owner.

**Behind the scenes:** the build agent assembles your worksheets — process
inventory, dial, persona, voice plan, connection register — into the client's
master configuration and validates it.

**Gate:** a validated client configuration + entitlement record + authored
persona + collected voice + connection register + **a client-signed process
model**. Nothing proceeds to Realize without the signed process model.

---

## Phase 3 — Realize

_Build what is not reusable, stand up the client's isolated Operator, and prove
every process on a rehearsal setup before any client data is involved._

**Steps**

1. **Build what is missing.** The build agent builds any new connectors and any
   new or adapted skills the process inventory calls for, and reuses the existing
   library everywhere it fits. Track what is being built against the inventory so
   nothing is missed and nothing is built twice.
2. **Seed persona and voice.** The build agent materializes the Operator's
   identity and seeds the voice library from the corpus you collected.
3. **Stand up the client's Operator.** The build agent provisions the client's
   own isolated environment, wires the connections, arms the cost limits, and
   runs the boot and safety checks. This environment is the client's alone; their
   data never leaves it.
4. **Build the rehearsal setup.** On a practice copy of the client's platform —
   one we own and fill ourselves — the build agent seeds representative work plus
   deliberately broken and hostile examples (wrong-record lookalikes, malformed
   inputs, documents that try to mislead an automated reader).
5. **Rehearse and grade.** Run each process end to end on the rehearsal setup;
   grade every step; fix what fails; rerun until clean. We can judge here because
   we authored every case. This setup stays alive for the life of the engagement:
   every later change proves itself here first.
6. **Produce the rehearsal report** — the plain-language evidence you show the
   client of what the Operator did, refused, and caught.

**Gate:** every process passes on the rehearsal setup with no safety-line
violations; rehearsal report produced.

---

## Phase 4 — Deploy

_Connect the Operator to the client's real systems and bring each area of work
live at the posture the client set — one area at a time, each on the client's
confirmation._

**Steps**

1. **Connect.** With the client, and their IT or system administrator where a
   system needs it, authorize the Operator into each real system. This ranges
   from a single sign-in to a multi-party effort across several systems — sequence
   it, and do it with them, not for them. Verify each connection with a live read
   before relying on it.
2. **Bring up the first area.** Start with the work the client said matters most.
   At the posture they dialed, the Operator begins producing on real work; the
   client reviews and corrects it directly. **Their confirmation that it is right
   on their own work is what turns the area on for the team** — you never flip
   that switch for them.
3. **Bring up the rest, area by area, the same way.** Each earns its place the
   way the first did. The order is the client's priority; the pace is the
   client's.

**Adoption (say this plainly):** the client starts the Operator cautious and
opens it up as it earns trust, the way they would with a new hire. Your job in
this phase is to put the evidence in front of them at each step, so their
confidence is earned, not asked for.

**Gate (per area):** live connection verified; the client has confirmed the area
on their own work before it works for their team.

---

## Phase 5 — Run

_Steady operation. The Operator does the work, learns from the client, and takes
on more only where the client opens the dial._

**Steps**

- **The client corrects the Operator directly**, and it carries those corrections
  forward. You are no longer the middle of that loop.
- **Widen only where the client opens the dial**, and only after the area has
  earned it — run the enable check before promoting any area to more autonomy.
- **Changes never go straight to the client's live Operator.** Every change proves
  itself on the rehearsal setup first, then a staging copy, then the client's
  seat.
- **The Operator's activity is mirrored to a private record** the client can
  review — their own oversight and evidence trail.
- **Watch the cost meter; the safety limits stay armed.**

**Gate:** none. This is the operating state the implementation converges into.

---

## The safety lines (never on the client's dial)

These hold no matter how the client sets the dial. State them plainly to the
client in Explore:

- It does not move money or post to a ledger.
- Nothing goes to an outside party or a tribunal without a person in front of it.
- It refuses rather than guesses when it cannot verify what it needs.
- It treats the contents of documents as information to handle, never as
  instructions to follow.
- Anything the client has not configured is refused, not improvised.

The client sets how far the Operator goes on everything else. These lines are not
on that dial.

## Earning delegation (the adoption arc)

Deploy and Run are a trust curve, and it is worth running deliberately rather than
hoping it happens:

- **They see the evidence** — the rehearsal report shows the Operator working and
  its guardrails holding.
- **It earns desire** — it takes the client's worst pain first, so the value is
  obvious.
- **They learn it** — they watch it work on their own real work while it is still
  in review.
- **They rely on it** — they act on its prepared work, then let it do more.
- **It reinforces** — it learns from their corrections, and they open the dial
  where it has earned it.

The specialist's job across this arc is to supply the evidence at each step. Trust
is earned with proof, never requested.

---

## Appendix — where the mechanics live (build agent / engineer only)

The specialist does not need this. It maps each phase's technical artifacts to the
specs and decisions that govern them, so the build agent and engineers can go
deep.

| Mechanic                                                      | Where it lives                                                                          |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Master configuration (`customer.yaml`) schema + validation    | `src/lib/operator/customer-yaml/`; ADR 0012, 0019                                       |
| Persona = Hermes profile (identity file = `SOUL.md`)          | ADR 0011; overlay bootstrap                                                             |
| Voice library + privacy-safe fingerprints + recipient cohorts | `docs/specs/operator/voice-ingestion.md`, `voice-gate-fallback.md`; ADR 0028; #855/#856 |
| Entitlement dial (initiation × exposure), fail-closed         | `operator/templates/ENTITLEMENTS.template.md`; ADR 0056                                 |
| Provisioning the isolated Operator + smoke tests              | `operator/templates/README.md`, `bin/provision-customer.sh`; ADR 0007, 0010             |
| Cost limits + breaker                                         | ADR 0062                                                                                |
| Connectors (ready / build)                                    | ADR 0020, 0053; `operator/connectors/`                                                  |
| Rehearsal setup, grading rubric, run records                  | `operator/customers/<slug>/TEST-PLAN.md`, `operator/grading/`                           |
| Change pipeline (rehearsal → staging → seat)                  | `operator/customers/<slug>/IMPLEMENTATION-PLAN.md` change-flow rule                     |
| Enable check before widening autonomy                         | `docs/runbooks/operator/enable-gate-checklist.md`                                       |
| Activity mirror / audit record                                | overlay audit ledger                                                                    |

**Governance and adoption frameworks this is built on:** the phase-gate lifecycle
follows the enterprise-implementation methodologies (SAP Activate, Microsoft Sure
Step). The safety-lines and dial are an application of the NIST AI Risk Management
Framework. The trust curve in Deploy/Run follows Prosci ADKAR change management.
