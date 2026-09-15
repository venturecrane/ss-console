#!/usr/bin/env bash
# stage-smokeball.sh — the Smokeball connector's staging block, sourced by
# provision-customer.sh in the same shell (it reads CUSTOMER_YAML, CUSTOMER_ID,
# the SMOKEBALL_* operator env, and calls stage_secret_from_env / die / log /
# authored_channel from there). Moved out of the provisioner as its own file
# in ss#2425, when adding SMOKEBALL_REGION pushed the provisioner past the
# shell size ratchet (tests/operator-module-size.test.ts): the ratchet's own
# instruction is to split, not to raise the baseline. Nothing here runs unless
# a customer.yaml connector declares backend mcp:smokeball.
#
# Tests that read this file as text: operator/bin/tests/test_secret_custody.py
# (every staged name must classify) and test_provision_smokeball_region.py.
# shellcheck shell=bash

# ---------- Step 6b-smokeball: Smokeball connector creds (ADR 0053, name remap) --
# mcp:smokeball reads env-agnostic SMOKEBALL_CLIENT_ID/SECRET/API_KEY. The operator
# env holds them under environment-specific names — SMOKEBALL_STAGING_* (the
# approved app's STAGING credentials; the pilot seat) and SMOKEBALL_PROD_* (its
# PRODUCTION credentials, staged for go-live) — so this is a NAME REMAP the
# manifest-driven loop below can't do. A third vault set, SMOKEBALL_SEED_*, is
# App 1 (the original client_credentials staging app) and is used ONLY by the
# rehearsal-office seeder (operator/customers/pilot-smokeball/seed/) — never by
# provisioning, and it must never be written over the STAGING/PROD names
# (2026-07-04: App 2's rollout once overwrote App 1's values; hence the split). The seat declares which environment it is via the smokeball connector
# block in customer.yaml:
#
#   connectors:
#     PracticeManagement:
#       backend: mcp:smokeball
#       environment: staging | production   # default staging; selects host pair + cred set
#       region: us | au | uk                 # default us; selects the regional gateway
#       auth_mode: client_credentials | authorization_code   # default client_credentials
#       account_id: <id>                     # optional; multi-account URL prefix
#
# SMOKEBALL_ENVIRONMENT is a REQUIRED runtime secret (the overlay fail-closes the
# connector if it is unset) so a prod seat can never silently default to staging.
# The authorization_code refresh token is NOT staged here — it is obtained at the
# connect step (bin/connect-smokeball.sh, repo root; ADR 0054) and set as SMOKEBALL_REFRESH_TOKEN
# directly. A prod seat whose SMOKEBALL_PROD_* creds are not yet in the operator env
# simply warns+skips → the connector is unwired this boot (boot-before-token), and
# wires once the creds land.
if authored_channel '^backend=mcp:smokeball$'; then
  SB_PARSE_PY="
import yaml
with open('${CUSTOMER_YAML}') as f:
    c = yaml.safe_load(f) or {}
sb = {}
for conn in (c.get('connectors') or {}).values():
    if isinstance(conn, dict) and str(conn.get('backend', '')) == 'mcp:smokeball':
        sb = conn
        break
print(str(sb.get('environment', 'staging')).strip().lower())
print(str(sb.get('auth_mode', 'client_credentials')).strip().lower())
print(str(sb.get('account_id') or '').strip())
print(str(sb.get('region', 'us')).strip().lower())
"
  SB_FIELDS=()
  while IFS= read -r _line; do SB_FIELDS+=("${_line}"); done \
    < <(uv run --quiet --with pyyaml python3 -c "${SB_PARSE_PY}")
  SB_ENV="${SB_FIELDS[0]:-staging}"
  SB_AUTH_MODE="${SB_FIELDS[1]:-client_credentials}"
  SB_ACCOUNT_ID="${SB_FIELDS[2]:-}"
  SB_REGION="${SB_FIELDS[3]:-us}"
  # The connector's own host table (smokeball_connector/client.py _HOSTS) knows
  # exactly these regions; anything else would fail at first token mint on the
  # seat. Refuse it here, where the authoring error is one line away.
  case "${SB_REGION}" in
    us|au|uk) ;;
    *) die "connectors.<PracticeManagement>.region '${SB_REGION}' is not a Smokeball region (us|au|uk); fix customer.yaml" ;;
  esac

  if [ "${SB_ENV}" = "production" ]; then
    _sb_cid="${SMOKEBALL_PROD_CLIENT_ID:-}"
    _sb_sec="${SMOKEBALL_PROD_CLIENT_SECRET:-}"
    _sb_key="${SMOKEBALL_PROD_API_KEY:-}"
    _sb_src="SMOKEBALL_PROD"
  else
    SB_ENV="staging"  # normalize anything non-production to staging (fail-safe)
    _sb_cid="${SMOKEBALL_STAGING_CLIENT_ID:-}"
    _sb_sec="${SMOKEBALL_STAGING_CLIENT_SECRET:-}"
    _sb_key="${SMOKEBALL_STAGING_API_KEY:-}"
    _sb_src="SMOKEBALL_STAGING"
  fi

  log "Smokeball seat: environment=${SB_ENV} auth_mode=${SB_AUTH_MODE} (creds from ${_sb_src}_*)"
  stage_secret_from_env SMOKEBALL_CLIENT_ID     "${_sb_cid}" "Smokeball OAuth client id (from ${_sb_src}_CLIENT_ID)"
  stage_secret_from_env SMOKEBALL_CLIENT_SECRET "${_sb_sec}" "Smokeball OAuth client secret (from ${_sb_src}_CLIENT_SECRET)"
  stage_secret_from_env SMOKEBALL_API_KEY       "${_sb_key}" "Smokeball x-api-key per-request app key (from ${_sb_src}_API_KEY)"
  # Scanned-document vision read (ss#2464) stages NOTHING here: the connector
  # transcribes with the seat's OWN per-seat Anthropic workspace key, already
  # staged above as ANTHROPIC_API_KEY (ADR 0062 §2 — per-customer workspaces are
  # the cost-attribution and revocation boundary). The overlay registry delivers
  # that same name into the connector subprocess. No second credential exists to
  # stage, rotate, or forget.
  # Required per-seat — value is always present (default staging), never silently prod-as-staging.
  stage_secret_from_env SMOKEBALL_ENVIRONMENT   "${SB_ENV}"  "Smokeball host environment (staging|production)"
  # Region is staged explicitly too (ss#2425). The connector defaults to "us" and
  # the API key is region-scoped (six keys on six gateways in three AWS regions),
  # so an AU or UK firm on an unstaged region would 403 with a valid key: the
  # confusing-403 class the credential family has already paid for once. Always
  # present, defaulting to us, so the seat's region is a read-back, not an inference.
  stage_secret_from_env SMOKEBALL_REGION        "${SB_REGION}"  "Smokeball regional gateway (us|au|uk); customer.yaml connectors.<cap>.region, default us"
  # Optional per-seat. client_credentials is the connector default, so stage AUTH_MODE
  # only when the firm-delegated grant is authored; account_id only when present.
  if [ "${SB_AUTH_MODE}" = "authorization_code" ]; then
    stage_secret_from_env SMOKEBALL_AUTH_MODE "authorization_code" "Smokeball grant: firm-delegated (refresh token set at the connect step)"
    # Per-customer OAuth state-signing key (ADR 0054): HMAC(master, slug), the same
    # derivation as the runtime-read key (ADR 0043). The Machine verifies the connect
    # state with this; the connect initiator derives the SAME key to sign. The master
    # lives ONLY in the operator env (/ss) — each Machine gets only its own derived key.
    if [ -n "${OPERATOR_OAUTH_STATE_MASTER:-}" ]; then
      _sb_state_key="$(printf '%s' "${SLUG}" | openssl dgst -sha256 -hmac "${OPERATOR_OAUTH_STATE_MASTER}" | awk '{print $NF}')"
      stage_secret_from_env SMOKEBALL_OAUTH_STATE_KEY "${_sb_state_key}" "per-customer Smokeball OAuth state key (ADR 0054; HMAC(master,slug))"
      unset _sb_state_key
    else
      log "WARN: OPERATOR_OAUTH_STATE_MASTER unset — SMOKEBALL_OAUTH_STATE_KEY not derived; the connect callback will reject all state until it is staged"
    fi
  fi
  if [ -n "${SB_ACCOUNT_ID}" ]; then
    stage_secret_from_env SMOKEBALL_ACCOUNT_ID "${SB_ACCOUNT_ID}" "Smokeball multi-account URL prefix"
  fi
  # Smokeball webhook ingress (overlay webhook-gate). Staged only when the seat
  # declares a smokeball webhook_url — otherwise these are unused. Two secrets:
  #   WEBHOOK_SECRET_SMOKEBALL — the HMAC key the gate verifies with. It MUST equal
  #     the `key` set on the Smokeball subscription. Smokeball uses it as RAW UTF-8
  #     bytes, so it is staged byte-identical (printf '%s', no whsec_/base64/newline
  #     transform — unlike the Svix secret). Per-customer
  #     WEBHOOK_SECRET_SMOKEBALL__<CUSTOMER_ID>, else the global.
  #   WEBHOOK_SMOKEBALL_CLIENT_ID — OUR Smokeball API ClientId, fed into the signed
  #     string {Timestamp}|{RequestId}|{ClientId} (Smokeball never sends it). It is
  #     the same client id the connector authenticates with (SMOKEBALL_CLIENT_ID ==
  #     ${_sb_cid}); a per-customer override exists only for the rare case the signing
  #     ClientId differs in byte form from the OAuth client id (confirm vs a real
  #     delivery). Without these the smokeball route fail-closes (gate 401).
  if authored_channel '^webhook_url=.*/webhooks/smokeball$'; then
    _SB_WH_KEY="WEBHOOK_SECRET_SMOKEBALL__$(printf '%s' "${CUSTOMER_ID}" | tr '[:lower:]-' '[:upper:]_' | tr -cd 'A-Z0-9_')"
    _SB_WH_SECRET="${!_SB_WH_KEY:-${WEBHOOK_SECRET_SMOKEBALL:-}}"
    stage_secret_from_env WEBHOOK_SECRET_SMOKEBALL "${_SB_WH_SECRET}" "Smokeball webhook HMAC key == subscription key, raw bytes (per-customer ${_SB_WH_KEY}, else global)"
    _SB_WH_CID_KEY="WEBHOOK_SMOKEBALL_CLIENT_ID__$(printf '%s' "${CUSTOMER_ID}" | tr '[:lower:]-' '[:upper:]_' | tr -cd 'A-Z0-9_')"
    _SB_WH_CID="${!_SB_WH_CID_KEY:-${_sb_cid}}"
    stage_secret_from_env WEBHOOK_SMOKEBALL_CLIENT_ID "${_SB_WH_CID}" "Smokeball API ClientId fed into the webhook HMAC (per-customer ${_SB_WH_CID_KEY}, else = SMOKEBALL_CLIENT_ID)"
    unset _SB_WH_KEY _SB_WH_SECRET _SB_WH_CID_KEY _SB_WH_CID
  fi
  unset SB_PARSE_PY SB_FIELDS SB_ENV SB_AUTH_MODE SB_ACCOUNT_ID _sb_cid _sb_sec _sb_key _sb_src
fi
