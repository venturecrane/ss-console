# The Operator Task-Execution Framework

**Version 2.0 — Lead Architect synthesis, hardened against adversarial review, 2026-06-18**
**Scope:** How the SMD Operator should structure, run, cost, and verify employee-grade work on the Hermes substrate.
**Decision record:** [ADR 0050](../adr/0050-operator-task-execution-framework.md) locks the taxonomy and strategy portfolio; this document is its backing detail.

> **Status banner (read first).** This document is the design source-of-truth. It is honest about three things that are NOT yet true: (a) the cost case is a _method_, not a proven number — every dollar figure other than Anthropic's published rates is flagged ILLUSTRATIVE or ESTIMATED; (b) the marquee "unattended employee that delivers a retrievable result" claim is blocked on an unbuilt durable runner (B1); (c) the recommended bulk-data primitive (delegate_task, B2a) **defeats the taint gate as written** and must not ship before the child-session taint fix (B0) lands. The framework's shape is sound; its gaps are named, not hidden.

---

## 1. Executive Summary

The Operator competes with a coordinator hire, not with software (ADR 0037, Tenet 1). To earn that price it must complete sustained, multi-step work **unattended** and deliver a **retrievable result**. The single architectural fact that determines whether this is economically viable:

> **Cost scales with the complexity of the reasoning, not the volume of the data — but only if the task is architected correctly.** The LLM orchestrates and reasons; it must never page raw data through its context window.

The ~$50 receipt-summation failure is the canonical violation. The task — "read ~40 receipt emails, sum the amounts" — requires trivial reasoning (one addition) over moderate data (40 message bodies). It cost $50 and never completed because it ran as an interactive agent loop reading one message per turn. Every turn re-presented the accumulated conversation to the model, so input tokens grew super-linearly; the 55-second synchronous reply budget (`webhook_gate.py:264`, `_MCP_POLL_TIMEOUT_S = 55.0`, verified) then killed each attempt and restarted it from zero with nothing checkpointed. The corrected architecture — fetch the data once, aggregate in Python, return only the summary to the model — is a fixed-cost task. Anthropic's published analogue for the same transformation measured 150,000 tokens → 2,000 tokens (98.7% reduction).

**Honest decomposition of the real $50** (Section 6.1): the dollar figure was driven by the _restart-from-zero_ multiplier, not by a single clean O(N²) curve. Each 55s timeout discarded all partial work and re-ran from the top with a context that had already grown; the incident is "growing context × an unknown number of full restarts." We have **not** reconstructed the exact token trace — so the precise mechanism split (growth vs restart count) is **unquantified**. What is certain: the architecture, not the task, set the cost.

**The verdict on economic viability is conditional and unproven-but-favorable.** The cost _model_ is built; the cost _number_ is unmeasured; a client quote is blocked on the measurement rollup (B6). The cross-vertical task universe (Section 2) is dominated by extraction + classification + draft work over small-to-bounded data — the task class that degrades _gracefully_ over duration (arxiv 2603.29231, GDS 0.74→0.71) rather than collapsing like open-ended planning (0.90→0.44). The portfolio is deliberately the favorable class. But "favorable" is a design bias, not a measured P&L.

Viability rests on four load-bearing conditions, each with a concrete state today:

1. **The cost lever is built and mostly applied — but its payoff is per-class, not uniform.** `execute_code` (Hermes native, governed `CODE_EXECUTION`, authored `autonomous` on the SMD seat, `customer.yaml:228`) collapses data-volume work off the LLM. ADR 0021 Stream A prescribes the two-phase pattern; several skills (`ar-chaser`, `retainer-hours-reconciler`, `status-report-assembler`) encode it. **Cost realism:** the "cents" outcome holds _only_ for a pure-sum task whose reduce returns one number. The common Class-C shape — fetch N records, then the model reads the aggregate to draft/classify — carries the aggregate into context. The framework's own reference skill `ar-chaser` reports **~30–50K input tokens/run** for 5–15 invoices and **"<$2/month in tokens"** (`SKILL.md:129,199,209`, verified). That is **low single-digit dollars per skill per month**, not cents. The failure mode is enforcement, not capability — a skill authored as a naive in-context loop is indistinguishable from the $50 failure.

2. **The reliability lever is mostly built but has two verified gaps.** The fail-closed entitlement stack (8 ordered gates, Section 7) terminates runaway loops cleanly. But (a) there is **no durable async runner that completes a multi-step job and delivers a retrievable result** — `operator_handoff_task` is wired through the gate and then fail-closes because no `handoff` route is materialized (verified: `webhook_gate.py:780,795`; zero `webhook_trigger`/`handoff` entries in `customer.yaml`); and (b) **the runaway-cost circuit breaker is implemented but its live wiring is unconfirmed** — `sticky_stop.py` defines a `$50/day` cap (`cost_daily_cents = 5_000`) and a `3600s` wall-clock cap, but its integration into the live Hermes dispatch path on customer-zero is not verified. **Until B3 closes, there is no verified financial circuit breaker on the live Machine.**

3. **A verified governance hole sits inside the recommended bulk-data strategy.** The taint gate — the system's primary _code-enforced_ injection defense — keys on `session_id` (`enforce.py:877` reads `SESSION_TAINT.trust_class(session_id)`; gated classes include `EXTERNAL_SEND`, `DESTRUCTIVE`, `CODE_EXECUTION`, `enforce.py:80-85,323`). A `delegate_task` child is constructed with a **fresh, never-tainted session** (verified: `delegate_tool.py:1102-1132` passes only `parent_session_id`; no `session_id` kwarg → child auto-generates its own, `run_agent.py:1921`; nothing marks the child's session). So a child running the full `workspace_*` + trust plugin stack can read untrusted email AND then autonomously send/destroy/execute **within the same child turn**, because the gate finds the child session `INTERNAL`. On the SMD seat — which authors `external_send: autonomous` and `code_execution: autonomous` (`customer.yaml:227-228`) — this is exploitable. **This must be fixed (B0) before delegate_task is used on untrusted data (B2a).** It ranks ahead of every other build item.

4. **We cannot yet give a client a defensible monthly number, because we have not measured one.** We have _zero_ metered datapoints. The single run usually cited (`smd-inbox-triage/2026-05-19-run-01-real-gmail.md`) is headed **"Cost telemetry (estimated for this run)"** with tilde-approximated tokens (~14K in, ~3.5K out) (verified `:98-105`) — it is a hand-estimate, the same kind of estimate flagged ILLUSTRATIVE elsewhere. The cost model in Section 6 is a _method_. A quote comes only after the B6 rollup runs against real fixtures with metered billing.

The rest of this document operationalizes the principle: a classification of every task the Operator does, the execution-strategy portfolio (tagged substrate-provided vs author-discipline), the matrix matching one to the other, the cost and reliability models, and the prioritized build list — now led by the taint fix.

---

## 2. The Task Universe

Deduplicating the per-vertical task lists across all 12 verticals collapses ~70 named skills into **seven archetypes** plus two cross-cutting concerns. The archetype, not the vertical, determines execution strategy. Vertical compliance floors ride on top as trust-ceiling modifiers.

Tagging dimensions (formalized in Section 3): **Trigger · Data horizon · Reason-vs-Compute · Latency · Action class / trust grade.**

| #      | Archetype                                               | Representative skills                                                                                                                      | Trigger         | Data horizon                    | Reason vs Compute                                      | Latency                                   | Action / trust                                       |
| ------ | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | --------------- | ------------------------------- | ------------------------------------------------------ | ----------------------------------------- | ---------------------------------------------------- |
| **A1** | **Inbound triage & route**                              | `inbox-triage`, all `*-intake`, `conflict-intake-router`, `fnol-intake-router`, `refill-request-router`                                    | webhook / poll  | small-N (1 msg, or 5–50 window) | REASON over content                                    | async-tolerant; **sync if S-guard trips** | read + internal_write; route autonomous, reply draft |
| **A2** | **Portfolio scan & batch draft**                        | `status-report-assembler`, `matter-status-digest`, `recare-recall`, `renewal-radar`, `reactivation-winback`, `deadline-and-sol-tracker`    | cron            | **large-N** (50–200+ records)   | COMPUTE fetch → REASON surface                         | async                                     | read + internal_write / external_send draft          |
| **A3** | **Item chaser**                                         | `ar-chaser`, `pbc-document-chaser`, `engagement-letter-chaser`, `condition-stip-chaser`, `appraisal-order-tracker`                         | cron            | small-N (open items per file)   | COMPUTE state-check → REASON per-item draft            | async                                     | read + internal_write + external_send draft          |
| **A4** | **Event-triggered single-step**                         | `consult-scheduler`, `appointment-scheduler`, `intake-to-system-sync`, `matter-memo-on-update`, `money-movement-request-coordinator`       | webhook / event | single-step                     | COMPUTE-heavy → pure-COMPUTE                           | mixed                                     | read + internal_write autonomous; send/booking draft |
| **A5** | **Authored-content delivery**                           | `pre-post-care-sender`, `title-commitment-deliverer`, `owner-report-deliverer`, `meeting-prep-assembler`, `referral-source-acknowledgment` | event / cron    | single-step                     | minimal REASON (assemble)                              | async                                     | read + external_send draft                           |
| **A6** | **Safety escalation router**                            | `wire-instruction-safety-router`, `adverse-event-escalation-router`, `money-movement-safety-router`                                        | A1 handoff      | single-step                     | REASON (confirm signal, fail-open)                     | **sync, never queued**                    | autonomous escalate + templated ack                  |
| **A7** | **Status responder**                                    | `matter-status-responder`, `borrower-status-updater`, `claims-status-followup`                                                             | interactive     | single-step                     | REASON (select client-safe facts, hold no-advice line) | **interactive**                           | read + draft                                         |
| **X1** | **Orchestration / conversational turn** (cross-cutting) | the `ask_operator` open turn: "where are we on everything," "also look at X while you're in there," clarifying back-and-forth              | interactive     | variable                        | REASON (decide which skill(s) to invoke, or answer)    | interactive                               | dispatch; inherits invoked skill's class             |
| **X2** | **Safety-guard branch** (cross-cutting)                 | the halt/escalate path embedded in A1/A4 skills (conflict-hold, wire/money detection)                                                      | runtime         | single-step                     | REASON (confirm signal)                                | **sync, fail-open**                       | autonomous escalate                                  |

Four observations that drive the framework:

- **Pure-COMPUTE no-model tasks exist and are the cheapest class — they get their own class.** `matter-memo-on-update` (field-diff + templated memo), all deadline-bucketing (date arithmetic), and `retainer-hours-reconciler` utilization math (sum + divide) have a _near-zero_ LLM contribution and no reasoning reduce-tail. These are **Class N** (Section 3.2), routed to `no_agent` cron with the LLM woken only on the exception path — not Class C.
- **Pure-REASON-over-large-data is the one true exception to "process in code."** `matter-document-review` must read document _bodies_ for meaning — the volume _is_ the reasoning substrate and cannot move off the LLM. This is **Class D**: sub-agent isolation + content-class taint handling, not code-offload.
- **The orchestration turn (X1) is the highest-reliability-risk surface and was previously unclassified.** The open conversational turn is what a coordinator-hire does most. It is the parent ReAct loop deciding which skill(s) to dispatch (or answering directly). It is open-ended planning — exactly the class the reliability literature says _collapses_ over duration. It must be kept short and decompose into registered skills fast (Section 7).
- **The coordinator-vs-advisor line is the universal invariant.** Across all 12 verticals the Operator may REPORT, RELAY, SURFACE, ROUTE — never COMPUTE a legal/clinical/actuarial determination, GUARANTEE an outcome, or ADVISE on substance. A task whose _correct_ answer requires substantive judgment is a draft/escalation task by construction.

---

## 3. The Classification Framework

### 3.1 The axes

Six axes. The first three determine the **execution shape**; the last three determine the **governance/delivery shape**.

1. **Reason-over-content vs Compute-over-data** — does the model read _content for meaning_ (intent, voice, adversity, scope-fit), or _aggregate/filter/compare structured records_? Most important axis. Compute-over-data → `execute_code`; reason-over-content → the model loop. Many tasks are MIXED (compute fetch → reason surface); the split _is_ the architecture.
2. **Data horizon** — `single-step` · `small-N` (≤~50) · `large-N` (full book). Large-N is the economic danger zone.
3. **Determinism / procedure vs judgment** — fixed knowable procedure (fetch→classify→draft) → prompt-chaining; genuinely adaptive (next step depends on last result) → ReAct.
4. **Latency tolerance** — `interactive` (≤55s sync budget) · `async` (delivered later) · `safety-synchronous` (escalate _now_, never queued).
5. **Action class / trust grade** — `ActionClass` (READ / INTERNAL_WRITE / EXTERNAL_SEND / COMMITMENT / DESTRUCTIVE / CODE_EXECUTION / REFUSED) × authored ceiling (autonomous / draft_for_review / refused) × non-raisable vertical floor.
6. **Reasoning depth** — maps to model tier and METR reliability zone. Shallow → main model, sub-4-min zone. Deep → escalation model, sub-agent candidate.

### 3.2 The task classes (MECE) — six classes

Crossing the execution-shape axes yields **six execution classes**. Every task falls into exactly one _for its normal path_. (Safety guards are a cross-cutting concern, §3.4.)

- **Class N — No-Model / Deterministic.** Zero model inference required: the answer is pure computation (field-diff, date-bucketing, sum/divide). Runs as `no_agent` cron with `pre_run.py`; LLM woken only on an exception. _(matter-memo-on-update, deadline-bucketing, utilization math.)_
- **Class C — Compute-Collapsible (with a reasoning reduce-tail).** Volume-bound fetch that dominates cost, followed by a _bounded_ model reduce that reads the aggregate to draft/classify. _(A2 fetch+surface, A3 state-check+draft.)_ This is the class whose cost is "low dollars/month," not cents.
- **Class R — Reason-Bounded.** Small data, judgment is the whole job. _(A1 classification, A7 framing, `scope-creep-flagger`, consult-scheduler conflict gate.)_
- **Class D — Deep-Reason-over-Volume.** Large data that _must_ enter context because meaning is in the content. The one class where code-offload does not apply. _(`matter-document-review`, `proposal-drafter` over a long transcript.)_
- **Class A — Authored-Assembly.** Deliver authored content with near-zero model contribution; the only failure mode is fabrication. _(A5 entirely.)_
- **Class O — Orchestration/Conversational.** The open `ask_operator` turn (X1): the parent loop answers directly or dispatches one or more registered skills. Class R over the conversation but with _unbounded latitude_ → governed as open-ended planning (the collapsing reliability profile).

**On Class S.** "Safety-synchronous" is NOT a standalone execution class for _most_ tasks — it is a cross-cutting guard (§3.4). The dedicated routers (A6) are the rare case where the guard _is_ the whole skill; those are classified **Class R with latency=safety-synchronous and trust=autonomous-escalate fused** (see §3.3, governance-fused note).

These six are MECE _for the normal path_. Mixed tasks (A2, A3) are Class C: the reduce step is a small Class-R tail in-context, and the architecture-determining decision is the code-offload of the fetch.

### 3.3 Decision procedure — design-time skill classifier

This procedure classifies a **skill** at authoring time (it sets the `execution_class` frontmatter, B5). It does NOT decide whether a runtime input trips a safety guard — that is §3.4, a runtime concern.

```
START: a SKILL to be authored (design-time).

Q1. Is this skill an OPEN conversational/orchestration turn
    (no fixed procedure; the agent decides what to do)?
      YES → Class O  (keep short; decompose to registered skills fast)
      NO  → Q2

Q2. Does the skill require ANY model inference at all
    (vs pure computation: diff, date math, sum)?
      NO  → Class N  (no_agent cron; LLM only on exception path)
      YES → Q3

Q3. Is the model's job to deliver pre-authored content,
    adding nothing of its own?
      YES → Class A  (assemble + draft; fabrication is the only risk)
      NO  → Q4

Q4. Does correct completion require reading CONTENT FOR MEANING
    (not just structured fields/dates/amounts)?
      NO  (aggregate/filter/compare over records) → Class C
      YES → Q5

Q5. Will the meaning-bearing content fit ONE sub-4-minute reasoning
    pass WITHOUT compaction or paging (single doc / small bounded input)?
      YES → Class R
      NO  (must page large/many bodies → needs isolation to stay
           in the reliable zone) → Class D

For any MIXED skill (fetch N records THEN judge): classify by the DOMINANT
COST DRIVER, defined as "the phase that, architected naively, produces the
worst-case cost." Whenever large-N data is present, the data-volume phase
wins → Class C (offload the fetch; the reduce is a small Class-R tail).
```

**Multi-mode skills.** A skill with multiple invocation modes that span classes (e.g. `conflict-intake-router`: `--matter <id>` is single-matter Class R; `--cadence-scan` is large-N Class C) gets **one `execution_class` tag per mode**, and the B5 lint checks each mode's algorithm path independently. A single frontmatter value cannot represent such a skill — per-mode tagging is required, or the modes are split into separate classifiable units.

**The R/D boundary is a reliability threshold, not a record count.** If the meaning-bearing content fits one sub-4-minute pass without compaction → R; if it requires paging/compaction/isolation to stay in the reliable zone → D. (Q4's "large-N" is record-count; Q5's threshold is single-document _length_. `scope-creep-flagger` reading a short SOW = R; reading a 40-page brief for buried scope = D.)

**Two orthogonal tags attach — but orthogonality is partial (honest correction).**

- **Latency tag** {interactive | async} → selects sync-return vs durable-async delivery (§4.5/4.6).
- **Trust tag** {autonomous | draft_for_review | refused} + vertical floor → selects gate posture; authored, fail-closed when unauthored.

Orthogonality holds for **Classes C / R / D / N** (the same execution shape can carry any trust posture). It is **FUSED, not orthogonal, for the safety-router case (A6) and Class A**: for a safety router the latency (synchronous) and the trust posture (autonomous-escalate, which runs _above_ draft-for-review because requiring review would negate the safety property) are constitutive of what the skill _is_; for Class A the trust posture (draft_for_review) is constitutive. Do not claim universal separation of execution-shape from governance-shape — it is true for four of six classes and false for two.

### 3.4 The safety guard is a cross-cutting concern (runtime, not a class)

A confirmed high-risk signal (wire instruction, adverse event, habitability/animal emergency, money movement) is an **embedded branch** inside otherwise-ordinary skills, not a standalone task. Verified in source: `conflict-intake-router` carries a detect-and-halt invariant; `new-matter-intake` halts the chain on any conflict hit; `money-movement-request-coordinator` routes to the safety router on a direct instruction; `consult-scheduler` must never schedule on a CONFLICT-HOLD matter. Each skill is simultaneously its normal class (C/R/A) _and_ carries an **S-guard**.

Therefore:

- A skill's `execution_class` describes its **normal path**.
- The **S-guard** is a cross-cutting guard that can fire inside _any_ class, with its own non-raisable posture: **synchronous escalation, fail-open, never queued or batched.**
- Whether the guard fires is a **runtime property of the input**, not knowable at authoring time. The B5 lint must therefore (a) tag the normal class and (b) separately assert the S-guard is present and synchronous wherever the skill family requires it — it must NOT try to classify the whole intake skill as "Class S" and thereby mis-tag it.
- The dedicated `*-safety-router` skills (A6) are the degenerate case where the guard is the entire skill.

This split (design-time class + runtime guard) is the fix for the previously-undecidable "Q1: is this a safety signal?" question — you cannot answer that about a skill, only about an input.

---

## 4. The Execution-Strategy Portfolio

Each strategy names what it is, when to use it, the implementing primitive (with source), cost profile, and reliability profile. **The first column tags whether the strategy is a substrate-provided primitive (the platform enforces/provides it) or author-discipline (the skill author must implement it; nothing enforces it today unless noted).**

| #    | Strategy                                            | Kind                                                          |
| ---- | --------------------------------------------------- | ------------------------------------------------------------- |
| 4.1  | Process-in-Code (`execute_code`)                    | **Substrate-provided**                                        |
| 4.2  | Map-Reduce / Decomposition                          | **Substrate-provided** (via `execute_code` / `delegate_task`) |
| 4.3  | Sub-Agent Context Isolation (`delegate_task`)       | **Substrate-provided**                                        |
| 4.4  | Deterministic Skill + Cron (`no_agent` + `pre_run`) | **Substrate-provided**                                        |
| 4.5  | Synchronous MCP Channel (`ask_operator`)            | **Substrate-provided**                                        |
| 4.6  | Durable / Checkpointed Async Runner                 | **NOT built (B1)** + author-discipline cursor                 |
| 4.7  | Context Compaction (`ContextCompressor`)            | **Substrate-provided**                                        |
| 4.8  | Prompt Caching                                      | **Substrate-provided**                                        |
| 4.9  | Model Routing / Cascade (two-tier)                  | **Substrate-provided** (config-driven)                        |
| 4.10 | Verification / Critic Loop                          | **Author-discipline, unenforced until B5**                    |
| 4.11 | Idempotency + Retry                                 | **Author-discipline, unenforced until B5**                    |

### 4.1 Process-in-Code (CodeAct / `execute_code`) — the backbone _(Substrate-provided)_

- **What.** The agent writes one Python script that calls tools as subroutines; intermediate results stay in the child process; only `print()` stdout (capped 50 KB) returns to context. One LLM turn to write, one to read the summary, regardless of N.
- **When.** Every Class C task. Any task with >3–4 tool calls over bulk data. This is the _baseline architecture_ for Class C, not an optimization.
- **Primitive.** Hermes native `execute_code`. Verified (`code_execution_tool.py`): `DEFAULT_TIMEOUT = 300` (5 min), `DEFAULT_MAX_TOOL_CALLS = 50`, `MAX_STDOUT_BYTES = 50_000`, `SANDBOX_ALLOWED_TOOLS` is a frozenset of **7 tools** (`web_search, web_extract, read_file, write_file, search_files, patch, terminal`). Governed `CODE_EXECUTION` (`action_classes.py`), fail-closed unless authored; authored `autonomous` on SMD (`customer.yaml:228`). Taint-gated: cannot fire on a turn that ingested untrusted content (in the _main_ session — see the §4.3 child caveat).
- **Cost.** O(1) in LLM turns. Anthropic published 150K→2K on the analogue. **But the reduce step's cost depends on payload size** — when the model must read the aggregate to draft (the common case), input is tens of K (`ar-chaser`: ~30–50K/run). "Cents" applies only to a pure-sum reduce returning one number.
- **Reliability.** One item's failure is a JSON row, not a loop abort; finishes inside the 5-min child timeout instead of restarting on the 55s budget.
- **Critical limit (load-bearing for the mailbox case).** `workspace_*` broker tools are **NOT** in `SANDBOX_ALLOWED_TOOLS` (verified), and the broker grant hook runs at the gateway, not inside the sandbox subprocess. **`execute_code` cannot reach the managed mailbox** — see B2.

### 4.2 Map-Reduce / Decomposition _(Substrate-provided)_

- **What.** N independent items each processed in isolation (map), aggregated deterministically (reduce). The natural vehicle is `execute_code` (the Python loop is the map; the model's read of the aggregate is the reduce).
- **When.** Class C over independent items.
- **Primitive.** `execute_code` for the in-process map; `delegate_task` fan-out only when each item needs its own tool-using sub-agent (rare; expensive).
- **Cost.** Bounded per-item context; one item's malicious content can't reach the aggregator as instructions (architectural injection defense — though see §4.3 for the delegate caveat).
- **Reliability.** Deterministic coverage; parse failure → flagged row.

### 4.3 Sub-Agent Context Isolation (`delegate_task`) _(Substrate-provided — with a verified taint caveat)_

- **What.** Parent spawns a child with a fresh context, restricted toolset; child explores; only its summary returns. Parent context stays flat.
- **When.** **Class D** (primary) — large-volume reason-over-content. Also compound parallel research.
- **Primitive.** Hermes native `delegate_task`. Verified (`delegate_tool.py`): `execute_code` is in `DELEGATE_BLOCKED_TOOLS` (line 46) — **children cannot run code**; `max_concurrent_children` default 3; NOT durable (dies with the parent turn). **Model routing (corrected vs the corpus):** the agent-facing tool schema exposes no `model` parameter, but the build path accepts a `model` kwarg internally (`delegate_tool.py:687,732`) set from the `delegation` config block. Delegated work runs on the configured `escalation_model` (SMD: `claude-opus-4-8`, `customer.yaml:38`) — **config-driven, not agent-chosen, and not influenceable by untrusted input**. We author the second tier; we do not build a router. Model escalation is therefore not itself an attack surface.
- **Why a child can reach `workspace_*` where `execute_code` cannot (the mechanism, stated explicitly).** The trust/workspace plugins register tools into the **process-global** registry, and `pre_tool_call` fires for any tool execution regardless of agent instance. A `delegate_task` child runs as a **thread inside the gateway process** (`ThreadPoolExecutor`, `delegate_tool.py:28`), so it shares the gateway PID that the broker's `SO_PEERCRED` peer-PID gate checks — it **passes** the broker gate. An `execute_code` sandbox runs as a **separate subprocess** with a different PID — it is **rejected** by design. This thread-vs-subprocess PID distinction is the reason B2a (delegate) is viable and B2b (sandbox-reaches-broker) is hard.
- **VERIFIED GOVERNANCE CAVEAT (the hole behind B0/B2a).** The taint register keys on `session_id` (`enforce.py:877`), and the child is constructed with a **fresh, never-tainted session** (verified: `delegate_tool.py:1102-1132` passes only `parent_session_id=...`; no `session_id` kwarg → child auto-generates its own, `run_agent.py:1921`). Taint is marked **only at the inbound chokepoints on the dispatch session** (`hermes-smd-inbound/__init__.py:83,217`); **nothing marks the child's session.** So a child that reads untrusted content runs as `INTERNAL` and the taint gate does **not** withhold `EXTERNAL_SEND`/`DESTRUCTIVE`/`CODE_EXECUTION`. The SOUL.md "escalate before reading" rule (`translate.py:762`) is a **prompt instruction, not a code gate.** **Until B0 lands, `delegate_task` must not be used to read untrusted Workspace data on a seat with autonomous send/exec.**
- **Cost.** Additive — each child is a full agent loop at the escalation-tier rate. Parallelism cuts wall-clock, not tokens. Reserve for genuinely heavy work.

### 4.4 Deterministic Skill + Cron (`no_agent` + `pre_run.py` wake gate) _(Substrate-provided)_

- **What.** Scheduled work where a Python script does the check; the LLM wakes only when there is something to reason about. `no_agent: true` short-circuits before any AIAgent/SessionDB is built (verified `scheduler.py:1052`); `wakeAgent:false` produces a silent run (`scheduler.py:1105`).
- **When.** **Class N** and Class C cron watchers where most ticks find nothing.
- **Primitive.** Hermes `no_agent` cron + overlay `pre_run.py`; materialized by `cron_materialize.py` (ADR 0047).
- **Cost.** **$0 LLM tokens on quiet ticks** — the largest single cron cost lever, larger than model selection.
- **Reliability.** **Non-obvious safety constraint (ADR 0021 Stream B):** the gate MUST emit a `SUPPRESSED_WAKE` audit row on the silent path and MUST fail toward `wakeAgent:true` on error — otherwise a silently-broken `pre_run.py` is indistinguishable from a quiet day (violates ADR 0016 mirror-don't-gate). **Verified correction to the corpus:** the cron inactivity timeout is `600.0`s (`scheduler.py:1487`, `HERMES_CRON_TIMEOUT`), **not** the "3-minute hard interrupt" AGENTS.md prose claims — the prose is stale.

### 4.5 Synchronous MCP Channel (`ask_operator`) _(Substrate-provided)_

- **What.** A conversational turn returned inline within the reply budget.
- **When.** Class R / A / O that fit one fast turn; directing the Operator to _start_ an async job.
- **Primitive.** Overlay `/mcp` JSON-RPC in `webhook_gate.py`; live on customer-zero (Clerk auth fixed, overlay #98).
- **Reliability.** **Hard 55s budget** (`_MCP_POLL_TIMEOUT_S = 55.0`, verified `webhook_gate.py:264`). Any task iterating many items here WILL time out and return empty — the exact $50 failure surface. **Never run Class C/D/N-with-volume synchronously here.**

### 4.6 Durable / Checkpointed Async Runner — **the missing strategy** _(NOT built — B1; cursor is author-discipline)_

- **What.** A human/system hands off a multi-step job; the Operator works it in the background and delivers a retrievable result, surviving the 55s budget and ideally Machine restarts.
- **When.** Any Class C/D task initiated interactively but too large for synchronous return. The "unattended employee" claim depends on this.
- **Primitive.** `operator_handoff_task` (console `tools.ts:185` → `/webhooks/handoff`, `webhook_gate.py:1209`). **Status: WIRED (ss#2616).** `translate.py` materializes the `handoff` route whenever `WEBHOOK_SECRET_MCP` resolves (no `webhook_trigger` needed — the route's two callers, the console tool and the seat's medchron runner daemon, are wired at provision), the adapter drives the turn with `_INBOUND_HANDOFF_PROMPT` (`{task}` interpolated), and the gate treats any non-2xx forward as a retryable 503 instead of swallowing a 4xx into a 202 (the pre-#2616 black hole: an unmaterialized route made a lost handoff indistinguishable from a delivered one). The 503 "handoff route not configured" arm remains for an unset secret. Hermes `/background` (in-memory asyncio) and the kanban queue (Fly-volume SQLite) exist but **neither survives Machine replacement.**
- **Reliability.** **No native intra-turn checkpoint exists** (author-discipline). The pattern: write an explicit cursor row (last-processed ID, phase, timestamp) to operator D1 at each phase boundary, read on resume; `context_from` chains a `no_agent` fetch to a reasoning job. **True cross-restart durability does NOT come for free from the handoff route** — it requires the D1 cursor. B1's DoD must keep this caveat load-bearing.

### 4.7 Context Compaction (`ContextCompressor`) _(Substrate-provided)_

- **What.** Reactive in-session compression: prune old tool results (free), LLM-summarize the unprotected middle, protect head + last-N. Fires ~50% fill; gateway safety net ~85%.
- **When.** Long interactive/multi-phase sessions. **Not** a substitute for `execute_code` on Class C — it caps damage after the fact; it does not prevent the loop.
- **Cost.** Pruning free; summarization is one auxiliary-model call.
- **Reliability.** Lossy on exact numbers/dates — write critical facts to a workspace file before they age into the compressible middle. **Taint must survive compaction** (sticky-stop invariant_4): if the compressor is downgraded to a cheaper model (B4), re-verify that an injection in an email body does not survive into the summary and that taint persists. (Distinct from the offline `trajectory_compressor.py` training tool — do not conflate.)

### 4.8 Prompt Caching _(Substrate-provided)_

- **What.** `system_and_3` caches the system prompt + last 3 messages; cache reads at 0.1× input price; cross-session 1h prefix cache (v0.14.0).
- **Primitive.** Hermes `agent/prompt_caching.py`, automatic.
- **Cost.** Up to ~90% on cached tokens (Anthropic verified: read $0.30/MTok vs $3/MTok input on Sonnet 4.6). Largest benefit on law seats with big stable SOUL.md + skill bodies. **Constraint:** inject nothing dynamic before the system cache_control marker — put per-turn content in the user message.

### 4.9 Model Routing / Cascade (two-tier) _(Substrate-provided, config-driven)_

- **What.** Sonnet-class main + Opus-class escalation; `weight: heavy` SKILL.md frontmatter signals escalation via SOUL.md.
- **Primitive.** ADR 0049; `customer.yaml` `model:` + `escalation_model:`; `translate.py` delegation block. No router to build.
- **Cost.** Verified pricing (June 2026): Haiku 4.5 $1/$5, Sonnet 4.6 $3/$15, Opus 4.8 $5/$25 per MTok in/out. **Second-order lever** — getting mechanical work off the LLM (4.1) dominates model choice.
- **Reliability.** Under-powering is a quality concern, not a safety one; **floors are model-independent** (ADR 0049 Tenet 4 — ceilings resolve before the model is consulted, `enforce.py`), so model escalation cannot relax a ceiling.
- **Adjacent unapplied lever (B4):** Hermes' 11 auxiliary model slots all default to the main model. Routing cosmetic slots (title-gen, etc.) to Haiku is a cut with no quality risk — **but exclude approval-scoring** (downgrading the model that scores injected content as low-risk is a safety regression) and **re-verify taint-survives-compaction** if the compressor is downgraded.

### 4.10 Verification / Critic Loop _(Author-discipline, unenforced until B5)_

- **What.** Generate → check against an _external, verifiable_ signal (schema validation, row-count, sum cross-check) → bounded retry (1–3) → escalate on exhaustion.
- **When.** Any task where a malformed/wrong output is worse than no output.
- **Primitive.** Skill-procedure-level. The `delegate_task` assembly-time schema contract (ADR 0021: missing required keys → `subagent_incomplete` audit, refuse to assemble) is this pattern done right. **There is no critic-loop harness in the substrate** — this is author responsibility. Intrinsic self-critique is unreliable; the signal must be external. This is the antidote to confabulated success (the demo-law bug shape).

### 4.11 Idempotency + Retry Architecture _(Author-discipline, unenforced until B5)_

- **What.** Key the _logical unit of work with external side effects_ (not the API call); check before execute; skip completed on retry.
- **When.** All Class C/A external-send chasers where a retry must not double-send.
- **Primitive.** **There is no idempotency key store in the substrate** — must-author at skill level, storing keys in the broker-owned audit ledger (OP-P1-4 live — the agent can't tamper with its own log, so a checked key can't be retroactively invalidated). **Until B5 ships a lint that checks for it, a Class-C chaser authored without idempotency will double-send on retry and nothing stops it.**

---

## 5. The Match (Task Class → Strategy Stack)

| Task class                      | Primary strategy                                     | Supporting strategies                                                                                      | Delivery                           | Why this is correct                                                                                                                                                                |
| ------------------------------- | ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **N — No-Model**                | **`no_agent` cron + `pre_run` (4.4)**                | idempotency (4.11) on any send; LLM woken only on exception                                                | async; silent on quiet ticks       | Zero LLM contribution — running it through the model loop is pure waste. $0 tokens on quiet ticks.                                                                                 |
| **C — Compute-Collapsible**     | **Process-in-Code (4.1)** / Map-Reduce (4.2)         | `no_agent`+`pre_run` for cron watchers; cursor-checkpoint (4.6) if multi-tick; idempotency (4.11) on sends | async; result to digest/Slack/file | Keeps O(N²) data volume off the LLM. The reduce is a small Class-R tail. **Direct fix for the $50 task** — but cost is low-dollars/skill/month (ar-chaser ~30–50K/run), not cents. |
| **R — Reason-Bounded**          | **Default ReAct in main loop**                       | Verification loop (4.10) on drafts; classifier first; caching (4.8)                                        | sync if interactive, else async    | Small data, judgment is the job. METR sub-4-min reliable zone. No volume to offload.                                                                                               |
| **D — Deep-Reason-over-Volume** | **Sub-agent isolation (4.3)** on the escalation tier | Compaction (4.7); write findings to file before compaction; verification (4.10)                            | async; surfaced internal artifact  | Content _is_ the reasoning substrate. Isolation keeps the large exploration out of the main context. **Requires B0 if the content is untrusted.**                                  |
| **A — Authored-Assembly**       | **Synchronous MCP / simple draft (4.5)**             | Outbound provenance gate (fabrication block) is load-bearing                                               | draft_for_review                   | Model adds nothing; the only risk is Pattern A/B fabrication, caught by the provenance gate.                                                                                       |
| **O — Orchestration**           | **Main ReAct loop, kept SHORT**                      | Decompose to registered skills fast; durable runner (4.6/B1) for anything heavy; caching                   | interactive                        | Open-ended planning collapses over duration (GDS→0.44). Keep the turn in the reliable zone by dispatching classified skills, not improvising long chains.                          |

**Safety-guard overlay (cross-cutting, §3.4).** Every A1/A4 skill carries an S-guard: synchronous, fail-open, autonomous escalate, **never queued**. This posture runs _above_ draft-for-review because requiring review would negate the safety property. The guard fires on a runtime signal regardless of the skill's normal class.

**Mixed-task worked example — `ar-chaser` (Class C with R tail):** `pre_run`/`execute_code` fetches QBO aging + per-invoice payment status + prior Gmail threads in one block (Class C, off-LLM); the model receives one JSON payload (~30–50K tokens for 5–15 invoices, verified `SKILL.md:199`) and does cadence-stage selection + voice-matching + draft composition (the bounded Class-R reduce, ~500 tokens out/draft); idempotency keys prevent double-chasing on retry; drafts land at `draft_for_review`; ~$2/month in tokens (the skill's own figure). This is the reference implementation of the matrix — and the evidence that the portfolio-wide cost is _low dollars_, not _cents_.

**Anti-pattern the matrix forbids:** running any Class C/D/volume-N task through the synchronous MCP channel (4.5). That is the $50 failure by construction.

---

## 6. Cost Model

### 6.1 The estimation method (per task)

```
$/task ≈ ( input_tokens × in_rate
         + cached_input_tokens × 0.1 × in_rate
         + output_tokens × out_rate )
         ÷ pass@1                         ← failed attempts still cost full price
```

The `÷ pass@1` term is the production reality (cost-per-success): a 50% pass@1 doubles effective cost. **Cost-normalized accuracy** CNA = accuracy / $ is the right single ranking number.

The class drives the dominant term — **stated by class, not generalized from the best case:**

- **Class N:** $0 LLM tokens on quiet ticks; cost is Fly CPU only.
- **Class C done right — TWO sub-cases (the correction):**
  - _Pure-sum reduce (returns one number):_ input ≈ system + a few K → **cents** (~5K × $3/MTok ≈ $0.015). This is the **narrowest, most favorable** case — the receipt task.
  - _Per-item reduce (the COMMON case — model reads the aggregate to draft/classify):_ input is the aggregate, tens of K. `ar-chaser` carries **~30–50K tokens/run** at ~$0.09–0.15/run, **"<$2/month in tokens"** per the skill itself (verified `SKILL.md:129,199,209`). This is **low single-digit dollars per skill per month**, not cents.
- **Class C done wrong (the loop):** input grows super-linearly — every turn re-reads prior bodies. The only class where the _architecture_, not the task, sets the cost.
- **Class D:** genuinely volume-bound in context; cost is real and scales with case-file size; escalation-tier rate. Sub-agent isolation keeps it off the _main_ context but the child still pays.
- **Class R / A / O:** small, bounded; reasoning-tier × a few turns.

**The real $50 failure — honest decomposition.** The incident cost ≈ (growing per-turn input) × (number of 55s-budget restarts that each discarded partial work). We have **not** reconstructed the exact token trace, so we cannot attribute the $50 to O(N²) growth vs restart count vs both. The "420K tokens ≈ $1.26 for a 20-call task" figure that appears in the research corpus is a **first-principles arithmetic illustration for a different (20-call) task size — ILLUSTRATIVE, and it does NOT reconcile to the real 40-message $50 incident.** Do not present the two as the same number. The bridge between them (restart multiplier) is unquantified.

**The sticky_stop $50/day cap is NOT the $50 incident.** They share a number by coincidence. `cost_daily_cents = 5_000` (verified `sticky_stop.py:180`) is a **per-day** ceiling; the incident was the cost of one (failed, repeatedly-restarted) task. The cap also **cannot be claimed as the bound** until B3 confirms it is wired live into the dispatch path.

### 6.2 Monthly per-seat

```
$/month/seat ≈ Σ_skills ( runs_per_month × $/run )
             + cron_idle_cost (≈ $0 with pre_run gate / Class N)
             + always-on Machine infra (Fly CPU; NOT token cost)
```

`wake_policy: always` burns Machine CPU continuously regardless of tokens — infrastructure cost, accounted separately from the token model.

### 6.3 What we must MEASURE to quote a client

We have **zero metered datapoints.** The run usually cited (`smd-inbox-triage/2026-05-19-run-01-real-gmail.md`) is headed **"Cost telemetry (estimated for this run)"** with tilde-tokens (~14K in, ~3.5K out, ~$0.18/day) — a hand-estimate, **demoted here from "measured" to "estimated from a single run."** To give a defensible monthly figure we must build the rollup (`rollup.py`, "pending Phase E" in `rubric.md`) and capture, from **metered billing** (Anthropic usage API or audit-trail `tokens_in/out` at real cache-hit rate — not hand-counts):

1. **Per-skill metered `tokens_in/out`** at real cache-hit rates.
2. **Measured cache-hit ratio** per seat (the caching swing factor).
3. **Measured `pass@1` and `pass^k`** per skill → CNA/CPS.
4. **Measured `runs_per_month`** per skill from cron + observed trigger volume.
5. **Machine infra cost** (Fly) separately.

**Hard precondition:** no figure is called "measured" until it comes from metered billing. **No dollar figure in this document other than Anthropic's published rates may go in front of a client.** The "~$4–8/month optimized" back-of-envelope in the corpus is a third-party directional estimate, not SMD-measured. The quote comes from the B6 rollup, run against real fixtures, per seat.

---

## 7. Reliability Model

### 7.1 Making long unattended tasks actually complete

Five mechanisms, in priority order:

1. **Keep tasks in the reliable zone (design-time).** METR's empirical curve (Claude 3.7 Sonnet): near-100% pass@1 under ~4 minutes of agent work, ~50% at ~59 minutes, <10% over ~4 hours. The $50 task was a _minutes_ task turned into an _hours_ task by the loop. **Design rule:** every skill's per-invocation agent-work stays sub-4-minute; heavier work decomposes (Class C offload, Class D sub-agent) or falls out of the reliable zone. Annotate each SKILL.md with an estimated duration; flag the unreliable zone. **Class O (orchestration) is the highest risk here** — open-ended planning collapses to GDS 0.44 over duration; keep the turn short and dispatch registered skills rather than improvising.

2. **Favor the gracefully-degrading task class.** Extraction/classification/draft holds GDS ~0.74→0.71 over duration; open-ended planning collapses to 0.44 (arxiv 2603.29231). New skills should be authored as extraction+classification+draft, not open-ended planning.

3. **Checkpoint to a durable cursor.** No native intra-turn checkpoint exists (author-discipline). Multi-tick jobs write a cursor row (last-ID, phase, timestamp) to operator D1 at each phase boundary and resume from it; `context_from` chains fetch→reason. (Build item B1.)

4. **Fail-closed as a completion guarantee — with a verified caveat.** The 8-gate stack terminates a runaway cleanly. The sticky-stop circuit breaker (`sticky_stop.py`) defines a daily cost cap ($50/day default), a 3600s wall-clock single-run cap, tool-failure-streak and refusal-cascade caps; HARD_STOP raises `StickyStopError` that the dispatch path "must propagate, NOT swallow." **CAVEAT (promoted to a viability condition):** its integration into the live Hermes dispatch path on customer-zero is **NOT confirmed** (B3). **Until B3 closes, there is no verified financial circuit breaker on the live Machine** — the literal $50 runaway has no enforced live ceiling today.

5. **Idempotency** (4.11) so a restart/retry doesn't double-act. Author-discipline, unenforced until B5.

### 7.2 Verifying to a billable standard

The grading harness (`operator/grading/`) embeds the right concepts (per-fixture verdicts, ≥4/5 threshold, zero-safety-violation gate, audit-trail schema, captain calibration). Five additions make it billable-grade:

- **pass^k, not pass@1.** Run each fixture N≥5 times; track all-pass rate. A 90% pass@1 is 57% at pass^8 — for a daily skill, ~one failure per two weeks. Wrap `assessment-eval/cli.ts` for N runs.
- **CPS/CNA in the rollup** (§6.3) from **metered** tokens.
- **Meltdown/loop detection as a RUNTIME breaker, not just a metric.** A sliding-window tool-call-entropy check hooked into `operator/adapter/audit_log.py` would have caught the $50 loop after ~5–10 turns. Wire it as a live circuit breaker (alongside sticky-stop), not only a grading number.
- **Failure-mode fixture coverage.** The `inbound-injection/` set (10 files) covers ~2–3 of the 14 Microsoft v2 agentic failure-mode categories; expand to Excessive Agency (OWASP LLM06 — the entitlement-ceiling's job), memory poisoning, tool abuse, human-in-the-loop bypass, **and the child-session taint bypass (B0's regression test).**
- **The billable bar.** For draft-tier work (already in `rubric.md`): the draft saves more time than it costs to review — edits < 30% of words OR strips < 30% of structure. For autonomous-tier: pass^k consistency + zero safety-invariant violations (a hard gate, not a score).

---

## 8. Capability Gaps + Prioritized Build Items

Ordered by impact on the "unattended employee that delivers a retrievable result" claim **and on safety**. The taint fix (B0) is now first — it is a P0 governance defect that the previously-recommended bulk-data strategy would have shipped.

### B0 — Propagate taint into `delegate_task` children _(P0 — verified governance hole; blocks B2a)_

**Gap.** The taint gate keys on `session_id` (`enforce.py:877`). A `delegate_task` child runs under a **fresh, never-tainted session** (verified: `delegate_tool.py:1102-1132` passes only `parent_session_id`; no `session_id` kwarg → child auto-generates one, `run_agent.py:1921`; nothing marks the child's session). A child running the full `workspace_*`+trust stack can read untrusted email AND then autonomously `EXTERNAL_SEND`/`DESTRUCTIVE`/`CODE_EXECUTION` in the same turn — the gate finds the child `INTERNAL`. On the SMD seat (`external_send: autonomous`, `code_execution: autonomous`, `customer.yaml:227-228`) this is exploitable. The "escalate before reading" SOUL rule is a prompt instruction, not a code gate.
**Build.** Either (a) when the parent session is tainted — or unconditionally for any delegation that will read external content — mark the **child's** `session_id` tainted at child construction so the child's trust plugin withholds the gated classes exactly as the parent would; or (b) wire `SESSION_TAINT.mark(child_session_id, trust_class)` into the child's `post_tool_call` the instant its first `workspace_*`/connector read returns `unknown_external` content (the hook already runs in-child). Add a regression test extending `invariant_2`: a child that reads `unknown_external` content cannot subsequently autonomously send/destroy/exec.
**DoD.** The new invariant test fails before the fix and passes after, on staging; a malicious-email fixture delegated to a child cannot trigger an autonomous send.
**Blocks:** B2a (and any Class-D use of delegation on untrusted data).

### B1 — Durable async runner that completes a job and delivers a result _(P0 — the headline capability gap)_

**Gap.** `operator_handoff_task` is wired through the console tool (`tools.ts:168`) and the gate handler (`webhook_gate.py:780`) but dead-ends: no `handoff` `webhook_trigger` in any `customer.yaml` → `translate.py` emits no route → Hermes 404s the delivery (gate 503s when the secret is set, `:795`). The agent never wakes. `/background` and kanban exist but neither survives Machine restart.
**Build.** (1) Add a `handoff` `webhook_trigger` to `customer.yaml`, declared in `operator/contracts/customer-yaml-blocks.yaml` (per the run-substrate-before-merge memory, so the operator-substrate pytest gates it) so `translate.py` materializes the route; (2) **ingest the handoff payload through the SAME fence as other inbound channels** — `shared/inbound.make_envelope` with an explicit `trust_class` (internal only if the caller is cryptographically the principal, else `known/unknown_external`) and **taint-mark the receiving session BEFORE the handling skill runs**; (3) author a handling skill that works the task and delivers to an authored channel; (4) add cursor-checkpoint persistence (D1 row per `job_id`+`phase`) for true cross-restart resume. Add an inbound-injection fixture for a malicious handoff payload.
**DoD.** A handoff posted from claude.ai completes asynchronously, delivers a retrievable result to email/Telegram, **survives a Machine restart mid-job** (via the D1 cursor — not the route alone), and a malicious handoff payload is fenced+taint-marked so it cannot drive an autonomous send.

### B2 — Bulk mailbox processing for the receipt task _(P0 — directly the canonical failure)_

**Gap.** `execute_code` cannot reach `workspace_*` (verified: not in `SANDBOX_ALLOWED_TOOLS`; broker grant hook doesn't run in the sandbox subprocess, whose PID the broker rejects). So "read 40 receipt emails and sum in code" is impossible in one `execute_code` turn. The naive workaround (fetch each body via `workspace_gmail_get`, write to file, then `execute_code`) re-incurs the context cost in the fetch phase.
**Build — pick one, B0 is a prerequisite for B2a:**

- **B2a (lower-risk, available after B0):** Use `delegate_task` as the bulk-mailbox primitive. A child runs the full plugin stack including `workspace_*` (it is a gateway-process thread sharing the gateway PID the broker checks — the mechanism, §4.3), starts fresh, loops the reads in _its own_ context, returns only the summary. **Must not ship before B0** (the child-session taint fix), and must be invoked before the main agent reads untrusted content. End-to-end on a real data-heavy mailbox task is **UNVERIFIED on customer-zero** — run one live fixture first.
- **B2b (higher-effort, cleaner):** Extend the sandbox to reach the broker (add `workspace_*` to a sandbox-reachable set; thread the broker grant through the sandbox RPC transport). This touches the broker authz boundary (the kernel-attested PID gate rejects sandbox subprocesses by design) and must preserve the OP-P0-3 identity-validation property. Do only if B2a proves insufficient.
  **DoD.** "summarize/aggregate N managed-mailbox messages" runs at O(1) main-context cost and completes for N≥40 in a single job, verified on customer-zero against a real fixture — **with B0's taint regression green.**

### B3 — Confirm sticky-stop is live in the dispatch path _(P0 — promoted from P1: no verified live cost ceiling)_

**Gap.** `sticky_stop.py` is implemented and tested but its integration into the live Hermes dispatch path is not confirmed on customer-zero. The $50/day cap and 3600s wall-clock cap that would bound a runaway may be inert live. An unbounded cost runaway with no live cap is a **safety** defect, not only a cost one.
**Build.** Wire the `StickyStopError`-raising check into the dispatch path; add a test proving the dispatch path does NOT swallow `StickyStopError` (the docstring forbids swallowing). Verify on staging that exceeding `cost_daily_cents` and the 3600s wall-clock cap actually halts.
**DoD.** A deliberately-looping fixture trips HARD_STOP on the live Machine; `StickyStopError` propagates uncaught. (Written when the ladder had four rungs; the 2026-09-02 collapse removed the SOFT_STOP step this DoD used to pass through. The stop count is unchanged, so the fixture is unchanged.)

### B5 — Encode the execution-strategy taxonomy in SKILL.md frontmatter _(P1 — the enforcement layer for 4.6/4.10/4.11)_

**Gap.** The SKILL.md governance proto-taxonomy (`action_class`, `trust_ceiling`, `connectors`, `trigger`, `weight`) has **no execution-strategy field**. Nothing forces a new skill onto the right strategy, so the next skill can be a naive in-context loop. The author-discipline strategies (cursor-checkpoint 4.6, verification 4.10, idempotency 4.11) are **aspirational until this lint exists.**
**Build.** Add `execution_class: no_model | compute_collapsible | reason_bounded | deep_reason | authored_assembly | orchestration` (**per-mode** for multi-mode skills). Add a `skill-review` lint that: fails a Class-C skill whose `algorithm.md` does not use `execute_code`/`pre_run`; fails a Class-D skill that doesn't delegate; fails a Class-C/A external-send skill with no idempotency key; **asserts a Class-C skill on a seat with `code_execution: autonomous` is taint-gated end-to-end including via delegation (depends on B0).** Assert the S-guard is present+synchronous in skill families that require it. Tie to the merge gate.
**DoD.** Lint blocks a Class-C skill authored as an in-context loop and a chaser authored without idempotency.

### B4 — Author per-seat auxiliary-model + per-skill toolset narrowing _(P2 — pure cost, with safety carve-outs)_

**Gap.** All 11 auxiliary slots default to the main model; no skill narrows its toolset, so all 40+ tool schemas bill every turn.
**Build.** Author `auxiliary:` overrides in `customer.yaml` routing cosmetic slots (title-gen, etc.) to Haiku 4.5. **Exclude approval-scoring** (do not downgrade the model that scores injected content). For the **context compressor**, if downgraded, add to DoD that taint survives compaction on the cheaper model (re-run invariant_4 against a Haiku compressor). Narrow per-skill toolsets for narrow-surface skills. Both are `customer.yaml`→`translate.py` line items; verify against runtime, not config.
**DoD.** Measured per-turn input-token reduction on a narrow skill; measured auxiliary-call cost cut; invariant_4 green on the downgraded compressor.

### B6 — Verification rollup: pass^k, CPS/CNA, runtime meltdown detection _(P2 — billing defensibility + a runtime breaker)_

**Gap.** The harness measures pass@1-equivalents and zero metered cost datapoints; no pass^k, no CPS/CNA, no loop detector. We cannot quote a defensible monthly number.
**Build.** Implement `rollup.py` (§6.3): N-run pass^k wrapper on `assessment-eval/cli.ts`; CPS/CNA from **metered** audit-trail tokens; sliding-window entropy meltdown check on `audit_log.py` wired as a **runtime breaker** (not only a grading metric).
**DoD.** A per-seat monthly cost projection with measured pass^k per skill — the first defensible client number — and a live meltdown breaker that trips an in-context loop after ~5–10 turns.

---

**Bottom line.** The economic principle holds and the substrate supports it: every favorable task class has a primitive that keeps cost on reasoning, not data — but the cost is _low dollars per skill per month_ (ar-chaser's own figures), not the headline "cents," and we have **zero metered numbers** to quote a client. Three P0s stand between us and the literal "coordinator hire" claim: a verified taint-gate bypass inside the recommended bulk-data strategy (B0 — fix first), no durable async runner that delivers a result (B1), and `execute_code` cannot reach the mailbox (B2). A fourth P0 (B3) is that the only runaway-cost breaker is not confirmed live. Close those, enforce the strategy taxonomy at author time (B5), and ship the measurement rollup (B6) — then the framework is the spec the skills are graded against, and the cost case is proven rather than asserted.

**Source anchors verified live this session:** `code_execution_tool.py` (7-tool sandbox / 300s / 50 calls / 50KB, `workspace_*` absent); `webhook_gate.py:264,780,795` (55s budget, handoff fail-closed 503); `scheduler.py:1052,1105,1487` (no_agent short-circuit, wakeAgent gate, 600s cron timeout); `delegate_tool.py:46,687,732,1102-1132` (execute_code blocked in children, internal model kwarg, child constructed with parent_session_id only / no session_id); `run_agent.py:1921` (child auto-generates its own session_id); `enforce.py:80-85,323,877` (taint-gated classes, gate reads SESSION_TAINT by session_id); `shared/inbound.py:334,373` + `hermes-smd-inbound/__init__.py:83,217` (taint marked only at inbound chokepoints on the dispatch session); `sticky_stop.py:176,180` ($50/day + 3600s caps, StickyStopError must propagate); `operator/skills/ar-chaser/SKILL.md:129,199,209` (~30–50K tokens/run, <$2/month); `smd-inbox-triage/2026-05-19-run-01-real-gmail.md:98-105` ("estimated for this run", tilde-tokens); `customer.yaml:38,227-228` (escalation_model, external_send/code_execution autonomous).

---

## Appendix: Prioritized Build Items (structured)

### B0 — Propagate taint into delegate_task children

**Why:** VERIFIED P0 governance hole. The taint gate (the system's primary code-enforced injection defense) keys on session*id (enforce.py:877). A delegate_task child is constructed with a fresh, never-tainted session (delegate_tool.py:1102-1132 passes only parent_session_id; child auto-generates its own at run_agent.py:1921; nothing marks the child's session). A child running the full workspace*\*+trust stack can read untrusted email and then autonomously EXTERNAL_SEND/DESTRUCTIVE/CODE_EXECUTION in the same turn because the gate finds the child INTERNAL. On the SMD seat (external_send + code_execution both autonomous, customer.yaml:227-228) this is exploitable. The 'escalate before reading' SOUL rule is a prompt instruction, not a code gate. Build: mark the child session tainted at construction when the parent is tainted (or unconditionally for any delegation reading external content), OR wire SESSION_TAINT.mark into the child's post_tool_call on first unknown_external read. Add an invariant_2 regression test.

**Blocks:** Blocks B2a and any Class-D use of delegation on untrusted data. Until it lands, the recommended bulk-mailbox strategy ships an exploitable injection path. This is the single highest-priority item — ahead of the headline capability gaps.

### B1 — Durable async runner that completes a job and delivers a retrievable result

**Why:** The 'unattended employee' claim depends entirely on this and it does not exist. operator_handoff_task is wired through the console tool (tools.ts:168) and gate handler (webhook_gate.py:780) but dead-ends: no handoff webhook_trigger in any customer.yaml means translate.py materializes no route and Hermes 404s the delivery (gate 503s when the secret is set, :795). /background (in-memory) and kanban (Fly-volume SQLite) exist but neither survives Machine restart. Build: add the handoff webhook_trigger (declared in operator/contracts/customer-yaml-blocks.yaml so the substrate pytest gates it); INGEST THE PAYLOAD THROUGH THE INBOUND FENCE with an explicit trust_class and taint-mark the session before the handler runs; author a handling skill; add D1 cursor-checkpoint persistence for cross-restart resume.

**Blocks:** Blocks the core product claim that the Operator completes sustained multi-step work unattended and delivers a result — i.e. that it competes with a coordinator hire. Also blocks any interactively-initiated Class C/D job too large for the 55s synchronous budget.

### B2 — Bulk mailbox processing for the canonical receipt task

**Why:** execute*code cannot reach workspace*_ broker tools (verified: not in SANDBOX*ALLOWED_TOOLS; the broker grant hook rejects the sandbox subprocess PID by design). So 'read 40 receipt emails and sum in code' — the exact $50 failure — has no clean one-turn path today. Two options: B2a use delegate_task as the bulk primitive (a gateway-process THREAD sharing the gateway PID the broker checks, so it reaches workspace*_ where the sandbox subprocess cannot) — UNVERIFIED end-to-end, run a live customer-zero fixture; or B2b extend the sandbox to reach the broker (touches the authz boundary, preserve OP-P0-3, heavier).

**Blocks:** Blocks resolution of the canonical failure mode. B2a is BLOCKED ON B0 — shipping it before the taint fix ships the injection hole. Blocks any Class-C task whose bulk data lives in the managed mailbox.

### B3 — Confirm sticky-stop is live in the dispatch path

**Why:** Promoted from P1 to P0. sticky_stop.py defines a $50/day cost cap (cost_daily_cents=5000) and a 3600s wall-clock cap and is fully tested, but its integration into the live Hermes dispatch path on customer-zero is NOT confirmed. An unbounded cost runaway with no live ceiling is a safety defect, not just a cost one. Build: wire the StickyStopError-raising check into dispatch; add a test that dispatch does NOT swallow StickyStopError (the docstring forbids it); verify on staging that exceeding the caps actually halts.

**Blocks:** Blocks any claim that a repeat of the $50 runaway is financially bounded. Until it closes, there is NO verified financial circuit breaker on the live Machine — every cost-safety statement in the framework is provisional.

### B5 — Encode the execution-strategy taxonomy in SKILL.md frontmatter + lint

**Why:** The SKILL.md schema has governance fields (action_class, trust_ceiling, trigger, weight) but NO execution-strategy field, so nothing forces a new skill onto the right strategy and the next skill can be a naive in-context loop — the $50 failure recurs. The author-discipline strategies (cursor-checkpoint, verification loop, idempotency) are aspirational until this lint exists. Build: add execution_class (no_model/compute_collapsible/reason_bounded/deep_reason/authored_assembly/orchestration; per-mode for multi-mode skills); lint fails a Class-C skill not using execute_code/pre_run, a Class-D skill not delegating, a chaser without idempotency, and (depends on B0) a Class-C skill on a code_execution:autonomous seat not taint-gated end-to-end including via delegation. Tie to the merge gate.

**Blocks:** Blocks enforcement of the entire framework. Without it, strategies 4.6/4.10/4.11 are unenforced author-discipline and a single mis-authored skill reintroduces the runaway. The lint is the only thing that makes the taxonomy load-bearing rather than advisory.

### B4 — Per-seat auxiliary-model routing + per-skill toolset narrowing

**Why:** All 11 Hermes auxiliary model slots default to the main model and no skill narrows its toolset, so all 40+ tool schemas bill every turn. Routing cosmetic slots (title-gen) to Haiku 4.5 is a cut with no quality risk. SAFETY CARVE-OUTS: exclude approval-scoring (do not downgrade the model that scores injected content as low-risk); if the context compressor is downgraded, re-verify taint survives compaction (invariant_4) on the cheaper model. Both are customer.yaml -> translate.py line items; verify against runtime not config.

**Blocks:** Blocks a pure-cost optimization. Lower priority — getting mechanical work off the LLM (B2/execute_code) dominates model-tier choice. Not a capability or safety gate.

### B6 — Verification rollup: pass^k, CPS/CNA, runtime meltdown detector

**Why:** The harness measures pass@1-equivalents and ZERO metered cost datapoints (the one cited run is hand-estimated). We cannot quote a client a defensible monthly number. Build rollup.py: N-run pass^k wrapper on assessment-eval/cli.ts; CPS/CNA from METERED audit-trail tokens (not hand-counts); sliding-window tool-call-entropy meltdown check on audit_log.py wired as a LIVE runtime breaker (would have caught the $50 loop after ~5-10 turns), not just a grading metric.

**Blocks:** Blocks the first defensible client price (per-seat monthly projection with measured pass^k). Also provides a second runtime cost-safety breaker alongside sticky-stop. Without it, every monthly cost figure is a method awaiting measurement, not a quote.

## Appendix: Cost-Model Notes

HONEST STATE OF OUR COST KNOWLEDGE:

WHAT IS SOLID:

- The estimation METHOD (Section 6.1) is sound: $/task = (input + 0.1x cached input + output) / pass@1, ranked by CNA = accuracy/$. The /pass@1 term (failed attempts cost full price) is the production reality the draft correctly included.
- Anthropic published rates (June 2026) are the only non-estimated dollars we can stand behind: Haiku 4.5 $1/$5, Sonnet 4.6 $3/$15, Opus 4.8 $5/$25 per MTok in/out; cache reads at 0.1x ($0.30/MTok read vs $3 input on Sonnet).
- The class-driven cost structure is real: Class N = $0 tokens on quiet ticks; Class C done-right keeps O(N^2) off the LLM; Class C done-wrong (the loop) is the only class where architecture sets cost.

WHAT I CORRECTED (cost critique was right on three of four points):

1. "CENTS" WAS OVERSTATED. The draft generalized the narrowest case to the portfolio. Verified against our OWN reference skill: ar-chaser SKILL.md (lines 129, 199, 209) reports ~30-50K input tokens/run for 5-15 invoices and "<$2/month in tokens." That is LOW SINGLE-DIGIT DOLLARS PER SKILL PER MONTH, not cents. "Cents" holds ONLY for a pure-sum reduce returning one number (the receipt task). The moment the model must read the aggregate to draft/classify (most of the task universe), the payload is tens of K. The doc now states cost by sub-case.
2. THE "MEASURED" DATAPOINT IS AN ESTIMATE. smd-inbox-triage/2026-05-19-run-01 is literally headed "Cost telemetry (estimated for this run)" with tilde-tokens (~14K in, ~3.5K out, ~$0.18/day). Verified at lines 98-105. Demoted from "measured" to "estimated from a single run." We have ZERO metered datapoints.
3. THE $50 ARITHMETIC DOESN'T RECONCILE AND THE DOC NOW SAYS SO. The corpus's "420K tokens ~= $1.26" is an illustration for a DIFFERENT (20-call) task size; the real incident was a 40-message run. The bridge (number of 55s-budget restarts x growing per-turn input) is UNQUANTIFIED — we have not reconstructed the token trace. The doc no longer presents $1.26 and $50 as the same number, and explicitly says the growth-vs-restart split is unknown.
4. THE STICKY-STOP $50/day CAP IS NOT THE $50 INCIDENT. cost_daily_cents=5000 (verified sticky_stop.py:180) is a per-DAY ceiling; the incident was one failed task's cost. They share a number by coincidence. AND the cap cannot be claimed as the bound until B3 confirms it is wired live.

WHERE I PARTLY PUSHED BACK: the cost critique's own conclusion — that a defensible monthly number requires the B6 rollup and none exists today — is correct and I kept it. The only adjustment was presentational: the draft's executive summary read "viability affirmative / cents" more settled than Sections 6-7 supported. Fixed by re-leading Section 1 with "cost MODEL built, cost NUMBER unmeasured, quote blocked on B6."

WHAT TO MEASURE NEXT (the hard precondition for any client quote — B6):

1. Per-skill METERED tokens_in/out (Anthropic usage API or audit-trail tokens at real cache-hit rate — NOT hand-counts).
2. Measured cache-hit ratio per seat (the single biggest swing factor; caching saves up to ~90% on cached tokens).
3. Measured pass@1 AND pass^k per skill (a 90% pass@1 is 57% at pass^8 — for a daily skill that is ~one failure per two weeks). Feeds CPS/CNA.
4. Measured runs_per_month per skill from cron schedule + observed trigger volume.
5. Fly Machine infra cost, accounted SEPARATELY from tokens (wake_policy:always burns CPU regardless of tokens).

RULE: no figure is called "measured" until it comes from metered billing, and no dollar figure other than Anthropic's published rates goes in front of a client. The corpus's "~$4-8/month optimized" is a third-party directional estimate, not SMD-measured — do not quote it.
