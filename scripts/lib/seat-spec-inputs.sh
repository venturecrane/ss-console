#!/usr/bin/env bash
# seat-spec-inputs.sh: resolve what a seat runs NOW, then compile its spec
# (scripts/compile-seat-spec.ts). Sourced, never executed. Read-only against
# R2: nothing here writes an object.
#
# Defines:
#   r2_fetch_optional <key> <dest>        0 fetched, 1 absent (404), 2 could not tell
#   seat_spec_inputs <slug> <dir>         fetch every input into <dir>; 0, or 2 on a read
#                                         that failed for a reason other than 404
#   seat_spec_compile <slug> <cfg> <dir> <out> [--sign]
#                                         the compiler's exit code (see its header)
#
# WHY ONE IMPLEMENTATION. A seat re-fetches vaults/<slug>/customer.yaml on every
# boot, so whatever writes that object decides what the seat runs at its next
# restart. The image guard first lived only in the merge-time publisher; the
# nightly reconciler, which then also wrote the object, undid its refusals. The
# reconciler now only reads, and the publisher is the one writer, but both
# judge a seat through this file and the one compiler, so the verdict a night
# reports is the verdict a merge would act on.
#
# WHAT IS FETCHED, per seat, from the image it runs now (one image serves every
# seat; skills and firm inputs arrive separately):
#   image-manifest.json, image-capabilities.json, lane-inputs.json
#       from fleet/images/<digest-hex>/ when FLEET_IMAGE_LOOKUP=1 resolves the
#       seat's heartbeat image_ref through D1 (wrangler); the manifest falls back
#       to vaults/<slug>/image-manifest.json, which the entrypoint uploads at boot.
#   skills-pointer.json and the skills release it names (fleet/skills/ only).
#   published.yaml      the customer.yaml the seat boots from now.
#   spec-pointer.json and the spec it names (fleet/specs/<slug>/ only).
#   <lane>.firm         each lane's firm-input manifest, HASHED by the compiler
#                       and never copied anywhere: firm inputs are private.
#
# Contract with the caller: an `r2` function running `aws "$@" --endpoint-url
# ...` with R2 creds, and R2_BUCKET_CONFIG set.
# bash 3.2 compatible (the callers are exercised on macOS operator boxes).
# shellcheck shell=bash

_SEAT_SPEC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEAT_SPEC_COMPILER="${_SEAT_SPEC_DIR}/../compile-seat-spec.ts"

_seat_tsx() {
  local bin="${_SEAT_SPEC_DIR}/../../node_modules/.bin/tsx"
  if [[ -x "$bin" ]]; then "$bin" "$@"; else npx --quiet tsx "$@"; fi
}

_seat_d1_one() { # <sql> -> first column of the first row, or nothing
  npx --quiet wrangler d1 execute ss-console-db --remote --json --command "$1" 2>/dev/null |
    node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{try{const r=JSON.parse(s);const row=(r[0]&&r[0].results||[])[0]||{};const v=Object.values(row)[0];process.stdout.write(typeof v==='string'?v:'')}catch{}})" || true
}

_json_field() { # <file> <field> -> the string value, or nothing
  node -e "try{const p=JSON.parse(require('fs').readFileSync(process.argv[1],'utf8'));const v=p[process.argv[2]];process.stdout.write(typeof v==='string'?v:'')}catch{}" "$1" "$2"
}

r2_fetch_optional() {
  local key="$1" dest="$2" err
  err="$(mktemp)"
  if r2 s3api head-object --bucket "${R2_BUCKET_CONFIG}" --key "$key" >/dev/null 2>"$err"; then
    rm -f "$err"
    r2 s3 cp "s3://${R2_BUCKET_CONFIG}/${key}" "$dest" --only-show-errors 2>/dev/null && return 0
    rm -f "$dest"
    return 2
  fi
  if grep -q '(404)' "$err"; then rm -f "$err"; return 1; fi
  sed 's/^/    /' "$err" >&2
  rm -f "$err"
  return 2
}

# The digest hex of the image the seat's heartbeat reports, or nothing.
seat_image_digest() {
  local slug="$1" ref digest=""
  [[ "${FLEET_IMAGE_LOOKUP:-}" == "1" ]] || return 0
  ref="$(_seat_d1_one "SELECT image_ref FROM fleet_status WHERE customer_slug = '${slug}'")"
  if [[ "$ref" =~ @(sha256:[0-9a-f]{64})$ ]]; then
    digest="${BASH_REMATCH[1]}"
  elif [[ "$ref" =~ ^[a-z0-9./:-]+$ && -n "$ref" ]]; then
    digest="$(_seat_d1_one "SELECT image_digest FROM fleet_releases WHERE kind = 'image' AND image_ref = '${ref}' ORDER BY created_at DESC LIMIT 1")"
  fi
  [[ "$digest" =~ ^sha256:([0-9a-f]{64})$ ]] && printf '%s' "${BASH_REMATCH[1]}"
  return 0
}

# Optional objects whose absence and whose failure mean the same thing to the
# compiler ("not available"), because "cannot determine" never passes there.
_fetch_soft() { r2 s3 cp "s3://${R2_BUCKET_CONFIG}/$1" "$2" --only-show-errors 2>/dev/null || rm -f "$2"; }

_fetch_image_inputs() {
  local slug="$1" dir="$2" hex f
  hex="$(seat_image_digest "$slug")"
  if [[ -n "$hex" ]]; then
    for f in image-manifest.json image-capabilities.json lane-inputs.json; do
      _fetch_soft "fleet/images/${hex}/${f}" "${dir}/${f}"
    done
  fi
  [[ -f "${dir}/image-manifest.json" ]] || _fetch_soft "vaults/${slug}/image-manifest.json" "${dir}/image-manifest.json"
}

_fetch_skills_inputs() {
  local slug="$1" dir="$2" mkey
  _fetch_soft "vaults/${slug}/skills-pointer.json" "${dir}/skills-pointer.json"
  [[ -f "${dir}/skills-pointer.json" ]] || return 0
  mkey="$(_json_field "${dir}/skills-pointer.json" manifest_key)"
  # A pointer outside the fleet/skills/ key space is ignored, never followed.
  if [[ ! "$mkey" =~ ^fleet/skills/[0-9]{12}-[0-9a-f]{8}\.json$ ]]; then
    rm -f "${dir}/skills-pointer.json"
    return 0
  fi
  _fetch_soft "$mkey" "${dir}/skills-manifest.json"
}

# The spec pointer and the spec it names. A read that fails other than by 404
# is "could not tell", which the callers treat as a hard error: the publisher
# must not mistake an unreadable pointer for "no spec yet" and the reconciler
# must not report a seat clean on one.
_fetch_spec_inputs() {
  local slug="$1" dir="$2" rc=0 skey
  r2_fetch_optional "vaults/${slug}/spec-pointer.json" "${dir}/spec-pointer.json" || rc=$?
  [[ "$rc" -eq 2 ]] && return 2
  [[ "$rc" -eq 1 ]] && return 0
  skey="$(_json_field "${dir}/spec-pointer.json" spec_key)"
  [[ "$skey" =~ ^fleet/specs/${slug}/[0-9a-f]{64}\.json$ ]] || return 0
  rc=0
  r2_fetch_optional "$skey" "${dir}/current-spec.json" || rc=$?
  [[ "$rc" -eq 2 ]] && return 2
  return 0
}

seat_spec_inputs() {
  local slug="$1" dir="$2" rc lane key
  mkdir -p "$dir"
  _fetch_image_inputs "$slug" "$dir"
  _fetch_skills_inputs "$slug" "$dir"
  _fetch_soft "vaults/${slug}/customer.yaml" "${dir}/published.yaml"
  _fetch_spec_inputs "$slug" "$dir" || return 2
  while read -r lane key; do
    [[ -n "$lane" ]] || continue
    rc=0
    r2_fetch_optional "$key" "${dir}/${lane}.firm" || rc=$?
    if [[ "$rc" -eq 2 ]]; then
      echo "::error::${slug}: could not tell whether ${key} exists; refusing to compile a spec that may omit a lane" >&2
      return 2
    fi
  done < <(_seat_tsx "$SEAT_SPEC_COMPILER" lanes --slug "$slug")
  return 0
}

seat_spec_compile() {
  local slug="$1" cfg="$2" dir="$3" out="$4" lane f
  shift 4
  local args=(compile --slug "$slug" --repo "$(pwd)" --config "$cfg" --out "$out")
  for f in image-manifest image-capabilities skills-pointer skills-manifest current-spec; do
    [[ -f "${dir}/${f}.json" ]] && args+=("--${f}" "${dir}/${f}.json")
  done
  [[ -f "${dir}/lane-inputs.json" ]] && args+=(--image-lane-inputs "${dir}/lane-inputs.json")
  [[ -f "${dir}/published.yaml" ]] && args+=(--published "${dir}/published.yaml")
  for f in "${dir}"/*.firm; do
    [[ -f "$f" ]] || continue
    lane="$(basename "$f" .firm)"
    args+=(--lane-pin "${lane}=${f}")
  done
  _seat_tsx "$SEAT_SPEC_COMPILER" "${args[@]}" ${1+"$@"}
}
