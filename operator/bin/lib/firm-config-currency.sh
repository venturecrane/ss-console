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
#
# CANNOT EVALUATE IS NOT PERMITTED (Law 2; code review 2026-09-25). The first
# cut warned and uploaded in the three states where the comparison could not be
# made: the tree is not a git checkout, the fetch failed (so origin/main is
# whatever it was last time, which is exactly how 09-24 happened), and the file
# is not on origin/main at all (an unreviewed config). Each now dies naming its
# fix, unless SS_ALLOW_DIVERGENT_SOURCE=1 says to upload the file as it stands.
# The same holds one step earlier: a missing engagements checkout cannot say
# whether this seat authors a firm config, so it must not read as "it does
# not" (boot smoke's medchron-firm-config-expected check makes the same call).
# >>> firm-config-currency
_firm_config_cannot_evaluate() {
  if [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" = "1" ]; then
    log "WARN: SS_ALLOW_DIVERGENT_SOURCE=1: proceeding BY REQUEST although $1"
    return 0
  fi
  die "$1, so ${SLUG}'s firm config cannot be checked against engagements origin/main. $2 \
Or set SS_ALLOW_DIVERGENT_SOURCE=1 to proceed on purpose."
}

assert_engagements_checkout_present() {
  local eng="${MEDCHRON_FIRM_YAML%/operator/customers/*}"
  [ -d "${eng}" ] && return 0
  _firm_config_cannot_evaluate "the engagements checkout is missing at ${eng}" \
    "Clone venturecrane/engagements there, or point SS_ENGAGEMENTS_DIR at a clone."
}

assert_firm_config_is_main() {
  local eng rel main_blob
  eng="${MEDCHRON_FIRM_YAML%/operator/customers/*}"
  rel="operator/customers/${SLUG}/medchron/firm.yaml"
  if ! git -C "${eng}" rev-parse --git-dir >/dev/null 2>&1; then
    _firm_config_cannot_evaluate "${eng} is not a git checkout" \
      "Point SS_ENGAGEMENTS_DIR at a clone of venturecrane/engagements."
    return 0
  fi
  if ! git -C "${eng}" fetch origin --quiet 2>/dev/null; then
    _firm_config_cannot_evaluate "fetching engagements origin failed (the local origin/main may be stale)" \
      "Check network access and 'gh auth status' for venturecrane/engagements, then retry."
    return 0
  fi
  if ! main_blob="$(git -C "${eng}" show "origin/main:${rel}" 2>/dev/null)"; then
    _firm_config_cannot_evaluate "${rel} is not on engagements origin/main" \
      "Merge the firm config to engagements main first; an unmerged file is an unreviewed one."
    return 0
  fi
  if [ "${main_blob}" != "$(cat "${MEDCHRON_FIRM_YAML}")" ]; then
    if [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" = "1" ]; then
      log "WARN: SS_ALLOW_DIVERGENT_SOURCE=1: uploading a firm config that differs from engagements origin/main BY REQUEST"
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
