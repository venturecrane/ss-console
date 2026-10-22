#!/usr/bin/env bash
# seat-image-guard.sh: the ONE image-capability guard every writer of
# vaults/<slug>/customer.yaml runs before it writes (ADR 0093, "config ahead of
# image"; the 2026-10-13 A&P outage).
#
# Sourced, never executed. Defines:
#   seat_image_manifest <slug> <dest>     what the seat's running image carries
#   seat_skills_manifest <slug> <dest>    what the seat's skills release carries
#   seat_image_guard <slug> <cfg> <work_dir> [<published>]
#       exit 0: the seat's running image (plus its skills release) can run <cfg>
#       exit 1: refuse; the reason is on stderr (from check-config-against-image.mjs)
#
# WHY ONE IMPLEMENTATION. A seat re-fetches its R2 customer.yaml on every boot
# (operator/templates/entrypoint.sh), so whichever process writes that object
# decides what the seat runs at its next restart. The guard first lived only in
# the merge-triggered publisher. The nightly reconciler, which republishes HEAD's
# bytes on byte drift, then undid every refusal the publisher made: the publisher
# left the old object in place, the reconciler saw drift, and wrote the refused
# config anyway. A guard on one writer of a key is a guard on none of them, so
# all of them source this file:
#   scripts/ci-publish-customer-configs.sh       (merge-triggered publisher)
#   scripts/ci-reconcile-r2-customer-configs.sh  (nightly reconciler)
#   operator/bin/sync-customer-yaml.sh           (operator-run sync)
# operator/bin/provision-customer.sh deliberately does not: provisioning ships a
# config together with the image that carries it, which is the documented way
# past a refusal, so checking it against the image being REPLACED would block
# the one path that resolves the skew.
#
# Contract with the caller (all three already have these):
#   - an `r2` function that runs `aws "$@" --endpoint-url ...` with R2 creds
#   - R2_BUCKET_CONFIG set
#   - FLEET_IMAGE_LOOKUP=1 (optional) to resolve the image by the seat's
#     heartbeat image_ref via D1 (needs wrangler + CLOUDFLARE_API_TOKEN);
#     otherwise, and on any miss, the per-seat vaults/<slug>/image-manifest.json
#     the entrypoint uploads on every boot.
#
# Read-only against R2: nothing here writes an object.
# bash 3.2 compatible (the callers are exercised on macOS operator boxes).
# shellcheck shell=bash

_SEAT_IMAGE_GUARD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEAT_IMAGE_CHECKER="${_SEAT_IMAGE_GUARD_DIR}/../check-config-against-image.mjs"

# One image serves every seat and skills arrive separately (fleet release,
# 2026-10-19), so a seat's capabilities are its CURRENT image's manifest plus its
# CURRENT skills release.

_seat_d1_one() { # <sql> -> first column of the first row, or nothing
  npx --quiet wrangler d1 execute ss-console-db --remote --json --command "$1" 2>/dev/null |
    node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{try{const r=JSON.parse(s);const row=(r[0]&&r[0].results||[])[0]||{};const v=Object.values(row)[0];process.stdout.write(typeof v==='string'?v:'')}catch{}})" || true
}

# seat_image_manifest <slug> <dest>: with FLEET_IMAGE_LOOKUP=1, read the seat's
# heartbeat image_ref from D1 fleet_status, resolve its digest (from the ref, or
# the fleet_releases row for a tag-only ref), and fetch
# fleet/images/<digest-hex>/image-manifest.json, the one `fleet build` read out of
# that image. Any miss falls back to vaults/<slug>/image-manifest.json. Prints
# nothing; leaves <dest> absent when neither exists.
seat_image_manifest() {
  local slug="$1" dest="$2" ref digest=""
  if [[ "${FLEET_IMAGE_LOOKUP:-}" == "1" ]]; then
    ref="$(_seat_d1_one "SELECT image_ref FROM fleet_status WHERE customer_slug = '${slug}'")"
    if [[ "$ref" =~ @(sha256:[0-9a-f]{64})$ ]]; then
      digest="${BASH_REMATCH[1]}"
    elif [[ "$ref" =~ ^[a-z0-9./:-]+$ && -n "$ref" ]]; then
      digest="$(_seat_d1_one "SELECT image_digest FROM fleet_releases WHERE kind = 'image' AND image_ref = '${ref}' ORDER BY created_at DESC LIMIT 1")"
    fi
    if [[ "$digest" =~ ^sha256:([0-9a-f]{64})$ ]] &&
      r2 s3 cp "s3://${R2_BUCKET_CONFIG}/fleet/images/${BASH_REMATCH[1]}/image-manifest.json" "$dest" --only-show-errors 2>/dev/null; then
      return 0
    fi
  fi
  r2 s3 cp "s3://${R2_BUCKET_CONFIG}/vaults/${slug}/image-manifest.json" "$dest" --only-show-errors 2>/dev/null || rm -f "$dest"
}

# seat_skills_manifest <slug> <dest>: the skills release the seat's pointer
# (vaults/<slug>/skills-pointer.json, written only by `fleet release --skills`)
# names. No pointer: <dest> left absent, and the image alone counts. A pointer
# outside the fleet/skills/ key space is ignored, never followed.
seat_skills_manifest() {
  local slug="$1" dest="$2" mkey
  r2 s3 cp "s3://${R2_BUCKET_CONFIG}/vaults/${slug}/skills-pointer.json" "${dest}.pointer" --only-show-errors 2>/dev/null || return 0
  mkey="$(node -e "try{const p=JSON.parse(require('fs').readFileSync(process.argv[1],'utf8'));process.stdout.write(String(p.manifest_key||''))}catch{}" "${dest}.pointer")"
  [[ "$mkey" =~ ^fleet/skills/[0-9]{12}-[0-9a-f]{8}\.json$ ]] || return 0
  r2 s3 cp "s3://${R2_BUCKET_CONFIG}/${mkey}" "$dest" --only-show-errors 2>/dev/null || rm -f "$dest"
}

# seat_image_guard <slug> <cfg> <work_dir> [<published>]
#
# <published> is the object the seat boots from NOW, if the caller already holds
# a copy (the reconciler downloaded it to compare bytes). Omitted, it is fetched.
# A missing or unreadable manifest is "cannot determine": the checker refuses
# unless <cfg> adds no requirement over <published>, so a removal (the fix-forward
# for a skew) never waits on a rebuild, and nothing new rides an unknown image.
seat_image_guard() {
  local slug="$1" cfg="$2" work_dir="$3" published="${4:-}"
  local manifest="${work_dir}/${slug}.image-manifest.json"
  local skills_manifest="${work_dir}/${slug}.skills-manifest.json"
  seat_image_manifest "$slug" "$manifest"
  [[ -f "$manifest" ]] || manifest=""
  seat_skills_manifest "$slug" "$skills_manifest"
  [[ -f "$skills_manifest" ]] || skills_manifest=""
  if [[ -z "$published" ]]; then
    published="${work_dir}/${slug}.published.yaml"
    r2 s3 cp "s3://${R2_BUCKET_CONFIG}/vaults/${slug}/customer.yaml" "$published" \
      --only-show-errors 2>/dev/null || published=""
  fi
  node "$SEAT_IMAGE_CHECKER" "$cfg" "$manifest" "$published" "$skills_manifest"
}
