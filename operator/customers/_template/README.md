# `_template/` -- the one Operator seat scaffold

This directory is the canonical starting point for every new seat. There is one product, the Operator (ADR 0100), so there is one template. What differs between a law firm, a couple and a self-serve buyer is a **preset**: a starting `customer.yaml` under `presets/` that names the starting capabilities and defaults. The commercial terms of an offering are not in this directory; they live with the offering (ADR 0067 for self-serve). It is never a real customer slug; the leading underscore reserves the name. Production tooling (`bin/provision-customer.sh`, `bin/decommission-customer.sh`) skips any directory whose name starts with `_`.

## Files

| File                | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dossier.md`        | Engagement dossier template (merged contract, 2026-07-26): relationship map, commercial rationale, firm research, canonical-document ledger, recorded absences, Captain-only facts. The read gate (`.claude/hooks/engagement-guard.mjs`) blocks engagement writes until the slug's dossier has been read; `tests/doctrine-integrity.test.ts` requires one for any engagement with a `correspondence/` archive. (This file was previously documented against Platform PRD §16.2, a citation that no longer resolves; the firm-research section absorbs that intent.) |
| `routine-grid.yaml` | The firm's routine grid (ADR 0075): every routine with the source's own verbatim wording, mapped onto this seat's skills and enforcement. It is what the client portal's Duties page renders. Authored from the letter that committed the matrix, then pinned to the agreement's Schedule A-1 once one exists. Lifecycle, including how a routine is changed, added and retired: `docs/runbooks/operator/routine-lifecycle.md`.                                                                                                                                     |
| `lane-proofs.yaml`  | The kinds of job proven on this seat (the lane-proof gate). Starts empty on every seat.                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `customer.yaml`     | The seat's configuration. Copied from ONE preset below, never written from scratch. Validates against `docs/specs/operator/customer-yaml-schema.md` once bracketed values are replaced.                                                                                                                                                                                                                                                                                                                                                                             |

## Presets

A preset is the starting set of capabilities and defaults for a market or an offering. Every preset is the same Operator: the same core, the same trust layer, the same portal. Pick the closest one; a seat can author more capabilities later.

| Preset                                                                   | Starts with                                                                                                                                          | Use when                                                                                                        |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| [`presets/pi-law-firm/customer.yaml`](presets/pi-law-firm/customer.yaml) | The fully bracketed law-firm default: practice-management, email, calendar and document bindings left for the assessment call to fill in             | A firm engagement whose capability bindings are not yet known                                                   |
| [`presets/self-serve/customer.yaml`](presets/self-serve/customer.yaml)   | One general-assistant persona, Telegram plus allowlisted-sender email, web search, the day-one jobs the product page sells; `external_send: confirm` | A self-serve purchase, authored from the buyer's intake row (runbook `docs/runbooks/hosted-agent/concierge.md`) |
| [`presets/couples/customer.yaml`](presets/couples/customer.yaml)         | The couples role (ADR 0090): two partners, spaces (ADR 0091), the role's skills                                                                      | A couples seat (runbook `docs/runbooks/hosted-agent/couples-setup.md`)                                          |

The seat's `seat.product` value names the offering whose commercial terms the seat is on (`operator` or `hosted-agent`, the stored values). Nothing on a seat branches on it: what a seat runs is derived from the skills it enables and the connectors it binds (`operator/hermes-smd-overlay/shared/capabilities.py`).

## Usage

1. Identify the firm with Captain.
2. Pick a slug matching `^[a-z0-9][a-z0-9-]{0,31}$`. Convention: `{first-name}-{last-name}-{firm-suffix}` for solo / small firms, `{firm-shortname}` for established brands.
3. Copy the scaffold:

   ```bash
   mkdir operator/customers/{firm-slug}
   cp operator/customers/_template/{routine-grid.yaml,lane-proofs.yaml} operator/customers/{firm-slug}/
   cp operator/customers/_template/presets/{preset}/customer.yaml operator/customers/{firm-slug}/
   ```

   The dossier is copied into the private engagements repo under the same `operator/customers/{firm-slug}/` path, never into ss-console (ADR 0081).

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
