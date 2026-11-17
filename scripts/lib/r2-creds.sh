#!/usr/bin/env bash
# r2-creds.sh: the one home of how a laptop or CI shell reaches the config
# bucket over R2's S3 API. Sourced, never executed; defines functions and the
# bucket default only, so sourcing it sets no R2_* and exports nothing until a
# function is called. bash 3.2 compatible
# (operator laptops are macOS) and safe under `set -u`.
#
#   derive_r2_creds      sets R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY and
#                        R2_ENDPOINT_URL (not exported) and exports AWS_REGION=auto
#   export_r2_creds      derive_r2_creds, then exports those three, for a CI step
#                        whose child (operator/bin/fleet) reads them itself
#   r2 <aws args...>     the aws CLI against R2, credentials on that call only
#   r2_bucket            the config bucket: R2_BUCKET_CONFIG, else its default
#   r2_uri <key>         s3://<config bucket>/<key>
#   r2_export_bucket     exports R2_BUCKET_CONFIG (the default when unset), so a
#                        child script sees the same bucket as its parent
#   r2_first_unset_cred  prints the first unset name of the three and returns 0;
#                        returns 1 when all three are set
#
# R2_* already set (the operator-local path, `infisical run --env=prod
# --path=/ss -- ...`): used as they are. Otherwise derived from
# CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID, which CI already holds: R2's S3
# API accepts a Cloudflare API token directly, the key id being the token's own
# id and the secret the sha256 of the token value. Never echoed, never logged.
# operator/bin/lib/fleet/r2.py is the Python twin of this file.
# shellcheck shell=bash

_R2_CONFIG_BUCKET_DEFAULT="smd-customer-config"

derive_r2_creds() {
  if [[ -z "${R2_ACCESS_KEY_ID:-}" || -z "${R2_SECRET_ACCESS_KEY:-}" ]]; then
    : "${CLOUDFLARE_API_TOKEN:?R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY unset and CLOUDFLARE_API_TOKEN not available to derive them}"
    local token_id token_digest
    token_id=$(curl -sS -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      https://api.cloudflare.com/client/v4/user/tokens/verify 2>/dev/null |
      node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{try{const r=JSON.parse(s);process.stdout.write(r.success&&r.result&&r.result.id?r.result.id:'')}catch{process.stdout.write('')}})" || true)
    if [[ -z "$token_id" ]]; then
      echo "::error::could not derive the R2 key id from CLOUDFLARE_API_TOKEN (/user/tokens/verify did not return a token id)"
      exit 1
    fi
    if command -v sha256sum >/dev/null 2>&1; then
      token_digest=$(printf %s "${CLOUDFLARE_API_TOKEN}" | sha256sum | awk '{print $1}')
    else
      token_digest=$(printf %s "${CLOUDFLARE_API_TOKEN}" | shasum -a 256 | awk '{print $1}')
    fi
    R2_ACCESS_KEY_ID="$token_id"
    R2_SECRET_ACCESS_KEY="$token_digest"
  fi
  if [[ -z "${R2_ENDPOINT_URL:-}" ]]; then
    : "${CLOUDFLARE_ACCOUNT_ID:?R2_ENDPOINT_URL unset and CLOUDFLARE_ACCOUNT_ID not available to build it}"
    R2_ENDPOINT_URL="https://${CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com"
  fi
  # R2 signs in region "auto"; an AWS region the runner sets (us-west-2) makes
  # every call 400 Bad Request. Exported so the aws CLI and fleet both see it.
  export AWS_REGION=auto AWS_DEFAULT_REGION=auto
}

export_r2_creds() {
  derive_r2_creds
  export R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_ENDPOINT_URL
}

r2() {
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws "$@" --endpoint-url "${R2_ENDPOINT_URL}"
}

r2_bucket() { printf '%s' "${R2_BUCKET_CONFIG:-${_R2_CONFIG_BUCKET_DEFAULT}}"; }

r2_uri() { printf 's3://%s/%s' "$(r2_bucket)" "$1"; }

r2_export_bucket() {
  R2_BUCKET_CONFIG="$(r2_bucket)"
  export R2_BUCKET_CONFIG
}

r2_first_unset_cred() {
  local name
  for name in R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_ENDPOINT_URL; do
    if [[ -z "${!name:-}" ]]; then
      printf '%s' "$name"
      return 0
    fi
  done
  return 1
}
