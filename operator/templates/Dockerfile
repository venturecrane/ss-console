# SMD Operator — customer-shape Hermes container
#
# Builds Hermes from upstream at a pinned ref, installs the hermes-smd
# overlay plugins + companion CLI, layers in our Operator adapter / skill
# library / connectors / safety-substrate tests.
#
# Memory (ADR 0016, revised 2026-05-30): Phase 1 runs on Hermes' always-on
# flat-file core (MEMORY.md/USER.md). Honcho — the inferred-memory engine —
# is deferred to Phase 2, when it is vendored as the real plastic-labs/honcho
# v3.0.7 source (api + deriver, pgvector). The earlier in-container `honcho-ai`
# server install was fictional (that package is the client SDK, not the server)
# and is removed. Postgres + Redis remain installed below but are NOT started
# at boot in Phase 1 — they exist only to serve Honcho and are reused in Phase 2.
#
# The container runs a multi-process startup under tini (PID 1, zombie
# reaper). bootstrap.sh fetches customer.yaml, materializes profiles, runs the
# safety substrate, and execs the Hermes gateway. See bootstrap.sh.
#
# Per §6 of the locked build plan and ADR 0019: customer.yaml is NOT baked
# into the image. Provisioning writes it to R2 at vaults/<slug>/customer.yaml;
# bootstrap.sh fetches it to /opt/data/customer.yaml on first boot.
#
# Per-customer build context expects:
#   - operator/adapter/         (Operator adapter — Python)
#   - operator/skills/          (canonical skill library — markdown)
#   - operator/connectors/      (Tier-1 custom MCP wrappers)
#   - operator/safety-substrate/ (Phase A.5 invariant fixtures + runner)
#   - operator/templates/bootstrap.sh

# ---------- Build args ----------
# customer.yaml.hermes_ref pins an upstream Hermes release as
# v{YYYY}.{M}.{D}@{40-hex-sha} per ADR 0024
# (docs/adr/0024-hermes-consumption-and-update-cadence.md). The customer.yaml
# validator (src/lib/operator/customer-yaml/helpers.ts) enforces that shape.
# provision-customer.sh splits the pin into the upstream tag (clone target) and
# the SHA (assertion) and passes both here. We clone NousResearch/hermes-agent
# directly; the venturecrane/hermes-agent fork was retired by ADR 0024.
#
# HERMES_REF is the full pin string, retained for provenance and logging.
ARG HERMES_REF=v2026.7.1@7c1a029553d87c43ecff8a3821336bc95872213b

# Upstream date-tag to clone (the part before '@').
ARG HERMES_UPSTREAM_TAG=v2026.7.1

# Upstream commit SHA (the part after '@'). The post-clone assertion verifies
# the cloned HEAD matches this exactly; any divergence (upstream re-tag, stale
# pin) fails the build. This is the integrity check proving we ship unmodified
# upstream Hermes.
ARG HERMES_UPSTREAM_SHA=7c1a029553d87c43ecff8a3821336bc95872213b

# hermes-smd overlay (venturecrane/hermes-smd-overlay): plugin pack + CLI
# (`hermes-smd bootstrap`, `hermes-smd customer-sync`) + the importable
# `shared` policy core the runtime plugins depend on. Pinned to a tagged
# release; bump in lockstep with overlay-side feature work.
#
# v0.2.0 was the FLOOR for a harness-complete Machine: the trust plugin does
# `from shared.outbound_gate import evaluate`, and that module first shipped
# after v0.1.1 (overlay #15). The import assert below still guards it.
#
# v0.3.0 restored Hermes' flat-file memory core (stops the MEMORY.md/USER.md
# tombstoning) and deferred Honcho (ADR 0016 revised); customer-zero booted end
# to end on it with 8/8 safety invariants passing. v0.3.1 retires the (dormant)
# composio per-connection isolation guard — composio is dropped, we connect to
# MCPs directly (ADR 0020 revised). v0.3.2 materializes customer.yaml `mcp:`
# connectors into the profile's mcp_servers block (AgentMail is the first
# vendor), bans the agentmail autonomous-send tools at the trust layer, and
# makes the profile-config write atomic (recovers a root-owned config.yaml).
# v0.4.4 adds the voice render-signal fix (#28): render_sample_block now surfaces
# the stored rhythm signal (sentence-length distribution + punctuation), so the
# pre_llm_call voice block can actually shape a draft instead of injecting a thin
# greeting/signoff label. Superset of v0.4.3 (carries all webhook-gate work too).
# v0.4.5 adds _materialize_telegram_platform (#32, ADR 0033): authors the Telegram
# allowlist from customer.yaml.telegram into the profile config.yaml (fail-closed on
# empty allow_from). Superset of v0.4.4.
# v0.4.6 (#35) adds a LOCAL stdio MCP transport to the connector materializer and
# wires Clio (mcp:clio-oktopeak) as a `clio-mcp` command server with a remapped
# env block (ENCRYPTION_KEY <- CLIO_ENCRYPTION_KEY). Pairs with the clio-mcp
# install + Clio token-seed block below. Superset of v0.4.5.
# v0.4.8 (#39) adds the authenticated read-only `GET /runtime/<kind>` to the
# webhook gate (ADR 0043 path A): the console→Machine runtime read the ss-console
# drill-ins call. Per-customer bearer (HMAC-SHA256(master, customer_id), staged
# below as OPERATOR_RUNTIME_READ_KEY), fresh mode=ro connection + busy_timeout,
# ULID keyset pagination, digests never exposed. Superset of v0.4.7.
# v0.4.9 (#40) fixes two gate env-read bugs the live verify-gate caught: the gate
# is a separate process from the Hermes agent, so it reads CUSTOMER_SLUG (its
# Machine-wide var) with SMD_CUSTOMER_SLUG preferred, and treats
# SMD_D1_AUDIT_BINDING as a direct path or a var-name. read_runtime returns
# honest-empty (not 500) when the audit DB/table doesn't exist yet. Superset of v0.4.8.
# v0.4.10 (#41, #42) makes audit emission actually WRITE: D1Client accepts the
# direct-path binding, and AuditLogWriter.ensure_schema() creates audit_log at
# register (the Machine's bootstrap never applied the per-customer migrations, so
# the table was missing). The runtime-read seam now has data to surface
# (ss-console#1285). Superset of v0.4.9.
# v0.4.11 (#43) is THE activation fix: the umbrella register() fans out to all
# sub-plugins. Hermes ignores the plugin.yaml `plugins:` aggregation list, so the
# umbrella had loaded as a manifest-only plugin with no register() — none of the
# five functional plugins (audit, trust, voice, webhook-router, memory-mirror)
# ever registered on a live gateway. No audit, and no trust/ceiling enforcement.
# v0.4.13 adds the boot AUDIT self-check to the gateway:startup ACTIVATION GATE
# (hooks/smd-overlay-activation): the handler force-loads the overlay into the gateway's
# live plugin singleton, proves trust FIRES (pre_tool_call blocks a banned tool), AND
# proves the AUDIT WRITE path works live (drives a real post_llm_call dispatch and
# confirms a row lands in audit_log) — the ss-console#1285 Q2 question answered
# self-provingly at boot. Fail-closed on any check (os._exit). The pre-gateway invariant
# 8 asserts registration/logic; the live gate asserts the live-turn + audit property.
# v0.4.14 makes the voice pre/post LLM hooks synchronous, matching Hermes'
# synchronous PluginManager dispatcher. Earlier versions returned coroutine objects,
# so live turns skipped voice-context injection and logged unawaited-coroutine warnings.
# v0.4.15 makes that contract fail-closed for every callback in the live manager
# and probes trust through the same block-message interpreter model_tools uses
# immediately before tool execution.
# aa0cc7bc adds the ADR 0045 Workspace capability tools, broker grants, paired
# audit evidence, and a live registry boot gate. Pin the reviewed commit until
# the corresponding overlay release tag is cut.
# Superset of v0.4.14 (synchronous voice) / v0.4.13 (audit self-check) /
# v0.4.12 (activation gate) / v0.4.11 (fan-out).
# f278c23 (#51) adds managed-mailbox targeting to the Gmail tools (optional
# `mailbox` + `from`), for the Operator's executive-assistant access to the
# principal's mailbox; pairs with the ss-console broker per-op subject + send-as
# validation (Wave A — read/draft only). Also carries #50 (retire the
# external-send identity brand) and #53 (security Phase 1: govern code execution,
# fence the managed-mailbox read, taint-gate autonomy, destructive Gmail ops).
# 10cd6599 (#56, Phase B Cut A) adds contracts/consumes.yaml — the overlay
# self-declares every env var it reads — plus an AST conformance test (CI
# fail-closed) and a WARN-only boot reconciliation. The consumer-side twin of
# this repo's env-consumption.yaml; closes the OP-P0-2 voice-break drift class.
# 75cb2de (#58, Phase B Cut C1, ADR 0047) materializes personas[].cron[] into
# Hermes-native cron jobs at bootstrap — closing the silent-drop that left
# customer-zero's hourly inbox-triage never firing. Declarative + idempotent,
# fail-closed on a bad/unsupported entry.
# v0.4.16 (#55) routes audit_log writes through the append-only workspace broker
# (OP-P1-4): the agent uid reads the ledger via the audit-readers group but cannot
# write it. Proven green on customer-zero.
# 2e31067 (#59) FIXES C1 firing: registers cron in each persona's PROFILE home
# (/opt/data/profiles/<slug>/cron) where `hermes -p <slug>` reads it, not the
# bare data home. This SHA is on overlay main ABOVE v0.4.16 (#55), so it carries
# the audit broker AND the cron fix. Verified live on staging — a scheduled turn
# actually fired (last_status=ok) and the broker boots clean on a fresh Machine.
# v0.4.17 (3b7bb99) — code-review 2026-06-12 security wave. Carries #60 (runtime
# config seam: operator.runtime.config/v1 facts snapshot), #61 (closes three
# fail-open seams: trust-ceiling volume-fault fail-open, Svix replay window,
# tool-name case bypass), #62 (fence-completeness CI gate, 0700 profile dirs on
# fresh provision, ceiling-comparison dedup), #63 (README). Superset of #59.
# DO NOT PIN v0.4.17: its gate boot self-check signs a fixed epoch that the
# #61 replay window rejects — the gate crash-loops (no ingress/seam).
# v0.4.18 (1ba04f1) — #66 fixes that self-check (signs a current timestamp,
# regression-tested via svix_self_check()) and carries #65 (audit-dark health
# signal: boot sentinel + audit.writer_wired config-seam fact + rate-limited
# NO-AUDIT-MODE warnings, closes overlay#64). Superset of v0.4.17.
# v0.4.20 (83cc1bd) — #67 audit_export + memory_export runtime-read seam kinds
# (the Machine-side half of ss-console#1355 pull-before-destroy: the
# decommission SeamAuditLogPreserver drains these into the compliance archive
# BEFORE fly apps destroy) + #68 (audit sentinel staleness = writer-pid
# liveness; kills the spurious pid-mismatch degradation live-verified on
# customer-zero). Superset of v0.4.18/v0.4.19.
# v0.4.21 (e0bc503) — #69 fences workspace_calendar_list/get (external invites
# carry third-party content; Captain call 2026-06-12). Superset of v0.4.20.
# v0.4.22 (8706af4) — #71 gate source-stamp (router can route AgentMail
# webhook_triggers) + #57 reply relay (then fail-closed off unless
# authored; the email-in/email-back loop). Both additive;
# safe for all customers (relay no-ops without the flag). Superset of v0.4.21.
# v0.4.23 (90dd7f7) — #72 classify AgentMail MCP runtime tool names
# (mcp_agentmail_*): the prior map keyed the colon spelling that never occurs at
# runtime, so sends defaulted to READ and slipped the trust ceiling + taint-gate
# (an inbound live test sent a reply autonomously on a tainted turn). P0 security;
# closes the bypass for ALL customers. Superset of v0.4.22.
# v0.4.24 (11bcdc5) — #73 recover the reply-channel recipient-lock origin when the
# dispatch session_id differs from the agent-loop session_id (the router records
# under an empty dispatch session_id, which record() dropped → relay found no
# origin → governed draft created but never sent). Address-keyed recovery index,
# injection-safe. Fixes the email-back loop. Superset of v0.4.23.
# v0.4.25 (c6ef10d) — #74 THE inbound fix: AgentMail delivers over Svix, which
# carries the event under `type` not the `event_type` the gate assumed, so the
# webhook route never matched → router silently early-returned before recording
# the recipient-lock origin → relay never sent (agent ran intake autonomously,
# masking it). Gate now stamps event_type from Svix `type`; origin extraction
# reads the Svix `data` envelope. Superset of v0.4.24.
# c410c52 — #78 content-floor disclaimer carve-out: standard UPL boilerplate
# ("Nothing in this note should be read as legal advice", "no attorney-client
# relationship is formed") no longer trips the LEGAL category. Removes a false
# positive, not a real signal — the carve-out is clause-local, so genuinely
# sensitive content elsewhere in the same message (money/contract/scope/real
# legal commitment) still forces draft, and the empty-body fail-toward-draft
# check is unchanged. Fixes REPLY_HELD reason=content_sensitive
# on a benign disclaimer (2026-06-14 live test). Superset of ed96cebe.
ARG OVERLAY_REPO="https://github.com/venturecrane/hermes-smd-overlay.git"
# 0f51821 — #89 ADR 0049 model selection: emit Hermes' native `delegation` block
# from customer.yaml `escalation_model`, render the roster-conditional SOUL
# escalation instruction, and read the skill weight marker. Enables the two-tier
# (light main + escalate-up) seam. Superset of 5a1e3e7 (#92, the security-audit
# remediation wave tip: #93 full Clio MCP classification — closes the live
# autonomous-write fail-open; #92 EFF-07 completeness gate; #90 SEC-05/13 AgentMail
# inbox-read fence; #91 SEC-33 hook-parity guard), which is atop 0e491f9 (#88 ADR
# 0047 phase 2: materialize wake_policy: pre_run_decides; unblocks pilot-law /
# pilot-smokeball boot), carrying #87/#86/#85.
# MCP conversational channel (#97): one ask_operator verb, principal-namespaced
# thread continuity, source==mcp turns fenced+tainted like inbound email. Lands
# the previously-unmerged channel spine (echo/fetch/store retired) + Clerk OAuth
# front door onto main, so reprovisions no longer revert it. Superset of 72fa21d.
# Gmail-search unfence (#99): workspace_gmail_search returns only {id, threadId}
# metadata (no body), so it left _FENCED_READ_TOOLS for UNFENCED_READ_BY_DESIGN —
# fencing the id list tainted the result so the agent would not reuse the ids as
# the message_id for the (still-fenced) body read, making a list→get mailbox read
# impossible. Superset of 8d633a4 (#98 MCP Clerk clerk_subjects plural) + #95 + #94.
# e93a3ff — native tool-governance PR-1: classifies mission-critical Hermes
# orientation reads (read_file/search_files/skills/session/memory/vision),
# internal session writes (todo/record_peer_preference/clarify), keeps
# high-power native tools CODE_EXECUTION, and removes stale unmapped->READ prose.
# 1c2171f — cron reconcile fix (#103): a persona that drops ALL its cron now has
# its orphaned managed job removed at boot (was firing forever); two-pass
# fail-closed across the reconcile set; HERMES_HOME snapshot/restore. Also
# bundles a pre-existing ruff-format fix of webhook_gate.py (no logic change).
# 7f493c6 — B1 durable task-execution substrate (overlay half, ADR 0051): job
# ledger client + hermes-smd-jobs plugin + the in-gateway worker thread. Adds
# the fan-out registration (plugin.yaml) + consumes.yaml env declarations that
# the first staging boot surfaced as missing. Pairs with the console broker
# job_* verbs COPYied into this image. STAGING (iteration 2).
# e8411ca — B1 merged to overlay main (#104, squash): the wave-2 work
# (observability `jobs` runtime-read + job_status/job_cancel MCP verbs, R2 result
# store, B0 worker-session taint) plus the ruff/format/per-plugin-manifest CI
# fixes. Twins unchanged; only the ref moves.
# af20ecd — ADR 0050 B0 (overlay#105): the code-execution INGESTION channel
# (execute_code/terminal/process/computer_use) now taints the session, closing
# the confirmed injection bypass where reading injected content in code left the
# session untainted and an autonomous send allowed. Distinct from e8411ca's
# durable-worker session taint. Additive on top of e8411ca (= 1c2171f + B1 + B0);
# twins unchanged, overlaySha256 stable.
# 713a1e2 — tool-schema parameters-wrap (overlay#106): every overlay-registered
# tool was advertised to the model with EMPTY parameters (a bare JSON-schema not
# wrapped under `parameters`, which Hermes model_tools.py reads). The model saw the
# tool names but could not pass arguments — the Operator could not actually drive
# its 18 workspace_* tools (mailbox reads, etc.). Additive on top of af20ecd;
# tracked twins unchanged, overlaySha256 stable.
# 96835fe — Machine-hosted Smokeball OAuth callback (overlay#110, ADR 0054): the
# connector OAuth consent lands on the customer's own Machine gateway, not the
# shared Worker. Superset of 713a1e2.
# 1d6d5b2 — reply channel (overlay#111, ADR 0055): the Operator is an employee.
# Promotes the recipient-locked autonomous reply from a fenced switch to a
# production capability — plugin hermes-smd-reply, authorized by the organization
# roster (scope.inbound_allow_from, live-read per draft), audit REPLY_SENT /
# REPLY_HELD / REPLY_FAILED. Security logic (recipient-lock, content/fabrication
# re-check, rate-limit) byte-for-byte unchanged. Superset of 96835fe.
# 33b58ad — inbound prompt drives create_draft (overlay#112, ADR 0055): the
# inbound email prompt now tells the agent to compose its reply as a create_draft
# addressed to the sender (the tool the reply channel acts on), not the trust-
# refused reply_to_message. Makes reply delivery deterministic + model-independent.
# Superset of 1d6d5b2.
# 9b20a1a — skill-driving prompt for vendor webhook routes (overlay#118): every
# materialized webhook route shared one prompt (_INBOUND_EMAIL_PROMPT), so a
# verified Smokeball matter.updated reached the agent as an email-draft instruction
# (it tried agentmail create_draft instead of running matter-memo-on-update).
# translate now gives non-email skill-routed channels a skill-driving prompt
# (names the routed skill + skill_view fallback + payload as untrusted DATA); the
# AgentMail email-reply channel keeps the email prompt; MCP route untouched. Not a
# tracked pair (bootstrap/translate.py); all 4 tracked twins unchanged across
# 2b694947→9b20a1ac, overlaySha256 stable. Superset of 33b58ad.
# 0c9d165 (#151, ss #1804, ADR 0071): add `confirm` ceiling value + external_send
# enforcement to plugins/hermes-smd-trust/enforce.py (mirrors the in-tree
# operator/adapter/trust_ceiling.py). Range 539f42c7..0c9d165 touches enforce.py +
# test only — NOT tracked twins, so every overlaySha256 is unchanged; only overlayRef.
# d5187194 (#152, ss ADR 0073): remove the law-firm external_send entry from
# VERTICAL_FLOORS (shared/action_classes.py) — outside-send is the firm's authored
# dial per ADR 0035; the floor machinery stays (empty) for future regulation-
# compelled floors. Range 0c9d165..d5187194 touches shared/action_classes.py +
# enforce.py + config_applier/safety.py + tests — NOT tracked twins, so every
# overlaySha256 is unchanged; only overlayRef. Superset of 0c9d165.
# 78064d3 (#154, ss #1805, ADR 0071): bootstrap/validate.py accepts the `confirm`
# exposure ceiling for external_send (rejects it off the send classes) — keeps the
# on-box config_applier validator in lockstep with the console. Range 8806099..78064d3
# touches bootstrap/validate.py + contract tests — NOT tracked twins, so every
# overlaySha256 is unchanged; only overlayRef. Range 78064d3..3724e78 (overlay#155,
# ss #1781 trigger cooldown) touches shared/gate_trigger_throttle.py (new) +
# webhook_gate.py + bootstrap/validate.py + contract tests — NOT tracked twins,
# so every overlaySha256 is unchanged; only overlayRef. Range 3724e78..17d33d7
# (overlay#156, ADR 0075 recipient-class enrichment) moves ONE tracked twin —
# shared/recipient_classifier.py (CLIENT/VENDOR classes + typed classifier) —
# so that pair's overlaySha256 is re-recorded alongside this bump; the other
# changed files (enforce.py, action_classes.py, customer_config.py,
# bootstrap/validate.py, bootstrap/translate.py) are not tracked twins.
# 5b7318cb (#158, ss ADR 0073): renders the persona's AUTHORED exposure into
# SOUL.md ("Authored autonomy") so the agent acts AT its authored ceiling
# instead of defaulting below it (the ADR 0073 live proof drafted a chase its
# autonomous ceiling allowed it to send). translate.py + tests only — no
# tracked twin moved. Superset of 17d33d7.
# f3e48d6b (#157, ss #1806, ADR 0071): the confirm-over-channel approval stamp —
# a send withheld at the confirm ceiling is captured, an allowlisted-Telegram
# bare-affirmative marks it approved, and the STORED payload is replayed verbatim
# on the re-invoke (shared/pending_send.py + plugins/hermes-smd-trust/{approval,
# enforce,__init__}.py + tests). None are tracked twins, so every overlaySha256 is
# unchanged; only overlayRef. Superset of 5b7318cb.
# ba5b8179 (#159): stop tracking the repo's .worktrees/ gitlinks. #158 accidentally
# committed two live git-worktree checkouts as gitlinks with no .gitmodules, so
# `uv pip install "git+<repo>@<sha>"` here died on `git submodule update --init`
# — it broke EVERY seat image build from #158 (f3e48d6/5b7318cb) onward. Untracked +
# gitignored. Twin recipient_classifier.py unchanged, so its overlaySha256 is unchanged.
# 63a3bca (#162, ADR 0075): proactive outbound relay — deliver graduated chases to
# rostered client/vendor. (Comment carried through the #163 rebase; #163 is its child.)
# fdf8870a (#163, ss #1806, ADR 0071 harden): out-of-band send of the approved
# confirm payload — the overlay dispatches the approved send itself (the LLM does
# not reliably re-invoke on "yes"), re-authorized through the SAME evaluate_tool_call
# gate + emitting CONFIRM_SEND_DISPATCHED/FAILED audit rows. Carries #162
# (proactive outbound relay, ADR 0075) which merged just ahead of it. Range
# 0f3e929..fdf8870 touches plugins/hermes-smd-trust/{outbound_send.py(new),__init__.py}
# + plugins/hermes-smd-reply + tests — NOT tracked twins, so every overlaySha256 is
# unchanged; only overlayRef. Superset of 63a3bca (#162) / 0f3e9291 (#161).
#
# 36fa158d (overlay #165 + #167, ss #1915/#1916): the escalation-ledger mediated
# tools (hermes-smd-escalation plugin: escalation_append via the broker verb +
# escalation_state over the ledger twin) that replace the refused execute_code
# append snippet, plus the durable-job tool mappings (unmapped ⇒ REFUSED had
# left both tool sets inert at runtime — found by the WP-D live proof). NEW
# tracked twin pair: shared/escalation_ledger.py <-> operator/workspace_broker/
# escalation_ledger.py (byte-identical, sha c4882668). Superset of d6739132.
# 3ffc2d1f (overlay #170, ss #1941): peer-memory capture nudge — the ADR 0048
# learned lane's WRITE side. pre_llm_call now injects the record_peer_preference
# capture instruction on every sender-attributed turn (zero rows on every seat
# because nothing ever told the agent to record). Touches only
# plugins/hermes-smd-peer-memory + its tests — no tracked twins, overlaySha256
# values unchanged; only overlayRef. Superset of a16f9580.
# aa7d78f2 (overlay #171, ss #1941 probe find): peer-memory keys the peer on
# the Svix-verified sender via a claim-once unbound-origin handoff on
# SessionInboundOrigin (the dispatch session_id is empty on the live email
# path, and Hermes threads the ROUTE as sender_id — the first captured
# preference keyed webhook:agentmail, a channel not a person). Touches
# shared/inbound.py (contract-tested vendored copy, not a sha-tracked twin) +
# plugins/hermes-smd-peer-memory + tests — every overlaySha256 unchanged,
# only overlayRef. Superset of 3ffc2d1f.
# eb17f3cb (overlay #172, ss #1943): inbound taint/fence rendezvous — the
# chokepoint claims the fresh dispatch-unkeyed PENDING bucket and fences +
# taints under the turn's own session id (the live email path enqueues under
# session=''; the wall never fired). Rostered senders (scope.inbound_allow_from
# — the list that already authorizes autonomous replies) classify internal: no
# fence/taint, keeping the ADR 0048 capture lane alive; strangers stay
# unknown_external and now actually hit the wall. shared/inbound.py +
# webhook-router + hermes-smd-inbound + tests — no sha-tracked twins,
# overlayRef-only. Superset of aa7d78f2.
# f8808c6c (overlay #173, ss #1946): per-turn tool-surface trim — translate
# emits agent.disabled_toolsets on every profile config (browser/computer_use/
# media/social/session_search; + workspace when the seat authors no
# google_auth), and agentmail blocked_tools excludes 8 inbox-admin/destructive
# tools via native mcp_servers.tools.exclude. No send/draft/read tool leaves
# the menu (ADR 0025 unchanged). Measured on the live payload
# (vfy_01KXKJEEV1R4EPYFKA6J7YDH16): first call 40,457 tokens, 70% tool
# schemas; trim = -7,272 tokens/turn of 5m prompt-cache write.
# bootstrap/translate.py + bootstrap/mcp_registry.py + tests — config
# emission only, overlayRef-only. Superset of eb17f3cb.
# 73a2df8 — #174 report emails render an html half at send time. The report
# skills author markdown (## headings, numbered items) that was only ever sent
# as plaintext, so a reader saw literal hash marks. shared/report_render.py
# renders that markdown to an inline-styled html body; hermes-smd-trust attaches
# it in pre_tool_call AFTER every gate allows (purity invariant: the html adds
# no content the fabrication/floor/taint scans did not already see). Block
# structure gates the render, so prose replies stay byte-identical. Superset of
# f8808c6.
# 9b3f712 — email-channel seam (ss#1978 / ADR 0078), slices 3+4 atop #176's
# tool-class map. #177: InboundMessage DTO + provider NORMALIZERS ({agentmail,
# msgraph}); the webhook-router derives origin fields from the DTO (behavior-
# identical for AgentMail — golden tests unmodified); D3 rejects an inbound-bound
# Email adapter with no seam normalizer; cross-repo validator parity for
# send_identity + msgraph_auth. #178: the Graph delta poller (daemon thread in the
# gate; re-injects each polled message through the SAME signed loopback as webhook
# mail so fence/taint/roster apply identically; durable cursor + seen-id dedupe;
# echo-loop guard) + a REAL send-gate bypass fix (body_text was omitted from the
# fabrication-scan keys; an msgraph send scanned "" and silently allowed — now
# scanned, and body-required send tools fail closed) + Graph reply/confirm
# transports (fail closed, never fall back to AgentMail) + the msgraph inbound
# prompt. Additive + behavior-neutral for every non-msgraph seat; the agentmail
# prompt is byte-untouched. Superset of 68ecea3.
# e031c09 — mcp:msgraph-mail registry entry (overlay#180, ss#1978): materializes
# the msgraph-mail connector's OUTBOUND tool surface (send/reply/draft/list/read)
# so an msgraph seat can act, not just receive — the smd-staging live-fire showed
# the connector was baked but never launched (no registry entry). Range also
# carries overlay#179 (connector-health ledger, ss#1990/ADR 0080). NONE of the 8
# tracked twins changed (git-diff-verified 9b3f712..e031c09). Superset of 9b3f712.
# 2026-07-30: 23ff1575 -> 12fea42b (ss#2070 Phase 1, the sustained-dialogue
# program). overlay#196 authored send_policy (trust-class reply caps +
# internal dialogue exemption + reply backstop), #197 held-reply persistence
# and the auto-release sweeper, #198 deterministic session->origin binding by
# message id (closes overlay#195, the wrong-thread reply race), #199 the
# per-person usage meter + usage_export runtime-read kind. NONE of the 8
# tracked twins changed (git-diff-verified 23ff1575..12fea42).
# 2026-07-30b: 12fea42b -> 711310ff (overlay#200, the two defects the Phase-1
# rehearsal exposed — vfy_01KYTG0B88R3B5K0D7FKPACRZT). The reply relay acted on
# the tool NAME alone, so a create_draft that returned "Message not found (HTTP
# 404)" still emailed the client and the agent's retry emailed the same answer
# again; it now requires a draft the tool confirmed and enforces one reply per
# inbound message id. And the relay re-ran the fabrication gate WITHOUT the
# provenance caption allowlist the drafting path passes, so the trust gate
# allowed a draft naming matters read from Smokeball and the relay then blocked
# the identical body as fabrication:tier2_citation — a whole class of question
# unanswerable by email, held silently. NONE of the 8 tracked twins changed
# (git-diff-verified 12fea42..711310f).
# 2026-07-30c: 711310ff -> 7243d3a7 (ss#2079 W0-prime seam, ADR 0083). overlay#201
# makes `seat` and `output_classes` live-writable and pins `personas.*.tone` as
# NEVER live-writable — tone is rendered into SOUL.md by translate at BOOT and
# the applier does not re-run translate, so a live tone apply would report
# APPLIED while the agent kept the old register (a silent partial apply, worse
# than an honest rejection). The register instead lands on the next restart via
# the unconditional boot fetch. NONE of the 8 tracked twins changed
# (git-diff-verified 12fea42b..7243d3a; all 8 sha256 recomputed at 7243d3a and
# identical). Superset of 711310ff — 711310f is 7243d3a's parent.
# 2026-07-31: 7243d3a7 -> 151d1340 (ss#2084 spec loader, ADR 0083). overlay#202
# installs the customer's authored voice/format specs as a ROOT-OWNED tree and
# adds the per-turn read mark that makes "the agent read its spec" checkable
# rather than assumed. Root ownership is the point: read_file is READ-class,
# unfenced, and does not taint, so an agent-writable spec would be a persistent
# self-authored prompt-injection channel surviving restarts. NONE of the 8
# tracked twins changed (git-diff-verified 7243d3a..151d134; all 8 sha256
# recomputed and identical). Superset of 7243d3a7.
# 2026-07-31b: 151d1340 -> d28f3713 (overlay#204, the defect the FIRST live
# install of a spec exposed — vfy_01KYWVR8PBBEP85W3F5SSNC9FD). mkdir(parents=True)
# creates intermediate dirs with 0o777 & ~umask and does not apply the caller's
# mode to them; the applier hardened only spec_dir and the LEAF, so classes/ kept
# root's 0o750 umask default. No world execute => the agent could not TRAVERSE to
# a spec whose own mode was a correct 0644. Since the gate passes only on a
# verified read, every staff autonomous send would have downgraded to draft
# permanently, with a healthy-looking tree and no error anywhere. The pre-existing
# test asserted classes==0755 and PASSED, because the runner's umask is 0o022 and
# the buggy code yields 0755 under it — it measured the environment, not the code.
# Touches spec_applier/applier.py + tests/test_spec_applier.py only — NOT tracked
# twins (git-diff-verified 151d134..d28f371, zero intersection with pair
# overlayPaths) — so every overlaySha256 is unchanged, only overlayRef.
# 2026-07-31c: d28f3713 -> 9cbc7ecf (overlay#205/#206/#207). #205: a client's FIRST
# authored spec no longer needs a reboot - the pointer refresh moved hermes-side
# into pre_llm_call, because the profile tree is hermes-owned and a root re-stamp
# would recreate the 2026-07-16 root-owned cron store. #206: the fail-closed
# message names the real exception instead of blaming the trust ceiling for any
# fault in ~100 lines. #207: authored FORMAT is now CHECKED and non-conforming
# output is refused (ADR 0083 criteria 7/8) - format was declared, delivered and
# manifested but read by no code. NOT tracked twins (git-diff-verified
# d28f371..9cbc7ec) - every overlaySha256 unchanged, only overlayRef.
# 2026-08-01: 9cbc7ecf -> a8bffaf6 (overlay#210, ss#2122 - the ledger records what
# AUTHORIZED the call, not just the call). The trust gate computed the whole
# authorization trail on pre_tool_call and the audit plugin wrote the row on
# post_tool_call with nothing joining them, so ceiling_level was null on 100% of
# 4130 live pilot rows and matter_ref on all of them. shared/trust_decision.py
# carries the decision across; the fallback slot is THREAD-LOCAL because
# delegate_task runs worker threads with their own event loops
# (/opt/hermes/model_tools.py:66-80) and a process-global slot would hand one
# thread's ceiling to another thread's row - a mis-attributed ceiling is worse
# than a null one. Also stops the boot self-check alarming: it drove the real
# post_llm_call fan-out with model="boot-selfcheck", which the cost meter
# correctly reported unpriced and logged as INVARIANT_VIOLATION every boot (all
# 61 such rows on pilot were that alarm). UNLIKE the recent bumps, this one DOES
# move a tracked twin: plugins/hermes-smd-audit/emit.py is a pair, so its
# overlaySha256 is re-recorded 0bbc831f -> b34e7440. The adapter side
# (operator/adapter/audit_log.py) is unchanged - the new fields ride the existing
# metadata JSON and the matter_ref column already existed - so its sha256 holds.
# 2026-08-02: 3af998c3 -> ea752ab5 (overlay#215 Sentry identifier scrub,
# ss#2150 P0 / DPA Exhibit B-1): matter-number + GUID shapes in redact_text,
# extra/contexts/logentry.params walked, breadcrumb data recursive,
# consumes.yaml DSN note reconciled. The range is exactly that one squash
# commit; it touches shared/sentry_init.py, tests, contracts/consumes.yaml —
# NO tracked twin moves, every overlaySha256 unchanged, only overlayRef.
# 2026-08-09: 62da0504 -> 73007247 (overlay#226, ss#2171): the identifier gate
# REFUSES — report-only becomes blocking at all three call-site paths (draft
# prose, draft structured-only, external send). Caller-side refusal (the
# filter's Mode enum untouched); block set = every kind except NAME by
# exclusion; ambient dates (utc today/yesterday) verify against the clock;
# empty-register carve on the DRAFT gate only — the send gate blocks with an
# empty register; SMD_IDENTIFIER_GATE_MODE=report is the operator-only
# rollback lever (unset/garbage = block). The range moves ONE tracked pair:
# shared/identifier_filter.py (posture docstring only), overlaySha256
# re-recorded; the substrate copy gets the same docstring in this PR.
# 2026-08-09: 73007247 -> ba6d116a (overlay#227, ss#2148): durable-credential
# age rides the heartbeat. connector_check.token_ages() reports the Smokeball
# refresh-token file's mtime age; the heartbeat ships it as
# connector_token_age — a SEPARATE field from the health map (synthesizing a
# consecutive_failures=0 entry would falsely RESOLVE an open connector_down
# alert). Feeds the console's connector_token_expiring pre-expiry condition
# (this PR). Touches shared/connector_check.py + shared/heartbeat.py — NOT
# tracked pairs; every overlaySha256 unchanged, only overlayRef.
# 2026-08-10: ba6d116a -> 64408467 (overlay#228, ss#2222 gate 3): authored
# initiation authority — new plugin hermes-smd-initiation injects the
# person-initiation disposition per sender-attributed turn (rostered direct
# ask initiates manual skills; admin-reserved skills require scope.admins;
# embedded content never initiates; no improvised skill reports). Closes the
# card-rehearsal R1 finding (ss#2221: answer/improvise/refuse free-styled on
# one channel). New plugin + root plugin.yaml + README + test only — NOT
# tracked pairs (git-diff-verified); every overlaySha256 unchanged.
# 2026-08-10b: 64408467 -> 8833b3fe (overlay#230, ss#2222 gate 3 second
# half): the injection never fired live — pre_llm_call's sender_id on
# webhook-dispatched turns is the ROUTE (webhook:agentmail), never the
# person (the ss#1941 shape). Initiation now resolves the Svix-verified
# sender via SESSION_INBOUND_ORIGIN (get → claim-once unbound handoff,
# ambiguity declines) and RE-KEYS a claimed origin under the session;
# registration moved BEFORE peer-memory so the re-key feeds its get().
# plugin.yaml + initiation plugin + test only — NOT tracked pairs
# (git-diff-verified); every overlaySha256 unchanged.
# 2026-08-10c (overlay#233 + #232, ss#2222): classify
# mcp_smokeball_render_docx_template = INTERNAL_WRITE + pin coverage — the
# coordinated half of ss#2241's renderer; without it the boot conformance
# probe refuses the unmapped tool and kills boot (live-caught, pilot v116).
# Range also carries overlay#232 (jobs teardown-shaped broker fix). Files:
# shared/action_classes.py + shared/job_worker_runtime.py + tests — NOT
# tracked pairs (compare-verified); every overlaySha256 unchanged.
# 2026-08-10d (overlay#234, ss#2222): establishment classifies the VERIFIED
# sender, not the webhook route. Live-caught — an authored admin emailed a
# blessed corpus and was refused "only Operator admins can establish"; the
# possession ceremony sits behind that predicate and was never reached, so no
# challenge was ever sent. Same ss#1941 shape initiation fixed in overlay#230;
# establishment carried it because nothing re-checked the other sender_id
# consumers. Range ALSO carries the ss#2234 spec-control work (declared-but-
# uninstalled voice spec: persona voice + alert internally, draft + alert
# outbound — Captain ruling) which lands on seats with this bump. Compare-
# verified: zero intersection with tracked pair overlayPaths.
# 2026-08-11 (overlay#240, ss#2167): the outbound matter-identity gate gains the
# two things it lacked. (1) It recognised a matter only as a raw UUID —
# `cited_matters` extracts human-readable numbers, but `_resolve_cited` compared
# them against a map keyed by connector id, so a letter citing "2026-PI-101"
# returned *unresolved* even against a CLOSED party set. Every shipped test
# seeded a UUID body, so the suite and both kill-tests passed straight over it
# (vfy_01KZRRW59N6HS3DHVQJRNMKVHW). Matters are now aliased by number; a number
# seen pointing at two matters is withdrawn, never guessed. (2) The gate never
# ran on the reply lane at all: it is guarded by `is_send`, true only for
# EXTERNAL_SEND*, and that lane calls create_draft (INTERNAL_WRITE) then relays
# the draft out over REST (vfy_01KZRRW066Y70TFEYKGQX6ME76). It now evaluates at
# the relay seam. Its exemption deliberately does NOT read `recipient_class is
# INTERNAL`: an inbound-roster match classifies INTERNAL before the typed roster
# is consulted, so that spelling would have exempted 100% of the lane and
# shipped a control that cannot fire (the conflation is filed as ss#2263).
# `unresolved` records MATTER_UNRESOLVED and does NOT hold — get_matter fires on
# 8 of 86 reply turns, so holding would withhold correct client replies at an
# unmeasured rate (Captain call). Files: shared/matter_binding.py +
# shared/matter_gate.py (moved there from plugins/hermes-smd-trust/ so the reply
# plugin can import it) + hermes-smd-{trust,reply,audit} + tests — NOT tracked
# pairs; diff-verified zero intersection with tracked overlayPaths across
# 57818798..658169eb; every overlaySha256 unchanged, only overlayRef.
# 2026-08-11b (overlay#245, ss#2269 + ss#2262): the matter gate can now SEE the
# number forms the firm's matters actually use. Alternation is first-match-wins
# and the short branch sat ahead of the long one, so the real matter
# `PI-2026-0001` matched as `PI-2026` and `-0001` was left behind; the truncated
# token resolved to nothing and the send was not withheld. Measured live on the
# pilot (vfy_01KZRZH044CH4N5EEKHQ9A6KHW), where PI-2026-0001 is the ONE matter of
# nine carrying a complete party list — so the gate could not withhold a
# number-cited send on that seat at all. ss#2167's number->id join was correct
# the whole time and this regex defeated it. Fix is longest-alternative-first
# plus IGNORECASE; IGNORECASE is safe here because _resolve_cited keeps only
# tokens that resolve to a matter the session actually read, so a false positive
# cannot manufacture a verdict — which is what let ss#2262 close in the same
# three lines. Also corrects the comment claiming byte-compatibility with
# identifier_filter._CASE_RE: they had diverged, and the comment saying otherwise
# is what hid it (same shape as ss#2252). Files: shared/matter_gate.py + tests —
# NOT tracked pairs; diff-verified zero intersection across
# 1d73e2c0..e9bfe987; every overlaySha256 unchanged, only overlayRef.
# e9bfe987 -> ad647365 (2026-08-11c, ss#2258 + ss#2255): a seat sends from ITS OWN
# inbox, or refuses. resolve_inbox_id took inboxes[0] on the reasoning "single
# tenant per Machine, so the first inbox is the agent's own". AGENTMAIL_API_KEY is
# account-wide — the agentmail block in provision-customer.sh says so outright
# ("It can reach the shared account's OTHER inboxes (cross-tenant)") — and the
# listing is newest-first: probed live it held EIGHT inboxes, the pilot's own at
# index SIX, a probe inbox created the previous afternoon at index 0. The caller is
# _dispatch_approved_send, which fires the moment a human approves a draft, and a
# client seat's day-one posture is external_send: draft_for_review — so EVERY
# approved letter went through it. The moment ashton-price's inbox is created it
# becomes inboxes[0] and every OTHER seat starts sending as that client. Latent
# only because CONFIRM_SEND_DISPATCHED has 0 rows ever, which is also why nobody
# caught it. Now: authored AGENTMAIL_INBOX_ADDRESS else the <slug>@agentmail.to
# convention, and the address MUST be in the listing — a miss RAISES rather than
# falling back, because sending from another firm's mailbox is worse than not
# sending. Also sweeps overlay#246 (docs; stranded 29 min after the last bump) and
# overlay#247 (webhook fallback instructs read_file, not the absent skill_view,
# ss#2255). Files: plugins/hermes-smd-trust/outbound_send.py,
# plugins/hermes-smd-reply/__init__.py + tests — NOT tracked pairs; every
# overlaySha256 unchanged, only overlayRef.
# ab352d5b -> 88f6daa (2026-08-20, ss#2444 PR-2 + ss#2464): three commits, and the
# third is why the pin goes to overlay main rather than to an intermediate.
# overlay#284 proves the ss#2167 matter-mixing fence survives the v0.19 tool
# rename — the BINDING was already tested, the wire NAME was not, and the rename
# is exactly what silently unbound every connector tool on the first v0.20 build
# (overlay#283). overlay#285 added a purpose-made SMOKEBALL_VISION_ANTHROPIC_KEY
# for the scanned-document read path; the Captain's review found that premise
# false and overlay#286 RETIRES it back to the seat's own ANTHROPIC_API_KEY,
# which provisioning already stages per seat. So pinning main means no new secret
# to stage for any seat, and pinning #285 alone would wire a seat to a credential
# nobody stages. Containment checked before writing this pin:
# `git merge-base --is-ancestor ab352d5b 88f6daa` — a fast-forward, no divergence.
# Files: plugins/hermes-smd-trust tests, connectors/registry — NOT tracked pairs;
# every overlaySha256 unchanged, only overlayRef.
# 88f6daa -> 78c2544 (2026-08-20, ss#2444 PR-2 follow-up): one commit, and it is a
# LIVE DEFECT found on the paying client's seat 17 minutes after promoting it.
# Hermes v0.20.4 inverted the order two hooks fire in --
# v0.18 was pre_tool_call -> post_tool_call -> transform_tool_result;
# v0.20.4 is pre_tool_call -> transform_tool_result -> post_tool_call,
# observed on five consecutive tool calls on hermes-ashton-price's own log
# (vfy_01M0G7DYTBHAGDRQYXX02DKMZJ). hermes-smd-inbound fences at
# transform_tool_result, so hermes-smd-establishment's on_post_tool_call stopped
# receiving the connector's raw text and started receiving the FENCE. json.loads
# died at char 1 ("[" then "U" of UNTRUSTED) on 100% of document reads -- 0.18: 3
# reads / 0 failures, 0.20.4: 2 reads / 2 failures -- so read_capture never
# recorded, and voice-establishment and document-library-establishment, the first
# two things a new firm is asked to run, would have refused in front of the
# client. overlay#287 adds shared.inbound.unwrap_inbound (nonce matched as a
# BACKREFERENCE so a forged inner sentinel cannot terminate the real fence) and
# parses through it. Pass-through on unfenced input is deliberate: correct under
# BOTH hook orders, so the ordering never becomes an invariant again. Falsifier
# run: reverting only the unwrap call reproduces the identical production
# traceback while the other 46 tests in that file still pass.
# STILL AT RISK, unconfirmed, deliberately not swept in: hermes-smd-audit:117
# (output_digest), hermes-smd-reply relay.py:180, hermes-smd-trust:348/:359 all
# consume kwargs["result"] the same way. Files: shared/inbound.py,
# plugins/hermes-smd-establishment + tests -- NOT tracked pairs; every
# overlaySha256 unchanged, only overlayRef.
# 78c2544 -> 16770d3 (2026-08-20, ss#2489): one commit, and it is the second
# live defect this evening on the paying client's seat. Four Operator replies
# reached the firm as ONE UNBROKEN BLOCK -- sections, lists and paragraphs all
# collapsed. Graph's /reply action composes the reply message IN HTML (its own
# reference says so where it explains `Prefer: outlook.timezone`), and this
# transport hands it a plain-text `comment`, so every newline dies to HTML
# whitespace collapsing before the message leaves the tenant. Proven from the
# wire, not inferred: the raw MIME of message 1a020fe88536d941 is
# multipart/alternative whose text/html part carries the model's text inline in
# <body> with ZERO <br>, and whose text/plain half is that same HTML with the
# tags stripped -- which is why both halves were equally unreadable and no mail
# client could rescue it. Second half of the cause: shared/report_render.py is
# escape-by-default, purity-tested and live-proven (vfy_01KXMVRX1C2NAMYKH6JSQWTJEV),
# but hermes-smd-trust injects it as args["html"], a field only the AgentMail
# transport reads, so it had never once run on an msgraph seat. overlay#288
# renders the body in `_send_msgraph_reply` and passes it beside the plain text;
# the broker half (operator/workspace_broker/msgraph_ops.py, this same PR) posts
# message.body instead of comment. Both sides treat the html as OPTIONAL on the
# wire, so the two repos deploy in either order. Falsifier run: reverting only
# the render call fails exactly the three new structure/escaping tests while the
# other 14 in that file pass. Files: plugins/hermes-smd-reply,
# shared/msgraph_broker.py + tests -- NOT tracked pairs; every overlaySha256
# unchanged, only overlayRef.
# 16770d3 -> 7a8ba42 (2026-08-20, ss#2489 follow-on): one commit, found by
# reading the wire on the reply the previous bump had just made readable. The
# formatting fix worked -- real <p>/<ul>/<li> reached the firm -- and the same
# MIME showed report_render emitting INVALID HTML underneath it. The <ul> opened
# at the introduction's first section was never closed, so three following
# paragraphs rendered INSIDE the list and the second bullet group joined the
# first list instead of opening its own; </li> outnumbered <li> 7 to 4.
# Reproduced offline at the old ref against the same source shape, and the
# counts invert at the new one (2 uls, 4/4 items, 0 trapped paragraphs).
# TWO CAUSES REMOVED, NOT PATCHED: _flush_paragraph emitted a <p> without
# closing the list (three sibling block handlers each remembered to; the rule
# now lives with the emitter, so a caller cannot forget it), and
# _close_open_item decided item state by sniffing parts[-1].endswith("</li>"),
# which is false both for a genuinely open item and for a paragraph emitted
# inside a list -- replaced with an explicit flag. NOT INTRODUCED BY overlay#288:
# this renderer has shipped in AgentMail report emails since 2026-07-16
# (vfy_01KXMVRX1C2NAMYKH6JSQWTJEV), so any report mixing a list with a following
# paragraph has been malformed there too; #288 only made it visible on a channel
# someone was reading closely. Nine green tests missed it because neither corpus
# in that file had a paragraph after a list item -- the live A&P shape is now a
# third corpus and joins the purity parametrize. Falsifier run: reverting only
# the close_list line fails exactly 3 of the 6 new tests while the other 13 pass.
# Files: shared/report_render.py, tests/test_report_render.py -- NOT tracked
# pairs; every overlaySha256 unchanged, only overlayRef.
#
# 7a8ba42 -> d35e0b0 (overlay #293, ss#2488 part 2): the webhook gate ships the
# gateway's loop-pulse age and the part-1 supervisor's state on the control-plane
# heartbeat (gateway_loop_ok, gateway_loop_age_seconds, gateway_supervisor_state,
# gateway_restarts_last_hour; shared/gateway_loop_check.py + shared/heartbeat.py).
# The gate is the one process on the seat that survives a gateway wedge, which is
# exactly why every liveness signal stayed green for 33 minutes on 2026-08-20 --
# and exactly why it can be the process that reports one. Two signals because one
# is racable: the part-1 restart also refreshes the heartbeat, so an age-only
# alert can be overwritten before the console's 2-minute cron samples it; the
# kill-ledger count cannot be. Arming latch mirrors part 1 (a stale beat from the
# previous boot is on the volume at every cold start and must not page on deploy).
# Consumer side: migration 0107, heartbeat.ts, fleet-alerts gateway-loop.ts, the
# admin roster. Files: shared/gateway_loop_check.py (new), shared/heartbeat.py,
# contracts/consumes.yaml, tests -- NOT tracked pairs; every overlaySha256
# unchanged, only overlayRef.
#
# 4ca6682 -> 349d86b (overlay#299 + #300, ss welcome rehearsal): two commits, and
# what they exist for is what the A&P Operator did with the old refusal. Drafting
# the setup reply to the firm's administrator, the Tier-2 citation gate fired
# four to six times in one turn on ordinary comparison prose; the refusal said
# only "content matching legal-citation patterns", naming neither the shape nor a
# fix, so the model retried blind and then shipped a trimmed reply that told the
# client a gate had blocked it. #299 gives the refusal a per-pattern hint that
# names the kind that hit and the remedy for it, and that never echoes the
# matched text (a refusal quoting the cite hands it back to the drafter). It also
# closes the hole those retries were finding: "Palsgraf versus Long Island
# Railroad" was caught by nothing, because CASE_NAME_RE folds only "v" and "vs".
# The new CASE_NAME_VERSUS_RE keeps its parties case-SENSITIVE, unlike
# CASE_NAME_RE, so "apples versus oranges" still passes while the capitalized
# form blocks, and provenance harvests versus-form captions so a caption the
# record spells in longhand registers as the same caption. #300 puts the same
# "versus" fold in canonical_caption on both sides of the repo boundary, so an
# allowlist entry registered by one copy still matches a hit canonicalized by the
# other. Files: shared/citation_filter.py (a TRACKED pair; overlaySha256
# re-recorded one-sided in overlay-pairs.json), shared/outbound_gate.py,
# shared/provenance.py, tests.
#
# 07ed486 -> b782926c (overlay#306, ss#2537 follow-up). The pilot seat
# crash-looped at boot on 2026-08-21 22:57Z because ss#2537 authored
# `commitment: confirm` on pilot-smokeball and ashton-price while the on-box
# config validator still refused `confirm` on every non-send class. The
# enforce branch that gives it meaning landed in overlay#303, carried into the
# 07ed486 bump; the validator had not moved with it, so the seat rejected the
# config the runtime was ready to honor. #306 accepts `confirm` on
# `commitment` in a persona's `exposure` and nowhere else: never on
# `exposure_ceiling` (the runtime entitlement dial derives that from send
# tiers) and never on another class, so it still cannot be authored where it
# would do nothing. This PR un-parks both seat lines in the same change, so
# the config and the validator that accepts it land together. Files:
# bootstrap/validate.py, tests/test_customer_config.py -- NOT tracked pairs;
# every overlaySha256 unchanged, only overlayRef.
#
# e9d9fae1 -> 2b0c786d (overlay#308, ss#2537 follow-up). Read live on
# pilot-smokeball 2026-08-22: SOUL.md rendered the persona's
# `commitment: confirm` exposure with the generic confirm sentence, which was
# written for sends (prepare the action, request explicit approval in the same
# turn). The model did exactly that -- it described the matter it would create,
# asked for a yes in its own words, and never called
# mcp_smokeball_create_matter. So the gate never minted an act proposal, the
# administrator's yes had nothing to bind to, and the readback the
# administrator saw was the model's own composition with identifiers in it,
# which is the one thing overlay#303/#305 exist to prevent. #308 adds a
# per-(action, ceiling) SOUL override: at (commitment, confirm) the line now
# tells the model to CALL the tool as soon as an Operator administrator asks,
# relay the gate's returned act line verbatim, and on the administrator's
# answer call the same tool again with the same values. Send classes keep the
# generic sentence. Files: bootstrap/translate.py,
# tests/test_bootstrap_translate.py -- NOT tracked pairs; every overlaySha256
# unchanged, only overlayRef.
#
# 241df3bc -> 119f6bf2 (overlay#311, ss-console#2546 PR 2). ONE merge in the
# range, checked rather than assumed: `git log 241df3bc..119f6bf` on the
# overlay returns a single commit.
#
# WHAT REACHES A SEAT AT THIS PIN. Under ss-console#2529 a person who is NOT an
# Operator admin could state a firm-level rule and be told an admin could apply
# it, and then nothing happened: no administrator was told there was anything to
# answer, an administrator's "no" did nothing at all, and a rule nobody answered
# was deleted by the broker sweep, so the person who asked could not even be
# told it had lapsed. From this ref the request is EMAILED to the administrators
# the firm names on scope.rule_requests_to (traffic, not authority: an admin not
# named receives nothing), the person who asked is told on apply, on decline and
# on lapse, a 30 s daemon reports the lapse because the client seat's crons are
# all off, and a routine/schedule/channel/memory/autonomy/on-off ask reaches SMD
# through the new operations_request tool instead of getting a sentence with
# nothing behind it. Every one of those sends re-authorizes through the same
# enforce.evaluate_tool_call a model's own send goes through, so a tainted turn
# refuses it and the Operator says so rather than claiming somebody was asked.
#
# VOCABULARY MOVED at this pin: 65 -> 68 types (RULE_DECLINED, RULE_LAPSED,
# RULE_REQUEST_NOTIFIED), re-extracted by AST at both refs and compared as sets;
# all three move out of consoleOnly in audit-action-vocabulary.json.
#
# Files: plugins/hermes-smd-audit/schemas.py,
# plugins/hermes-smd-establishment/{__init__.py,plugin.yaml,sweeper.py},
# plugins/hermes-smd-trust/{__init__.py,enforce.py},
# shared/{action_classes,customer_config,operations_request,rule_confirm,
# rule_dispatch,send_dispatch,spec_gate}.py and three test files -- NOT tracked
# pairs; every overlaySha256 unchanged, only overlayRef.
#
# 45cc19e7 -> 727f3f5b (overlay#337, ss#2664 WS-RENDER pair). ONE merge in the
# range. From this ref the seat dispatches a routine's PRE-RENDERED outbound
# out of turn at pre_llm_call (shared/prerendered_dispatch.py: consume-once
# envelope, full -> skeleton -> failure-note ladder, post-dispatch ledger
# appends under the witness session), enforces the in-turn rendered-body slot
# check (shared/rendered_body_gate.py), stamps rendered_body_sha256 +
# body_variant + routing_leg onto CONFIRM rows (pre-mutation canonical hash),
# down-renders the text/plain half of report sends (report_render.render_plain),
# and runs the outbound fabrication/identifier scans on the out-of-turn send
# path. Mirrors tests/contract/send-render.yaml + the canon-vector arbiter.
# Files: plugins/hermes-smd-trust/{__init__.py,outbound_send.py},
# shared/{agentmail_broker,msgraph_broker,prerendered_dispatch,
# rendered_body_gate,report_render}.py and eight test files -- NOT tracked
# pairs; every overlaySha256 unchanged, only overlayRef. Vocabulary identical
# (71, AST re-read at both refs); shared/heartbeat.py byte-identical.
#
# 727f3f5b -> 799647d1 (TWO merges: overlay#338 then overlay#339; both console
# halves already on main, ss#2674 and ss#2677 respectively).
#
# overlay#338 closes the hash the previous pin opened. _attach_html_body
# replaces `text` with report_render.render_plain(text) before the payload
# leaves the process, so the channel never stores the bytes
# rendered_body_sha256 names, and every conformant templated send graded
# channel_mismatch_hold -- a loud hold on a correct send, the worst kind,
# because it teaches the reader to ignore the instrument. From this ref
# _dispatch_internal_message stamps a SECOND canonical hash on the CONFIRM row,
# plain_body_sha256, over the exact text/plain handed to the channel, and
# _attach_html_body returns whether it actually attached so the stamp is
# OMITTED (never duplicated from the rendered one) on every path where no
# down-render happened. Two hashes, two joins: rendered answers wake<->confirm
# (did the authored body reach dispatch), plain answers confirm<->channel (did
# the dispatched body reach the reader).
#
# overlay#339 widens shared/gateway_loop_check.py's SUPERVISOR_STATES from five
# words to seven, adding `starting` and `never-healthy`. That frozenset is a
# CLOSED vocabulary and the closure is load-bearing: _read_supervisor_state
# forwards nothing it does not recognise, so a word entrypoint.sh writes and
# the set omits is dropped to None -- a NULL, which the console holds on rather
# than pages on. Silence, not a failure. entrypoint.sh (authored HERE) already
# emits both words, so this pin is what stops them being discarded in transit.
# `never-healthy` pages (gateway wedged during startup; the supervisor
# deliberately does not kill a slow starter, so a human is the only recovery
# path); `starting` deliberately does not (bootstrap has not yet exec'd the
# gateway -- a normal, minutes-long window every boot, previously mislabelled
# `inert`, which paged). fleet-alerts grades both at
# workers/fleet-alerts/src/gateway-loop.ts.
#
# Merge-range files (the union, read at the merge commit -- NOT a two-dot diff
# off either PR head; #339 was branched from 727f3f5b, before #338 landed, so
# such a diff hides #338's three files): plugins/hermes-smd-trust/__init__.py,
# shared/gateway_loop_check.py, tests/contract/send-render.yaml,
# tests/test_dispatch_body_stamp.py, tests/test_gateway_loop_check.py -- NONE a
# tracked pair. All nine overlaySha256 re-hashed from the runtime files at
# 799647d1 and byte-identical to the recorded values, so only overlayRef moves.
# Vocabulary identical (71, AST re-read at both refs, set-diff empty both ways;
# schemas.py absent from the range). shared/heartbeat.py byte-identical
# (cc8c23d8...) -- #339 changes a heartbeat field's VALUE vocabulary, not the
# field set, so the manifest's field list is unaffected.
ARG OVERLAY_REF="5770b79fea3206f01df343489d232daf2fdc4a2b"

ARG CUSTOMER_SLUG=customer-zero

# ---------- Stage 1: clone Hermes upstream at the pinned tag, assert SHA ----------
FROM alpine/git:2.45.2 AS hermes_source
ARG HERMES_UPSTREAM_TAG
ARG HERMES_UPSTREAM_SHA
WORKDIR /src
RUN git clone --depth 1 --branch ${HERMES_UPSTREAM_TAG} \
    https://github.com/NousResearch/hermes-agent.git . \
 && git rev-parse HEAD > /src/HERMES_SHA \
 && INSTALLED_SHA="$(cat /src/HERMES_SHA)" \
 && if [ "${INSTALLED_SHA}" != "${HERMES_UPSTREAM_SHA}" ]; then \
      echo "FATAL: upstream tag ${HERMES_UPSTREAM_TAG} points at ${INSTALLED_SHA}" >&2 ; \
      echo "       Expected pinned SHA ${HERMES_UPSTREAM_SHA} (from customer.yaml hermes_ref)." >&2 ; \
      echo "       Upstream re-tagged, or the pin is stale; refusing to build." >&2 ; \
      echo "       See ADR 0024." >&2 ; \
      exit 1 ; \
    fi

# ---------- Stage 2: runtime image ----------
FROM ghcr.io/astral-sh/uv:0.11.6-python3.13-trixie AS uv_source
# Node 26 source stage (Hermes v2026.8.18 / 0.20.4 promotion, ss-console#2444).
# Debian 13 trixie's apt `nodejs` is 20.19.x, EOL April 2026. Upstream Hermes
# pins its toolchain to Node 26 everywhere and, as of v2026.8.18, ships
# `.npmrc engine-strict=true` with `engines.node >=22.22.0` — so `npm install`
# over the Hermes tree hard-fails on the apt Node (vfy_01M0DP257XDYJ0Z04V1ZF57G2X:
# seat had v20.19.2 / npm 9.2.0). We copy node + npm from the official image
# exactly the way upstream's own Dockerfile does at that tag (its `node_source`
# stage, same digest), so the runtime matches what Hermes is built and tested
# against. The `engines.npm` range (`<11.10.0 || >=11.17.0`) is satisfied by
# construction: upstream builds its published image from this digest under
# engine-strict. Bumping the Node major is a one-line change to this FROM; the
# next digest bump re-checks that npm range. Node is needed at RUNTIME too:
# `@oktopeak/clio-mcp` (below) is a Node stdio MCP server the gateway launches.
FROM node:26-bookworm-slim@sha256:9e6f9357d371591e32ab6f2d8a26d63bdd0d17c29eee3f4f3e7e454d9634bf73 AS node_source
FROM debian:13.4

ARG CUSTOMER_SLUG
ARG OVERLAY_REPO
ARG OVERLAY_REF

ENV CUSTOMER_SLUG=${CUSTOMER_SLUG}
# ADR 0023 Wave 1: expose the baked overlay ref at runtime so the gate's
# heartbeat emitter reports it as the fleet-view `version` — the boot-time
# overlay refresh pins /opt/data to this exact ref, so it is the running code.
ENV SMD_OVERLAY_REF=${OVERLAY_REF}
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright
ENV HERMES_HOME=/opt/data
ENV PATH="/opt/data/.local/bin:/usr/lib/postgresql/17/bin:${PATH}"
# Pin every uv invocation to the SYSTEM interpreter (Hermes v0.20.4 promotion,
# ss-console#2444). The v2026.8.18 tree ships `.python-version: 3.11`; because
# this Dockerfile copies the WHOLE tree before `uv sync` (upstream copies only
# pyproject.toml + uv.lock, so their image never sees the pin), every bare
# `uv sync` / `uv venv` — the Hermes venv, the workspace-broker venv, the
# per-connector venvs — resolved that pin, downloaded a managed CPython into
# /root/.local/share/uv (mode 0700), and symlinked venv pythons into it. The
# non-root broker uid then got `Permission denied` executing its own venv
# python (rc=126 crash-loop; the pilot seat went down and was restored by
# image-ref rollback). UV_PYTHON outranks .python-version, so this keeps all
# venvs on Debian 13's python3 (3.13 — inside requires-python >=3.11,<3.14),
# exactly what the v0.18 seats ran and what upstream's published image runs.
ENV UV_PYTHON=/usr/bin/python3

# ---------- System packages ----------
# tini: PID 1 zombie reaper (signals + orphaned MCP stdio subprocesses).
# postgresql-17 + redis: installed for Phase 2 Honcho's data plane but NOT
# started at boot in Phase 1 (Debian 13 trixie ships PostgreSQL 17 as default;
# postgresql-16 is not in trixie's repos — keep 17 so the Phase-2 vendor lands
# cleanly). awscli: bootstrap.sh fetches customer.yaml from R2 (S3-compat).
# curl + ca-certificates: R2 fetch + general TLS.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential curl unzip python3 python3-pip python3-venv \
      ripgrep ffmpeg gcc python3-dev libffi-dev procps git openssh-client \
      docker-cli tini ca-certificates util-linux \
      postgresql-17 postgresql-client-17 redis-server awscli \
      poppler-utils fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
# poppler-utils + fonts-dejavu-core (ss#2614): the chronology runner's exhibit
# rendering fallback and matplotlib's timeline visuals need a real font.

# ---------- runtime principals ----------
# The gateway and credential broker are distinct non-root principals. The
# shared group grants socket access only; it grants no credential-file access.
# audit-readers (OP-P1-4): the broker (workspace-broker) OWNS the audit ledger
# file; hermes joins audit-readers to READ it via group but, lacking ownership
# and group-write, cannot open it for write. The agent's only write path is the
# broker's append-only audit_append verb.
RUN groupadd -r workspace-connectors \
 && groupadd -r audit-readers \
 && useradd -u 10000 -m -d /opt/data hermes \
 && useradd -u 10001 -r -m -d /opt/workspace-broker workspace-broker \
 && usermod -a -G workspace-connectors hermes \
 && usermod -a -G workspace-connectors workspace-broker \
 && usermod -a -G audit-readers hermes \
 && usermod -a -G audit-readers workspace-broker
# medchron (ss#2614): the uid the chronology runner's driver child runs as. No
# home, no shell, no group beyond smokeball-token, which is the ONE thing it
# shares with hermes: the Smokeball refresh-token file (setgid dir, 0660),
# because both principals mint against the same rotating token. The runner
# daemon itself stays root (it owns the queue and the job dirs); only the
# stages that read the matter and call the model run as this uid.
RUN groupadd -r smokeball-token \
 && useradd -u 10002 -r -M -d /nonexistent -s /usr/sbin/nologin medchron \
 && usermod -a -G smokeball-token medchron \
 && usermod -a -G smokeball-token hermes
COPY --chmod=0755 --from=uv_source /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/

# Node 26 + npm from the node_source stage (see the stage comment above). npm
# and npx are recreated as symlinks into the bundled npm install, mirroring
# upstream's Dockerfile; no corepack (unbundled upstream; nothing here needs it).
COPY --chmod=0755 --from=node_source /usr/local/bin/node /usr/local/bin/
COPY --from=node_source /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && node --version && npm --version

WORKDIR /opt/hermes

# ---------- Pull Hermes source from stage 1 ----------
COPY --from=hermes_source /src/ /opt/hermes/
COPY --from=hermes_source /src/HERMES_SHA /opt/hermes/HERMES_SHA

# ---------- Layer-cached Hermes deps install ----------
ENV npm_config_install_links=false
# Playwright browsers are PREFETCHED with curl, then `playwright install` runs
# against the already-populated cache (Hermes v0.20.4 promotion, ss-console#2444).
# WHY: at v2026.8.18 `npx playwright install --with-deps chromium --only-shell`
# deterministically WEDGED on the Depot builder — the chromium-headless-shell
# download hit 100% and Node's downloader never observed the socket close (three
# independent builds; one sat silent for 59 minutes). Upstream's own Dockerfile
# wraps the same command in a 3-attempt retry loop (their :201-202) — they hit
# it too — but a retry cannot rescue a hang, and every retry re-enters the same
# downloader. So: `--dry-run` prints each browser's install location + download
# url; curl (hard --max-time, --retry, immune to the close-hang) fetches each
# zip; unzip + the INSTALLATION_COMPLETE marker reproduce playwright's own
# on-disk layout; the real install then finds every browser present, downloads
# nothing, and only installs apt deps. The timeout+retry loop stays as the
# backstop so NO variant of this step can wedge the seat-rebuild path:
# rollback-by-rebuild depends on this Dockerfile finishing.
RUN npm install --prefer-offline --no-audit \
 && npx playwright install --dry-run chromium --only-shell > /tmp/pw-plan.txt \
 && cat /tmp/pw-plan.txt \
 && awk '/Install location:/{loc=$3} /Download url:/{print loc" "$3}' /tmp/pw-plan.txt > /tmp/pw-pairs.txt \
 && [ -s /tmp/pw-pairs.txt ] \
 && while read -r loc url; do \
      echo "prefetch: $url -> $loc"; \
      curl -fL --retry 3 --retry-delay 5 --max-time 900 -o /tmp/pw-browser.zip "$url"; \
      mkdir -p "$loc"; \
      unzip -q /tmp/pw-browser.zip -d "$loc"; \
      rm -f /tmp/pw-browser.zip; \
      touch "$loc/INSTALLATION_COMPLETE"; \
    done < /tmp/pw-pairs.txt \
 && for i in 1 2 3; do \
      timeout 900 npx playwright install --with-deps chromium --only-shell && break || \
      { [ "$i" = 3 ] && exit 1; echo "playwright install failed/wedged (attempt $i); retrying in 10s" >&2; sleep 10; }; \
    done \
 && rm -f /tmp/pw-plan.txt /tmp/pw-pairs.txt \
 && (cd web && npm install --prefer-offline --no-audit) \
 && (cd ui-tui && npm install --prefer-offline --no-audit) \
 && npm cache clean --force

# `--extra all` deliberately EXCLUDES the `messaging` extra (python-telegram-bot,
# discord.py, slack-bolt) — verified against the pinned ref's pyproject. Without
# `--extra messaging` the native Telegram adapter imports fail and the platform is
# inert (TELEGRAM_AVAILABLE=False), so a TELEGRAM_BOT_TOKEN env would silently do
# nothing. We add it explicitly: lock-governed (frozen) and forward-aligned with the
# Slack/Discord channels the productized Operator will offer. See ADR 0033.
# `--extra anthropic` (Hermes v0.20.4 promotion, ss-console#2444): v2026.8.18
# moved the anthropic SDK out of `[all]` into its own extra (pinned
# anthropic==0.87.0 for CVE-2026-34450/34452; upstream's own Dockerfile passes
# it too). Without it the gateway boots but EVERY turn dies at
# build_anthropic_client with ImportError — observed live on the pilot seat's
# first 0.20.4 rehearsal turn. bedrock/azure/matrix/otlp/hindsight stay out:
# no seat consumes them.
RUN uv sync --frozen --no-install-project --extra all --extra messaging --extra anthropic
RUN cd web && npm run build && cd ../ui-tui && npm run build
# --python is explicit because ENV UV_PYTHON (above) would otherwise aim this
# bare `uv pip` at the SYSTEM interpreter (PEP 668 refusal on Debian); an
# explicit flag outranks the env var and targets the project venv uv sync made.
RUN uv pip install --no-cache-dir --no-deps --python /opt/hermes/.venv/bin/python -e "."

# ---------- Honcho: deferred to Phase 2 (ADR 0016, revised 2026-05-30) ----------
# No in-container Honcho server is installed. The prior `honcho-ai` pip install
# was fictional — that package is the Honcho CLIENT SDK, not the server; the
# real server is the plastic-labs/honcho v3.0.7 SOURCE repo (fastapi api +
# `python -m src.deriver` worker, pgvector, mandatory LLM provider). Phase 2
# vendors it the same way upstream Hermes is vendored above (clone at a pinned
# tag, own venv). Phase 1 runs on Hermes' flat-file memory core.

# ---------- hermes-smd overlay (plugins + CLI) ----------
# Pip-install the overlay (provides `hermes-smd` console script for the
# bootstrap translator and customer-sync sidecar) and then register the
# plugin pack with Hermes. Plugins land under ~/.hermes/plugins/ where the
# Hermes plugin loader discovers them at runtime.
RUN uv pip install --no-cache-dir --python /opt/hermes/.venv/bin/python \
      "git+${OVERLAY_REPO}@${OVERLAY_REF}"

# ---------- Workspace capability broker ----------
# Google libraries and credentials live in a broker-only venv. The Hermes venv
# deliberately has neither, so execute_code and terminal cannot recreate an
# ambient privileged connector path.
COPY operator/workspace_broker/ /opt/workspace-broker/workspace_broker/
RUN uv venv /opt/workspace-broker/.venv \
 && uv pip install --python /opt/workspace-broker/.venv/bin/python \
      google-api-python-client google-auth pyyaml \
 && chmod -R a+rX /opt/workspace-broker \
 && chown -R root:root /opt/workspace-broker

# ---------- Clio MCP server (law vertical practice-management connector) ----------
# @oktopeak/clio-mcp (MIT) is a LOCAL stdio MCP server. The overlay (v0.4.6+)
# materializes customer.yaml `mcp:clio-oktopeak` into the profile mcp_servers
# block as a `clio-mcp` command server; install it globally so the binary is on
# PATH for the gateway to launch. Pinned to 2.0.0 — the version the connector
# reality-check and live loop test ran against. It reads its OAuth token from
# ~/.clio-mcp/tokens.enc (seeded by bootstrap.sh) under ENCRYPTION_KEY.
# Run from a neutral cwd (npm ignores a project .npmrc in -g mode, but the
# Hermes tree's npm policy should never govern a third-party global install),
# then prove the bin resolves on PATH and the package landed under the Node 26
# global prefix (/usr/local/lib/node_modules) so a regression fails the BUILD,
# not a seat. Presence checks only: it is a stdio MCP server, so invoking it
# here would block on stdin.
RUN cd /tmp && npm install -g @oktopeak/clio-mcp@2.0.0 \
 && test -x "$(command -v clio-mcp)" \
 && test -f /usr/local/lib/node_modules/@oktopeak/clio-mcp/package.json

# NOTE: web search needs NO installed binary. It is Hermes' NATIVE brave-free
# provider (plugins/web/brave_free, bundled in the pinned Hermes), selected by
# customer.yaml `backend: native:brave-free` -> config web.search_backend and
# keyed on BRAVE_SEARCH_API_KEY. The former @brave/brave-search-mcp-server install
# was removed with the ADR 0070 native cut (2026-07-08) — MCP-wrapping a native
# feature was the redundant layer.

# Register the plugin pack with Hermes' loader. As of Hermes v2026.8.18
# `hermes plugins install` accepts `--ref`, so the install is pinned to the same
# OVERLAY_REF the pack below is staged at (the old "cannot be pinned through
# this command" caveat is retired). The pip install above is also pinned and
# provides the importable `shared` policy core the runtime plugins `import`.
#
# Build-step scan opt-out (Hermes 0.20.4 promotion, ss-console#2444): v2026.8.18
# security-scans a plugin tree at install (hermes_cli/plugins_cmd.py
# `_scan_plugin_tree`, tools/plugin_guard.py); a `dangerous` verdict is a hard
# fail that `--force` does NOT override. The overlay is OUR plugin, and its own
# security TEST fixtures (prompt-injection strings, fake keys, /etc/passwd in
# gate tests, an AGENTS.md filename) trip the pattern set. So the scan is
# disabled in the build-time root $HERMES_HOME/config.yaml (the install then
# merges its `plugins.enabled` record into that same file, exactly as before).
# At runtime /opt/data is the volume mount and shadows this file on every
# persisted seat; on a fresh volume Fly's empty-volume copy carries it over,
# where it also covers bootstrap.sh's rare runtime-install fallback (which
# would otherwise hit the same scan). The pack is pinned and import-gated two
# steps below; that is the real integrity check for our own code.
#
# FAIL-CLOSED: the prior `|| echo "WARN ...; continuing"` swallowed an install
# failure and booted a silently harness-less Machine — the exact fail-open
# defect the venture forbids. Both the install and the policy-core import below
# are hard build gates: a Machine whose trust/inbound plugins cannot load
# their own policy core MUST NOT build green.
RUN mkdir -p "${HERMES_HOME}" \
 && printf 'plugins:\n  scan_on_install: false\n' > "${HERMES_HOME}/config.yaml" \
 && /opt/hermes/.venv/bin/hermes plugins install \
      venturecrane/hermes-smd-overlay --enable --ref "${OVERLAY_REF}"

# Stage a PINNED copy of the overlay at a NON-VOLUME path (/app/overlay-pack) so
# bootstrap.sh can refresh the per-customer volume's overlay to this exact
# OVERLAY_REF on EVERY boot. /opt/data is the Fly volume mount and SHADOWS the
# build-time `hermes plugins install` above, so without this the volume keeps
# whatever overlay a past provision left behind — an OVERLAY_REF bump never
# reaches the running gateway (the ss-console#1285 delivery bug: the overlay was
# inert because the volume's copy was stale and bootstrap only checked presence,
# never freshness). Cloned at the PINNED ref (unlike the unpinned install above), and
# hard-gated on BOTH the fan-out __init__.py and the gateway:startup activation gate
# (the live boot check, ss-console#1285) so a mis-pinned ref lacking either fails the
# build instead of shipping a Machine with no live governance gate.
RUN git init /app/overlay-pack \
      && cd /app/overlay-pack \
      && git remote add origin "${OVERLAY_REPO}" \
      && git fetch --depth 1 origin "${OVERLAY_REF}" \
      && git checkout --detach FETCH_HEAD \
      && rm -rf /app/overlay-pack/.git \
      && test -f /app/overlay-pack/__init__.py \
      && test -f /app/overlay-pack/hooks/smd-overlay-activation/handler.py

# Deterministic build-time assertion that the pinned `shared` policy core is
# importable in the venv. This catches a stale/mis-pinned OVERLAY_REF (the exact
# v0.1.1 `ModuleNotFoundError: shared.outbound_gate`) at BUILD time rather than
# as a runtime plugin-load failure. Runtime plugin REGISTRATION (Hermes actually
# loaded the pack) is verified live by boot-smoke-test.sh step 7, which runs
# `hermes plugins list | grep hermes-smd-` inside the booted Machine where a
# profile exists.
RUN /opt/hermes/.venv/bin/python3 -c \
      "import shared.outbound_gate, shared.inbound, shared.action_classes; \
from shared.outbound_gate import evaluate; \
print('overlay policy core import OK (outbound_gate.evaluate resolved)')"

# ---------- Layer in SMD's Operator bits ----------
WORKDIR /app

# Operator adapter (trust-ceiling enforcement layer)
COPY operator/adapter/ /app/adapter/

# Canonical skill library (markdown + references)
COPY operator/skills/ /app/skills/

# Drafting discipline + the mechanical gate checker (ss-console #2083).
#
# The destination mirrors the repo path on purpose. Four work-product skills
# (demand-letter-drafter, discovery-response-drafter, follow-up-discovery-drafter,
# mediation-brief-drafter) load `operator/templates/drafting/drafting-discipline.md`
# verbatim into every drafting run, and their SKILL.md bodies invoke
# `python3 operator/templates/drafting/drafting_gate_check.py` by that literal
# relative path. The final WORKDIR below is /app, so ONLY this destination makes
# the cited path resolve. A tidier /app/drafting/ would put both files in the
# image and still leave every citation dangling, which is the same defect wearing
# a better filename.
#
# Absent from this COPY set until 2026-07-30: a whole-filesystem `find` on the
# live seat hermes-pilot-smokeball returned neither drafting-discipline.md nor
# drafting_gate_check.py (vfy_01KYTXF76E1QHWQ2FQ9CYRGQBV), so the drafting lane
# ran on every seat with neither its discipline nor its gates while four SKILL.md
# files asserted both were loaded. The checker is stdlib-only, so the apt
# `python3` above is the whole runtime dependency.
#
# Presence is the instance. The class is guarded by
# tests/shipped-runtime-paths.test.ts, which derives the shipped set from the
# COPY lines in this file and fails on any runtime path a shipped SKILL.md cites
# but the image does not carry.
COPY operator/templates/drafting/ /app/operator/templates/drafting/

# ---------- The distillation compilers: the establishment control (ADR 0085 §4) ----------
#
# ADR 0085 §4 moves the witness-never-author line for admin-instructed
# establishment, and what replaces the blanket prohibition is provenance +
# mediation + THESE FIVE FILES. The leak check refuses client prose beyond the
# approved fixed strings, the digit invariant refuses asserted numbers, and the
# self-test demotes any block rule the firm's own writing violates. They are the
# mechanism that makes an agent-derived spec trustworthy enough to install.
#
# THE DESTINATION IS A REPO MIRROR, AND THAT IS LOAD-BEARING, NOT TIDINESS.
# Three import relationships resolve off the files' OWN locations, so a flattened
# layout breaks them at import time:
#
#   * spec_leak_check.py:  Path(__file__).resolve().parents[2] / "operator" /
#     "templates" / "drafting" / "drafting_gate_check.py". From
#     /opt/smd/operator/bin/, parents[2] is /opt/smd — which is precisely why the
#     mirror root exists and why the gate check must ALSO sit under it. It
#     imports the checker rather than reimplementing it so the containment
#     threshold (_HELD_OUT_NGRAM) cannot drift between the check that stops a
#     draft reproducing held-out record text and the check that stops a spec
#     reproducing the corpus.
#   * spec_selftest.py and spec_fixed_strings.py: Path(__file__).resolve().parent
#     / "voice_profile.py" — a SIBLING load, executed at MODULE level (`_VP =
#     _load_profile_module()`), so a misplaced voice_profile.py is an immediate
#     ImportError, not a latent one.
#
# WHY /opt/smd RATHER THAN /app. The overlay's establishment intake names these
# five paths as CONSTANTS (establish_intake/gates.py: COMPILER_BIN_DIR,
# DRAFTING_GATE_CHECK, REQUIRED_COMPILERS) and probes for them before every run:
# an absent compiler REFUSES the run rather than skipping the gate, because a
# gate that silently did not run reads exactly like a gate that passed (Law 12).
# /app is swept to the agent uid by the blanket chown below; /opt/smd is not, and
# must not be — the SEC-31 argument that keeps drafting_gate_check.py root-owned
# is the same argument here, only stronger. An agent that owns the compilers owns
# the control that decides whether its own authored spec is installable.
#
# WHY drafting_gate_check.py SHIPS TWICE. /app/operator/templates/drafting/ is
# where four SKILL.md bodies invoke it by literal relative path against WORKDIR
# (#2083); /opt/smd/operator/templates/drafting/ is where spec_leak_check
# resolves it. Both COPY the SAME repo file, so the two cannot drift. Moving the
# /app copy to satisfy the intake would break every drafting skill instead.
#
# Only the one file is mirrored, not the whole drafting tree: the checker takes
# every path it reads from argv and loads no siblings, so a fuller mirror would
# ship bytes nothing resolves. tests/shipped-runtime-paths.test.ts pins the
# shipped set against the intake's constants and executes the loaders inside a
# layout-faithful temp tree, so a partial mirror that stops being sufficient
# fails there rather than on a live seat.
COPY operator/bin/voice_profile.py /opt/smd/operator/bin/voice_profile.py
COPY operator/bin/spec_fixed_strings.py /opt/smd/operator/bin/spec_fixed_strings.py
COPY operator/bin/spec_leak_check.py /opt/smd/operator/bin/spec_leak_check.py
COPY operator/bin/spec_selftest.py /opt/smd/operator/bin/spec_selftest.py
COPY operator/templates/drafting/drafting_gate_check.py /opt/smd/operator/templates/drafting/drafting_gate_check.py

# Root-owned, world-readable, agent-unwritable. COPY already lands root:root, so
# this is belt-and-braces against a future --chown creeping in, and it makes the
# posture greppable next to the files it protects rather than inferable from the
# absence of a chown 90 lines below. a+rX (not a+rx) so directories are
# traversable without marking the .py files executable — the root daemon invokes
# them as `python3 <path>`, never as programs.
RUN chown -R root:root /opt/smd && chmod -R a+rX /opt/smd

# Tier-1 custom MCP wrappers (one subdir per connector)
COPY operator/connectors/ /app/connectors/

# ---------- Author-built MCP connectors: one isolated venv per connector ----------
# Each connector under /app/connectors/ (except the `_sdk` library it shares) is
# a Python stdio MCP server. Install each into its OWN venv via the proven
# workspace-broker pattern above, so connectors never share a dependency closure
# with each other or with the Hermes venv. The overlay registry launches each by
# the ABSOLUTE path to its venv console-script
# (/opt/connectors/<name>/.venv/bin/<name>-mcp) — no PATH manipulation, no
# ~/.local/bin. A connector is INERT until a customer.yaml binds it; baking it
# here costs only disk, never a running process (identical posture to a skill in
# the catalog that no persona enables). The `*-mcp` assertion fails the build
# loudly rather than letting a missing console-script surface as a runtime
# MCP-spawn failure on a live Machine.
RUN set -eu; \
    for cdir in /app/connectors/*/; do \
      name=$(basename "$cdir"); \
      [ "$name" = "_sdk" ] && continue; \
      [ -f "${cdir}pyproject.toml" ] || continue; \
      venv="/opt/connectors/${name}/.venv"; \
      echo "== installing author-built connector: ${name} =="; \
      mkdir -p "/opt/connectors/${name}"; \
      uv venv "$venv"; \
      uv pip install --python "$venv/bin/python" --no-cache /app/connectors/_sdk "$cdir"; \
      ls "$venv"/bin/*-mcp >/dev/null 2>&1 || { echo "FATAL: connector ${name} produced no *-mcp console-script"; exit 1; }; \
    done; \
    chmod -R a+rX /opt/connectors

# ---------- The chronology runner (routine 11, ss#2614) ----------
# operator/runners/medchron is installed into its OWN root-owned venv from the
# same three paths CI installs it from (the connector SDK, the Smokeball
# connector, the runner), so the daemon and the driver child never share a
# dependency closure with the Hermes venv or the broker venv. The venv is
# root:root a+rX: the medchron uid executes it and can write nothing in it.
# The `medchron` console-script assertion fails the BUILD, not a seat.
COPY operator/runners/ /app/runners/
# The pricing table the runner's cents cap and ledger price from, beside the
# venv (NOT under /opt/smd, which is the establishment compilers' contract
# tree and is pinned file-by-file by tests/shipped-runtime-paths.test.ts).
COPY operator/adapter/cost_telemetry/anthropic_pricing.json /opt/medchron/anthropic_pricing.json
RUN set -eu; \
    uv venv /opt/medchron/.venv; \
    uv pip install --python /opt/medchron/.venv/bin/python --no-cache \
      /app/connectors/_sdk /app/connectors/smokeball /app/runners/medchron; \
    [ -x /opt/medchron/.venv/bin/medchron ] || { echo "FATAL: the medchron runner produced no console-script"; exit 1; }; \
    /opt/medchron/.venv/bin/python -c "import medchron.daemon, medchron.stages.upload"; \
    chown -R root:root /opt/medchron; \
    chmod -R a+rX /opt/medchron

# Safety-substrate tests — bootstrap.sh runs these on container start
COPY operator/safety-substrate/ /app/safety-substrate/

# Env-consumption contract — the single source of truth the boot-time invariant
# (safety-substrate/tests/test_invariant_9) reads to enforce that no var the
# agent consumes is stripped from its env (the voice-class regression guard).
COPY operator/contracts/ /app/contracts/

# Bootstrap entrypoint
COPY operator/templates/bootstrap.sh /app/bootstrap.sh
COPY operator/templates/entrypoint.sh /app/entrypoint.sh
# REQUIRED_ENV / OPTIONAL_ENV, generated from operator/contracts/env-consumption.yaml
# (operator/bin/gen-env-arrays.py); bootstrap.sh sources this instead of hardcoding them.
COPY operator/templates/_env-arrays.generated.sh /app/_env-arrays.generated.sh
RUN chmod +x /app/bootstrap.sh /app/entrypoint.sh

# Curator disable guard (ADR 0017). bootstrap.sh step 7b runs this after the
# overlay materializes profile configs, ensuring the autonomous curator is off
# before Hermes starts; the boot smoke test re-runs it with --check.
COPY operator/templates/ensure-curator-disabled.py /app/ensure-curator-disabled.py
COPY operator/templates/ensure-disabled-skills.py /app/ensure-disabled-skills.py
COPY operator/templates/ensure-operator-identity.py /app/ensure-operator-identity.py
COPY operator/templates/ensure-telegram-allowlist.py /app/ensure-telegram-allowlist.py
# Egress webhook-subscription reconciler orchestrator (hermes venv; dispatches to
# each connector's own venv). Invoked non-fatally by bootstrap.sh after the R2 strip.
COPY operator/bin/webhook_reconcile.py /app/webhook_reconcile.py
# OP-P1-4 affirmative proof: boot-smoke runs this as the agent uid to confirm
# the ledger is not agent-writable.
COPY operator/templates/audit-write-fail-probe.py /app/audit-write-fail-probe.py
# OP-P2-1 affirmative proof: boot-smoke runs this as root to confirm NO agent-uid
# process (gateway or same-uid child) still carries the account-wide R2 key.
COPY operator/templates/r2-account-key-strip-probe.py /app/r2-account-key-strip-probe.py
# ADR 0053: boot-smoke asserts every baked author-built connector's declared
# tool_classes agree with the overlay's enforced action-class map (the cross-repo
# manifest<=map check, run where both the baked manifest and the installed overlay
# are present).
COPY operator/templates/connector-classification-probe.py /app/connector-classification-probe.py
# ss#2167: boot-smoke asserts the matter-mixing READ fence is installed and
# discriminates (refuses a second matter, allows the same one). The capture path
# it depends on swallows exceptions by contract, so a broken fence is otherwise
# indistinguishable from a clean fleet — no error, no alert, sends simply stop
# being checked.
COPY operator/templates/matter-mixing-fence-probe.py /app/matter-mixing-fence-probe.py

# NOTE: customer.yaml is NOT copied into the image. It lives on the Fly
# volume at /opt/data/customer.yaml; bootstrap.sh fetches it from R2 on
# first boot if the volume copy is absent. See §6 of the build plan and
# the customer.yaml -> per-profile config translation ADR.

# ---------- Volume data dirs for Postgres + Redis ----------
# The volume mounts at /opt/data, but initdb (run on first boot in
# bootstrap.sh) needs the parent dir to exist with hermes ownership. We
# create them here so the directory tree is right before the volume mounts;
# the volume mount will shadow them on first boot, then bootstrap.sh's
# mkdir + initdb populate the mounted directories.
RUN mkdir -p /opt/data/honcho/pg /opt/data/honcho/redis /opt/data/.local/bin

# ---------- Permissions ----------
# Everything Hermes / Honcho / bootstrap writes to must be owned by hermes
# (uid 10000). The plugin install above ran as root and dropped files into
# /opt/data/.hermes — chown sweeps them to the right owner.
RUN chmod -R a+rX /opt/hermes /app \
 && chown -R hermes:hermes \
      /opt/hermes/.venv \
      /opt/hermes/ui-tui \
      /opt/hermes/node_modules \
      /app \
      /opt/data

# SEC-31 (audit 2026-06-15): the blanket `chown -R hermes:hermes /app` above
# swept the GOVERNANCE artifacts to the agent uid — the pinned overlay pack the
# gateway:startup activation gate force-loads, the boot scripts, and the
# boot-smoke probes. A code-executing agent must not own its own governance
# self-check. Re-assert root ownership on them. This is functionally inert:
# bootstrap only READS these (it COPIES /app/overlay-pack to the hermes-owned
# volume and the gateway loads from THERE; it execs the *.sh; it runs the *.py as
# scripts) — none are imported in place, so no __pycache__ write depends on the
# agent owning them. They stay world-readable/executable (a+rX from the chmod
# above). NOTE: /app/safety-substrate is intentionally NOT re-owned — its
# run_invariants.py writes a boot log under that tree, so it must stay
# hermes-writable (a smaller, separately-tracked residual).
#
# /app/operator/templates/drafting is in this set for the same reason (#2083):
# drafting_gate_check.py is the mechanical gate a draft must pass before it
# reaches the attorney, and drafting-discipline.md is the rule it enforces. An
# agent that owns its own gate checker can rewrite the check it is graded by.
# Nothing writes under that tree at runtime: the checker is INVOKED as a script
# (`python3 <path>`), never imported, so no __pycache__ write depends on the
# agent owning it, and a+rX below keeps it readable and traversable.
RUN chown -R root:root \
      /app/overlay-pack \
      /app/operator/templates/drafting \
      /app/_env-arrays.generated.sh \
 && chown root:root /app/*.sh /app/*.py \
 && chmod -R a+rX /app/overlay-pack /app/operator/templates/drafting

ENV HERMES_WEB_DIST=/opt/hermes/hermes_cli/web_dist

VOLUME [ "/opt/data" ]

# tini as PID 1: reaps zombies, forwards signals to the foreground child
# and starts a root-only launcher. The launcher creates broker-owned runtime
# paths, starts the broker as uid 10001, strips Google secrets, then execs the
# existing bootstrap and gateway as uid 10000 with no_new_privs.
# nosemgrep: dockerfile.security.missing-user-entrypoint.missing-user-entrypoint - Root PID 1 only creates protected paths, starts uid 10001, and execs uid 10000.
ENTRYPOINT [ "/usr/bin/tini", "-g", "--", "/app/entrypoint.sh" ]
