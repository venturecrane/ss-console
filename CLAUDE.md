# CLAUDE.md - SS

This file provides guidance for Claude Code agents working in this repository.

## About This Repository

SS Console is the central infrastructure and documentation hub for the ss venture.

## Session Start

Every session must begin with:

1. Call the `crane_preflight` MCP tool (no arguments)
2. Call the `crane_sod` MCP tool with `venture: "ss"`

This creates a session, loads documentation, and establishes handoff context.

## Enterprise Rules

- **All changes through PRs.** Never push directly to main. Branch, PR, CI, QA, merge.
- **Never echo secret values.** Transcripts persist in ~/.claude/ and are sent to API providers. Pipe from Infisical, never inline.
- **Verify secret VALUES, not just key existence.** Agents have stored descriptions as values before.
- **Never auto-save to VCMS** without explicit Captain approval.
- **Scope discipline.** Discover additional work mid-task - finish current scope, file a new issue.
- **Escalation triggers.** Credential not found in 2 min, same error 3 times, blocked >30 min - stop and escalate.

## Build Commands

```bash
npm install             # Install dependencies
npm run dev             # Local dev server
npm run build           # Production build
npm run test            # Run tests
npm run lint            # Run linter
npm run typecheck       # TypeScript validation
```

## Development Workflow

| Command             | Purpose                                                    |
| ------------------- | ---------------------------------------------------------- |
| `npm run verify`    | Full local verification (typecheck + format + lint + test) |
| `npm run format`    | Format all files with Prettier                             |
| `npm run lint`      | Run ESLint on all files                                    |
| `npm run typecheck` | Check TypeScript                                           |
| `npm test`          | Run tests                                                  |

### Pre-commit Hooks

Automatically run on staged files:

- Prettier formatting
- ESLint fixes

### Pre-push Hooks

Full verification runs before push:

- TypeScript compilation check
- Prettier format check
- ESLint check
- Test suite

### CI Must Pass

- Never merge with red CI
- Fix root cause, not symptoms
- Run `npm run verify` locally before pushing

## Tech Stack

- Framework: TBD
- Hosting: Cloudflare Pages / Workers
- Database: Cloudflare D1
- Language: TypeScript

## Code Patterns

TBD - document as the project evolves.

## Instruction Modules

Detailed domain instructions stored as on-demand documents.
Fetch the relevant module when working in that domain.

| Module              | Key Rule (always applies)                                                    | Fetch for details                             |
| ------------------- | ---------------------------------------------------------------------------- | --------------------------------------------- |
| `secrets.md`        | Verify secret VALUES, not just key existence                                 | Infisical, vault, API keys, GitHub App        |
| `content-policy.md` | Never auto-save to VCMS; agents ARE the voice                                | VCMS tags, storage rules, editorial, style    |
| `team-workflow.md`  | All changes through PRs; never push to main                                  | Full workflow, QA grades, escalation triggers |
| `fleet-ops.md`      | Bootstrap phases IN ORDER: Tailscale -> CLI -> bootstrap -> optimize -> mesh | SSH, machines, Tailscale, macOS               |

Fetch with: `crane_doc('global', '<module>')`

## Related Documentation

- `docs/api/` - API documentation
- `docs/adr/` - Architecture Decision Records

---

_Update this file as the project evolves. This is the primary context for AI agents._
