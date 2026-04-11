# /update - Update Session Context

Update your session with current branch, commit, and work metadata.

## What It Does

1. Finds your active session from Context Worker
2. Auto-detects current git branch and commit
3. Updates session with current work context
4. Refreshes heartbeat (keeps session alive)
5. Provides visibility: "Agent X is on branch Y working on issue #123"

## When to Use

- Started working on a new issue
- Switched branches
- Made significant progress (checkpoint)
- Want to update team visibility

**Tip:** Call this when you start work on an issue and after major milestones.

## Usage

```bash
# Auto-detect branch/commit
/update

# With issue number
/update 123

# With custom metadata
/update --meta '{"issue": 123, "priority": "P0"}'
```

## Execution Steps

### 1. Find Active Session

```bash
# Get current repo
REPO=$(git remote get-url origin 2>/dev/null | sed -E 's/.*github\.com[:\/]([^\/]+\/[^\/]+)(\.git)?$/\1/')

if [ -z "$REPO" ]; then
  echo "❌ Not in a git repository"
  exit 1
fi

# Determine venture from repo org
ORG=$(echo "$REPO" | cut -d'/' -f1)
case "$ORG" in
  durganfieldguide) VENTURE="dfg" ;;
  siliconcrane) VENTURE="sc" ;;
  venturecrane) VENTURE="vc" ;;
  *)
    echo "❌ Unknown venture for org: $ORG"
    exit 1
    ;;
esac

# Check for CRANE_CONTEXT_KEY
if [ -z "$CRANE_CONTEXT_KEY" ]; then
  echo "❌ CRANE_CONTEXT_KEY not set"
  echo ""
  echo "Export the key:"
  echo "  export CRANE_CONTEXT_KEY=\"your-key-here\""
  exit 1
fi

# Detect CLI client (matches sod-universal.sh logic)
CLIENT="universal-cli"
if [ -n "$GEMINI_CLI_VERSION" ]; then
  CLIENT="gemini-cli"
elif [ -n "$CLAUDE_CLI_VERSION" ]; then
  CLIENT="claude-cli"
elif [ -n "$CODEX_CLI_VERSION" ]; then
  CLIENT="codex-cli"
fi
AGENT_PREFIX="$CLIENT-$(hostname)"

# Query Context Worker for active sessions
ACTIVE_SESSIONS=$(curl -sS "https://crane-context.automation-ab6.workers.dev/active?agent=$AGENT_PREFIX&venture=$VENTURE&repo=$REPO" \
  -H "X-Relay-Key: $CRANE_CONTEXT_KEY")

# Extract session ID for this agent
SESSION_ID=$(echo "$ACTIVE_SESSIONS" | jq -r --arg agent "$AGENT_PREFIX" \
  '.sessions[] | select(.agent | startswith($agent)) | .id' | head -1)

if [ -z "$SESSION_ID" ]; then
  echo "❌ No active session found"
  echo ""
  echo "Run /sos first to start a session"
  exit 1
fi
```

### 2. Detect Current Work Context

```bash
# Get current branch
BRANCH=$(git branch --show-current 2>/dev/null)

# Get current commit
COMMIT=$(git rev-parse --short HEAD 2>/dev/null)

# Parse arguments for issue number or metadata
ISSUE_NUMBER=""
META_JSON=""

# If first arg is a number, it's an issue
if [[ "$1" =~ ^[0-9]+$ ]]; then
  ISSUE_NUMBER="$1"
  META_JSON=$(jq -n --argjson issue "$ISSUE_NUMBER" '{issue: $issue}')
fi

# If --meta flag provided, use that
if [[ "$1" == "--meta" && -n "$2" ]]; then
  META_JSON="$2"
fi

echo "## 📝 Updating Session"
echo ""
echo "Session: $SESSION_ID"
echo "Branch: $BRANCH"
echo "Commit: $COMMIT"
if [ -n "$ISSUE_NUMBER" ]; then
  echo "Issue: #$ISSUE_NUMBER"
fi
echo ""
```

### 3. Build Update Request

```bash
# Build request body with current context
REQUEST_BODY=$(jq -n \
  --arg session_id "$SESSION_ID" \
  --arg branch "$BRANCH" \
  --arg commit "$COMMIT" \
  --argjson meta "${META_JSON:-null}" \
  '{
    session_id: $session_id,
    branch: $branch,
    commit_sha: $commit
  } + (if $meta != null then {meta: $meta} else {} end)')
```

### 4. Call Context Worker /update

```bash
# Call API
RESPONSE=$(curl -sS "https://crane-context.automation-ab6.workers.dev/update" \
  -H "X-Relay-Key: $CRANE_CONTEXT_KEY" \
  -H "Content-Type: application/json" \
  -X POST \
  -d "$REQUEST_BODY")

# Check for errors
ERROR=$(echo "$RESPONSE" | jq -r '.error // empty')
if [ -n "$ERROR" ]; then
  echo "❌ Update failed"
  echo ""
  echo "Error: $ERROR"
  exit 1
fi
```

### 5. Display Results

```bash
UPDATED_AT=$(echo "$RESPONSE" | jq -r '.updated_at')
NEXT_HEARTBEAT=$(echo "$RESPONSE" | jq -r '.next_heartbeat_at')
INTERVAL=$(echo "$RESPONSE" | jq -r '.heartbeat_interval_seconds')

# Convert to human readable
MINUTES=$((INTERVAL / 60))

echo "✅ Session updated"
echo ""
echo "Updated at: $UPDATED_AT"
echo "Next heartbeat: ~$MINUTES minutes"
echo ""
echo "Your session context is now visible to other agents."
```

## What Gets Updated

- `branch`, `commit_sha`, `last_heartbeat_at` (always)
- `meta` (optional freeform JSON: issue, priority, tags, etc.)
- Venture, repo, track are set at session creation (not updated)

## Notes

- Auto-detects git branch and commit
- Requires active session (run `/sos` first) and CRANE_CONTEXT_KEY
- Refreshes heartbeat (prevents timeout)
- Safe to call frequently
- The `meta` field is freeform JSON - use it for whatever context helps your workflow
