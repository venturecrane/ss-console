#!/usr/bin/env bash
# 1) Merges PR #3741 (token-sdk, reviewed by the lead, all checks green) through the merge queue.
# 2) Publishes the 2026-11-04 model-as-logic audit + ADR 0104 + the resume kit (docs/plans/logic-in-code): pushes the worktree branch and opens the PR.
# 3) Pushes the paused agents' branches from their worktrees so another machine can resume them (the classifier refuses these pushes from an agent session).
set -euo pipefail
WT=/Users/scottdurgan/dev/ss-console/.claude/worktrees
if git -C "$WT/agent-a9b8cc8cca1af4e88" push -u origin logic-in-code/connector-values; then
  gh pr create --draft --base logic-in-code/token-sdk --head logic-in-code/connector-values \
    --title "feat(connectors): the post list says what the tools actually did, and every figure keyed from the post is read as printed and confirmed on its page" \
    --body-file "$(dirname "$0")/pr-body-connector-values.md" || echo "connector-values PR not created (already open?)"
else
  echo "connector-values push refused by its pre-push hook: see RESUME.md"
fi
if git -C "$WT/agent-ad9ad7b51ae95a6d1" push -u origin logic-in-code/writes-and-conflict; then
  gh pr create --draft --base logic-in-code/token-sdk --head logic-in-code/writes-and-conflict \
    --title "feat(operator): a write on a matter the Operator chose opens only with that matter's resolution, and an outside sender's email moves nothing until the conflict check clears it" \
    --body-file "$(dirname "$0")/pr-body-writes-and-conflict.md" || echo "writes-and-conflict PR not created (already open?)"
else
  echo "writes-and-conflict push refused by its pre-push hook: see RESUME.md"
fi
git -C "$WT/agent-af4d46e2ae25807ae" push origin logic-in-code/fixed-wording || echo "fixed-wording push refused: see RESUME.md"
gh pr merge 3741 --squash
echo "merge of #3741 (token-sdk) queued"
# 1b) PR #3742 (fixed-wording, broker + reply plugin; reviewed by the lead). The merge queue runs its checks before merging.
gh pr merge 3742 --squash
echo "merge of #3742 (fixed-wording) queued"
cd /Users/scottdurgan/dev/ss-console/.claude/worktrees/audit-model-as-logic
git log --oneline -1
git push -u origin worktree-audit-model-as-logic
gh pr create \
  --title "docs: the model-as-logic audit of every job lane and ADR 0104, logic in code (2026-11-04)" \
  --body-file /Users/scottdurgan/.claude/plans/logic-in-code/pr-body-audit.md
