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
3. **Automated alerts** - retainer payment failures email team@smd.services; every new or regressed Operator error in Sentry is emailed to team@smd.services (below).
4. **Client reports** - the portal change-request path and direct channels.

**The pager (#1709):** the `ss-fleet-alerts` Worker evaluates `fleet_status` every 2 minutes and emails team@smd.services on heartbeat-red (last heartbeat older than the period+grace envelope) and cost-breaker HARD_STOP transitions. Edge-triggered: one alert when a seat goes red, one recovery notice when it comes back, silence in between. Seats that have never heartbeated are provisioning-gray and never page. Worst-case detection-to-email is the red threshold plus one cron interval, about 7 minutes. The Worker only observes and emails; the response ladder stays human. Since 2026-10-05 a recovery notice is sent as a reply to its alert (same subject, `In-Reply-To` the alert's Message-ID, stored in `fleet_alert_state.alert_message_id`), so one incident is one inbox conversation rather than two unrelated mails.

**Shortfalls: when the Operator could not give someone what they asked (2026-10-05):** the outcome that matters most to a client is the one no seat-health condition sees. A seat can be green while telling a paralegal it cannot read her scan, or while filing none of the 52 pages it was handed (2026-10-01; every one of those tool results scored "ok", so nothing paged and SMD found out by watching). Each seat now classifies those tool results as a third audit outcome and reports the trailing day's events on its heartbeat, from person-initiated work as well as routines. Four classes: `limit` (a size, page or allowance cap stopped the work), `failed` (a call failed and nothing in the session recovered it; a refusal the Operator then retried successfully is not counted), `partial` (the mail routine filed fewer pages than it received), and `not_allowed` (outside what the seat is entitled to do). The first three email team@smd.services as `[SMD Ops] SHORTFALL <seat>`, at most once per seat per 30 minutes with everything new listed together. `not_allowed` is working as designed, so it goes in a Monday digest, `[SMD Ops] Weekly: what the Operator refused`, which a person reads as either a scope conversation with the client or an entitlement set wrong. Events land in `operator_shortfalls` (migration 0120) keyed by the seat's own stable key, so an event that rides every heartbeat for a day is one row and one email. Codes are a closed vocabulary (a reason token, a gate name, or "filed N of M"); no client text reaches the email. Nothing is sent to the client: whether a client should hear "SMD has been told" is held until this is tuned (Captain, 2026-10-05).

**Request cards: SMD sees every request a person sends a seat (2026-10-07):** a shortfall is the Operator saying it could not; the harder case is a request nobody answered at all, which looks exactly like a quiet day. Each seat now posts a card to `POST /api/internal/operator-request-card` for every request a person at the client emails it, and ss-web emails it to team@smd.services in plain text. Three kinds: `[SMD Ops] <seat> request: first reply in <N>m: <subject>` once the Operator's first reply has settled (who asked, the subject, the opening of the reply, the matter, the tools it ran, refusals and failures); `[SMD Ops] <seat> NO REPLY after <N>m: <subject>`, the alarm, when nothing answered within 30 minutes and no queued job or held reply accounts for it; and `[SMD Ops] <seat> <lane> job <state>: <subject>` when a demand or chronology the request started is delivered, fails or is held. A reply that arrives after its alarm still gets its own card. The email is the only place the request's text lives: `operator_request_cards` (migration 0122) holds the send ledger and the counts, never the sender, subject, reply or matter (ADR 0052 s5). A card is marked sent only when Resend accepted it, and the seat retries a failed send for a day under the same idempotency key, so a retry is never a second email. Read recent cards from a terminal with `.claude/bin/requests [--client <slug>] [--since 7d]`; its footer counts alarms with no later reply, which is the number to act on. These go to SMD only, never to the client.

**Sentry (repaired 2026-10-05):** `/api/webhooks/sentry` writes a `source='sentry'` alert-sink row that the same Worker emails. From 2026-09-14 to 2026-10-01 every delivery was refused (`missing_tenant_tag`, 19 of 19 in Sentry's delivery log): the integration sent `issue` payloads, which carry no tags, to a receiver that required one, and no Operator error reached anyone. An error the receiver cannot pin to one seat is now paged as `fleet` instead of refused, and a second error on the same seat on the same day is its own row (one per Sentry issue). The intended source is a global Sentry alert rule whose `event_alert` payload carries the `tenant` tag; while that rule does not exist, `SENTRY_ISSUE_RESOURCE = "page"` in `wrangler.toml` pages the `issue` payloads instead. Set it to `ignore` once the rule exists, or every issue pages twice.

**The outside-in probe (2026-09-11):** everything above is read out of `fleet_status`, which the seats write into the web Worker, so a dead web Worker would freeze that table and page nobody. The same `ss-fleet-alerts` Worker therefore also fetches `https://smd.services/api/health` from outside every 2 minutes. Three consecutive failed probes (a non-200, a body that is not `status: ok`, or no answer inside 10 seconds) open one `edge_down` alert to team@smd.services; two consecutive good probes close it; a single bad or good sample changes nothing, so a flapping edge pages once. Worst-case detection-to-email is about 8 minutes. Only `smd.services` carries the route: the admin and portal hosts answer `/api/health` with their auth redirect, and all three ride the same Worker, so a dead edge takes them down together. The counters live in `edge_poll_state` (migration 0115); the alert row is the usual `fleet_alert_state` row under the host name. The alert path was proven on 2026-09-11 with a deliberate 404 target beside the real one: one page when it went down, one recovery notice when it came back (that proof also found migration 0115 had not admitted `edge_down` into the alert table's vocabulary; 0116 did). A target removed from the list is retired on the next tick: its counters are deleted and an alert still open for it is resolved with a notice saying it is no longer probed, so nothing outlives the config line. A green tick proves the edge and its database answer, nothing about the seats.

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
