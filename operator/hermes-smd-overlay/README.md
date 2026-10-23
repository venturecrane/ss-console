# hermes-smd-overlay

The Operator seat runtime: a plugin overlay for the [Nous Hermes Agent](https://github.com/NousResearch/hermes-agent) that adds SMD Services' governance, audit, voice and vertical capabilities to a customer's seat without modifying Hermes core.

This directory IS the overlay. Until 2026-10-22 it was the separate repository `venturecrane/hermes-smd-overlay`, pinned into the seat image by a commit sha; ADR 0095 folded it into ss-console so that a runtime change is one PR and the version a seat reports is the ss-console commit it was built from. The standalone repository's history is preserved as `refs/heads/overlay-history` in this repo and in the archived repository itself.

## What this is

One Hermes plugin (the root `__init__.py` is the fan-out register) that loads the plugins under `plugins/`. Each plugin's `plugin.yaml` is the authoritative list of the hooks it attaches to; the table below is the catalog.

| Plugin | Hooks | Purpose |
|---|---|---|
| `hermes-smd-audit` | `post_tool_call`, `post_llm_call`, `subagent_stop` | Per-tool, per-LLM-call, and per-subagent audit emission to per-customer D1. Also emits `AGENT_SKILL_CREATED` when the dispatched tool is `skill_manage` (ADR 0017 §40). |
| `hermes-smd-trust` | `pre_tool_call` | Content-class trust ceilings + outbound fabrication gate (`pre_tool_call` runs a second evaluation blocking draft bodies that carry banned fabrication markers / fabricated citations, emitting `FABRICATION_FILTER_TRIGGERED`, ADR 0028). |
| `hermes-smd-voice` | `pre_llm_call`, `post_llm_call` | Sample-driven voice transformation for customer-facing drafts. |
| `hermes-smd-memory-mirror` | `on_session_end` | Mirrors Honcho conclusions to per-customer D1 with provenance; supports Captain dismissal. |
| `hermes-smd-webhook-router` | `pre_gateway_dispatch` | Routes inbound webhook payloads to skills via `customer.yaml.webhook_triggers[]`. Emits `WEBHOOK_ROUTED` + attaches an inbound provenance envelope and emits `INBOUND_RECEIVED` (ADR 0021 Stream E + ADR 0027). On a verified sender the config authors on `scope.admins`, it also inserts the SENDER STATUS work-request paragraph into the dispatched email prompt, above the untrusted-body delimiter (ss-console#2416); a rostered NON-admin is reply-authorized, not work-directing (Decision #55), and dispatches unchanged. |
| `hermes-smd-inbound` | `pre_llm_call` | Nonce-fenced quarantine of untrusted inbound content at the single pre-LLM chokepoint (ADR 0027 defense-in-depth). |
| `hermes-smd-workspace` | registered tools | First-class Google Workspace tools (Gmail / Calendar / Drive / Docs / Sheets) backed by the local capability broker; every execution is broker-validated against the authored `google_auth` posture and audit-recorded. |
| `hermes-smd-reply` | `post_tool_call`, `transform_tool_result` | The Operator replies to a rostered colleague without weakening any floor (ADR 0055): recipient-locked, roster-authorized replies, emitting `REPLY_SENT` / `REPLY_HELD` / `REPLY_FAILED`; a hold is appended to the draft tool's result so the agent learns in the same turn that nobody was told (ss-console#2367). |
| `hermes-smd-peer-memory` | `pre_llm_call`, `post_tool_call`, `on_session_end` | Per-peer working-preference memory (ADR 0048 learned lane), mirrored to D1. |
| `hermes-smd-mcp-result-sink` | `post_llm_call` | Captures a completed turn for synchronous MCP return (`ask_operator`, ADR 0057). |
| `hermes-smd-jobs` | registered tools | Agent-facing tools for the durable task-execution substrate (ADR 0051). |
| `hermes-smd-initiation` | `pre_llm_call` | Authored initiation authority (ss#2222 gate 3): resolves the turn's attributed sender against the live roster + `scope.admins` and states the person-initiation disposition to the model. |
| `hermes-smd-space-gate` | `pre_tool_call` | Couples role (ADR 0091): a call that can write somewhere a person reads is refused when the turn's space cannot be resolved. Inert on seats without `people:`. |
| `hermes-smd-group-audience` | `pre_llm_call` | Couples role (ADR 0091 R7): posts one fixed notice when anyone outside `people:` can read a shared Telegram group. |
| `hermes-smd-space-delivery` | `post_llm_call`, `on_session_end` | Couples role (ADR 0091): a space-bound routine whose Telegram chat is not bound yet delivers its final response by email. |
| `hermes-smd-couples-settings` | registered tools | Couples role (ss-console#3264): the couple sets its schedule and settings by talking. |
| `hermes-smd-calendar` | `pre_tool_call`, `pre_gateway_dispatch`, registered tools | The seat's own calendar (ss-console#3264): iMIP invitations through the audited broker send path; records the people's Accept/Decline at dispatch. |
| `hermes-smd-hook-probe` | all six | Smoke plugin for verifying Hermes' hook surface at each rebase. |

Plus the `bootstrap/` CLI (`hermes-smd bootstrap`, the `customer.yaml` to Hermes-profile translator that runs at Machine startup), the importable `shared/` policy core the plugins depend on, and the root-side modules the image's entrypoint runs before the privilege drop (`config_applier`, `spec_applier`, `establish_intake`, `skills_applier`, `webhook_gate`).

## What this is not

- **Not a fork of Hermes.** Hermes core is untouched (Teknium's May 2026 plugin policy; ADR 0015). The pin is `ARG HERMES_UPSTREAM_SHA` in `operator/templates/Dockerfile`.
- **Not multi-tenant.** Each customer runs its own Fly.io Machine with its own Hermes install, OAuth tokens, D1 and R2. Tenant isolation is at the Machine boundary, not inside a process; there is no multi-tenant routing in any plugin.
- **Not a generic plugin pack.** These plugins encode SMD-specific concerns (the D1 audit schema, the `customer.yaml` format, the R2 voice-sample vault).

## How it ships

The seat image (`operator/templates/Dockerfile`) copies this directory twice: once as `/app/overlay-src`, which is pip-installed into the Hermes venv with `--no-deps` (its third-party dependencies come hash-locked from `operator/requirements/hermes-overlay.txt`, which `operator/requirements/compile.sh` derives from this `pyproject.toml`) and then removed; and once as `/app/overlay-pack`, which `bootstrap.sh` refreshes onto the seat's volume under `$HERMES_HOME/plugins/hermes-smd-overlay` on every boot and enables in the active profile. The version is the ss-console commit the image was built from (`SMD_OVERLAY_REF`, also written to `/app/OVERLAY_REF`); the seat's heartbeat reports it, and `fleet release` requires the heartbeat to echo the release it rolled out.

A change here reaches a seat the same way as any other image change: a PR, then `operator/bin/fleet build` and `operator/bin/fleet release`. See `docs/handbook/deployment-release.md`.

## Hard rules (the former AGENTS.md)

1. **Never modify Hermes core.** Plugins attach to documented hooks only. If a feature appears to require a core change, propose it upstream; never vendor a patch.
2. **Hermes core APIs are the contract.** The hooks each plugin attaches to are listed in its `plugin.yaml` and cited at the pinned ref in `docs/hook-surface.md`. When the Hermes pin moves, run `plugins/hermes-smd-hook-probe/` against the new ref before changing plugin code, and re-cite.
3. **Plugin callbacks must be exception-safe.** Hermes wraps each callback, but a noisy callback is log spam. Catch and log inside the plugin; never raise out of a hook.
4. **No secrets in code.** Credentials come from the environment, per each `plugin.yaml`'s `requires_env`; `shared/secrets.py` is the only reader.
5. **Per-customer isolation is at the Machine boundary.** One tenant per process; routing lives in `customer.yaml` and is materialized at provisioning.
6. **Adding a plugin:** `plugins/<name>/` with `plugin.yaml` (`name`, `version`, `description`, `requires_env`, `hooks`) and `__init__.py` exposing `register(ctx)`, a `tests/test_<name>.py`, and a row in the table above.

## Layout

```
__init__.py            the fan-out plugin Hermes loads (this directory is the plugin)
plugins/<name>/        one directory per plugin; plugin.yaml + __init__.py
shared/                the policy core the plugins import (not a plugin itself)
bootstrap/             the `hermes-smd` CLI (customer.yaml to profile config)
config_applier/ spec_applier/ establish_intake/ skills_applier/ webhook_gate.py
                       root-side modules the image entrypoint runs
contracts/             package data the runtime reads (consumes.yaml)
docs/                  design docs and the hook-surface citations
tests/                 the pytest suite
```

The directory is named for the plugin on purpose and must keep a name that is not a Python identifier: pytest's package walk stops at it, which is what keeps `tests.conftest` meaning this suite's conftest. `tests/test_layout.py` pins the reason.

## Development

The suite runs on the seat interpreter (3.13) with the pinned Hermes source available for the tests that build real Hermes requests:

```bash
cd operator/hermes-smd-overlay
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
# a NousResearch/hermes-agent checkout at ARG HERMES_UPSTREAM_SHA (operator/templates/Dockerfile)
HERMES_AGENT_SRC=/path/to/hermes-agent .venv/bin/python -m pytest -q
```

Lint and format are ruff, configured in this `pyproject.toml` (`uvx ruff@0.15.17 check . && uvx ruff@0.15.17 format --check .` from this directory). CI runs all of it as the `overlay` job of `.github/workflows/operator-substrate.yml`.

Fixtures the console also reads are read in place (`operator/contracts/`, `src/lib/operator/customer-yaml/__contract__/`); there are no mirrored copies to keep in step.

## License

MIT. See [LICENSE](LICENSE).

## History

Every commit of the standalone repository is at `refs/heads/overlay-history` in this repository (`git log overlay-history`). ADR 0095 records the fold; the ADRs under `docs/adr/` (0006, 0011, 0015, 0016, 0017, 0019, 0020, 0021, 0053, 0095) record the architecture.
