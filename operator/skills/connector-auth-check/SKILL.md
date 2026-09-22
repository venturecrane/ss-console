---
name: connector-auth-check
description: >-
  Probes a connector's auth to catch a dead credential. A scheduled auth-plane liveness probe for
  connectors holding durable credentials (Smokeball first). Calls the connector's auth_status
  tool, which mints a real token at the vendor's auth host and touches no vendor data API. On
  failure it retries twice, so a dead credential crosses the connector-health ledger's
  3-consecutive-failures threshold and pages the ops inbox the same day it dies, not the day
  someone needs the connector. Where the vendor rotates refresh tokens on use, this probe is also
  the keepalive that prevents idle expiry (ADR 0080 amendment, ss#2148).
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Ops, ConnectorHealth, AuthPlane, Keepalive, FailLoud]
  smd:
    vertical: neutral # ops skill - every seat with a durable-credential connector runs it
    weight: light # three tool calls at most; zero synthesis
    action_class: read # auth_status is READ-class; this skill performs no writes of any kind
    content_ceiling: surface_only # emits nothing outward; its only output is the tool outcome the connector-health ledger observes
    connectors:
      - smokeball # PracticeManagement - auth_status only. No data endpoint is ever called.
---

# Connector Auth Check

Call `mcp_smokeball_auth_status` once.

A successful result reports `authenticated: true` AND `refresh_token_persisted`
not `false`. (`refresh_token_persisted: false` means the durable token file no
longer holds the current refresh token - the connector works right now and
bricks at the next restart. That is a failure, and it must be treated as one
even though `authenticated` is true.)

If the call errors, or reports `authenticated: false`, or reports
`refresh_token_persisted: false`: call `mcp_smokeball_auth_status` again, and
if it fails the same way, a third time. Then stop.

That is the whole skill. Do not diagnose, do not attempt repair, do not write
a memo or task, do not notify anyone yourself. The retries exist because the
connector-health ledger counts consecutive failures and the fleet alerter
pages at three - your three failed calls are the page. A single failed call
followed by silence would wait until tomorrow's run to page.

## Why this probe is allowed to exist (ADR 0080 amendment, ss#2148)

ADR 0080 rejected synthetic probes because "a probe is a write path into
vendor APIs." `auth_status` is the carve-out: it exercises the OAuth token
mint at the vendor's **auth host** and touches no vendor **data** API. It also
performs a real refresh grant, which means where the vendor rotates refresh
tokens on use, this daily probe _renews_ the credential - it is a keepalive
that prevents the idle-expiry death (the 2026-08-02 pilot outage: token
expired unrotated at day 30 because nothing had exercised the auth path),
not merely a detector. The console's token-age horizon alert
(`connector_token_expiring`) is the backstop for this probe itself dying.

## What this skill never does

- Never calls any Smokeball data endpoint (no matters, no contacts, no tasks).
- Never writes anywhere (no memo, no task, no email, no escalation entry).
- Never retries more than twice after the first failure.
- Never treats `refresh_token_persisted: null` as a failure (that value means
  not-applicable - client-credentials mode has no durable file).
