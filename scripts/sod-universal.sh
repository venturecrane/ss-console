#!/bin/bash
#
# ⚠️  DEPRECATED: This script is being replaced by MCP tools.
#
# The /sod command now uses crane-mcp tools (crane_preflight, crane_sod, crane_status).
# This script is kept for backwards compatibility but will be removed in a future version.
#
# Migration:
#   - Use /sod command instead (calls MCP tools automatically)
#   - Doc caching: scripts/cache-docs.sh
#   - Preflight: crane_preflight MCP tool
#   - Session start: crane_sod MCP tool
#   - Work queue: crane_status MCP tool
#
# Original description:
# Universal SOD (Start of Day) Script
# Works with any CLI that can execute bash scripts
#
# Integrates with Crane Context Worker to:
# - Load session context
# - Cache operational documentation
# - Display handoffs and work queues
#
# Usage: ./scripts/sod-universal.sh

echo ""
echo "⚠️  DEPRECATED: This script is replaced by MCP tools."
echo "   Use /sod command instead, which calls crane_sod MCP tool."
echo ""
echo "   Continuing with legacy script..."
echo ""

# Don't use set -e - we want graceful degradation
set -o pipefail

# Track what succeeded/failed for summary (declared early so all sections can use)
declare -a SUCCESSES=()
declare -a FAILURES=()

# ============================================================================
# Pre-flight Check (if available)
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/preflight-check.sh" ]; then
  echo "Running pre-flight environment check..."
  echo ""
  PREFLIGHT_EXIT=0
  bash "$SCRIPT_DIR/preflight-check.sh" || PREFLIGHT_EXIT=$?
  # Exit code 0 = all passed, 1 = critical failure, 2 = warnings only (OK to proceed)
  if [ "$PREFLIGHT_EXIT" -eq 1 ]; then
    echo ""
    echo "Pre-flight check failed. Fix critical issues before starting session."
    exit 1
  fi
  echo ""
fi

# ============================================================================
# Spool Flush (offline resilience)
# ============================================================================

if [ -f "$SCRIPT_DIR/ai-spool-lib.sh" ]; then
  source "$SCRIPT_DIR/ai-spool-lib.sh"
  SPOOL_COUNT=$(_ai_spool_count)
  if [ "$SPOOL_COUNT" -gt 0 ]; then
    echo "Flushing $SPOOL_COUNT spooled request(s)..."
    ai_spool_flush 2>/dev/null || true
    echo ""
  fi
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color


# Context Worker Configuration
CONTEXT_API_URL="https://crane-context.automation-ab6.workers.dev"
RELAY_KEY="${CRANE_CONTEXT_KEY:-}"  # Set via: export CRANE_CONTEXT_KEY="your-key-here"
CACHE_DIR="/tmp/crane-context/docs"

# ============================================================================
# Helper Functions
# ============================================================================

# Retry wrapper for curl calls (AC2: retry 2x before failing)
curl_with_retry() {
  local max_attempts=3
  local attempt=1
  local delay=2
  local result

  while [ $attempt -le $max_attempts ]; do
    if result=$(curl -sS --max-time 15 "$@" 2>&1); then
      echo "$result"
      return 0
    fi

    if [ $attempt -lt $max_attempts ]; then
      echo -e "${YELLOW}Network error, retrying ($attempt/$max_attempts)...${NC}" >&2
      sleep $delay
      delay=$((delay * 2))
    fi
    ((attempt++))
  done

  echo "$result"
  return 1
}

# Check if key is set (AC1: actionable error message)
if [ -z "$RELAY_KEY" ]; then
  echo -e "${RED}Error: CRANE_CONTEXT_KEY environment variable not set${NC}"
  echo ""
  echo -e "${YELLOW}To fix:${NC}"
  echo "  1. Get your key from Bitwarden (item: 'Crane Context Key')"
  echo "  2. Add to your shell config:"
  echo "     echo 'export CRANE_CONTEXT_KEY=\"your-key\"' >> ~/.zshrc"
  echo "  3. Reload: source ~/.zshrc"
  echo ""
  echo "  Or run the bootstrap script:"
  echo "     bash scripts/refresh-secrets.sh"
  exit 1
fi

# ============================================================================
# Step 1: Detect Repository Context
# ============================================================================

echo -e "${CYAN}## 🌅 Start of Day${NC}"
echo ""

REPO=$(git remote get-url origin | sed -E 's/.*github\.com[:\/]//;s/\.git$//')
ORG=$(echo "$REPO" | cut -d'/' -f1)
REPO_NAME=$(echo "$REPO" | cut -d'/' -f2)

# Determine venture from org via API
# Single source of truth: crane-context VENTURE_CONFIG
_lookup_venture_from_org() {
  local org="$1"
  local api_response

  if ! api_response=$(curl -sS --max-time 5 "$CONTEXT_API_URL/ventures" 2>/dev/null); then
    echo -e "${RED}Error: Cannot reach Context Worker${NC}" >&2
    echo "  URL: $CONTEXT_API_URL/ventures" >&2
    echo "  Check network connectivity and try again." >&2
    return 1
  fi

  if ! echo "$api_response" | jq -e '.ventures' > /dev/null 2>&1; then
    echo -e "${RED}Error: Invalid response from Context Worker${NC}" >&2
    echo "  Response: $api_response" >&2
    return 1
  fi

  local venture
  venture=$(echo "$api_response" | jq -r --arg org "$org" '.ventures[] | select(.org == $org) | .code' 2>/dev/null)

  if [ -z "$venture" ]; then
    echo -e "${RED}Error: Unknown GitHub org '$org'${NC}" >&2
    echo "  Known orgs:" >&2
    echo "$api_response" | jq -r '.ventures[] | "    - \(.org) → \(.code)"' >&2
    echo "" >&2
    echo "  To add a new venture, update crane-context VENTURE_CONFIG" >&2
    return 1
  fi

  echo "$venture"
}

VENTURE=$(_lookup_venture_from_org "$ORG")

echo -e "${BLUE}Repository:${NC} $REPO_NAME"
echo -e "${BLUE}Venture:${NC} $VENTURE"
echo ""

if [ "$VENTURE" = "unknown" ]; then
  echo -e "${RED}Error: Could not determine venture from org '$ORG'${NC}"
  exit 1
fi

# ============================================================================
# Step 2: Call Context Worker /sod API
# ============================================================================

echo -e "${CYAN}### 🔄 Loading Session Context${NC}"
echo ""

# Detect CLI client
CLIENT="universal-cli"
if [ -n "$GEMINI_CLI_VERSION" ]; then
  CLIENT="gemini-cli"
elif [ -n "$CLAUDE_CLI_VERSION" ]; then
  CLIENT="claude-cli"
elif [ -n "$CODEX_CLI_VERSION" ]; then
  CLIENT="codex-cli"
fi

# Create SOD request payload
# Note: docs_format="full" returns full documentation content (not just metadata index)
SOD_PAYLOAD=$(cat <<EOF
{
  "schema_version": "1.0",
  "agent": "$CLIENT-$(hostname)",
  "client": "$CLIENT",
  "client_version": "1.0.0",
  "host": "$(hostname)",
  "venture": "$VENTURE",
  "repo": "$REPO",
  "track": 1,
  "include_docs": true,
  "docs_format": "full",
  "scripts_format": "full"
}
EOF
)

# Call Context Worker with retry logic (AC2)
CONTEXT_LOADED=false
CONTEXT_RESPONSE=""

if CONTEXT_RESPONSE=$(curl_with_retry "$CONTEXT_API_URL/sod" \
  -H "X-Relay-Key: $RELAY_KEY" \
  -H "Content-Type: application/json" \
  -X POST \
  -d "$SOD_PAYLOAD"); then

  # Check for valid response
  if echo "$CONTEXT_RESPONSE" | jq -e '.session' > /dev/null 2>&1; then
    CONTEXT_LOADED=true
    SESSION_ID=$(echo "$CONTEXT_RESPONSE" | jq -r '.session.id')
    SESSION_STATUS=$(echo "$CONTEXT_RESPONSE" | jq -r '.session.status')
    CREATED_AT=$(echo "$CONTEXT_RESPONSE" | jq -r '.session.created_at')

    echo -e "${GREEN}✓ Session loaded${NC}"
    echo -e "${BLUE}Session ID:${NC} $SESSION_ID"
    echo -e "${BLUE}Status:${NC} $SESSION_STATUS"
    SUCCESSES+=("Session context loaded")
  else
    # API returned error
    ERROR_MSG=$(echo "$CONTEXT_RESPONSE" | jq -r '.error // "Unknown error"' 2>/dev/null)
    echo -e "${RED}✗ Context Worker error: $ERROR_MSG${NC}"
    FAILURES+=("Session context: $ERROR_MSG")
  fi
else
  echo -e "${RED}✗ Failed to reach Context Worker after 3 attempts${NC}"
  echo -e "${YELLOW}Continuing with degraded functionality...${NC}"
  FAILURES+=("Session context: network unreachable")
fi
echo ""

# ============================================================================
# Context Confirmation (v1.9 - prevents wrong-repo issues)
# ============================================================================

echo -e "${CYAN}### 🎯 Context Confirmation${NC}"
echo ""
CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo 'N/A')
echo -e "┌─────────────────────────────────────────────────┐"
echo -e "│  ${YELLOW}VENTURE:${NC}  $VENTURE"
echo -e "│  ${YELLOW}REPO:${NC}     $REPO"
echo -e "│  ${YELLOW}BRANCH:${NC}   $CURRENT_BRANCH"
if [ "$CONTEXT_LOADED" = true ]; then
echo -e "│  ${YELLOW}SESSION:${NC}  $SESSION_ID"
fi
echo -e "└─────────────────────────────────────────────────┘"
echo ""

# AC4: Validate git remote matches expected repo
if [ "$CONTEXT_LOADED" = true ]; then
  EXPECTED_REPO=$(echo "$CONTEXT_RESPONSE" | jq -r '.session.repo // empty' 2>/dev/null)
  if [ -n "$EXPECTED_REPO" ] && [ "$EXPECTED_REPO" != "$REPO" ]; then
    echo -e "${RED}⚠ WARNING: Repo mismatch!${NC}"
    echo -e "  Git remote: $REPO"
    echo -e "  Session expects: $EXPECTED_REPO"
    echo -e "  ${YELLOW}Check your working directory before proceeding.${NC}"
    echo ""
    FAILURES+=("Repo mismatch: git=$REPO, session=$EXPECTED_REPO")
  fi
fi

echo -e "${YELLOW}⚠ Verify this is correct before proceeding.${NC}"
echo -e "  If wrong, check your git remote and working directory."
echo ""

# ============================================================================
# Step: Check Weekly Plan
# ============================================================================

echo -e "${CYAN}### 📅 Weekly Plan Check${NC}"
echo ""

PLAN_FILE="$SCRIPT_DIR/../docs/planning/WEEKLY_PLAN.md"
PLAN_STATUS="missing"
PLAN_AGE_DAYS=999

if [ -f "$PLAN_FILE" ]; then
  # Check file age (cross-platform)
  if [[ "$OSTYPE" == "darwin"* ]]; then
    PLAN_MTIME=$(stat -f %m "$PLAN_FILE")
  else
    PLAN_MTIME=$(stat -c %Y "$PLAN_FILE")
  fi
  NOW=$(date +%s)
  PLAN_AGE_DAYS=$(( (NOW - PLAN_MTIME) / 86400 ))

  if [ "$PLAN_AGE_DAYS" -lt 7 ]; then
    PLAN_STATUS="valid"
  else
    PLAN_STATUS="stale"
  fi
fi

case "$PLAN_STATUS" in
  valid)
    echo -e "${GREEN}✓ Weekly plan found (${PLAN_AGE_DAYS} days old)${NC}"
    echo ""
    echo "---"
    cat "$PLAN_FILE" | head -20
    echo "---"
    SUCCESSES+=("Weekly plan loaded")
    ;;
  stale)
    echo -e "${YELLOW}⚠ Weekly plan is stale (${PLAN_AGE_DAYS} days old)${NC}"
    echo -e "  Consider updating before starting work."
    FAILURES+=("Weekly plan: stale (${PLAN_AGE_DAYS} days)")
    ;;
  missing)
    echo -e "${YELLOW}⚠ No weekly plan found${NC}"
    echo -e "  Set priorities before diving into work."
    FAILURES+=("Weekly plan: missing")
    ;;
esac
echo ""

# ============================================================================
# Step 3: Cache Documentation Locally
# ============================================================================

echo -e "${CYAN}### 📚 Caching Documentation${NC}"
echo ""

# Create cache directory
mkdir -p "$CACHE_DIR"

if [ "$CONTEXT_LOADED" = true ]; then
  # Extract and save documentation
  DOC_COUNT=$(echo "$CONTEXT_RESPONSE" | jq -r '.documentation.count // 0')

  if [ "$DOC_COUNT" -gt 0 ]; then
    echo "$CONTEXT_RESPONSE" | jq -r '.documentation.docs[]? | @json' | while read -r doc; do
      DOC_NAME=$(echo "$doc" | jq -r '.doc_name')
      CONTENT=$(echo "$doc" | jq -r '.content')
      SCOPE=$(echo "$doc" | jq -r '.scope')
      VERSION=$(echo "$doc" | jq -r '.version')

      echo "$CONTENT" > "$CACHE_DIR/$DOC_NAME"
      echo -e "  ${GREEN}✓${NC} ${SCOPE}/${DOC_NAME} (v${VERSION})"
    done
    echo ""
    echo -e "${GREEN}Cached $DOC_COUNT docs to $CACHE_DIR${NC}"
    SUCCESSES+=("Cached $DOC_COUNT docs")
  else
    echo -e "${YELLOW}No documentation available from Context Worker${NC}"
  fi
else
  # Check for existing cached docs
  CACHED_COUNT=$(find "$CACHE_DIR" -type f -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$CACHED_COUNT" -gt 0 ]; then
    echo -e "${YELLOW}Using $CACHED_COUNT previously cached docs${NC}"
    echo -e "  (Context Worker unreachable - cache may be stale)"
  else
    echo -e "${YELLOW}No cached documentation available${NC}"
    FAILURES+=("Documentation: no cache available")
  fi
fi
echo ""

# ============================================================================
# Step 4: Display Last Handoff
# ============================================================================

echo -e "${CYAN}### 📋 Last Handoff${NC}"
echo ""

if [ "$CONTEXT_LOADED" = true ]; then
  HANDOFF_SUMMARY=$(echo "$CONTEXT_RESPONSE" | jq -r '.last_handoff.summary // "N/A"')

  if [ "$HANDOFF_SUMMARY" != "N/A" ] && [ "$HANDOFF_SUMMARY" != "null" ] && [ -n "$HANDOFF_SUMMARY" ]; then
    HANDOFF_FROM=$(echo "$CONTEXT_RESPONSE" | jq -r '.last_handoff.from_agent')
    HANDOFF_DATE=$(echo "$CONTEXT_RESPONSE" | jq -r '.last_handoff.created_at')
    HANDOFF_STATUS=$(echo "$CONTEXT_RESPONSE" | jq -r '.last_handoff.status_label // "N/A"')

    echo -e "${BLUE}From:${NC} $HANDOFF_FROM"
    echo -e "${BLUE}When:${NC} $HANDOFF_DATE"
    echo -e "${BLUE}Status:${NC} $HANDOFF_STATUS"
    echo -e "${BLUE}Summary:${NC} $HANDOFF_SUMMARY"
    SUCCESSES+=("Handoff loaded")
  else
    echo -e "${YELLOW}*No previous handoff found*${NC}"
  fi
else
  echo -e "${YELLOW}*Handoff unavailable (Context Worker unreachable)*${NC}"
fi
echo ""

# ============================================================================
# Step 5: Display Active Sessions
# ============================================================================

if [ "$CONTEXT_LOADED" = true ]; then
  ACTIVE_COUNT=$(echo "$CONTEXT_RESPONSE" | jq -r '.active_sessions | length' 2>/dev/null || echo "0")

  if [ "$ACTIVE_COUNT" -gt 1 ]; then
    echo -e "${CYAN}### 👥 Other Active Sessions${NC}"
    echo ""

    echo "$CONTEXT_RESPONSE" | jq -r '.active_sessions[]? | select(.agent != "'$CLIENT'-'$(hostname)'") | "  • \(.agent) - Track \(.track // "N/A") - Issue #\(.issue_number // "N/A")"'
    echo ""
  fi
fi

# ============================================================================
# Step 6: Check GitHub Issues
# ============================================================================

# Check if gh CLI is available and authenticated
if command -v gh &> /dev/null && gh auth status &> /dev/null 2>&1; then

  echo -e "${CYAN}### 🚨 P0 Issues (Drop Everything)${NC}"
  echo ""

  P0_ISSUES=$(gh issue list --repo "$REPO" --label "prio:P0" --state open --json number,title --jq '.[] | "- #\(.number): \(.title)"' 2>/dev/null || echo "")
  GH_SUCCESS=true

  if [ -n "$P0_ISSUES" ]; then
    echo "$P0_ISSUES"
    echo ""
    echo -e "${RED}**⚠️ P0 issues require immediate attention**${NC}"
  else
    echo -e "${GREEN}*None - no fires today* ✅${NC}"
  fi
  echo ""

  echo -e "${CYAN}### 📥 Ready for Development${NC}"
  echo ""

  READY_ISSUES=$(gh issue list --repo "$REPO" --label "status:ready" --state open --json number,title --jq '.[] | "- #\(.number): \(.title)"' 2>/dev/null || echo "")

  if [ -n "$READY_ISSUES" ]; then
    echo "$READY_ISSUES"
  else
    echo "*No issues in status:ready*"
    echo ""
    echo -e "${CYAN}###  backlog (Triage Queue)${NC}"
    echo ""
    
    TRIAGE_ISSUES=$(gh issue list --repo "$REPO" --label "status:triage" --state open --limit 3 --json number,title --jq '.[] | "- #\(.number): \(.title)"' 2>/dev/null || echo "")

    if [ -n "$TRIAGE_ISSUES" ]; then
      echo "$TRIAGE_ISSUES"
    else
      echo "*Backlog is empty*"
    fi

  fi
  echo ""

  echo -e "${CYAN}### 🔧 Currently In Progress${NC}"
  echo ""

  IN_PROGRESS=$(gh issue list --repo "$REPO" --label "status:in-progress" --state open --json number,title --jq '.[] | "- #\(.number): \(.title)"' 2>/dev/null || echo "")

  if [ -n "$IN_PROGRESS" ]; then
    echo "$IN_PROGRESS"
  else
    echo "*Nothing currently in progress*"
  fi
  echo ""

  echo -e "${CYAN}### 🛑 Blocked${NC}"
  echo ""

  BLOCKED=$(gh issue list --repo "$REPO" --label "status:blocked" --state open --json number,title --jq '.[] | "- #\(.number): \(.title)"' 2>/dev/null || echo "")

  if [ -n "$BLOCKED" ]; then
    echo "$BLOCKED"
    echo ""
    echo "*Review blockers - can any be unblocked?*"
  else
    echo -e "${GREEN}*Nothing blocked* ✅${NC}"
  fi
  echo ""

  SUCCESSES+=("GitHub issues loaded")

else
  if ! command -v gh &> /dev/null; then
    echo -e "${YELLOW}Note: Install gh CLI to see GitHub issues${NC}"
    echo "  brew install gh && gh auth login"
  else
    echo -e "${YELLOW}Note: GitHub CLI not authenticated${NC}"
    echo "  Run: gh auth login"
  fi
  echo ""
  FAILURES+=("GitHub issues: CLI not available or not authenticated")
fi

# ============================================================================
# Step 7: Summary
# ============================================================================

echo "---"
echo ""
echo -e "${CYAN}**What would you like to focus on this session?**${NC}"
echo ""

# Provide recommendations based on context
if [ -n "$P0_ISSUES" ]; then
  echo -e "Recommendations:"
  echo -e "1. ${RED}Address P0 issues immediately${NC}"
  echo "2. Review blocked items"
  echo "3. Continue in-progress work"
else
  echo "Recommendations:"
  if [ -n "$READY_ISSUES" ]; then
    echo "1. Pick an issue from Ready queue"
  else
    echo "1. Triage an issue from the backlog"
  fi
  echo "2. Continue in-progress work"
  echo "3. Review blocked items"
fi

echo ""
echo -e "${BLUE}Documentation cached at:${NC} $CACHE_DIR"
if [ "$CONTEXT_LOADED" = true ]; then
  echo -e "${BLUE}Session ID:${NC} $SESSION_ID"
fi
echo -e "${BLUE}Context API:${NC} $CONTEXT_API_URL"
echo ""

# ============================================================================
# AC3: Summary of what worked and what didn't
# ============================================================================

if [ ${#FAILURES[@]} -gt 0 ]; then
  echo -e "${YELLOW}### ⚠ Partial Success${NC}"
  echo ""
  if [ ${#SUCCESSES[@]} -gt 0 ]; then
    echo -e "${GREEN}Succeeded:${NC}"
    for item in "${SUCCESSES[@]}"; do
      echo "  ✓ $item"
    done
    echo ""
  fi
  echo -e "${RED}Failed:${NC}"
  for item in "${FAILURES[@]}"; do
    echo "  ✗ $item"
  done
  echo ""
  echo -e "${YELLOW}Some features may be unavailable. Check errors above.${NC}"
  echo ""
fi
