#!/usr/bin/env bash
# machine-size.sh — derive the Fly [[vm]] cpu fields from customer.yaml.machine.size.
#
# WHY THIS EXISTS (ss#2612): fly.toml.template hardcoded `cpus = 1` while
# rendering `size` and `memory` from customer.yaml, so authoring
# `machine.size: shared-cpu-2x` produced a Machine that reported 2x and ran
# on one vCPU. The size name carries the cpu count and the cpu kind; this is
# the one place that reads them. Sourced by provision-customer.sh; exercised
# directly by tests/operator-fly-template-env.test.ts for both seat sizes.
#
#   machine_cpu_kind shared-cpu-2x   -> shared
#   machine_cpus     shared-cpu-2x   -> 2
#   machine_cpus     performance-4x  -> 4
#
# Anything outside the two Fly families is refused loudly: a typo must fail
# provisioning, never render a one-vCPU Machine by fallback.

machine_cpu_kind() {
  case "$1" in
    shared-cpu-*x) echo "shared" ;;
    performance-*x) echo "performance" ;;
    *)
      echo "machine-size: unrecognised machine.size '$1' (expected shared-cpu-Nx or performance-Nx)" >&2
      return 1
      ;;
  esac
}

machine_cpus() {
  local n
  case "$1" in
    shared-cpu-*x) n="${1#shared-cpu-}" ;;
    performance-*x) n="${1#performance-}" ;;
    *)
      echo "machine-size: unrecognised machine.size '$1' (expected shared-cpu-Nx or performance-Nx)" >&2
      return 1
      ;;
  esac
  n="${n%x}"
  case "${n}" in
    1|2|4|6|8|16) echo "${n}" ;;
    *)
      echo "machine-size: unsupported cpu count '${n}' in machine.size '$1' (expected 1, 2, 4, 6, 8, or 16; fly platform vm-sizes)" >&2
      return 1
      ;;
  esac
}
