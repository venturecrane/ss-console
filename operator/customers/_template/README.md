# `_template/` -- customer config scaffold

This directory is the canonical starting point for a new customer's per-firm config. It is never a real customer slug; the leading underscore reserves the name. Production tooling (`bin/provision-customer.sh`, `bin/decommission-customer.sh`) skips any directory whose name starts with `_`.

## Files

| File                | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dossier.md`        | Engagement dossier template (merged contract, 2026-07-26): relationship map, commercial rationale, firm research, canonical-document ledger, recorded absences, Captain-only facts. The read gate (`.claude/hooks/engagement-guard.mjs`) blocks engagement writes until the slug's dossier has been read; `tests/doctrine-integrity.test.ts` requires one for any engagement with a `correspondence/` archive. (This file was previously documented against Platform PRD §16.2, a citation that no longer resolves; the firm-research section absorbs that intent.) |
| `routine-grid.yaml` | The firm's routine grid (ADR 0075): every routine with the source's own verbatim wording, mapped onto this seat's skills and enforcement. It is what the client portal's Duties page renders. Authored from the letter that committed the matrix, then pinned to the agreement's Schedule A-1 once one exists. Lifecycle, including how a routine is changed, added and retired: `docs/runbooks/operator/routine-lifecycle.md`.                                                                                                                                     |
| `customer.yaml`     | Per-customer configuration template. Validates against `docs/specs/operator/customer-yaml-schema.md` once bracketed values are replaced.                                                                                                                                                                                                                                                                                                                                                                                                                            |

## Usage

1. Identify the firm with Captain.
2. Pick a slug matching `^[a-z0-9][a-z0-9-]{0,31}$`. Convention: `{first-name}-{last-name}-{firm-suffix}` for solo / small firms, `{firm-shortname}` for established brands.
3. Copy the scaffold:

   ```bash
   cp -r operator/customers/_template operator/customers/{firm-slug}
   ```

4. Replace every bracketed field in `dossier.md`.
5. Replace every bracketed field in `customer.yaml`. Validate (canonical TS validator per ADR 0019):

   ```bash
   npx tsx scripts/validate-customer-yaml.ts \
     operator/customers/{firm-slug}/customer.yaml
   ```

6. Author `routine-grid.yaml` from the letter that committed the routine matrix: each row's
   `routine`, `start_verbatim` and `ceiling_verbatim` verbatim, in the letter's order, with a
   `start_tier_note` on any row whose starting setting is not a plain tier name. Validate it by
   running the grid tests (`npx vitest run tests/routine-grid-projection.test.ts`).

7. Provision the Machine with `operator/bin/provision-customer.sh {firm-slug}`.

## Voice samples

Voice samples live in R2 under `vaults/{firm-slug}/voice/samples/`, not in this directory. The pipeline stores structural-diffs, not raw text. See `docs/specs/operator/voice-ingestion.md`. The provisioning script does not move voice samples on its own; Captain ingests them per the runbook.

## What does NOT belong here

- Real partner names, client names, settlement amounts under seal, or any privileged material. Use only public-record citations.
- Anthropic / Composio / AgentMail / Fly API keys. Secrets go through `fly secrets import` via `provision-customer.sh` and never appear in any file in this directory.
- Raw email bodies or voice samples. Voice ingestion stores structural-diffs only; raw text is discarded at ingestion time.

## Reserved-slug guarantee

Any directory whose name starts with `_` is treated as a template scaffold and ignored by all provisioning / decommissioning tooling.

If a real firm slug ever needs to start with an underscore, change the prefix; do not weaken the reservation.
