# /status - Show Session Status

Display current session state, tasks, and git status for situational awareness.

## What to Show

### 1. Session Information

Query crane-context for current session (if available):

- Session ID
- Session age (how long since /sod)
- Last heartbeat time
- Venture context

If no session exists, note: "No active session. Run /sod to start."

### 2. Active Tasks

Use TaskList to show current task state:

- Pending tasks (not started)
- In-progress tasks (being worked on)
- Recently completed tasks

### 3. Git Status

Show current repository state:

- Current branch
- Uncommitted changes (staged and unstaged)
- Commits ahead/behind remote

### 4. Context Summary

- Current working directory
- Repository name
- Machine name (hostname)

## Output Format

```
== Session Status ==
Session: [ID or "None"]
Age: [duration or "N/A"]
Venture: [venture name or "Unknown"]

== Tasks ==
In Progress: [count]
  - [task subject]
Pending: [count]
  - [task subject]
Completed (recent): [count]

== Git ==
Branch: [branch name]
Changes: [staged] staged, [unstaged] unstaged
Remote: [ahead/behind status]

== Context ==
Repo: [repo name]
Dir: [current directory]
Machine: [hostname]
```

## Implementation Steps

1. Check for active session by querying crane-context /session endpoint
2. Call TaskList to get task state
3. Run `git status --porcelain` and `git branch -vv` for git info
4. Get hostname and current directory from environment
5. Format and display results

## Notes

- This is a read-only status check - it should not modify anything
- If crane-context is unavailable, show what information is available locally
- Keep output concise and scannable
