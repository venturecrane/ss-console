#!/usr/bin/env bash
# stage-web-connectors.sh - staging for the web-facing connectors: native web
# search (Brave), and the marketing seat's mcp:wordpress and mcp:ga4. Sourced by
# provision-customer.sh in the same shell. It reads
# CUSTOMER_YAML, CUSTOMER_ID and the operator env, and calls
# stage_secret_from_env / die / log / authored_channel from there. A file of its
# own from the start (with the Brave block moved in beside it), because adding
# the marketing blocks inline pushed the provisioner past the
# shell size ratchet (tests/operator-module-size.test.ts), whose instruction is
# to split rather than raise the baseline. Nothing here runs unless a
# customer.yaml connector declares the matching backend.
#
# Tests that read this file as text: operator/bin/tests/test_secret_custody.py
# (every staged name must classify), tests/provisioner-gates-parse-not-grep.test.ts
# and tests/stage-web-connectors.test.ts (drives both blocks against fixture seats).
# shellcheck shell=bash

# Brave Search (native:brave-free, ADR 0070). BRAVE_SEARCH_API_KEY is the env var
# Hermes' native brave-free provider reads. The Hosted-Agent tier uses Brave's
# FREE tier (one shared, SMD-owned key; $0, no runaway spend; keeps "your only
# bill is Anthropic" true). Staged ONLY for a customer whose customer.yaml binds a
# native:brave-* backend. Missing at boot => the provider stays unavailable
# (Hermes falls back / no web search), fail-closed, no crashloop.
if authored_channel '^backend=native:brave'; then
  stage_secret_from_env BRAVE_SEARCH_API_KEY "${BRAVE_SEARCH_API_KEY:-}" "Brave Search API key (native brave-free provider; web search)"
fi

# Firecrawl web extract (WebExtract: native:firecrawl). FIRECRAWL_API_KEY is the
# env var Hermes' firecrawl provider reads; SMD's shared, SMD-owned key (infra,
# like Brave). Unset => skipped, and the overlay then does NOT name firecrawl as
# the extract backend (an unkeyed explicit selection would route URLs to
# Firecrawl's anonymous cloud), so web_extract answers "not configured".
if authored_channel '^backend=native:firecrawl$'; then
  stage_secret_from_env FIRECRAWL_API_KEY "${FIRECRAWL_API_KEY:-}" "Firecrawl API key (native firecrawl provider; web extract)"
fi

# WordPress (backend: mcp:wordpress, capability Website; a marketing seat). The
# connector authenticates as ONE WordPress user the firm issues (a Contributor)
# with an Application Password over HTTPS Basic auth. site_url and username are
# non-secret and read from the seat's connector stanza; the password is client-
# issued and per-customer ONLY: <secret_ref name>__<CUSTOMER_ID>, with no global
# fallback, because a password for one firm's site is never another seat's.
# Unset => warn and skip; the overlay then leaves the server unwired this boot.
if authored_channel '^backend=mcp:wordpress$'; then
  WP_PARSE_PY="
import yaml
with open('${CUSTOMER_YAML}') as f:
    c = yaml.safe_load(f) or {}
conn = {}
for v in (c.get('connectors') or {}).values():
    if isinstance(v, dict) and str(v.get('backend', '')) == 'mcp:wordpress':
        conn = v
        break
print(str(conn.get('site_url') or '').strip())
print(str(conn.get('username') or '').strip())
print(str(conn.get('secret_ref') or '').strip())
"
  WP_FIELDS=()
  while IFS= read -r _line; do WP_FIELDS+=("${_line}"); done \
    < <(uv run --quiet --with pyyaml python3 -c "${WP_PARSE_PY}")
  _WP_SITE="${WP_FIELDS[0]:-}"
  _WP_USER="${WP_FIELDS[1]:-}"
  _WP_SECRET_REF="${WP_FIELDS[2]:-}"
  _WP_SECRET_NAME="${_WP_SECRET_REF#fly-secret:}"
  [ -n "${_WP_SECRET_NAME}" ] && [ "${_WP_SECRET_NAME}" != "${_WP_SECRET_REF}" ] \
    || _WP_SECRET_NAME="WORDPRESS_APP_PASSWORD"
  _WP_CID_KEY="${_WP_SECRET_NAME}__$(printf '%s' "${CUSTOMER_ID}" | tr '[:lower:]-' '[:upper:]_' | tr -cd 'A-Z0-9_')"
  _WP_PASSWORD="${!_WP_CID_KEY:-}"
  log "WordPress seat: site=${_WP_SITE} user=${_WP_USER} (application password from ${_WP_CID_KEY})"
  stage_secret_from_env WORDPRESS_SITE_URL     "${_WP_SITE}"     "WordPress site root (from connectors.Website.site_url)"
  stage_secret_from_env WORDPRESS_USERNAME     "${_WP_USER}"     "WordPress user (from connectors.Website.username)"
  stage_secret_from_env WORDPRESS_APP_PASSWORD "${_WP_PASSWORD}" "WordPress application password (per-customer ${_WP_CID_KEY})"
  unset WP_PARSE_PY WP_FIELDS _WP_SITE _WP_USER _WP_SECRET_REF _WP_SECRET_NAME _WP_CID_KEY _WP_PASSWORD
fi

# Google Analytics 4 (backend: mcp:ga4, capability Analytics; a marketing seat).
# property_id is non-secret, from the seat's connector stanza. The key is an
# ANALYTICS-ONLY service account (the firm grants its email Viewer on the
# property): GA4_SERVICE_ACCOUNT_JSON__<CUSTOMER_ID>, else GA4_SERVICE_ACCOUNT_JSON.
# It lands in the agent-readable MCP config, so it must NEVER be the domain-wide-
# delegation key GOOGLE_SERVICE_ACCOUNT_JSON, which can impersonate users; staging
# refuses that value outright rather than trusting the vault to be right.
if authored_channel '^backend=mcp:ga4$'; then
  GA4_PARSE_PY="
import yaml
with open('${CUSTOMER_YAML}') as f:
    c = yaml.safe_load(f) or {}
for v in (c.get('connectors') or {}).values():
    if isinstance(v, dict) and str(v.get('backend', '')) == 'mcp:ga4':
        print(str(v.get('property_id') or '').strip())
        break
"
  _GA4_PROPERTY="$(uv run --quiet --with pyyaml python3 -c "${GA4_PARSE_PY}")"
  _GA4_KEY_NAME="GA4_SERVICE_ACCOUNT_JSON__$(printf '%s' "${CUSTOMER_ID}" | tr '[:lower:]-' '[:upper:]_' | tr -cd 'A-Z0-9_')"
  _GA4_KEY="${!_GA4_KEY_NAME:-${GA4_SERVICE_ACCOUNT_JSON:-}}"
  if [ -n "${_GA4_KEY}" ] && [ "${_GA4_KEY}" = "${GOOGLE_SERVICE_ACCOUNT_JSON:-}" ]; then
    die "GA4 key for ${CUSTOMER_ID} is the domain-wide-delegation key (GOOGLE_SERVICE_ACCOUNT_JSON). The ga4 connector holds its key in the agent-readable MCP config, so it must be an analytics-only service account with no delegation. Vault one as GA4_SERVICE_ACCOUNT_JSON and re-run."
  fi
  log "GA4 seat: property=${_GA4_PROPERTY} (key from ${_GA4_KEY_NAME}, else GA4_SERVICE_ACCOUNT_JSON)"
  stage_secret_from_env GA4_PROPERTY_ID          "${_GA4_PROPERTY}" "GA4 property id (from connectors.Analytics.property_id)"
  stage_secret_from_env GA4_SERVICE_ACCOUNT_JSON "${_GA4_KEY}"      "analytics-only service-account key (${_GA4_KEY_NAME}, else GA4_SERVICE_ACCOUNT_JSON)"
  unset GA4_PARSE_PY _GA4_PROPERTY _GA4_KEY_NAME _GA4_KEY
fi
