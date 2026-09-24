#!/usr/bin/env bash
# firm-config-currency.sh - sourced by provision-customer.sh. Expects log, die,
# SLUG and MEDCHRON_FIRM_YAML from the caller.
# The firm config must be the one on engagements main, not whatever a shared
# checkout happens to hold (2026-09-24). assert_build_source_is_current guards
# THIS repo; nothing guarded the engagements tree the bytes below come from.
# ~/dev/engagements is a shared checkout that peers leave on their own branches
# and that sat 24 letters behind main on 09-23; a reprovision from it at 05:02Z
# silently put ashton-price's chronology tiers back on claude-opus-5 forty
# minutes after engagements#142 moved them to claude-opus-5-5, and exited zero.
# Compared by CONTENT against origin/main rather than by a behind-count: a
# checkout on a peer's feature branch can be zero commits behind main and still
# carry a different file. Same escape hatch as the source guard.
# >>> firm-config-currency
assert_firm_config_is_main() {
  local eng rel main_blob
  eng="$(cd "$(dirname "${MEDCHRON_FIRM_YAML}")/../../../.." && pwd)"
  rel="operator/customers/${SLUG}/medchron/firm.yaml"
  if ! git -C "${eng}" rev-parse --git-dir >/dev/null 2>&1; then
    log "WARN: ${eng} is not a git checkout; the firm config's currency cannot be verified"
    return 0
  fi
  git -C "${eng}" fetch origin --quiet 2>/dev/null \
    || log "WARN: could not fetch engagements origin; comparing against the last-known origin/main"
  if ! main_blob="$(git -C "${eng}" show "origin/main:${rel}" 2>/dev/null)"; then
    log "WARN: ${rel} is not on engagements origin/main; uploading the local file as it stands"
    return 0
  fi
  if [ "${main_blob}" != "$(cat "${MEDCHRON_FIRM_YAML}")" ]; then
    if [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" = "1" ]; then
      log "WARN: SS_ALLOW_DIVERGENT_SOURCE=1 — uploading a firm config that differs from engagements origin/main BY REQUEST"
      return 0
    fi
    die "the firm config at ${MEDCHRON_FIRM_YAML} differs from engagements origin/main \
(checkout on '$(git -C "${eng}" rev-parse --abbrev-ref HEAD 2>/dev/null)'). Uploading it would put \
${SLUG}'s chronology on whatever that checkout holds. Point SS_ENGAGEMENTS_DIR at an up-to-date \
engagements tree, or set SS_ALLOW_DIVERGENT_SOURCE=1 to upload this file on purpose."
  fi
  log "Firm config matches engagements origin/main"
}
# <<< firm-config-currency
