#!/usr/bin/env bash
#
# Install this repo's Captain-side skills so Claude Code can find them.
#
# `.agents/skills/` and `.claude/commands/` are gitignored (the enterprise skill
# triplet is mirrored into them from crane-console on every `crane ss` launch).
# A venture-local skill therefore has nowhere tracked to live inside those
# directories, so its authored copy lives at `docs/skills/<name>/SKILL.md` and
# this script symlinks it into place.
#
# Symlinks, not copies, on purpose: a copy is a second file that drifts, and
# nothing would catch the drift because neither destination is tracked.
#
# The launcher's syncVentureSkills() only ever adds and overwrites files that
# exist in crane-console; it never deletes target-only entries. Nothing here is
# owned by crane-console, so these links survive a launch.
#
# Idempotent. Safe to re-run.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_ROOT="$REPO_ROOT/docs/skills"

[ -d "$SRC_ROOT" ] || { echo "no docs/skills/ in $REPO_ROOT"; exit 0; }

installed=0
for skill_md in "$SRC_ROOT"/*/SKILL.md; do
  [ -e "$skill_md" ] || continue
  name="$(basename "$(dirname "$skill_md")")"

  mkdir -p "$REPO_ROOT/.agents/skills/$name" "$REPO_ROOT/.claude/commands"
  ln -sfn "$skill_md" "$REPO_ROOT/.agents/skills/$name/SKILL.md"
  ln -sfn "$skill_md" "$REPO_ROOT/.claude/commands/$name.md"

  echo "installed /$name -> docs/skills/$name/SKILL.md"
  installed=$((installed + 1))
done

echo "$installed skill(s) installed"
