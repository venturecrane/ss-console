# ADR 0092: Calendar and documents for hosted agents: the agent owns the calendar and sends invites

- **Status:** Accepted (2026-10-17). Live invite test passed the same day on Google and Apple (section 1a); Apple's removal on cancel is pending the Captain's visual confirmation
- **Decider:** Captain (2026-10-17, recorded on [#3264](https://github.com/venturecrane/ss-console/issues/3264)); the optional Google upgrade follows the Captain's standing ruling of 2026-07-06
- **Supersedes:** #3264 Captain decision 2 (a Workspace account for the agent through the domain-wide-delegation broker), and the same day's interim decisions (a purchased domain with its own Workspace tenant; then the customer connecting their own account through an SMD-owned verified OAuth app)
- **Builds on:** ADR 0045 (mediated connector capability broker), ADR 0067 (Hosted Agent self-serve SKU: Telegram plus allowlisted email), ADR 0090 (hosted-agent roles), ADR 0091 (privacy by space)
- **Scope:** how a hosted-agent seat puts events on people's calendars and shares documents with them. Operator seats on a firm's own Workspace keep the ADR 0045 broker posture and are not changed here.

## Context

The couples role (ADR 0090) needs events that both partners see in their own calendar app, including all-day and recurring events and an approved delete, and a place for itineraries, lists and journal photos (#3264 gates 4, 6 and 7). Every later hosted-agent role needs the same two things, so the choice is a platform model, not a couples detail.

Three paths were considered and closed the same day.

1. **An agent-owned account in an SMD Workspace, through domain-wide delegation (DWD).** Google's admin documentation states that a DWD grant gives the client "access to the data belonging to all of your users", and the grant cannot be limited to a user or an organizational unit ([control API access with domain-wide delegation](https://knowledge.workspace.google.com/admin/apps/control-api-access-with-domain-wide-delegation)). Every consumer seat would sit behind one credential, the shape ADR 0007 and ADR 0009 exist to prevent. A tenant per customer means a domain and a manual Workspace signup per couple.
2. **A central SMD-owned OAuth app, verified by Google.** The Captain ruled on 2026-07-06 that a central SMD OAuth app needing Google verification or a CASA assessment is permanently off the table. It is not proposed in any form.
3. **Each customer connecting their own Google account.** It works without SMD verification only if each customer has their own GCP OAuth client. That is real setup, so it cannot be the default for a couple who want a shared calendar on day one.

A calendar invite needs none of this. iCalendar invitations sent by email (iTIP and iMIP, [RFC 5546](https://www.rfc-editor.org/rfc/rfc5546) and [RFC 6047](https://www.rfc-editor.org/rfc/rfc6047)) are accepted natively by Google Calendar, Apple Calendar and Outlook, and every hosted seat already has an email address: its AgentMail inbox (ADR 0067).

## Decision

**By default, a hosted agent owns its calendar and sends standard invites from its AgentMail address, and holds its documents and shares them by link or attachment. Connecting a customer's own Google account is an optional later upgrade, on the customer's own OAuth client. SMD never runs a central verified OAuth app.**

### 1. Calendar: the agent is the organizer

- The agent keeps the calendar on the seat and sends each event as an iMIP invite (`METHOD:REQUEST`) from its AgentMail address to the people the event is for. The event lands in each person's own Google, Apple or Outlook calendar with no setup on their side.
- An update goes out under the same `UID` with a higher `SEQUENCE` (`METHOD:REQUEST`). A delete goes out as `METHOD:CANCEL` under the same `UID`, and is an approval-gated act like any other delete.
- All-day events (`DTSTART;VALUE=DATE`) and recurring events (`RRULE`) are standard iCalendar and need nothing beyond the invite.
- People add items two ways: by inviting the agent's address to an event (AgentMail ingests the incoming invite), or by telling the agent.
- Who receives an invite follows ADR 0091. A shared-space event goes to both partners; a private-space item is never sent to the other partner, and private plans that should not appear on anyone's calendar stay in the record store.

The default needs no AgentMail Labs or beta feature. A plain AgentMail send carrying a `text/calendar` attachment is enough. **AgentMail Calendar** (beta, 2026-10-15) stays an optional managed alternative with the same organizer model. It is not needed.

### 1a. Live invite test (2026-10-17)

Hand-built raw iMIP `.ics` files were sent through the plain AgentMail send endpoint from a test inbox (`smd-calendar-test@agentmail.to`). AgentMail Calendar was not used.

- **Google Calendar.** The create landed within about 3 seconds with no click: one timed event and one all-day `RRULE:FREQ=YEARLY` event (`vfy_01M55T51PV1YB4D56PW7KK8WK0`). The update (same `UID`, `SEQUENCE:1`) moved the existing event in place, with no duplicate. The `METHOD:CANCEL` removed both events from Google with no action by the recipient (recorded in the verify ledger).
- **Apple Calendar (iCloud).** Both events rendered as native invites. The update replaced the original (one entry, at the new 7:30 PM time), and the yearly event showed "Event will occur every year". Apple ran its own conflict check (`vfy_01M55THDN2KSHQ8YKAN0QQKPTE`). Removal on cancel is pending the Captain's visual confirmation.

### 2. Documents: the agent holds them

The agent keeps documents on the seat (the record store, under ADR 0091's spaces) and shares them with the people they are for, by link or as an email attachment. Itineraries, packing lists and journal photos need no third-party account.

### 3. Optional upgrade: the customer's own Google account

When a customer later wants the agent to act directly in their Google Calendar and Drive, they connect their own Google account using **their own GCP OAuth client**, created once with the agent guiding them through the native Hermes `google-workspace` skill setup (the 2026-07-06 ruling). The customer is never handed raw GCP console steps; the agent walks them through it. The skill uses the customer's own downloaded `client_secret` JSON, starts the app in Testing with test users, and stores a token that refreshes itself at `~/.hermes/google_token.json` (Hermes `skills/productivity/google-workspace/SKILL.md`, lines 99-101, 135 and 163).

- The setup publishes the customer's app to "In production", because refresh tokens issued while an app is in Testing status expire after 7 days.
- The token goes to the workspace broker (ADR 0045), never to the agent. The skill's default token location is in the agent's own home directory, so on an SMD seat the setup is adapted to hand the token to the broker rather than leaving it there.
- It is not needed at launch and is not built until a customer asks for it.

Gmail, if a customer wants the agent to read their own mailbox, is a separate tier: an app password and the `himalaya` skill.

## Consequences

- No Google account, no OAuth client and no domain are needed to give a couple a shared calendar. Onboarding asks only for each person's email address, which the seat's email allowlist already requires (ADR 0067).
- **The partners cannot edit the agent's events directly.** The organizer owns an iMIP event, and the organizer is the agent. A partner changes an event by asking the agent, or by sending their own invite to the agent's address.
- **There is no single overlay calendar in their app.** Each event is an invite in each person's own calendar, not a calendar they can toggle on and off. An optional read-only subscription link can be added for an overlay view, but Google can take up to 48 hours to refresh a subscribed feed (per Cozi's published guidance at cozi.com), so it is a convenience, never the delivery path.
- Each person's calendar provider controls how the invite is shown and whether it is auto-added. A provider that holds invites from unknown senders for acceptance is a one-time onboarding step, covered in the guided onboarding.
- "SMD owns the data" (#3264 Captain decision 6) holds in full by default: the calendar and documents live on the seat. Copies sit in each person's calendar as accepted invites.
- The broker's existing Google verbs (`operator/workspace_broker/operations.py:168-252`) are not on the default path. They matter only to the optional upgrade.
- The OAuth consent flow in the portal (`src/pages/portal/products/operator/oauth/[connector]/index.ts`) and its relay (`src/lib/oauth/store.ts:54-159`) assume an SMD-owned client. They are not used for hosted seats under this record.

## Rejected alternatives

- **An SMD-verified central OAuth app, sensitive or restricted tier.** Permanently off the table by the Captain's ruling of 2026-07-06.
- **Agent-owned accounts in an SMD Workspace with domain-wide delegation, for consumer seats.** DWD reaches every user in the tenant and cannot be scoped to a user or an organizational unit.
- **A domain and Workspace tenant per customer.** Isolates correctly, but costs a domain and a Workspace seat per couple and a manual signup each time. Superseded the same day it was decided.
- **A vendor's shared OAuth app (Nylas Shared App, Unipile, Cronofy).** The vendor's name is on the consent screen, the vendor holds every customer's tokens, one vendor suspension takes every seat down at once, and the offerings are calendar only.
- **Fastmail.** It would force couples onto a new provider, Android needs DAVx5 to sync it, and its terms for automated access were not reviewed.
- **iCloud.** Apple's terms prohibit automated access.
- **Couple and family apps (Cozi, TimeTree and similar).** None offers a write API; TimeTree shut its API on 2023-12-22.
- **A subscribed ICS or CalDAV feed as the primary path.** Read-only in Google Calendar and refreshed on Google's schedule, up to 48 hours. Kept only as the optional overlay view above.
