#!/usr/bin/env bash
# build-source-guard.sh: refuse to build an image from a tree that is not the
# code you reviewed. Extracted from provision-customer.sh (fleet release,
# 2026-10-19) so `fleet build` refuses exactly what a from-source provision
# refuses. Two ways in:
#
#   SOURCED (provision-customer.sh): call assert_build_source_is_current with
#   REPO_ROOT, TEMPLATE_DIR, log and die already defined by the caller.
#
#   EXECUTED (fleet build): run build-source-guard.sh <repo-root>; exits non-zero
#   with the refusal on stderr.
#
# Escape hatch is the same as ever: SS_ALLOW_DIVERGENT_SOURCE=1.
# shellcheck shell=bash

# REPO_ROOT is derived from THIS SCRIPT'S OWN LOCATION, so the image is built
# from whichever checkout you invoked — not from a canonical one. That is easy
# to state and easy to forget, because runbooks, muscle memory, and shell
# history all say `~/dev/ss-console/operator/bin/reprovision.sh` while an
# agent's verified work is usually sitting in a worktree under
# `.claude/worktrees/`.
#
# WHAT THIS COSTS WHEN IT GOES WRONG (2026-07-31). The primary checkout sat two
# commits behind origin/main carrying thirty staged entries that reverted a
# whole merged programme — the config publisher, both CI guards, a migration,
# the Dockerfile and entrypoint changes — with OVERLAY_REF still on the previous
# pin. A reprovision in that state builds an image containing none of the work,
# pins the wrong overlay, and EXITS ZERO. Every observation taken afterwards is
# then a true statement about the wrong artifact, which is worse than a failure,
# because a failure gets investigated and a green run gets believed.
#
# So: refuse by default, and print the resolved source on every run so a
# reprovision can never leave doubt about which tree produced the image.
# Escape hatch is deliberate, named, and loud — the same shape as
# SS_ALLOW_PRIMARY_WRITES and SS_ALLOW_UNREAD_ENGAGEMENT_WRITES.
assert_build_source_is_current() {
  log "Build source: ${REPO_ROOT}"

  if ! git -C "${REPO_ROOT}" rev-parse --git-dir >/dev/null 2>&1; then
    log "WARN: ${REPO_ROOT} is not a git checkout; source currency cannot be verified"
    return 0
  fi

  local head_sha upstream_sha dirty behind
  head_sha="$(git -C "${REPO_ROOT}" rev-parse --short HEAD)"
  log "Build source HEAD: ${head_sha}"

  # A stale index reverting merged work is exactly the incident above, and it
  # presents as ordinary dirt. Report the paths — "dirty" alone sends people
  # looking for their own edits rather than at a damaged index.
  #
  # `.claude/` is excluded, and ONLY `.claude/`. It holds agent session markers
  # and the worktrees themselves, so it is dirty in essentially every working
  # checkout; it is in `.dockerignore` and no COPY in the Dockerfile names it,
  # so it cannot reach the image this guard protects. On the guard's FIRST real
  # use it refused a rebuild over a lone `parallel-isolation-required-<uuid>`
  # marker (#2101). That matters more than the nuisance: a guard that trips on
  # ordinary working conditions teaches people to reach for
  # SS_ALLOW_DIVERGENT_SOURCE by reflex, and a reflexive bypass is worse than no
  # guard — it is one everybody believes is protecting them while it is waved
  # through unread. Reaching for the flag has to stay a decision.
  #
  # Narrow ON PURPOSE. The tempting widening is "exclude whatever .dockerignore
  # excludes", which is wrong: an untracked source file elsewhere in the tree
  # genuinely can change the image, and .dockerignore also excludes paths whose
  # presence in git still matters. `.claude/` is the one directory that is
  # session state by definition — the repo already treats it specially, since
  # .claude/hooks/worktree-guard.mjs exempts it from the read-only primary rule
  # for exactly this reason.
  #
  # The pattern anchors to porcelain's `XY ` prefix (two status chars plus a
  # space) so it matches a top-level `.claude/` and not some nested
  # `src/.claude/` that would be a genuine surprise worth refusing.
  # `|| true` is load-bearing: grep exits 1 when it emits nothing, which under
  # `set -euo pipefail` aborts the script on a CLEAN tree — the guard would kill
  # every well-formed build while letting dirty ones through to the next check.
  dirty="$(git -C "${REPO_ROOT}" status --porcelain 2>/dev/null | grep -v '^...\.claude/' || true)"
  dirty="$(printf '%s' "${dirty}" | head -20)"
  if [ -n "${dirty}" ] && [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" != "1" ]; then
    echo "${dirty}" >&2
    die "build source ${REPO_ROOT} has uncommitted changes (above). An image built from a \
dirty tree is not the code you reviewed. Commit, stash, or reset it — or set \
SS_ALLOW_DIVERGENT_SOURCE=1 if you deliberately mean to build from these exact bytes."
  fi

  # Non-fatal: an offline operator should not be blocked, but they should know
  # the comparison below is against whatever origin/main was last fetched.
  git -C "${REPO_ROOT}" fetch origin --quiet 2>/dev/null \
    || log "WARN: could not fetch origin; comparing against the last-known origin/main"

  if git -C "${REPO_ROOT}" rev-parse --verify origin/main >/dev/null 2>&1; then
    upstream_sha="$(git -C "${REPO_ROOT}" rev-parse --short origin/main)"
    behind="$(git -C "${REPO_ROOT}" rev-list --count HEAD..origin/main 2>/dev/null || echo 0)"
    if [ "${behind}" != "0" ] && [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" != "1" ]; then
      die "build source is ${behind} commit(s) behind origin/main (HEAD ${head_sha}, \
origin/main ${upstream_sha}). Whatever landed in those commits will NOT be in this image. \
Update it, or set SS_ALLOW_DIVERGENT_SOURCE=1 to build this ref on purpose."
    fi
  fi

  # No overlay pin to cross-check since the fold (ADR 0095): the overlay is
  # operator/hermes-smd-overlay in this same tree, so the currency checks above
  # cover it exactly as they cover every other file the image is built from.

  if [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" = "1" ]; then
    log "WARN: SS_ALLOW_DIVERGENT_SOURCE=1 — source currency checks bypassed BY REQUEST. \
The image is being built from ${REPO_ROOT} at ${head_sha} exactly as it stands."
  fi
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -euo pipefail
  REPO_ROOT="$(cd "${1:?Usage: build-source-guard.sh <repo-root>}" && pwd)"
  log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [build-source] $*" >&2; }
  die() { log "FATAL: $*"; exit 1; }
  assert_build_source_is_current
fi
