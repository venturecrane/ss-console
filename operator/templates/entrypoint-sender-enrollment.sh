#!/usr/bin/env bash
# entrypoint-sender-enrollment.sh: the invite-code enrollment store (ADR 0106),
# SOURCED by entrypoint.sh (same shell, as root, after the audit ledger's bind
# mount and before the broker and the gateway launch). Its own file because
# entrypoint.sh sits at its shell size ratchet (tests/operator-module-size.test.ts
# says split, never raise).
#
# THE STORE. /opt/data/sender-enrollment on the volume, the audit ledger's
# posture exactly: the workspace-broker uid is the only writer; the agent uid
# reads enrolled.json (who enrolled, and the name their code was issued for)
# through the audit-readers group and can write nothing; private/ (the salted
# code hashes and the attempt rows) is 0700 to the broker, unreadable to the
# agent. An enrolled list the agent could write would let it widen its own
# roster, the self-loopback the root-owned customer.yaml closed. The broker
# reaches it through a bind mount at /run/smd-sender-enrollment because the
# gateway chmods /opt/data to 0700 mid-boot (the audit note in entrypoint.sh).
#
# Created on EVERY seat, empty: only a guide seat that authors
# sender_enrollment.enabled reads it (both sides check the live config), and a
# directory that exists everywhere keeps the ownership convergence the same on
# every seat. It survives restart and reprovision by design, like the exposure
# override store; decommission removes it (operator/bin/lib/seat_dirs.py).
# shellcheck shell=bash

SENDER_ENROLLMENT_DIR="/opt/data/sender-enrollment"
SENDER_ENROLLMENT_BIND="/run/smd-sender-enrollment"
export SMD_SENDER_ENROLLMENT_DIR="${SENDER_ENROLLMENT_BIND}"
install -d -o workspace-broker -g audit-readers -m 2750 "${SENDER_ENROLLMENT_DIR}"
install -d -o workspace-broker -g workspace-broker -m 0700 "${SENDER_ENROLLMENT_DIR}/private"
if [ -f "${SENDER_ENROLLMENT_DIR}/enrolled.json" ]; then
  chown workspace-broker:audit-readers "${SENDER_ENROLLMENT_DIR}/enrolled.json"
  chmod 0640 "${SENDER_ENROLLMENT_DIR}/enrolled.json"
fi
# Every file under private/ is the broker's (a root-run command line that did
# not drop privileges would otherwise lock the broker out of its own store).
chown -R workspace-broker:workspace-broker "${SENDER_ENROLLMENT_DIR}/private"
mkdir -p "${SENDER_ENROLLMENT_BIND}"
mountpoint -q "${SENDER_ENROLLMENT_BIND}" \
  || mount --bind "${SENDER_ENROLLMENT_DIR}" "${SENDER_ENROLLMENT_BIND}" \
  || { log "FATAL: could not bind-mount ${SENDER_ENROLLMENT_DIR} -> ${SENDER_ENROLLMENT_BIND}"; exit 1; }
