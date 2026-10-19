#!/usr/bin/env bash
# render-fly-toml.sh: the ONE renderer of a seat's fly.toml (fleet release,
# 2026-10-19). Extracted from provision-customer.sh step 3 so `fleet release`
# deploys every seat with exactly the toml provisioning would render, and
# records that rendering as the seat's rollback toml.
#
# Two ways in:
#
#   SOURCED (provision-customer.sh): call
#     render_fly_toml <out-path>
#   with SLUG, FLY_REGION, MACHINE_SIZE, MEMORY_MB, MACHINE_CPU_KIND,
#   MACHINE_CPUS, R2_BUCKET_CONFIG and TEMPLATE_DIR already set by the caller.
#
#   EXECUTED (operator/bin/lib/fleet): run
#     render-fly-toml.sh <slug> <out-path>
#   It reads operator/customers/<slug>/customer.yaml from THIS checkout, derives
#   the same fields provisioning derives (machine-size.sh for the cpu fields),
#   and renders. R2_BUCKET_CONFIG defaults to smd-customer-config, as in
#   provisioning.
#
# Every substituted value is checked against a narrow shape first: these land in
# a sed replacement and in the Machine's config, so a value outside the shape is
# a refusal, never a best effort.
# shellcheck shell=bash

_render_check() { # <name> <value> <regex>
  if [[ ! "$2" =~ $3 ]]; then
    echo "render-fly-toml: $1 '$2' does not match $3; refusing to render" >&2
    return 1
  fi
}

render_fly_toml() { # <out-path>
  local out="$1"
  _render_check slug "${SLUG}" '^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$' || return 1
  _render_check fly_region "${FLY_REGION}" '^[a-z]{3}$' || return 1
  _render_check machine.size "${MACHINE_SIZE}" '^(shared-cpu|performance)-[0-9]+x$' || return 1
  _render_check machine.memory_mb "${MEMORY_MB}" '^[0-9]{3,6}$' || return 1
  _render_check cpu_kind "${MACHINE_CPU_KIND}" '^(shared|performance)$' || return 1
  _render_check cpus "${MACHINE_CPUS}" '^[0-9]{1,2}$' || return 1
  _render_check r2_bucket_config "${R2_BUCKET_CONFIG}" '^[a-z0-9][a-z0-9.-]{1,62}$' || return 1
  # Per-customer skill bodies bucket name (ADR 0022 Stream 2). One bucket per
  # customer; the bucket itself is the trust boundary. The name is not a
  # credential, so it is rendered into fly.toml [env].
  local skill_bodies="ss-operator-${SLUG}-skills"
  mkdir -p "$(dirname "${out}")"
  sed -e "s/{{CUSTOMER_SLUG}}/${SLUG}/g" \
      -e "s/{{FLY_REGION}}/${FLY_REGION}/g" \
      -e "s/{{MACHINE_SIZE}}/${MACHINE_SIZE}/g" \
      -e "s/{{MEMORY_MB}}/${MEMORY_MB}/g" \
      -e "s/{{MACHINE_CPU_KIND}}/${MACHINE_CPU_KIND}/g" \
      -e "s/{{MACHINE_CPUS}}/${MACHINE_CPUS}/g" \
      -e "s|{{R2_BUCKET_CONFIG}}|${R2_BUCKET_CONFIG}|g" \
      -e "s|{{R2_SKILL_BODIES_BUCKET}}|${skill_bodies}|g" \
      "${TEMPLATE_DIR}/fly.toml.template" > "${out}"
  # Comment lines may name a {{TOKEN}} in prose; only configuration lines count.
  local left
  left="$(grep -v '^[[:space:]]*#' "${out}" | grep -o '{{[A-Z_]*}}' | sort -u | tr '\n' ' ' || true)"
  if [ -n "${left}" ]; then
    echo "render-fly-toml: unrendered token(s) left in ${out}: ${left}" >&2
    return 1
  fi
}

_render_main() { # <slug> <out-path>
  local repo yaml fields
  SLUG="${1:-}"
  [ -n "${SLUG}" ] && [ -n "${2:-}" ] || { echo "Usage: $0 <slug> <out-path>" >&2; return 2; }
  repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
  TEMPLATE_DIR="${repo}/operator/templates"
  yaml="${repo}/operator/customers/${SLUG}/customer.yaml"
  _render_check slug "${SLUG}" '^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$' || return 1
  [ -f "${yaml}" ] || { echo "render-fly-toml: ${yaml} missing" >&2; return 1; }
  fields="$(uv run --quiet --with pyyaml python3 -c '
import sys, yaml
c = yaml.safe_load(open(sys.argv[1])) or {}
m = c.get("machine") or {}
print(c.get("customer_id", ""), c.get("fly_region", ""), m.get("size", "shared-cpu-2x"), m.get("memory_mb", 2048))
' "${yaml}")" || return 1
  local cid
  read -r cid FLY_REGION MACHINE_SIZE MEMORY_MB <<<"${fields}"
  [ "${cid}" = "${SLUG}" ] || { echo "render-fly-toml: customer_id '${cid}' != slug '${SLUG}'" >&2; return 1; }
  # shellcheck source=machine-size.sh
  source "${repo}/operator/bin/lib/machine-size.sh"
  MACHINE_CPU_KIND="$(machine_cpu_kind "${MACHINE_SIZE}")" || return 1
  MACHINE_CPUS="$(machine_cpus "${MACHINE_SIZE}")" || return 1
  R2_BUCKET_CONFIG="${R2_BUCKET_CONFIG:-smd-customer-config}"
  render_fly_toml "$2"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -euo pipefail
  _render_main "$@"
fi
