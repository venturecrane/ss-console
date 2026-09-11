#!/usr/bin/env bash
# Rotate one seat's Machine credential without a reprovision (migration 0114).
#
# WHAT IT DOES. Mints a fresh per-seat key with lib/machine_credential.py,
# upserts its hash into the console's machine_credentials table (the previous
# hash is kept as prev_* for 24h), proves the row exists, then sets the
# plaintext as the seat's MACHINE_HEARTBEAT_KEY Fly secret. `fly secrets
# import` WITHOUT --stage applies immediately, which RESTARTS the Machine;
# that is why this script exists separately from provision-customer.sh and
# why running it against a live seat needs the Captain's go for that turn.
#
# ORDER IS SAFE BY CONSTRUCTION. The D1 write lands first, and the Worker
# honours both the new and the previous key until prev_expires_at, so a seat
# that is still on the old key between the two steps keeps authenticating.
#
# WHEN TO RUN IT.
#   * First per-seat rotation of a seat provisioned before 0114 (it is still
#     on the fleet-wide shared key). Run it once per live seat; when every
#     shipped seat has a row, unset MACHINE_HEARTBEAT_KEY on the ss-web Worker
#     and the shared key is retired.
#   * Any time a seat's key is suspected exposed.
#
# The plaintext is never printed, never passed as argv, never logged.
#
# Usage: operator/bin/rotate-machine-credential.sh <slug>

set -euo pipefail

SLUG="${1:-}"
if [ -z "${SLUG}" ]; then
  echo "usage: $0 <slug>" >&2
  exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
APP_NAME="hermes-${SLUG}"
DB="ss-console-db"

log() { printf '[rotate-machine-credential] %s\n' "$*" >&2; }

if ! fly apps list --json 2>/dev/null | python3 -c 'import json, sys; apps={a["Name"] for a in json.load(sys.stdin)}; sys.exit(0 if sys.argv[1] in apps else 1)' "${APP_NAME}"; then
  log "FATAL: Fly app ${APP_NAME} not found (or fly is not logged in); nothing rotated"
  exit 1
fi

log "Minting a new credential for ${SLUG}..."
MK_SQL="$(mktemp)"
trap 'rm -f "${MK_SQL}"' EXIT
if ! MK_KEY="$(python3 "${HERE}/lib/machine_credential.py" --slug "${SLUG}" --sql-out "${MK_SQL}")"; then
  log "FATAL: mint failed (see stderr above)"
  exit 1
fi

log "Upserting the hash into ${DB}.machine_credentials (previous key stays valid 24h)..."
if ! MK_ERR=$( cd "${REPO_ROOT}" && npx --quiet wrangler d1 execute "${DB}" --remote --file "${MK_SQL}" 2>&1 >/dev/null ); then
  log "FATAL: upsert failed; the seat's current key is untouched"
  log "  wrangler stderr: ${MK_ERR}"
  exit 1
fi
MK_COUNT=$( cd "${REPO_ROOT}" && npx --quiet wrangler d1 execute "${DB}" --remote --json \
  --command "SELECT COUNT(*) AS n FROM machine_credentials WHERE customer_slug = '${SLUG}'" 2>/dev/null \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)[0]["results"][0]["n"])' 2>/dev/null || echo "?")
if [ "${MK_COUNT}" != "1" ]; then
  log "FATAL: no machine_credentials row for ${SLUG} (count=${MK_COUNT}); the console has no customer_configs projection for it"
  exit 1
fi

log "Setting MACHINE_HEARTBEAT_KEY on ${APP_NAME} (this restarts the Machine)..."
printf 'MACHINE_HEARTBEAT_KEY=%s\n' "${MK_KEY}" | fly secrets import -a "${APP_NAME}" >/dev/null
unset MK_KEY
log "Rotated. Watch the next heartbeat on the admin fleet view; the Worker log line"
log "'[machine-key] shared-key fallback used' must NOT appear for ${SLUG} any more."
