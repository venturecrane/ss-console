---
title: 'Vertical Spec: Law Firm (Operator pack)'
date: 2026-06-02
status: draft
captain: Scott Durgan
related-adr: 0022-vertical-pack-architecture.md, 0037-operator-thesis.md, 0005-external-send-identity.md, 0025-autonomy-ceilings-configurable-exposure-vs-initiation.md
---

# Vertical Spec: Law Firm

The brief that drives the Law pack's manifest, N=0 proof, marketing surface, and delivery SOP, and the worked reference the other packs are skinned from. Per [ADR 0037](../../adr/0037-operator-thesis.md), the Operator competes with a **hire**, not with software; the firm's practice-management suite is a **connection target, not a competitor**.

The substance of the pack is here: the domain read, the personas, the nine specified skills (twelve until three were removed unused on 2026-11-10), and the connector map. The manifest (`operator/verticals/law-firm/vertical.yaml`) declares the identifiers; the runtime skill bodies are built from this spec in `hermes-smd-overlay`.

## The law-firm coordinator's world

A small firm's coordination runs across systems that don't talk to each other: the inquiry arrives in email or a web form, the matter lives in Clio, the consult goes on a calendar, the engagement letter goes out for e-sign, the retainer runs through LawPay, documents land in storage. The connective work is moving an inquiry through that chain and keeping every matter current. An inquiry becomes a contact and a matter, conflicts get checked before the firm commits, the consult gets booked, the engagement letter chased to signature, the trust balance watched, the routine status question answered, the document logged, the deadline tracked, the quiet matter nudged.

It is the same chain whether the firm does immigration, estate, family, or small-business work; the matter types change, the coordination does not. That coordination is a real seat. A firm covers it with a person, or splits it across people who would rather be on case work. The Operator takes the coordination so the seat is covered, or the person is freed for the work only they can do. We make no assumption about which it is for a given firm.

## Personas (the seat, described by role)

- **Intake coordinator** (`intake-coordinator`) at a 3-5 attorney firm: owns the front door, fields every new inquiry, books consults. When the seat is empty or the person is out, the front door slows.
- **Paralegal running intake and matters** (`paralegal-intake`) at a solo or two-attorney shop: wears several hats. The Operator can take the routine coordination so that time goes to substantive case work.
- **Office manager handling intake** (`office-manager-intake`), not a lawyer: the reason the unauthorized-practice line has to be architectural, not a matter of remembering to be careful.

## Skill catalog

Nine law-specific skills plus two spine skills reused as-is. Format per skill: **what** | trigger | reads -> writes | connectors | trust posture | guardrail. Trust posture follows [ADR 0025](../../adr/0025-autonomy-ceilings-configurable-exposure-vs-initiation.md); external send follows the engagement's authored `external_send` ceiling (fail-closed when unauthored).

### Intake

**`new-matter-intake`** | an inquiry becomes a structured matter plus a drafted acknowledgment. | _trigger:_ intake email / web-form / manual | _reads_ the inquiry, Clio contacts and matters (dedupe), the firm's practice areas -> _writes_ a draft contact and matter, a draft reply, an internal log | PracticeManagement, Email, IntakeCRM | matter draft autonomous, send draft-for-review, conflict routing human | never says "we can take your case"; flags statute-sensitive matter types.

**`conflict-intake-router`** | captures every party and routes for the human conflict check. | _trigger:_ new matter or party added | _reads_ the parties plus existing matters for name hits -> _writes_ a conflict-check request to the assigned person | PracticeManagement | gather-and-route autonomous, clearance always human | surfaces possible matches, makes no judgment, never clears a conflict.

**`consult-scheduler`** | offers times, books, confirms, puts it on the calendar. | _trigger:_ after intake or on request | _reads_ attorney availability, consult length by practice area -> _writes_ the event and a confirmation | Calendar, Email, PracticeManagement | booking autonomous within rules, confirmation per authored exposure | respects blackout and availability rules.

### Keeping matters moving

**`engagement-letter-chaser`** | tracks the unsigned engagement, nudges on a cadence, logs the signature. | _trigger:_ sent and unsigned past the cadence | _reads_ e-sign status -> _writes_ a nudge draft, logs on signature | ESign, Email, PracticeManagement | send draft-for-review | never interprets the letter's terms.

**`trust-balance-nudge`** | watches the retainer or trust balance against a floor and drafts the replenishment request. | _trigger:_ balance below threshold | _reads_ the LawPay trust balance -> _writes_ a replenishment-request draft | Payments, Email, PracticeManagement | send draft-for-review | read-only on trust funds; it reports, it never moves money.

**`matter-status-responder`** | answers the routine "where are we" from the system of record. | _trigger:_ a client status question routed by inbox-triage | _reads_ matter status, recent activity, next step -> _writes_ a status reply draft | PracticeManagement, Email | send draft-for-review | reports status only, no opinion or prediction.

**`document-receipt-logger`** | acknowledges a received document, files it, logs it, flags if it was the last thing blocking the next step. | _trigger:_ inbound document | _reads_ the document and its matter -> _writes_ the filed document, an acknowledgment draft, a log | DocumentStorage, Email, PracticeManagement | file and log autonomous, ack draft-for-review | does not assess the document for legal sufficiency.

**`stalled-matter-nudge`** | surfaces matters with no activity in the firm's window and drafts the follow-up. | _trigger:_ scheduled scan | _reads_ matter activity timestamps -> _writes_ a list for the team and per-matter follow-up drafts | PracticeManagement, Email | surfacing autonomous, send draft-for-review | flags, does not decide what the matter needs.

**`deadline-and-sol-tracker`** | tracks the deadlines and statute dates the firm has entered and surfaces what is approaching. | _trigger:_ scheduled | _reads_ the matter's deadline and statute-of-limitations fields -> _writes_ an approaching-deadline digest and calendar reminders | PracticeManagement, Calendar | surfacing autonomous | tracks the dates the firm authored; it never computes a limitations period itself, which is legal judgment.

### Proactive (removed)

The three proactive skills specified here (`client-matter-digest`, `referral-source-acknowledgment`, `intake-to-system-sync`) were built as catalog entries, enabled on no seat, and removed 2026-11-10.

### Spine (reused as-is)

**`inbox-triage`** routes inbound to the right skill. **`status-report-assembler`** compiles the digests.

## Connector map (the real small-firm stack)

| Capability         | Common tools                             | Backend                                                 | Used by                                   |
| ------------------ | ---------------------------------------- | ------------------------------------------------------- | ----------------------------------------- |
| PracticeManagement | Clio, MyCase                             | `mcp:clio-oktopeak` / `build:mycase`                    | every skill (system of record)            |
| Email              | M365, Google                             | `mcp:m365-mail` / `build:google-gmail`                  | intake, scheduling, status, digests       |
| Calendar           | M365, Google                             | `mcp:m365-calendar` / `build:google-calendar`           | scheduling, deadlines                     |
| DocumentStorage    | SharePoint, Drive, NetDocuments, Dropbox | `mcp:softeria/ms-365-mcp-server` / `build:google-drive` | document logging                          |
| ESign              | DocuSign, Clio e-sign                    | `build:docusign`                                        | engagement-letter chase                   |
| Payments / Trust   | LawPay                                   | `build:lawpay`                                          | trust-balance nudges (read-only on funds) |
| IntakeCRM          | Clio Grow, Lawmatics                     | `build:clio-grow`                                       | intake, system sync                       |

The pilot rides Clio's MCP, so the system of record needs no BUILD adapter. Payments and IntakeCRM are BUILD adapters in the overlay hand-off.

## Compliance floor (authored, not assumed)

Per [ADR 0037](../../adr/0037-operator-thesis.md) Tenet 3, no imposed defaults; floors are fail-closed until raised.

- **UPL boundary** — connective work only. Never legal advice, never a recommended course, never legal substance. The twelve skills are intake, scheduling, chasing, status, logging, tracking. This scope discipline is what keeps the pack clear of unauthorized practice.
- **Authored send posture** — outside sends follow the firm's authored `external_send` ceiling ([ADR 0025](../../adr/0025-autonomy-ceilings-configurable-exposure-vs-initiation.md), ADR 0035 — fail-closed when unauthored). The former non-raisable draft floor was removed 2026-07 (ADR 0073); `draft_for_review` is the recommended starting posture for a new engagement.
- **Privilege and conflicts** — privileged content stays inside the firm's surfaces; conflict capture routes to a human and never auto-clears.
- **Trust funds read-only** — the Operator reports trust balances; it never moves money.
- **Supervision** — the posture maps to the supervising-attorney requirement (ABA Model Rule 5.3, ABA Formal Opinion 512) and to state AI-disclosure rules; the audit log records draft, review, edit, send.

## Labor-market context (the demand, without presumption)

Firms cover the coordinator seat for different reasons, and we do not assume which applies. Some cannot keep it staffed and want it filled. Some want to free an existing person for higher-value case work. Some are under cost pressure: the PI add-on rides a specific, dated forcing function, California ballot **Initiative 25-0022**, which would cap auto and rideshare contingency fees at 25% (from 33-40%), fold case costs inside the cap, and bench medical liens, compressing the cost a firm can carry per case. This hook belongs in outreach and channel timing, not on the evergreen landing page; we do not date the public surface to a ballot measure, and we do not imply pre-knowledge of any firm's situation.

## Competitive read (system-features excluded)

Per the corrected lens: **system-features are connection targets, not rivals; only true employee-replacers count.**

- **Connection targets (zero threat):** Clio Work, Lawmatics QualifyAI, MyCase AI, Clio Draft. Features inside the systems we connect across. The firm still has the coordinator seat; these tools take a slice of the routine, and what remains is the cross-system connective layer the Operator covers.
- **Employee-replacers (the real column):** the funded legal-AI cluster (Eve, Supio, CaseFlood) sits in PI and litigation drafting, which is legal substance, not the connective intake-coordinator seat. The connective-coordinator seat is open.

## The wedge

> The intake-and-matter coordinator seat at solo and small firms: answer the new-client inquiry, book the consult, chase the signed engagement, keep the system of record current, watch the trust balance, nudge the stalled matter. Connects to Clio or MyCase over their open APIs, runs the connective layer only, stays clear of legal substance. It wins because the system of record is open and its vendor is an ally, the buyer is the most reachable of any vertical, and the seat is a real coordination job a firm pays for one way or another.

## Base vs. add-on

- **`law-firm` (base):** general small-firm coordination, immigration / family / estate / small-business. The lowest-friction, open-seat entry; lead here.
- **`law-firm/pi` (add-on):** PI-specific connective skin, medical-records request and follow-up, treatment-status tracking, lien-status logging, demand-package **assembly** (collation of authored components, never legal argument). Additive on the base; rides the Init. 25-0022 timing.

## Channel

Clio App Directory ecosystem and developer community; bar associations and legal-tech media; Clio Con and ABA TECHSHOW; active small-firm forums. PI add-on: warm practitioner intros plus the Init. 25-0022 timing window.
