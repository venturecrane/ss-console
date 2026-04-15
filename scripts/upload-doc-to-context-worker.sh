#!/bin/bash
#
# Upload Documentation to Context Worker
#
# Uploads a markdown file to Context Worker with appropriate scope.
# Used by GitHub Actions for automatic sync and manual uploads.
#
# Usage:
#   ./scripts/upload-doc-to-context-worker.sh <doc-path> [scope]
#
# Arguments:
#   doc-path: Path to markdown file (e.g., docs/process/cc-cli-starting-prompts.md)
#   scope:    (Optional) Override scope. If not provided, auto-determined from doc name.
#
# Environment Variables (Required):
#   CRANE_ADMIN_KEY: Admin key for Context Worker authentication
#   GITHUB_REPOSITORY: (Optional) Used to determine venture for venture-specific docs
#
# Examples:
#   # Upload global doc (auto-detected from whitelist)
#   ./scripts/upload-doc-to-context-worker.sh docs/process/cc-cli-starting-prompts.md
#
#   # Upload venture-specific doc (scope from repo)
#   GITHUB_REPOSITORY="venturecrane/crane-console" \
#     ./scripts/upload-doc-to-context-worker.sh docs/process/PROJECT_INSTRUCTIONS.md
#
#   # Force specific scope
#   ./scripts/upload-doc-to-context-worker.sh docs/process/custom-doc.md global
#

set -e  # Exit on error

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

API_BASE="https://crane-context.automation-ab6.workers.dev"

# Global docs whitelist (docs that apply to all ventures)
GLOBAL_DOCS=(
  "cc-cli-starting-prompts.md"
  "team-workflow.md"
  "slash-commands-guide.md"
  "parallel-dev-track-runbook.md"
  "eod-sod-process.md"
  "dev-directive-pr-workflow.md"
  "agent-persona-briefs.md"
)

# Validate arguments
if [ -z "$1" ]; then
  echo -e "${RED}Error: Doc path required${NC}"
  echo "Usage: $0 <doc-path> [scope]"
  exit 1
fi

DOC_PATH="$1"
OVERRIDE_SCOPE="$2"

# Check file exists
if [ ! -f "$DOC_PATH" ]; then
  echo -e "${RED}Error: File not found: $DOC_PATH${NC}"
  exit 1
fi

# Check admin key
if [ -z "$CRANE_ADMIN_KEY" ]; then
  echo -e "${RED}Error: CRANE_ADMIN_KEY environment variable not set${NC}"
  exit 1
fi

# Extract doc name from path
DOC_NAME=$(basename "$DOC_PATH")

# Determine scope
if [ -n "$OVERRIDE_SCOPE" ]; then
  # Scope explicitly provided
  SCOPE="$OVERRIDE_SCOPE"
  echo -e "${BLUE}Scope: $SCOPE (explicit)${NC}"
else
  # Auto-determine scope
  IS_GLOBAL=false
  for global_doc in "${GLOBAL_DOCS[@]}"; do
    if [ "$DOC_NAME" = "$global_doc" ]; then
      IS_GLOBAL=true
      break
    fi
  done

  if [ "$IS_GLOBAL" = true ]; then
    SCOPE="global"
    echo -e "${BLUE}Scope: global (whitelisted doc)${NC}"
  else
    # Venture-specific: determine from repo
    if [ -n "$GITHUB_REPOSITORY" ]; then
      case "$GITHUB_REPOSITORY" in
        *venturecrane/crane-console*)
          SCOPE="vc"
          ;;
        *siliconcrane/sc-console*)
          SCOPE="sc"
          ;;
        *durganfieldguide/dfg-console*)
          SCOPE="dfg"
          ;;
        *)
          echo -e "${RED}Error: Cannot determine venture from repo: $GITHUB_REPOSITORY${NC}"
          echo "Supported repos: venturecrane/crane-console, siliconcrane/sc-console, durganfieldguide/dfg-console"
          exit 1
          ;;
      esac
      echo -e "${BLUE}Scope: $SCOPE (from repository: $GITHUB_REPOSITORY)${NC}"
    else
      echo -e "${RED}Error: Cannot determine scope${NC}"
      echo "Doc '$DOC_NAME' is not in global whitelist and GITHUB_REPOSITORY not set."
      echo ""
      echo "Options:"
      echo "  1. Add doc to GLOBAL_DOCS whitelist in this script"
      echo "  2. Set GITHUB_REPOSITORY environment variable"
      echo "  3. Provide scope as second argument: $0 $DOC_PATH <scope>"
      exit 1
    fi
  fi
fi

# Read doc content
DOC_CONTENT=$(cat "$DOC_PATH")

# Extract title from first # heading
TITLE=$(echo "$DOC_CONTENT" | grep -m 1 "^# " | sed 's/^# //' || echo "$DOC_NAME")

# Create JSON payload (properly escape content)
PAYLOAD=$(jq -n \
  --arg scope "$SCOPE" \
  --arg doc_name "$DOC_NAME" \
  --arg content "$DOC_CONTENT" \
  --arg title "$TITLE" \
  --arg source_repo "${GITHUB_REPOSITORY:-manual}" \
  --arg source_path "$DOC_PATH" \
  --arg uploaded_by "${GITHUB_ACTOR:-script}" \
  '{
    scope: $scope,
    doc_name: $doc_name,
    content: $content,
    title: $title,
    source_repo: $source_repo,
    source_path: $source_path,
    uploaded_by: $uploaded_by
  }')

# Upload to Context Worker
echo -e "${YELLOW}Uploading: $DOC_PATH → $SCOPE/$DOC_NAME${NC}"

RESPONSE=$(curl -s -X POST \
  "$API_BASE/admin/docs" \
  -H "X-Admin-Key: $CRANE_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

# Check success
SUCCESS=$(echo "$RESPONSE" | jq -r '.success')
if [ "$SUCCESS" = "true" ]; then
  VERSION=$(echo "$RESPONSE" | jq -r '.version')
  CREATED=$(echo "$RESPONSE" | jq -r '.created')
  CONTENT_HASH=$(echo "$RESPONSE" | jq -r '.content_hash')
  SIZE=$(echo "$RESPONSE" | jq -r '.content_size_bytes')

  if [ "$CREATED" = "true" ]; then
    echo -e "${GREEN}✓ Created: $SCOPE/$DOC_NAME (v$VERSION, ${SIZE} bytes)${NC}"
  else
    PREV_VERSION=$(echo "$RESPONSE" | jq -r '.previous_version')
    echo -e "${GREEN}✓ Updated: $SCOPE/$DOC_NAME (v$PREV_VERSION → v$VERSION, ${SIZE} bytes)${NC}"
  fi

  echo -e "${BLUE}Content hash: $CONTENT_HASH${NC}"
else
  ERROR=$(echo "$RESPONSE" | jq -r '.error // "Unknown error"')
  echo -e "${RED}✗ Upload failed: $ERROR${NC}"
  echo "$RESPONSE" | jq '.'
  exit 1
fi
