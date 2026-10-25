#!/usr/bin/env bash
# entrypoint-marketing.sh: a marketing seat's advertising-gate registers
# (ADR 0098), SOURCED by entrypoint.sh (same shell, as root, before the broker
# and the gateway launch). Its own file because entrypoint.sh sits at its shell
# size ratchet (tests/operator-module-size.test.ts says split, never raise). It
# reads CONFIG_DIR, CUSTOMER_SLUG, the R2_* env and _seed_endpoint from the
# entrypoint and _fetch_lane_inputs from entrypoint-fetch-inputs.sh.
#
# The registers (marketing-firm.yaml), staged by provision-customer.sh Step 2g
# from engagements, land at ${CONFIG_DIR}/marketing/marketing-firm.yaml, the
# path the gate reads by default (overlay shared/advertising_gate.py and the
# WordPress connector's gate.py, DEFAULT_REGISTERS). Same fail-static shape as
# the demand inputs: refreshed from the vault every boot, the existing copy kept
# on a failed fetch, a loud line when there is none.
#
# OWNERSHIP. Root-owned, group hermes, files 0640 and dirs 0750. Both readers
# run as the hermes uid: the gateway's outbound gate (a plugin in the gateway
# process) and the WordPress connector, a stdio MCP server the gateway spawns.
# Group medchron would be wrong here: no medchron process reads them, and the
# hermes uid is not in that group. The agent can read the registers and can
# never rewrite them (a gate that can edit its own registers checks nothing).
#
# ONLY ON A MARKETING SEAT. _fetch_lane_inputs retries an absent prefix three
# times with backoff (about 15 s), so every other seat would pay that on every
# boot for registers it never authors. The live customer.yaml decides: exit 3
# means it parsed and selects another role (skip, and remove any copy: gone
# means gone); anything else, including a config that cannot be read or a
# Python error (exit 1), fetches (cannot-evaluate never reads as "not
# expected"). A skipped seat writes no state file, and selftest.sh treats the
# lane as unauthored because the marketing router skill is not enabled there.
# shellcheck shell=bash
# shellcheck disable=SC2154 # CUSTOMER_SLUG, LIVE_CUSTOMER_YAML and the R2_* names are set by entrypoint.sh, which sources this file

SMD_MARKETING_INPUTS="${CONFIG_DIR}/marketing"
_marketing_rc=0
/opt/hermes/.venv/bin/python3 -I - "${LIVE_CUSTOMER_YAML}" <<'PY' 2>/dev/null || _marketing_rc=$?
import sys, yaml
with open(sys.argv[1], encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh) or {}
sys.exit(0 if (cfg.get("seat") or {}).get("role") == "marketing" else 3)
PY
if [ "${_marketing_rc}" = 3 ]; then
  rm -rf "${SMD_MARKETING_INPUTS}" "${SMD_MARKETING_INPUTS}.r2.tmp" # not a marketing seat
elif _fetch_lane_inputs marketing "${SMD_MARKETING_INPUTS}" marketing-firm.yaml; then
  rm -rf "${SMD_MARKETING_INPUTS}"
  mv "${SMD_MARKETING_INPUTS}.r2.tmp" "${SMD_MARKETING_INPUTS}"
  log "marketing registers refreshed from R2 into ${SMD_MARKETING_INPUTS}"
elif [ -f "${SMD_MARKETING_INPUTS}/marketing-firm.yaml" ]; then
  log "WARN: R2 fetch of the marketing registers failed (${_FETCH_INPUTS_STATE}); keeping the existing root-owned copy"
elif [ "${_FETCH_INPUTS_STATE}" = absent ]; then
  log "No marketing registers in the vault for ${CUSTOMER_SLUG}; the advertising gate refuses every marketing draft on this seat"
else
  log "ERROR: the marketing registers could not be fetched from the vault for ${CUSTOMER_SLUG} and this seat has no copy; the advertising gate refuses every marketing draft until a restart fetches them"
fi
if [ -d "${SMD_MARKETING_INPUTS}" ]; then
  chown -R root:hermes "${SMD_MARKETING_INPUTS}"
  find "${SMD_MARKETING_INPUTS}" -type d -exec chmod 0750 {} +
  find "${SMD_MARKETING_INPUTS}" -type f -exec chmod 0640 {} +
fi
