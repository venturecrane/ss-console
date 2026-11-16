#!/usr/bin/env bash
# entrypoint-mailbox-keeper.sh: the mailbox keeper's state store
# (mailbox_keeper_verbs.py), SOURCED by entrypoint.sh (same shell, as root,
# before the broker and the gateway launch). Its own file because entrypoint.sh
# sits at its shell size ratchet.
#
# WHY. The keeper verb runs in the workspace broker (uid workspace-broker) and
# keeps listed.json and trashed.jsonl. Its default home, /opt/data/mailbox-keeper,
# sits under /opt/data, which the gateway chmods to 0700 hermes mid-boot, so the
# broker could not create it: the first dry run on smd-office (2026-11-15)
# returned status state_unreadable (PermissionError). The sender-enrollment
# store's posture, minus the agent-readable half: the broker uid is the only
# reader and writer, reached through a bind mount at /run/smd-mailbox-keeper.
#
# Created on EVERY seat, empty: only a seat authoring the mailbox-keeper skill
# writes it, and one convergence everywhere keeps ownership the same. It
# survives restart and reprovision by design (it is what stops a thread being
# listed twice); decommission removes it with the volume.
# shellcheck shell=bash
MAILBOX_KEEPER_DIR="/opt/data/mailbox-keeper"
MAILBOX_KEEPER_BIND="/run/smd-mailbox-keeper"
export SMD_MAILBOX_KEEPER_DIR="${MAILBOX_KEEPER_BIND}"
install -d -o workspace-broker -g workspace-broker -m 0700 "${MAILBOX_KEEPER_DIR}"
# Every file in it is the broker's (a root-run command that did not drop
# privileges would otherwise lock the broker out of its own state).
chown -R workspace-broker:workspace-broker "${MAILBOX_KEEPER_DIR}"
mkdir -p "${MAILBOX_KEEPER_BIND}"
mountpoint -q "${MAILBOX_KEEPER_BIND}" \
  || mount --bind "${MAILBOX_KEEPER_DIR}" "${MAILBOX_KEEPER_BIND}" \
  || { log "FATAL: could not bind-mount ${MAILBOX_KEEPER_DIR} -> ${MAILBOX_KEEPER_BIND}"; exit 1; }
