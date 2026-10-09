#!/usr/bin/env bash
# entrypoint-negotiation.sh: the negotiation watch lane's boot-time tree, SOURCED
# by entrypoint.sh right after entrypoint-drafting.sh (same shell, as root,
# before the broker and the medchron daemon launch). The drafting lane's
# queue and job dirs plus a state dir, without firm inputs: the watch's settings (the
# recipient, the runaway guards) are authored on the skill in customer.yaml and
# the broker reads them there. It reads MEDCHRON_DATA_DIR and MEDCHRON_RUN_DIR
# from the entrypoint and exports SMD_NEGOTIATION_QUEUE_DIR (the broker's env -i
# line passes it on) and MEDCHRON_NEGOTIATION_STATE_DIR (the daemon inherits it
# and hands it to each negotiation child).
# shellcheck shell=bash

# negotiation-queue/ is written by the broker (negotiation_job_submit,
# negotiation_job_resume) and read by the root daemon; negotiation-jobs/ is
# root, group medchron execute-only (the drafting lane's modes, same reasons).
install -d -o root -g workspace-broker -m 0770 "${MEDCHRON_DATA_DIR}/negotiation-queue"
install -d -o root -g medchron -m 0710 "${MEDCHRON_DATA_DIR}/negotiation-jobs"
export SMD_NEGOTIATION_QUEUE_DIR="${MEDCHRON_RUN_DIR}/negotiation-queue"

# The watch's state on the volume: each open matter's file cursor and the
# per-document read attempts. The child (uid medchron) owns and writes it; it
# names file ids and matter ids, so it is 0700 and never leaves the volume.
install -d -o root -g medchron -m 0750 "${MEDCHRON_DATA_DIR}/negotiation"
install -d -o medchron -g medchron -m 0700 "${MEDCHRON_DATA_DIR}/negotiation/state"
export MEDCHRON_NEGOTIATION_STATE_DIR="${MEDCHRON_RUN_DIR}/negotiation/state"
