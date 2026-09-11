#!/bin/bash
#
# Reflex Primer Hook (UserPromptSubmit) -- the always-on doctrine surface.
#
# Emits the operating-law primer lines (registry: fourteen laws as of
# 2026-09-10) into context on every user prompt. Ported from crane-console's redirect-reflex-hook.sh (v2 always-on:
# pattern-matching redirect language proved too brittle to build a forcing
# function on; the laws are universal, so the primer fires universally).
#
# Source of truth: docs/doctrine/agent-operating-doctrine.md. Each line below
# is a law's canonical `primer_line` and MUST match it verbatim --
# tests/doctrine-integrity.test.ts pins the parity, so editing one without
# the other fails `npm run verify`. A correction that changes a law updates
# both files in the same PR (the doctrine maintenance contract).
#
# Incident: 2026-07-26 Christa-reply session (context unloaded, verb
# inflated, terms invented, ignorance reported as findings). Cost/exit
# review: 2026-09-30 per the doctrine's mechanisms-under-review block.
#
# Wire protocol:
#   stdin:  JSON with .prompt, .cwd, .session_id (Claude Code hook contract)
#   stdout: lines below become additional context for the next turn
#   exit 0 always; never block the Captain on hook plumbing.

set -e

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# Capture stdin ONCE. The payload is needed three times (.prompt, .cwd,
# .session_id) and stdin cannot be re-read, so a bare `jq` here would consume
# it and starve the staleness block below.
PAYLOAD=$(cat 2>/dev/null) || exit 0
# `.prompt` is what this harness sends and is load-bearing; `.prompt_text`
# appears in the published hook reference. Accept either so a field rename
# cannot silently reduce the primer to a no-op.
PROMPT=$(printf '%s' "$PAYLOAD" | jq -r '.prompt // .prompt_text // empty' 2>/dev/null) || exit 0
[ -z "$PROMPT" ] && exit 0

# The session's real tree, computed once and shared by the doctrine, board,
# and freshener blocks below. Payload .cwd first for the same reason as
# everywhere else in this file: CLAUDE_PROJECT_DIR is pinned at launch and
# does not follow EnterWorktree.
TREE_EARLY=$(printf '%s' "$PAYLOAD" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$TREE_EARLY" ] && [ -d "$TREE_EARLY" ] || TREE_EARLY="$PWD"
ROOT_EARLY=$(git -C "$TREE_EARLY" rev-parse --show-toplevel 2>/dev/null || true)
HOOK_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)

# --- Doctrine emission: fresh from origin/main, heredoc as fallback ---------
#
# Incident 2026-08-01: Law 11 merged to main at 18:37 and was absent from a
# session started that same evening -- both the primary checkout and every
# worktree predate any given merge, so a law could ship and never reach the
# sessions having the problem it named. The shared origin/main REF does not
# have that problem: any session's fetch refreshes it for all of them. So the
# laws are served from `git show origin/main:` when that parses sanely,
# labeled with their commit and age (stale-but-labeled is acceptable;
# stale-and-silent is not), and the heredoc below is the fallback -- kept
# verbatim in sync with the doctrine by tests/doctrine-integrity.test.ts and
# the maintenance contract, exercised whenever extraction fails.
emit_laws() {
  set +e
  local extractor fresh meta
  extractor="$HOOK_DIR/lib/extract-primer-lines.mjs"
  [ -f "$extractor" ] || extractor="$ROOT_EARLY/.claude/hooks/lib/extract-primer-lines.mjs"
  if [ -n "$ROOT_EARLY" ] && [ -f "$extractor" ] && command -v node >/dev/null 2>&1; then
    fresh=$(git -C "$ROOT_EARLY" show origin/main:docs/doctrine/agent-operating-doctrine.md 2>/dev/null \
      | node "$extractor" 2>/dev/null)
    if [ -n "$fresh" ]; then
      meta=$(git -C "$ROOT_EARLY" log -1 --format='%h, %cr' origin/main -- docs/doctrine/agent-operating-doctrine.md 2>/dev/null)
      echo "[doctrine] Operating laws (origin/main${meta:+ @ ${meta}}):"
      printf '%s\n' "$fresh"
      return 0
    fi
  fi
  # Fallback: the judgment laws (tier primer/radar) verbatim, gate-tier laws
  # compressed to the pointer line -- the same rendering the extractor
  # produces, pinned to the doctrine by tests/doctrine-integrity.test.ts.
  cat <<'PRIMER'
[doctrine] Operating laws (docs/doctrine/agent-operating-doctrine.md):
1. Resolve whose call it is before acting: agents execute, the Captain owns strategy, commitments, and spend, clients author their own posture. Never default-claim or default-defer.
3. The verb is the scope: "review X" or "let's review X" delivers exactly the text of X plus "what would you like to discuss", nothing volunteered; verdicts only under an evaluating ask, edits only under an editing verb. Never edit Captain-authored client documents unasked.
4. A gap in your context is a question, not a finding. Never report your own ignorance as a defect; never fill it with plausible content.
8. Finish or say why: no stopping-point offers, no hedging finished work as draft, no relitigating settled calls.
10. Your snapshot is not the system. Tree state, branch lists, merged PRs, and installed dependencies decay within minutes of the briefing that reported them. Re-probe before acting on any of them.
12. A check that cannot fail has measured nothing. Before reporting an observation, name what would have made it false and confirm your instrument would have shown it.
13. Do the work, do not file it: work found mid-task gets done now unless it is blocked on something that does not exist yet or needs a Captain decision. Standing target is zero open issues.
14. A program's report about the world is a claim: a manifest, a completion, a wired map, or a done flag is proven by reading the world back after acting, never by echoing the statement that ran. Ask what the report would say if the action had matched nothing.
Gate-enforced laws (mechanisms, not memory -- registry has the prose): 2 load-before-touch, 5 claims-trace, 6 authored-voice, 7 blast-radius, 9 deliverable-is-the-act, 11 signal-not-volume.
PRIMER
}
emit_laws || true

# --- Ref freshener ----------------------------------------------------------
#
# The doctrine block above (and Law 10's behind-count) are only as fresh as
# the last fetch. Per-turn fetching is not affordable across 4-8 sessions, so
# this fires a BACKGROUND fetch at most every 30 minutes, gated on
# FETCH_HEAD's mtime. All three fds are detached: a background child that
# inherits this hook's stdout keeps the pipe open and hangs the harness's
# read of the primer.
freshen_refs() {
  set +e
  [ -n "$ROOT_EARLY" ] || return 0
  local gitdir fh
  gitdir=$(git -C "$ROOT_EARLY" rev-parse --git-common-dir 2>/dev/null)
  [ -n "$gitdir" ] || return 0
  case "$gitdir" in /*) ;; *) gitdir="$ROOT_EARLY/$gitdir" ;; esac
  fh="$gitdir/FETCH_HEAD"
  if [ ! -f "$fh" ] || [ -n "$(find "$fh" -mmin +30 2>/dev/null)" ]; then
    # Touch first so concurrent sessions and fetch-failure loops (fixture
    # repos with no origin) cannot spawn a fetch per turn.
    touch "$fh" 2>/dev/null
    ( git -C "$ROOT_EARLY" fetch origin main --quiet </dev/null >/dev/null 2>&1 & )
  fi
}
freshen_refs || true

# --- Mission + board --------------------------------------------------------
#
# The session's mission line, re-injected every turn so it survives /compact
# and 8-hour drift (2026-08-01 autopsy: "state the mission, accomplishments,
# and next steps" asked 8+ times across sessions is the Captain doing this
# job by hand). Then every live peer's mission line: 4-8 concurrent sessions
# were mutually blind past session start, which is how two of them built the
# same featureset in parallel. No collision matcher -- the model reads the
# lines and judges overlap, which is exactly the judgment call it can make
# when the evidence is in front of it every turn.
board_block() {
  set +e
  [ -n "$ROOT_EARLY" ] || return 0
  command -v node >/dev/null 2>&1 || return 0
  local lib
  lib="$HOOK_DIR/lib/board.mjs"
  [ -f "$lib" ] || lib="$ROOT_EARLY/.claude/hooks/lib/board.mjs"
  [ -f "$lib" ] || return 0
  node "$lib" refresh "$ROOT_EARLY" 2>/dev/null
  node "$lib" peers "$ROOT_EARLY" 2>/dev/null
}
board_block || true

# Law 2 escape-hatch visibility.
#
# SS_ALLOW_UNREAD_ENGAGEMENT_WRITES bypasses the engagement read gate. It is
# path-scoped and audited by engagement-guard.mjs, but an exported hatch is
# still invisible in the moment it matters most -- the session that has it set
# is the session that never sees the gate. Surfacing the recent count here
# turns a quiet permanent bypass into something stated on every prompt.
HATCH_LOG="${SS_HATCH_AUDIT_FILE:-$HOME/.claude/ss-hatch-audit.log}"
if [ -f "$HATCH_LOG" ]; then
  CUTOFF=$(date -u -v-7d +%Y-%m-%d 2>/dev/null || date -u -d '7 days ago' +%Y-%m-%d 2>/dev/null)
  if [ -n "$CUTOFF" ]; then
    RECENT=$(awk -v c="$CUTOFF" -F'\t' '$1 >= c' "$HATCH_LOG" 2>/dev/null | wc -l | tr -d ' ')
    if [ "${RECENT:-0}" -gt 0 ]; then
      echo "[doctrine] Law 2 escape hatch used ${RECENT}x in the last 7 days (${HATCH_LOG}). If it is exported permanently, unset it."
    fi
  fi
fi

# Law 10 surface: your snapshot is not the system.
#
# The gitStatus block, branch list, and dependency state seeded into a
# session's context are captured ONCE at session start. With concurrent
# sessions merging into main every ~25 minutes, they are wrong within
# minutes, and an agent reasoning carefully from them is still wrong.
# Session-start detection cannot close this: session start is exactly when
# the answer is still right. So the check runs every turn.
#
# Incident 2026-07-31: sync-primary.sh fast-forwarded the primary across a
# major-version lockfile change on behalf of a NEWLY starting session,
# invalidating node_modules for sessions already running in that tree three
# hours after their briefing rendered "Deps | current". Six of seven
# checkouts ended the day running a stale toolchain against new source.
#
# Three invariants this block must never violate:
#   1. It must never kill the primer. The script runs `set -e`, and a failing
#      `git rev-parse` or `stat` -- the NORMAL case here -- would propagate.
#      Verified 2026-07-31 with both guards removed: exit 128, and on
#      UserPromptSubmit a non-zero exit forfeits the stdout injection, so the
#      laws are computed and then discarded. Two independent containments are
#      kept, and EITHER ALONE IS SUFFICIENT (invoking as `staleness_block ||
#      true` puts the body in an `||` list, which already suspends `set -e`
#      for it). That redundancy is deliberate, not an oversight to tidy: a
#      later edit that drops one must not silently arm the failure. The block
#      also sits AFTER the heredoc, so even total containment failure leaves
#      the laws already printed. tests/staleness-detection.test.ts asserts
#      exit 0 and all primer lines on stdout across every failure path.
#   2. It must measure the tree the session is actually in. CLAUDE_PROJECT_DIR
#      is pinned at launch and does not follow EnterWorktree, so preferring it
#      would report on the primary from inside a worktree: this law's own
#      failure, reintroduced by its enforcement, wearing doctrine's authority.
#      Payload .cwd first, matching worktree-guard.mjs:53.
#   3. It must not cry wolf. A line that is sometimes wrong and always loud
#      teaches agents to skim past laws 1 through 10. mtime alone fires on
#      `git stash`, `git restore`, and rebases that rewrite the lockfile
#      without changing it, so mtime only GATES a content comparison.
staleness_block() {
  set +e

  local tree root
  tree=$(printf '%s' "$PAYLOAD" | jq -r '.cwd // empty' 2>/dev/null)
  [ -n "$tree" ] && [ -d "$tree" ] || tree="$PWD"
  [ -d "$tree" ] || tree="${CLAUDE_PROJECT_DIR:-}"
  [ -n "$tree" ] && [ -d "$tree" ] || return 0

  root=$(git -C "$tree" rev-parse --show-toplevel 2>/dev/null)
  [ -n "$root" ] && [ -d "$root" ] || return 0
  local name
  name=$(basename "$root")

  # --- Signal 1: main moved under you --------------------------------------
  # Reads the local origin/main ref; deliberately does NOT fetch, because a
  # network round trip on every turn is not affordable. Concurrent sessions
  # fetch at every start, so the ref stays close to current in practice.
  local behind
  behind=$(git -C "$root" rev-list --count HEAD..origin/main 2>/dev/null)
  if [ -n "$behind" ] && [ "$behind" -gt 0 ] 2>/dev/null; then
    echo "[doctrine] Law 10: origin/main is ${behind} commit(s) ahead of your HEAD (as of the last fetch). Anything you were briefed about main predates them."
  fi

  # --- Signal 2: the working tree moved since you were briefed --------------
  # Baselined per session so this speaks only on CHANGE, not merely on dirt.
  # Without a session id there is no baseline to diff against, so it stays
  # silent rather than guess.
  local sid dirty base_file prev
  sid=$(printf '%s' "$PAYLOAD" | jq -r '.session_id // empty' 2>/dev/null)
  if [ -n "$sid" ]; then
    dirty=$(git -C "$root" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    base_file="${TMPDIR:-/tmp}/ss-staleness-$(printf '%s' "${sid}:${root}" | cksum | cut -d' ' -f1)"
    if [ -f "$base_file" ]; then
      prev=$(cat "$base_file" 2>/dev/null)
      if [ -n "$prev" ] && [ -n "$dirty" ] && [ "$prev" != "$dirty" ]; then
        echo "[doctrine] Law 10: working tree changed since session start (${prev} path(s) then, ${dirty} now). The gitStatus in your system prompt is out of date; re-read it before reasoning about the tree."
      fi
    else
      printf '%s' "${dirty:-0}" >"$base_file" 2>/dev/null
    fi
  fi

  # --- Signal 3: dependencies no longer match the lockfile ------------------
  local lock rec
  lock="$root/package-lock.json"
  rec="$root/node_modules/.package-lock.json"
  [ -f "$lock" ] || return 0

  if [ ! -d "$root/node_modules" ]; then
    echo "[doctrine] Law 10: ${name} has no node_modules. Run npm ci before trusting build, typecheck, or test results."
    return 0
  fi
  if [ ! -f "$rec" ]; then
    echo "[doctrine] Law 10: ${name} has node_modules but no install record; an install may be in flight. Do not start a second one."
    return 0
  fi

  # Cheap gate: is the lockfile newer than npm's install record?
  #
  # `find -newer` rather than `stat`, because stat's mtime flag is not
  # portable and fails UNSAFELY. BSD spells it `stat -f %m`; GNU spells it
  # `stat -c %Y`. Writing `stat -f %m ... || stat -c %Y ...` looks like a
  # correct fallback and is not: on GNU, `-f` means "display filesystem
  # status", so it SUCCEEDS with filesystem information instead of failing,
  # the `||` branch never runs, and the arithmetic silently gives up. Caught
  # by CI on 2026-07-31 after passing on macOS, which is the same
  # works-on-my-machine failure this law is about.
  #
  # Over-triggering here is harmless: this only gates the content comparison
  # below, which is what actually decides whether to speak.
  [ -n "$(find "$lock" -newer "$rec" 2>/dev/null)" ] || return 0

  # mtime says stale. Confirm against content before speaking: compare only
  # packages present in BOTH files, and only flag version mismatches. Entries
  # absent from the install record are optional platform-specific binaries
  # (141 of them in this repo on darwin) and are NOT drift -- counting them
  # would make this line fire in every checkout, forever.
  command -v node >/dev/null 2>&1 || return 0
  local verdict count example
  verdict=$(node -e '
    const fs = require("fs")
    try {
      const lock = JSON.parse(fs.readFileSync(process.argv[1], "utf8"))
      const rec = JSON.parse(fs.readFileSync(process.argv[2], "utf8"))
      let n = 0, ex = ""
      for (const [k, v] of Object.entries(lock.packages || {})) {
        if (!k.startsWith("node_modules/")) continue
        const i = rec.packages && rec.packages[k]
        if (!i) continue
        if (i.version !== v.version) {
          n++
          if (!ex) ex = k.slice("node_modules/".length) + " " + i.version + " vs " + v.version
        }
      }
      process.stdout.write(n ? n + "|" + ex : "0")
    } catch (e) { process.stdout.write("0") }
  ' "$lock" "$rec" 2>/dev/null)
  count=${verdict%%|*}
  example=${verdict#*|}
  [ -n "$count" ] && [ "$count" != "0" ] 2>/dev/null || return 0
  echo "[doctrine] Law 10: ${name} dependencies are stale; ${count} package version(s) differ from package-lock.json (e.g. ${example}). Run npm ci before trusting build, typecheck, or test results."
}
staleness_block || true

exit 0
