---
title: Operator Model Selection — Native Two-Tier Seam, Light-Main / Escalate-Up
date: 2026-06-16
status: accepted
captain: Scott Durgan
related-adr: 0037-operator-thesis.md, 0021-leverage-hermes-native-primitives.md, 0019-customer-yaml-to-profile-config-translation.md, 0048-operator-relationship-model.md, 0007-per-customer-machine-isolation.md
---

# ADR 0049 — Operator Model Selection

**Status:** Accepted (Captain decision, 2026-06-16).

**Purpose.** The canonical answer to _how the Operator chooses which LLM does a piece of work_ — across cost, quality, market-agility, and privacy/self-contained deployment. The question recurs ("can we switch models by task? do we need to build a router?") and kept pulling toward over-engineered answers. This ADR locks the frame so it is built upon, not re-derived. It is grounded in a source read of the Hermes runtime (`NousResearch/hermes-agent`), not in assumption.

## Context

The inquiry: Opus-class intelligence is overkill for inbox sorting; we want to be smart about model cost without degrading quality, stay agile as the model market evolves, and support privacy clients who run fully self-contained on local (Ollama) models. The instinct was to design a per-task model router.

A source read of Hermes plus our overlay and packs corrected the instinct. What is actually true, natively:

1. **Skills run in-line in the conversation loop.** A skill's `SKILL.md` body loads on demand (`skill_view`) into the current agent; it runs on the conversation's model. There is no per-skill model field (`agent/skill_utils.py` frontmatter parser reads none).
2. **`delegate_task` exposes no `model` parameter** to the agent (`tools/delegate_tool.py:1913`). Delegated work runs on a single model set by the native `delegation` config block (`hermes_cli/config.py:1140`). The `model` kwarg at `delegate_tool.py:687` is a TUI display field on the relay-callback builder, not a selector.
3. **The mechanical bulk is already off the LLM.** Per [ADR 0021](./0021-leverage-hermes-native-primitives.md), our skills push their fetch/loop work to `execute_code` (deterministic Python) and their wake decisions to `pre_run.py` (arithmetic). No skill uses `delegate_task` for model purposes today. Only the genuine _reasoning_ runs on the LLM.
4. **The cron heartbeat is already LLM-gated.** Native `no_agent` cron plus an arithmetic `pre_run` wakes the model only when there is real work; idle ticks cost nothing.
5. **The main model is already per-customer.** `customer.yaml` carries a single `model:` field, emitted into the per-profile config by `translate.py`. Changing the brain for a whole seat is a one-line edit, live today.

The net: the cost problem we set out to solve for mechanical work was already solved — by a _better_ mechanism than model-switching (off-LLM code), not by routing. Model selection governs reasoning only, and the dominant lever already exists.

## Decision — the tenets

### 1. Model choice is an internal sourcing decision, never client-facing.

The Operator competes with a hire ([ADR 0037](./0037-operator-thesis.md) Tenet 1). The client hires an outcome at a price; which brain produces it is ours, the way a firm decides which staff handles a task. There is **no client-facing model picker** — exposing one turns a hire back into software. Model selection is invisible by default.

### 2. The native seam is two tiers, and we build no more than that.

Hermes already gives us exactly two model slots per seat:

- **The main model** — the conversation and every in-line skill (including the heavy judgment skills, which run in-loop by default).
- **The `delegation` model** — one configured model that all `delegate_task` work runs on.

We **extend** this, not replace it: `customer.yaml` may express both models; `translate.py` emits the native `delegation` block. We do **not** build a router, a per-seat proxy shim, a per-skill model field, an N-tier framework, or a dynamic complexity classifier. Two tiers is a conscious sufficiency, not an oversight.

### 3. Default posture: light main, escalate heavy reasoning up.

The main model is the lighter, capable default (Sonnet-class). It runs the high-frequency volume — the conversation, dispatch, and the many small reasoning calls — cheaply. The rare genuinely-heavy task (e.g. a `matter-document-review` over a long production) escalates **up** to the bigger model. For most seats, choosing the right main model in `customer.yaml` is the whole of it.

**The escalation behavior is authored once, not per skill.** It lives as a standing instruction in every persona's `SOUL.md` — rendered by `translate.py` (`_escalation_soul_section`) and gated on the seat having an `escalation_model`, so a single-tier seat omits it and a two-tier seat receives it. The instruction tells the agent to hand work heavier than it can reliably give to a sub-agent on its escalation model. Putting it in identity rather than in a skill is what makes the design hold up:

- **Skills stay tier-unaware.** A skill never names a model or carries delegation boilerplate; at most it declares `metadata.smd.weight: heavy`. That marker is a property of the _work_ (it's heavy), not of the roster (which model) — so it stays tier-unaware. The convention is **default-light**: a skill is light unless it declares `weight: heavy`, which the SOUL instruction reads as a definite escalate signal (everything else escalates on the agent's judgment). The pack skills are **pre-weighted** — today `matter-document-review`, `assessment-findings-draft`, and `proposal-drafter` — so a new Machine ships with correct escalation out of the box. The _same_ pack runs on every roster: the SOUL adapts, not the skill. No double packs.

  _2026-09-29: assessment-findings-draft and proposal-drafter retired; see operator/templates/retired-skills.txt._

- **Agent-created skills inherit it.** `skill_manage` stays enabled (ADR 0017 — only the autonomous _curator_ is disabled, and it never touches bundled packs anyway); a skill the Operator authors through real work gets the escalation behavior for free, because the behavior is not in any skill.
- **One-off requests are covered.** The agent escalates on judged heaviness whether or not a skill matched.

**The path is governed.** `delegate_task` spawns a subagent, which the trust layer classifies as `CODE_EXECUTION` (`hermes-smd-overlay/plugins/hermes-smd-trust/enforce.py`) — fail-closed unless the seat authors a `code_execution: autonomous` ceiling, and taint-gated (subagent-spawning is withheld on a turn that has already read untrusted content). The SOUL instruction encodes both constraints once: **escalate first, before reading** untrusted material (the sub-agent does the reading, keeping the heavy reasoning and the reading together on the stronger model), and **fall back to doing the work in-loop** when escalation is unavailable. Written and tested once, in the standing instruction — never re-derived per skill.

### 4. Under-powering is a worker-quality concern, not a safety one.

Because the governed floors are **model-independent** — the broker and entitlement ceilings ([ADR 0045](./0045-mediated-connector-capability-broker.md), [ADR 0037](./0037-operator-thesis.md) Tenet 3) enforce send-posture, trust-funds-read-only, privilege, etc. regardless of which model ran — a model-miss can only ever degrade _quality_, never breach compliance. The worst case of "the light model handled the deposition review" is a weaker internal surface a reviewer still reads and judges; nothing wrong leaves the building. We therefore hold model choice to a **worker's** standard, not a compiler's: reliably good, improves when sent back, rarely slips. The correction loop is real — a sent-back result teaches the per-customer relationship memory ([ADR 0048](./0048-operator-relationship-model.md)), so a dip becomes a tuning signal, not a recurring defect.

### 5. The roster is per-customer; the tiers are universal.

What changes per customer is only the **roster** — which models fill the two slots and where they run. A normal seat fills them with cloud models. A privacy/self-contained seat fills both with local (Ollama) models of differing weight, running entirely inside the client's isolation boundary ([ADR 0007](./0007-per-customer-machine-isolation.md)). Same structure, same escalate-up posture; only the bench differs. There is **no central relay** in the model path — a privacy seat that routed through an SMD hub would defeat the isolation it is paying for. Market agility is the same lever: when a better or cheaper model appears, swap the roster value; task semantics are untouched.

## What we deliberately did NOT build

Recorded so it is not re-litigated: central relay/model router; per-seat OpenAI-compatible routing shim; per-skill `model` frontmatter field; an N-tier (strong/mid/cheap) framework; a dynamic per-turn complexity classifier; roster-relative floor machinery. Each was considered and rejected as over-engineering against a native seam that already does the job.

## Consequences

- **The two-tier mechanism is built (this ADR's accompanying change).** `customer.yaml` carries an optional `escalation_model:` (schema in `src/lib/operator/customer-yaml/`, declared in `operator/contracts/customer-yaml-blocks.yaml`); `translate.py` emits Hermes' native `delegation` block from it. Setting `model:` + `escalation_model:` on a seat is now a standing capability, not a future task.
- **Per seat, escalation is opt-in by authoring, not by us re-deciding.** A seat that wants the second tier authors `escalation_model:` (which auto-renders the SOUL escalation instruction) plus a `code_execution: autonomous` ceiling (the `CODE_EXECUTION` gate above). The behavior is the standing SOUL instruction — skills stay tier-unaware. A seat that authors neither runs single-tier on its `model:`, byte-identical to before. The choice is the engagement's, expressed in config; the machinery is always present.
- **Margin reality:** the savings from model-switching are smaller than gross task volume implies, because mechanical work is already off-LLM ([ADR 0021](./0021-leverage-hermes-native-primitives.md)). Model selection is a reasoning-tier optimization, not a bulk-work one.
- **No skill-execution restructuring, ever.** The escalate-up pattern is skill _content_ (instructions a skill author writes), not a change to how skills run. Restructuring skill invocation is off-limits.

## Verification

Source-read this session against `NousResearch/hermes-agent`: skill load path (`run_agent.py:6153–6169`, `agent/prompt_builder.py:988–1200`); `delegate_task` signature with no model param (`tools/delegate_tool.py:1913`); native `delegation` config block (`hermes_cli/config.py:1140`); `no_agent` cron (`tools/cronjob_tools.py:278`); `execute_code`/`pre_run` offload in our skills (`operator/skills/{ar-chaser,inbox-triage,retainer-hours-reconciler,status-report-assembler}/references/algorithm.md`); main model already emitted (`hermes-smd-overlay/bootstrap/translate.py`, `customer.yaml model:`).
