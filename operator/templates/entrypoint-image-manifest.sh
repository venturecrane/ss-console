#!/usr/bin/env bash
# entrypoint-image-manifest.sh: tell the config publisher what this image can run.
# SOURCED by entrypoint.sh (same shell, as root, before the R2 key is stripped).
# It reads CUSTOMER_SLUG, R2_BUCKET_CONFIG, the R2_* env and _seed_endpoint from
# the entrypoint.
# shellcheck shell=bash
# shellcheck disable=SC2154 # _seed_endpoint, CUSTOMER_SLUG and the R2_* names are set by entrypoint.sh, which sources this file

# 2026-10-13: CI published a customer.yaml naming a cron whose pre_run.py shipped
# only in a newer image; the seat rebooted onto the older image and its boot died.
# /app/image-manifest.json (baked by gen-image-manifest.py) lists every skill,
# its pin and its files. Uploading it on EVERY boot makes
# vaults/<slug>/image-manifest.json describe the image that last booted, which
# is the image the next restart boots, and scripts/ci-publish-customer-configs.sh
# refuses to publish a config naming anything it lacks.
#
# Never fatal: a seat that cannot upload its manifest still serves the firm. The
# publisher then reads "cannot determine" and refuses any config that adds a
# requirement, so a failure here costs a publish, never the seat.
#
# The seat writes ONLY this key; the publisher writes customer.yaml and the
# portal output-classes.json. The source is a root-owned image file the agent
# cannot write, and this runs before any agent process exists.
if [ -f /app/image-manifest.json ]; then
  if AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" \
       AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
         aws s3 cp \
           --endpoint-url "${_seed_endpoint}" \
           --only-show-errors \
           /app/image-manifest.json \
           "s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/image-manifest.json"; then
    log "image manifest published (vaults/${CUSTOMER_SLUG}/image-manifest.json)"
  else
    log "WARN: could not publish the image manifest; CI will refuse config that adds a skill until a boot publishes it"
  fi
else
  log "WARN: /app/image-manifest.json absent from this image; CI will refuse config that adds a skill"
fi
