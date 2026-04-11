# /heartbeat - Keep Session Alive

Send a heartbeat to prevent your session from timing out.

## What It Does

1. Finds your active session from Context Worker
2. Updates the `last_heartbeat_at` timestamp
3. Prevents 45-minute session timeout
4. Shows when next heartbeat is recommended

## When to Use

- Working on long tasks (>30 minutes)
- Want to keep session active while reading/researching
- Need to maintain "active" status visibility

**Not needed if:** You're actively using `/sos`, `/update`, or `/eos` - those all refresh heartbeat automatically.

## Usage

```bash
/heartbeat
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

### 2. Send Heartbeat

```bash
# Build request body
REQUEST_BODY=$(jq -n \
  --arg session_id "$SESSION_ID" \
  '{
    session_id: $session_id
  }')

# Call API
RESPONSE=$(curl -sS "https://crane-context.automation-ab6.workers.dev/heartbeat" \
  -H "X-Relay-Key: $CRANE_CONTEXT_KEY" \
  -H "Content-Type: application/json" \
  -X POST \
  -d "$REQUEST_BODY")

# Check for errors
ERROR=$(echo "$RESPONSE" | jq -r '.error // empty')
if [ -n "$ERROR" ]; then
  echo "❌ Heartbeat failed"
  echo ""
  echo "Error: $ERROR"
  exit 1
fi
```

### 3. Display Results

```bash
LAST_HEARTBEAT=$(echo "$RESPONSE" | jq -r '.last_heartbeat_at')
NEXT_HEARTBEAT=$(echo "$RESPONSE" | jq -r '.next_heartbeat_at')
INTERVAL=$(echo "$RESPONSE" | jq -r '.heartbeat_interval_seconds')

# Convert to human readable
MINUTES=$((INTERVAL / 60))

echo "💓 Heartbeat sent"
echo ""
echo "Session: $SESSION_ID"
echo "Last heartbeat: $LAST_HEARTBEAT"
echo "Next heartbeat in: ~$MINUTES minutes"
echo ""
echo "Your session will stay active for 45 minutes from this heartbeat."
```

## Notes

- Automatic heartbeats: `/sos`, `/update`, `/eos` all refresh heartbeat
- Manual heartbeat needed when working >30 minutes without those commands
- Recommended interval: every 10-15 minutes during long tasks
- Sessions become "abandoned" after 45 minutes without heartbeat (next `/sos` creates new session)
- Requires active session and CRANE_CONTEXT_KEY
- Safe to call frequently (idempotent)
