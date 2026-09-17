#!/usr/bin/env bash
# stage-agentmail.sh - the AgentMail channel's staging block, sourced by
# provision-customer.sh in the same shell (it reads CUSTOMER_ID and the
# AGENTMAIL_*/WEBHOOK_SECRET_AGENTMAIL* operator env, and calls
# stage_secret_from_env / unset_stale / die / authored_channel from there).
# Moved out of the provisioner as its own file on 2026-09-17, when adding the
# webhook-read credential pushed the provisioner past the shell size ratchet
# (tests/operator-module-size.test.ts): the ratchet's own instruction is to
# split, not to raise the baseline. Same move, same reason, as
# lib/stage-smokeball.sh in ss#2425. Nothing here runs unless a customer.yaml
# connector declares adapter=agentmail or backend=mcp:agentmail.
#
# Tests that read this file as text: tests/agentmail-webhook-secret-fence.test.ts
# and tests/agentmail-webhook-read-fence.test.ts drive the two fence blocks by
# their sentinels; operator/bin/tests/test_secret_custody.py requires every
# staged name to classify.
# shellcheck shell=bash

if authored_channel '^adapter=agentmail$|^backend=mcp:agentmail$'; then
  # PER-SEAT, and it has to be. Both keys are scoped to ONE inbox at the vendor
  # (ss#2258), so a single shared value is no longer merely untidy — staging one
  # seat's key onto another gives that seat a credential for a mailbox it does
  # not own, and its own mailbox becomes unreachable. Same convention and the
  # same reasoning as WEBHOOK_SECRET_AGENTMAIL__<CID> below, whose comment
  # records the identical bug one layer over: a reprovision silently overwriting
  # a customer's own value with the global one.
  #
  # The global fallback is kept ONLY for the pre-scoped-key transition. It is a
  # migration affordance, not a supported end state: once every seat has vaulted
  # AGENTMAIL_API_KEY__<CID>, the bare names should go.
  _AGENTMAIL_CID="$(printf '%s' "${CUSTOMER_ID}" | tr '[:lower:]-' '[:upper:]_' | tr -cd 'A-Z0-9_')"
  _AGENTMAIL_READ_NAME="AGENTMAIL_API_KEY__${_AGENTMAIL_CID}"
  _AGENTMAIL_SEND_NAME="AGENTMAIL_SEND_API_KEY__${_AGENTMAIL_CID}"
  stage_secret_from_env AGENTMAIL_API_KEY "${!_AGENTMAIL_READ_NAME:-${AGENTMAIL_API_KEY:-}}" "AgentMail read/draft credential for the gateway (inbox-scoped, NO send permission; per-seat ${_AGENTMAIL_READ_NAME}, else global)"
  stage_secret_from_env AGENTMAIL_SEND_API_KEY "${!_AGENTMAIL_SEND_NAME:-${AGENTMAIL_SEND_API_KEY:-}}" "AgentMail send credential for the broker ONLY (inbox-scoped, message_send; stripped from agent env; per-seat ${_AGENTMAIL_SEND_NAME}, else global)"
  unset _AGENTMAIL_CID _AGENTMAIL_READ_NAME _AGENTMAIL_SEND_NAME
  # The webhook-READ key, for boot smoke's proof and nothing else. ORG-scoped
  # (webhook_read alone) and therefore ACCOUNT-wide by design, which is the one
  # place a shared value is correct here: AgentMail's webhooks are org-level
  # objects, and a key scoped to one inbox lists ZERO of them even when granted
  # webhook_read (measured live 2026-09-17). entrypoint.sh materializes it to a
  # 0600 root-owned file and unsets it before the gateway exists, so org scope
  # never reaches the agent.
  #
  # NO FALLBACK to AGENTMAIL_API_KEY. Falling back to the inbox key would restore
  # exactly the state this replaces: a check that cannot pass, reported as a seat
  # defect. Refuse and name the key to vault.
  #
  # Sentinels below delimit the block tests/agentmail-webhook-read-fence.test.ts
  # drives in a bash harness; keep them, and keep the block self-contained.
  # >>> agentmail-webhook-read-fence
  [ -n "${AGENTMAIL_WEBHOOK_READ_API_KEY:-}" ] || die "agentmail-webhook-read-fence: AGENTMAIL_WEBHOOK_READ_API_KEY is not vaulted in Infisical /ss (prod). Boot smoke proves this seat's staged webhook secret against the vendor, and that lookup needs an ORG-scoped key holding webhook_read alone — an inbox-scoped key lists zero webhooks (measured 2026-09-17). Mint one with POST /v0/api-keys carrying {\"permissions\":{\"webhook_read\":true}} and NO inbox_id, vault it as AGENTMAIL_WEBHOOK_READ_API_KEY (crane_secret_set from clipboard), then re-run."
  # <<< agentmail-webhook-read-fence
  stage_secret_from_env AGENTMAIL_WEBHOOK_READ_API_KEY "${AGENTMAIL_WEBHOOK_READ_API_KEY}" "AgentMail webhook_read credential for boot smoke ONLY (org-scoped, webhook_read alone; materialized root-only and stripped from agent env)"
  # The sentinels below delimit the block tests/agentmail-webhook-secret-fence.test.ts
  # drives in a bash harness; keep them, and keep the block self-contained.
  # >>> agentmail-webhook-secret-fence
  # NO GLOBAL FALLBACK. The fallback that used to sit here staged the global
  # secret onto scott (no per-seat key vaulted) and every inbound email to that
  # seat was rejected 401 for weeks while boot smoke passed (2026-09-15,
  # vfy_01M2HXT17Q32RX6TCV5NVZA9D6). Its own comment above already named the
  # 2026-06-12 failure it caused; a fallback that can only produce a silently
  # dead inbound path is not an affordance. Refuse, and name the key to vault.
  _AGENTMAIL_WH_KEY="WEBHOOK_SECRET_AGENTMAIL__$(printf '%s' "${CUSTOMER_ID}" | tr '[:lower:]-' '[:upper:]_' | tr -cd 'A-Z0-9_')"; _AGENTMAIL_WH_SECRET="${!_AGENTMAIL_WH_KEY:-}"
  [ -n "${_AGENTMAIL_WH_SECRET}" ] || die "agentmail-webhook-secret-fence: ${_AGENTMAIL_WH_KEY} is not vaulted in Infisical /ss (prod). This seat binds the agentmail adapter, so its webhook gate needs the signing secret of ITS OWN vendor webhook; the global WEBHOOK_SECRET_AGENTMAIL is never used for it. Copy the secret from the AgentMail dashboard webhook for hermes-${CUSTOMER_ID}.fly.dev and vault it as ${_AGENTMAIL_WH_KEY} (crane_secret_set from clipboard), then re-run."
  # <<< agentmail-webhook-secret-fence
  stage_secret_from_env WEBHOOK_SECRET_AGENTMAIL "${_AGENTMAIL_WH_SECRET}" "AgentMail Svix webhook signing secret (per-customer ${_AGENTMAIL_WH_KEY}; no global fallback)"
  # SMD_WEBHOOK_SIGNING_SECRET is what the Hermes-side router verifies the gate's
  # forwarded signature with (HMAC V2 over "<timestamp>.<bytes>", overlay
  # shared/forward_signature.py). The gate re-signs its forward hop with the
  # ROUTE secret (webhook_gate.py: "same secret"), so the router's signing secret IS
  # the agentmail route secret — stage them equal, or inbound never routes to a skill.
  stage_secret_from_env SMD_WEBHOOK_SIGNING_SECRET "${_AGENTMAIL_WH_SECRET}" "router forward-verify secret (== agentmail route secret)"
  unset _AGENTMAIL_WH_KEY _AGENTMAIL_WH_SECRET
else
  # A seat that binds no agentmail adapter must carry NO AgentMail secret. Fly
  # secrets persist across deploys, so a value staged by an earlier provision
  # (before the per-seat fence, when the global WEBHOOK_SECRET_AGENTMAIL went
  # onto every seat) outlives the config that stopped staging it. That is how
  # the first client seat, an msgraph seat, failed boot smoke on 2026-09-16:
  # `agentmail-webhook-secret-matches-vendor` found the stale global secret in
  # the agent env with no API key beside it, and the reprovision reported
  # FATAL on a Machine that was otherwise healthy. Same shape as the
  # R2_SKILL_BODIES_* removal above: converge the Machine on the authored
  # state, never on what a previous run happened to leave behind.
  # SMD_WEBHOOK_SIGNING_SECRET is NOT touched here: the msgraph block below
  # stages its own value on an msgraph seat.
  unset_stale "no agentmail adapter authored" WEBHOOK_SECRET_AGENTMAIL AGENTMAIL_API_KEY AGENTMAIL_SEND_API_KEY AGENTMAIL_WEBHOOK_READ_API_KEY
fi
