#!/usr/bin/env bash
# r2-creds.sh: R2 S3 credentials for a CI job, without minting a credential.
# Sourced; defines derive_r2_creds, which sets R2_ACCESS_KEY_ID,
# R2_SECRET_ACCESS_KEY and R2_ENDPOINT_URL, and exports AWS_REGION=auto.
#
# R2_* already set (the operator-local path, `infisical run --env=prod
# --path=/ss -- ...`): used as they are. Otherwise derived from
# CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID, which CI already holds: R2's S3
# API accepts a Cloudflare API token directly, the key id being the token's own
# id and the secret the sha256 of the token value. The same derivation
# ci-publish-customer-configs.sh carries inline. Never echoed, never logged.
# shellcheck shell=bash

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
