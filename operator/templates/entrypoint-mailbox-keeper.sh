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
# shellcheck source=/dev/null
. "$(dirname "${BASH_SOURCE[0]}")/_seat-layout.generated.sh"
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

# THE MODEL KEY, for the keeper's classify step (2026-11-16,
# workspace_broker/mailbox_keeper_model.py). A cron pre_run never receives the
# seat's model key (Hermes scrubs provider keys from a script's environment,
# vfy_01M7JJEB32QDGMMM22J9PCDC35), and the broker launches through env -i, so
# the broker reads the seat's own key from a file, the agentmail.json custody
# shape: 0600, workspace-broker's, in the broker-only BROKER_DIR. Written ONLY
# when the seat lists the mailbox-keeper skill and authors its `classify`
# setting (true or false, so the rehearsal dry run works before the switch-on),
# and removed on every other boot. The gateway's interpreter writes it, because
# the overlay's pricing table is installed there beside model_call.py.
export SMD_MODEL_CREDENTIAL_PATH="${BROKER_DIR}/model.json"
rm -f "${SMD_MODEL_CREDENTIAL_PATH}"
if SMD_KEEPER_CUSTOMER_YAML="${LIVE_CUSTOMER_YAML}" PYTHONPATH="${SMD_LAYOUT_BROKER_ROOT}" ${SMD_LAYOUT_BROKER_PYTHON} -c \
  'import os, sys; from pathlib import Path; from workspace_broker.mailbox_keeper_verbs import classify_authored; sys.exit(0 if classify_authored(Path(os.environ["SMD_KEEPER_CUSTOMER_YAML"])) else 1)'; then
  install -d -o workspace-broker -g workspace-broker -m 0700 "${BROKER_DIR}"
  _mk_rc=0
  ${SMD_LAYOUT_HERMES_PYTHON} -c \
    'import os; from pathlib import Path; from shared.model_call import materialize_credential; raise SystemExit(0 if materialize_credential(Path(os.environ["SMD_MODEL_CREDENTIAL_PATH"])) else 3)' \
    || _mk_rc=$?
  if [ "${_mk_rc}" -eq 0 ] && [ -f "${SMD_MODEL_CREDENTIAL_PATH}" ]; then
    chown workspace-broker:workspace-broker "${SMD_MODEL_CREDENTIAL_PATH}"
    chmod 0600 "${SMD_MODEL_CREDENTIAL_PATH}"
    log "mailbox keeper: model credential materialized to the broker store"
  else
    log "mailbox keeper: model credential NOT materialized (exit ${_mk_rc}); classify fails closed"
  fi
else
  log "mailbox keeper: classify not authored; no model credential for the broker"
fi
