---
title: Incident Response
section: operations
order: 9
summary: The severity ladder, the detection surfaces, the escalation path, the client-communication commitments, and the exposure-ladder demotion rule a SEV1 triggers - ADR 0064 as a runbook.
sources:
  - label: ADR 0064 - Operator service commitments
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0064-operator-service-commitments.md
  - label: docs/security/smd-services-security-overview.md
    href: https://github.com/venturecrane/ss-console/blob/main/docs/security/smd-services-security-overview.md
  - label: docs/legal/operator-dpa-template.md
    href: https://github.com/venturecrane/ss-console/blob/main/docs/legal/operator-dpa-template.md
  - label: docs/runbooks/operator/enable-gate-checklist.md - the promotion instrument and the demotion rule
    href: https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/enable-gate-checklist.md
  - label: docs/runbooks/operator/incidents/ - the post-incident notes and their template
    href: https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/incidents/README.md
---

## The commitment shape

ADR 0064 (Captain decision, 2026-07-04) locked the service commitment as a severity ladder over detection, response, and communication. No uptime percentage, no service credits at launch. The reasoning: a founder-led firm with continuous automated monitoring but business-hours humans commits to what it can underwrite, in the same register as an employee's sick day - we fix it fast and communicate honestly.

## The ladder

| Severity | Definition | Response | Client communication |
| --- | --- | --- | --- |
| SEV1 | Operator down (heartbeat past threshold) or acted outside authorized entitlements | Work begins immediately on detection, any day | Within 24 hours; at least daily updates until resolved |
| SEV2 | Degraded: connector broken, skills failing, drafts not flowing, breaker tripped | Same business day | If client-visible; updates as facts change |
| SEV3 | Questions, cosmetic issues, configuration requests | Next business day | In the same thread |

Business hours are Monday through Friday, Arizona time. Incident notification windows for security incidents are 24 hours (client data or access affected) and 72 hours (platform-level), matching the standing partner-review commitment and DPA §6.

## Detection surfaces

1. **Heartbeats** - every Machine reports every 60 seconds into `fleet_status`; staleness renders on the admin fleet dashboard and the client-portal aliveness chip.
2. **Cost breaker** - the level rides the heartbeat, along with the reason and condition that drove it; HARD_STOP parks inbound at the gate. Two states since 2026-09-02, OK and HARD_STOP: the old WARN and SOFT_STOP rungs restricted nothing and only named a cause, which read as a brake that was holding.
3. **Automated alerts** - retainer payment failures email team@smd.services; Sentry errors sync to the fleet view.
4. **Client reports** - the portal change-request path and direct channels.

**The pager (#1709):** the `ss-fleet-alerts` Worker evaluates `fleet_status` every 2 minutes and emails team@smd.services on heartbeat-red (last heartbeat older than the period+grace envelope) and cost-breaker HARD_STOP transitions. Edge-triggered: one alert when a seat goes red, one recovery notice when it comes back, silence in between. Seats that have never heartbeated are provisioning-gray and never page. Worst-case detection-to-email is the red threshold plus one cron interval, about 7 minutes. The Worker only observes and emails; the response ladder stays human.

## Running an incident

1. **Classify** against the ladder. When in doubt between two severities, take the higher.
2. **Stabilize** - for a down Machine: `flyctl status`, then the deploy/rollback runbooks (never root SSH on a live Machine; it crash-loops bootstrap). For out-of-authorization behavior: pause the seat first, investigate second; the audit log is the record. The kill-switch is `operator/bin/pause-customer.sh <slug> --reason "<text>"`, which halts the agent loop while keeping the Machine warm for diagnosis.
3. **Demote** - a SEV1 also demotes every routine involved, per the pre-committed rule below. This happens during stabilization, not after the postmortem.
4. **Communicate** - first client message inside the window with what is known, what is being done, and when the next update comes. Plain language, no hedging, no blame.
5. **Track to resolution** - updates at the committed cadence; the incident is over when the client agrees it is.
6. **Record** - a dated post-incident note in `docs/runbooks/operator/incidents/`, written from `_TEMPLATE.md` in that directory, covering what broke, how it was detected, the timeline **as recorded**, and what changed to prevent recurrence with the PR, issue or gate cited. Where a source does not establish a fact - detection-to-resolution time is the usual one - the note writes `not recorded` rather than a plausible number. Recurring patterns become memory lessons or executable gates. Every note is also given a **class** from the fixed set in that directory's `README.md` (`gate-regression`, `built-not-wired`, `gone-not-gone`, `identity`, `other`) and indexed there, so the next reader can ask whether this shape has happened before and get a real answer; `tests/incidents-register.test.ts` blocks merge on a note that is on disk and not in the index, or classed outside the set.

The register carries one rule that came out of the `gate-regression` cluster (2026-08-04, 2026-08-13, 2026-08-19): **a new refusal gate on an outbound path is done when its refusal shows in `send_refusals` on the heartbeat and the pager has been proven on a seat, not when its unit tests pass.** A gate that can refuse and a human who can learn that it refused are one deliverable.

## The exposure ladder and the demotion rule

Severity governs how we respond to an incident. The **exposure ladder** governs how much a routine was allowed to do before one, and it is the instrument that bounds blast radius: `docs/runbooks/operator/enable-gate-checklist.md`.

Each routine climbs three rungs, and each rung is claimed by a named artifact rather than by a report: a shadow-firm run id (rehearsed), dated review-period observations (drafting on the client seat under human review), then Captain sign-off (acting on its own). A routine with an empty evidence slot is on the rung below, and nothing climbs by age.

The demotion rule is pre-committed, so the decision is not made by whoever is holding the incident:

> **Any SEV1 pauses the seat and demotes every routine involved to the bottom rung, and the routine stays there until BOTH the root cause has landed as a merged change with its own evidence AND the incident exists as a shadow-firm scenario observed to fail against the unfixed state before it passes against the fixed one.**

There is no restoration to the prior rung, because the prior rung's evidence is exactly what the incident falsified. The routine re-climbs from the bottom. Two clarifications that have each cost us once: a fix that closes the reported symptom does not count if the class survives, and a gate re-enabled in report-only mode does not restore a routine, because report-only is a staging state with an expiry date rather than a steady state.

## Escalation

Everything escalates to Captain (scott@smd.services); operational alerts land at team@smd.services. There is no second tier at this stage, and the runbook says so rather than implying an on-call rotation that does not exist.
