# law-firm / pi — the PI-Litigation Lifecycle pack

The connective substrate for a **California personal-injury plaintiff litigation
practice**, layered additively on the `law-firm` base vertical. Where the base is
the generic intake-and-matter spine any firm needs, `pi` is the litigation depth:
the whole life of a case, discovery deepest.

- **Commitment it implements:** `operator/customers/ashton-price/CLIENT-PROPOSAL.md`
  (the sent "Litigation Lifecycle Solution").
- **Build posture it encodes:** `operator/customers/ashton-price/REDUNDANCY-AUDIT.md`
  (CUT / GATE / BUILD) and `SCOPING.md`.
- **Manifest:** `addon.yaml` (this dir). **Skill bodies:** `operator/skills/<name>/`.

## The lane (why this pack is safe to sell)

The Operator is the connective tissue between the firm's systems (ADR 0037). It
**never re-performs what a certified incumbent owns.** Three bright lines govern
every skill here:

| Line                         | The incumbent                                         | What the Operator does instead                                                                                                                                                                                                                |
| ---------------------------- | ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Deadlines**                | Court-rules engine (LawToolBox / Smokeball-InfoTrack) | Captures the service date + method off the served doc and surfaces it; reads the engine's dates and chases. Computes **only** where the firm confirms it is done by hand today — then calibrated on their matters, always attorney-confirmed. |
| **Work product**             | BriefPoint / CoCounsel                                | Stages their inputs into the matter folder, routes their outputs. Drafts only connective artifacts (verification requests, meet-and-confer letters) for internal review — never legal argument, never autonomous external send.               |
| **Money / filing / signing** | Smokeball (trust + math), the attorney                | Never moves money, files, or signs. Prepares numbers and packages for a person to execute.                                                                                                                                                    |

Every skill body also carries the **training-output property**: what it did, why,
what comes next, the governing rule, and when to bring in the attorney — so a
junior paralegal builds competency working alongside it (a proposal commitment).

## Lifecycle → skills

| Phase                            | Skills                                                                                                                                                                                                                                                                                                                                                                                                   |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Discovery** (deepest)          | `discovery-served-watch`, `discovery-response-tracker` (direction-parameterized: inbound response-deadline confirm + outbound propounded-discovery watch, merging the former `discovery-response-clock` + `propounded-discovery-tracker`), `discovery-response-staging`, `client-verification-tracker`, `opposing-response-deficiency-review`, `meet-and-confer-drafter`, `separate-statement-assembler` |
| **Medical records & chronology** | `medical-records-chaser`, `medical-chronology-maintainer`                                                                                                                                                                                                                                                                                                                                                |
| **Case initiation**              | `matter-initiation-setup`, `service-confirmation-watcher`                                                                                                                                                                                                                                                                                                                                                |
| **Motions**                      | `motion-calendar-tracker`, `motion-package-assembler`                                                                                                                                                                                                                                                                                                                                                    |
| **Minor's compromise**           | `minors-compromise-packet`                                                                                                                                                                                                                                                                                                                                                                               |
| **Trial prep**                   | `trial-binder-assembler`                                                                                                                                                                                                                                                                                                                                                                                 |
| **Mediation & settlement**       | `lien-ledger-tracker`, `settlement-statement-feeder`, `mediation-settlement-tracker`                                                                                                                                                                                                                                                                                                                     |
| **Cross-cutting**                | `daily-needs-you-digest`                                                                                                                                                                                                                                                                                                                                                                                 |

The connective shape is uniform across phases (watch / capture / stage / trigger /
chase / assemble / surface / train), which is why the full lifecycle is one
architected pack, not seven builds. **Architecture day-one; bodies built one at a
time through the adversarial gate; activation per-matter as seams come live.**

## Connector map (customer-wired; per-connector verification status)

Not declared in `addon.yaml` — these are wired on the customer's `customer.yaml`,
and we do not assert a backend we have not verified. Most signals ride the
**Smokeball hub**: InfoTrack, YoCierge, and BriefPoint all import into / draw from
the Smokeball matter, so the Operator observes them through Smokeball events
without a direct integration to each. YoCierge is the one exception on the order
side: the Smokeball connector places a records order through YoCierge's own API,
as an admin-confirmed act, on a seat that holds the firm's YoCierge token.

| System           | Role                                                     | Operator path                                                                           | Backend status                                                                                                        |
| ---------------- | -------------------------------------------------------- | --------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| **Smokeball**    | System of record (matters, docs, tasks, calendar, trust) | The hub it watches + writes                                                             | `mcp:smokeball` — **shipped** (write-cut, 33 tests green)                                                             |
| **M365 / Graph** | Mail + calendar; the inbound-email-discovery gap         | Watch the inbox; consolidate calendar                                                   | `build:msgraph` (Track E) — **not yet runtime-wired**                                                                 |
| **InfoTrack**    | Court filing + service of process                        | Stage + present; a human submits (attorney-gated)                                       | `mcp:infotrack` — **verified** (OAuth2, filing + serve toolsets)                                                      |
| **BriefPoint**   | Discovery-response drafting                              | Stage inputs to / pick outputs from the matter folder                                   | via Smokeball Documents (no direct API)                                                                               |
| **CoCounsel**    | Broader drafting                                         | Stage inputs / route outputs via matter folder                                          | **open** — division of labor settled post-TR meeting                                                                  |
| **YoCierge**     | Medical records vendor                                   | Order records (admin-confirmed act); detect new records via Smokeball doc events; chase | `mcp:smokeball` `records-order` tools (order + read-back; needs the firm's API token); records land via Smokeball hub |
| **Adobe**        | PDF / Bates / exhibits                                   | Assemble where it's a real gap                                                          | `build:` (PDF Services API) — research                                                                                |
| **Dropbox**      | Client/defense doc sharing                               | Place / retrieve (outbound sharing human-gated)                                         | research MCP → else `build:`                                                                                          |

## Status (2026-07-01)

Manifest rewritten to the full lifecycle (was demand-side-only + carried a stale
"bodies in overlay" claim and an unsourced labor-market line — both removed).
**Skill bodies built so far:** `client-verification-tracker` (their #1 slip),
`discovery-response-tracker` (direction-parameterized inbound/outbound). The A&P production seat
(`operator/customers/ashton-price/customer.yaml`) is **not** bound to this addon
until bodies exist and pass the gate — enabling a body-less skill crash-loops the
boot. Build order (by the firm's stated slippage): `client-verification-tracker`
first (their #1 slip), then `discovery-served-watch`, `separate-statement-assembler`,
`discovery-response-tracker`, `daily-needs-you-digest`.
