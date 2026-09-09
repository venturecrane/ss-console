/**
 * Regression guard: the customer-Machine Dockerfile must pin the overlay and
 * fail CLOSED on a broken harness install.
 *
 * Two stacked bugs once shipped a silently harness-less Machine (first-boot
 * audit, 2026-05-29):
 *   1. OVERLAY_REF was pinned at v0.1.1, which predates `shared/outbound_gate.py`
 *      — the trust plugin's `from shared.outbound_gate import evaluate` would
 *      ImportError at runtime.
 *   2. The `hermes plugins install` step ended in `|| echo "WARN ...; continuing"`,
 *      swallowing that failure so the image built green and booted an agent with
 *      no trust/inbound/outbound harness.
 *
 * This is the exact fail-open antipattern the venture forbids (stub/NoOp default
 * reaches the live path and reports success). These assertions lock the fix.
 *
 * @see operator/templates/Dockerfile
 * @see docs/runbooks/operator/first-boot.md
 * @see docs/adr/0028-outbound-integrity-gates-provenance-and-voice.md
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const DOCKERFILE = readFileSync(resolve('operator/templates/Dockerfile'), 'utf8')
const BOOTSTRAP = readFileSync(resolve('operator/templates/bootstrap.sh'), 'utf8')

// Strip `#`-comment lines so NEGATIVE assertions (e.g. "no honcho.server")
// match executable content only — the Honcho-deferral comments deliberately
// NAME the removed commands to explain why they are gone.
const stripHashComments = (s: string): string =>
  s
    .split('\n')
    .filter((l) => !/^\s*#/.test(l))
    .join('\n')
const DOCKERFILE_CODE = stripHashComments(DOCKERFILE)
const BOOTSTRAP_CODE = stripHashComments(BOOTSTRAP)

describe('Operator customer Machine Dockerfile', () => {
  it('pins OVERLAY_REF to a release tag or a full commit SHA, never a branch', () => {
    // During a first-boot proof OVERLAY_REF is a 40-hex commit SHA on a feature
    // branch; after the boot is green it is repointed to a vX.Y.Z release tag
    // (see the plan's "tag last" sequencing). A bare branch name (e.g. `main`)
    // is never acceptable — it would let the pip-installed `shared` policy core
    // drift silently. The import assert below is the real harness-present gate.
    const m = DOCKERFILE.match(/ARG\s+OVERLAY_REF=["']?([^"'\s]+)["']?/)
    expect(m, 'OVERLAY_REF must be set').not.toBeNull()
    const ref = m![1]
    const isSemver = /^v\d+\.\d+\.\d+$/.test(ref)
    const isSha = /^[0-9a-f]{40}$/.test(ref)
    expect(
      isSemver || isSha,
      `OVERLAY_REF must be a vX.Y.Z tag or a 40-hex commit SHA, got "${ref}"`
    ).toBe(true)
  })

  it('pins the broker-capable overlay revision', () => {
    // c6ef10d is overlay v0.4.25 (v0.4.24 + #74): THE inbound fix — AgentMail
    // delivers over Svix, which carries the event under `type` not the
    // `event_type` the gate assumed, so the webhook route never matched and the
    // recipient-lock origin was never recorded → relay never sent. Gate now
    // stamps event_type from Svix `type`; origin extraction reads the `data`
    // envelope. v0.4.24 (11bcdc5, #73) added address-keyed origin recovery.
    // v0.4.23 (90dd7f7, #72) classifies AgentMail MCP runtime tool names (P0 —
    // mcp_agentmail_* sends had defaulted to READ). v0.4.22 (8706af4) added #71
    // gate source-stamp + #57 reply relay (then fail-closed). Atop v0.4.21
    // (e0bc503, #69 calendar-read fences) and the #60–#68 security wave. v0.4.17
    // must never be re-pinned (fixed-epoch probe crash-loops the gate).
    // c410c52 (#78) carves standard not-legal-advice / attorney-client
    // disclaimer boilerplate out of the content-floor LEGAL category (clause-
    // local; genuinely sensitive content elsewhere still forces draft) — fixes
    // REPLY_HELD on a benign disclaimer. Atop ed96cebe.
    // 37a27aa (#79) exposes voice_corrections via memory_export (ADR 0048) — the
    // legible relationship surface reads the operator's taught style rules through
    // the runtime-read seam. Superset of c410c52.
    // e4d1f23 (#82) adds the relationship authored behavioral lane (ADR 0048 Phase 2):
    // translate.py materializes the customer.yaml `relationship:` block into SOUL.md +
    // config.yaml, and the config_export seam serves it to the admin surface. Atop 37a27aa.
    // b3294de (#81) adds live reconfiguration (ADR 0044): WS2 live reads, validator parity
    // + on-box secret scan, and the root-owned config_applier (pull → validate → safety →
    // atomic write of /opt/data/customer.yaml). Verified end-to-end on hermes-smd-staging.
    // Superset of e4d1f23 — merges and carries the relationship lane forward.
    // 8d6f1a95 (#85, ss-console #1408) exempts Clerk public IDs (user_/org_) from the
    // secret-scan high-entropy heuristic — fixes a customer-zero crash-loop. Superset of b3294de.
    // a548086 (overlay #87) adds the hermes-smd-peer-memory plugin (ADR 0048 learned lane:
    // per-peer working-preference memory — pre_llm_call sender stash + inject, record_peer_preference
    // tool, post_tool_call server-side attribution + taint-gate, peer_preferences on the agent-state
    // D1 binding + runtime_read seam). Carries overlay #86 (voice_corrections seam rip). Superset of 8d6f1a95.
    // 0e491f9 (#88) materializes wake_policy: pre_run_decides (ADR 0047 phase 2);
    // unblocks pilot-law / pilot-smokeball boot. Superset of a548086.
    // 5a1e3e7 (#92) is the security-audit remediation wave tip: classifies the full
    // Clio MCP surface (#93 — closes a live fail-open where Clio writes ran
    // autonomous on injection-tainted turns), the EFF-07 tool-classification
    // completeness gate (unmapped writes fail-closed), the SEC-05/13 AgentMail
    // inbox-read fence (#90), and the SEC-33 hook-parity guard (#91). Superset of 0e491f9.
    // 0f51821 (#89) emits Hermes' native `delegation` block from customer.yaml
    // `escalation_model`, renders the roster-conditional SOUL escalation instruction,
    // and reads the skill weight marker — the ADR 0049 two-tier (light main +
    // escalate-up) seam. Superset of 5a1e3e7.
    // 72fa21d (#96) wires the previously-orphaned transform_draft() into the
    // transform_llm_output hook — Voice Layer 2 structural reshape post-LLM.
    // 3dcef42 (#97) lands the MCP conversational channel: one ask_operator verb
    // (echo/fetch/store retired), principal-namespaced thread continuity, and
    // source==mcp turns fenced+tainted like inbound email — plus the Clerk OAuth
    // front door. Merging it to main is the durable fix for the reprovision-revert.
    // 8d633a4 (#98) fixes the Machine Clerk binding to authorize clerk_subjects
    // (plural — the authored form), not only the singular key, which had refused
    // every real token identity_not_authored (proven live on hermes-smd 2026-06-17).
    // d8c178e (#99) unfences workspace_gmail_search: it returns only {id, threadId}
    // metadata (no body), so fencing it tainted the result and blocked the agent
    // from reusing the ids as the message_id for the still-fenced body read —
    // making a list→get mailbox read impossible. Superset of 8d633a4; also carries
    // already-merged #95 (unmapped tool → REFUSED) and #94 (SOUL.md principal).
    // e93a3ff classifies native Hermes orientation reads and in-band session
    // writes so mission-critical reads do not get refused after the unknown-tool
    // fail-closed change.
    // a97013e (#101) adds /webhooks/handoff to webhook_gate.py — console→Machine
    // async task handoff (Phase 2, ADR 0043). HMAC bearer auth (WEBHOOK_SECRET_MCP),
    // stamps source=handoff, forwards to Hermes adapter. Enables operator_handoff_task
    // MCP tool (ss-console#1458). Superset of e93a3ff.
    // 1c2171f (#103) fixes cron reconciliation: a persona that drops ALL its cron
    // now has its orphaned managed job removed at boot (it was firing forever);
    // two-pass fail-closed across the reconcile set; HERMES_HOME snapshot/restore.
    // Also a pure ruff-format pass on webhook_gate.py (no logic change). Superset
    // of a97013e.
    // baa9495 (B1 staging): adds the durable task-execution overlay half (job
    // ledger client + hermes-smd-jobs plugin + in-gateway worker). Additive on
    // top of 1c2171f; the vendored adapter twins (and their overlaySha256) are
    // unchanged, so only overlayRef is re-pinned in overlay-pairs.json.
    // af20ecd (#105, ADR 0050 B0): taints the code-execution ingestion channel
    // (execute_code/terminal/process/computer_use). Additive on top of e8411ca;
    // twins unchanged.
    // 713a1e2 (#106): wraps overlay tool schemas under `parameters` so the model
    // can drive them — the 18 workspace_* tools shipped with empty params. Additive
    // on top of af20ecd; tracked twins unchanged.
    // 7f35eba6 (#107, ADR 0053 PR2): the author-built MCP connector platform seam —
    // additive McpConnectorSpec.auth_model + the `reference` synthetic self-test
    // registry entry + its mcp_reference_echo/record literal classification (surprise
    // left unmapped → fail-closed REFUSED). The range 713a1e2..7f35eba6 is exactly
    // this one commit; all four tracked twins verified unchanged.
    // a4db1154 (#108, ADR 0053): register mcp:smokeball — the first real author-built
    // connector — in MCP_CONNECTOR_REGISTRY so translate.py materializes it (its
    // mcp_smokeball_* classification already existed). The range 7f35eba6..a4db1154 is
    // exactly this one commit; all four tracked twins verified unchanged.
    // 3a75703 (#109, ADR 0053): smokeball per-seat env — env_secrets_optional on
    // McpConnectorSpec + the smokeball spec (required SMOKEBALL_ENVIRONMENT, optional
    // auth_mode/refresh_token/account_id) + translate.py optional-env staging loop, for
    // the firm-delegated authorization_code path. The range a4db1154..3a75703 is exactly
    // this one commit; all four tracked twins verified unchanged.
    // 96835fe (#110, ADR 0054): the Machine-hosted Smokeball OAuth callback —
    // shared/oauth_callback.py + a GET /oauth/smokeball/callback route on the gateway,
    // so the firm-delegated consent lands on the customer's own Machine, not a shared
    // Worker. The range 3a75703..96835fe is exactly this one commit; all four tracked
    // twins verified unchanged.
    // 1d6d5b2 (#111, ADR 0055): the reply channel — promotes the recipient-locked
    // autonomous reply to a production capability (plugin hermes-smd-reply) gated on
    // the organization roster (scope.inbound_allow_from), not a fenced switch. Security
    // logic byte-for-byte unchanged. Superset of 96835fe.
    // 33b58ad (#112, ADR 0055): inbound prompt drives create_draft (reply channel)
    // instead of the trust-refused reply_to_message. Superset of 1d6d5b2.
    // 97c59f3 (#114, ADR 0056): the entitlement hard-rebuild — persona exposure +
    // skill initiation replace the scalar trust_ceiling / scope+skill+mailbox
    // action_ceilings; enforce.py reads per-persona exposure from the trusted
    // customer.yaml (never tool args), fail-closed unauthored. Range 33b58ad..97c59f3
    // also includes #113 (smokeball calendar/task/folder classification); all four
    // tracked twins verified unchanged. Lockstep with ss-console #1523 (shared parity
    // hash). Superset of 33b58ad.
    // 22498d6 (#115): the Smokeball webhook gate verifier — webhook_gate.py gains
    // verify_smokeball_signature (raw-key hex HMAC over {Timestamp}|{RequestId}|
    // {ClientId}, body unsigned) + a fail-closed per-vendor dispatch registry +
    // an authoritative source stamp (fixes the Smokeball source="API" routing
    // collision) + a RequestId replay cache + a boot self-check; tests +
    // consumes.yaml. Range 97c59f3..22498d6 is exactly this one commit; all four
    // tracked twins verified unchanged. Pairs with ss-console #1529 (provisioning).
    // Superset of 97c59f3.
    // e1044f19 (#116): the Smokeball connect-triggered webhook reconcile —
    // shared/oauth_callback.py fires the egress reconciler (/app/webhook_reconcile.py
    // --trigger connect) after the OAuth token lands, so a newly-connected firm's
    // webhook subscriptions are ensured without a reboot. Range 22498d6..e1044f19 is
    // exactly this one commit; all four tracked twins verified unchanged. Pairs with
    // the ss-console egress reconciler (engine + orchestrator + boot backstop).
    // Superset of 22498d6.
    // 2b694947 (#117): the gate stamps the verified per-delivery id (RequestId) as
    // top-level event_id so the header-less webhook router has a replay key — fixes
    // the first real Smokeball matter.updated verifying (202) but the router
    // refusing "missing event id". Range e1044f19..2b694947 is exactly this one
    // commit; all four tracked twins verified unchanged. Superset of e1044f19.
    // 9b20a1ac (#118): translate gives non-email skill-routed webhook channels a
    // skill-driving prompt instead of the shared _INBOUND_EMAIL_PROMPT — a verified
    // Smokeball matter.updated reached the agent as an email-draft instruction (it
    // tried agentmail create_draft instead of running matter-memo-on-update). The
    // AgentMail email-reply channel keeps the email prompt; MCP route untouched.
    // 8db15f0 (#119): console-sole Claude door — the Machine retires the direct
    // public /mcp door (POST /mcp → 410; stub-bearer + Clerk-direct auth removed)
    // and adds the authenticated /mcp/turn console-proxy endpoint; the console is
    // now the sole public Claude door and enforces the ADR 0057 grant kill-switch
    // per request. Range 9b20a1ac..8db15f0 touched only webhook_gate.py +
    // test_mcp_channel.py + consumes.yaml; all four tracked twins verified unchanged.
    // d7ce7cc7 (#121): fabrication-gate + self-approval hardening (EFF-01/03,
    // SEC-36/16) — autonomous EXTERNAL_SEND routed through the fabrication/citation
    // gate, Unicode-normalized citation scan, forgeable _current_turn_approval
    // stripped. Changed outbound.py/__init__.py/relay.py/citation_filter.py/tests;
    // all four tracked twins verified unchanged.
    // 22eecbea (#122): Machine-side heartbeat emitter (ADR 0023 Wave 1) — new
    // shared/heartbeat.py runs a fail-soft ticker in the webhook-gate that POSTs
    // to the apex console /api/internal/heartbeat and pings healthchecks.io,
    // closing the gap where the console receiver + fleet_status + admin columns
    // were built but nothing phoned home. Range d7ce7cc7..22eecbea changed
    // heartbeat.py [new] + webhook_gate.py + consumes.yaml + tests; all four
    // tracked twins verified unchanged.
    // d97eb27f (#123): memory-mirror lane silence (ss-console#1643) — the
    // unconfigured Honcho lane (ADR 0016 Phase 2 deferred) no longer logs a
    // per-session 'degraded' WARNING; register() classifies the env contract
    // once at boot and a PARTIAL contract is a loud ERROR naming the missing
    // vars. Range 22eecbea..d97eb27f changed only the mirror plugin + its
    // tests; all four tracked twins verified unchanged.
    // a6c8e3fb (#124): cost circuit breaker + inbound wake guard (ADR 0062,
    // ss-console #1661) — vendored sticky_stop twin, cost_breaker glue
    // (Machine-local sqlite + broker-ledger audit sink), job-path exact-cents
    // cap (HARD_STOP dead-letters needs_review), webhook-gate InboundWakeGuard
    // (parks at HARD_STOP / authored inbound_daily_cap), heartbeat gains
    // sticky_stop_level. Range d97eb27f..a6c8e3fb changed shared/{sticky_stop,
    // cost_breaker,gate_inbound_cap}[new] + job_* + webhook_gate + heartbeat +
    // customer_config + consumes.yaml + tests; all four tracked twins verified
    // unchanged.
    // 5b4c99e1 (#125): Captain clear surface (ADR 0062 §6) — gate POST
    // /sticky-stop/clear (console-proxy bearer) clears non-OK sticky_stop rows
    // via the state machine's clear() with audited AGENT_RESUMED; the un-trip
    // path the live trip-fire probe requires. Range a6c8e3fb..5b4c99e1 changed
    // cost_breaker.py + webhook_gate.py + tests; all four tracked twins
    // verified unchanged.
    // 378fd82d (#126): seam exposes persisted cron next_run_at/last_run_at
    // (ss-console#1691) — makes wrong-timezone first fires observable from
    // /runtime/config right after reprovision instead of as a surprise fire.
    // Range 5b4c99e1..378fd82d changed shared/config_snapshot.py + tests;
    // all four tracked twins verified unchanged.
    // 8df1992a (#128): interactive-turn cost metering (option C, ADR 0062 §4
    // amendment, #1701) — shared/interactive_cost_meter.py estimates each
    // interactive turn's cents at the post_llm_call hook and feeds the same
    // sticky_stop ladder, closing the gap the trip probe found. Range
    // 378fd82d..8df1992a changed interactive_cost_meter.py[new] + the audit
    // plugin + tests + vendored pricing; all four tracked twins verified
    // unchanged.
    // 3b2481b8 (#129): ship anthropic_pricing.json in the wheel — the
    // interactive cost meter was dark in prod (pricing excluded from the
    // wheel; alarmed model_unpriced every turn). Range 8df1992a..3b2481b8
    // changed pyproject.toml + a guard test; all four tracked twins unchanged.
    // 122ee0a2 (#130): gate clear succeeds without a Machine audit row —
    // the broker PID-gates appends to the gateway process (OP-P1-4), so
    // the gate can't write the ledger; the resume is audited console-side.
    // 7e70b376 (#131+#132, ss#1686): audit hash chain — CREATE/ensure gain
    // prev_hash/row_hash, audit_export serves them, shared/audit_chain.py
    // lands as the new tracked twin of workspace_broker/chain.py. emit.py
    // twin changed (ensure applies CHAIN_COLUMN_ALTERS) and is re-pinned in
    // overlay-pairs; the broker stamps the chain, writers untouched.
    // c85b0ddb (#133, ss#1701): cost-breaker boot self-probe — the
    // gateway:startup activation handler drives run_boot_probe (throwaway db,
    // ladder trips HARD_STOP + assert_allowed refuses, os._exit(1) if inert),
    // the recurring negative-fire check that earns sticky_stop_cost_cap its
    // `enforced` status. Range 7e70b376..c85b0ddb is a fast-forward touching
    // only handler.py + shared/cost_breaker.py + tests; no tracked twin moved.
    // 50a6545e (#136, L2 DISC-1): mcp_smokeball_read_document mapped READ and
    // FENCED as a tainting content-read — the connector's new server-side
    // document fetch+extraction returns externally-authored text (served
    // discovery, opposing responses), so reading a matter document taints the
    // session like an inbound email. Range c85b0ddb..50a6545e touches only
    // shared/action_classes.py + the inbound fence + tests; no tracked twin moved.
    // (That range ALSO carried #134/#135, the Sentry init + PII scrub — see the
    // Sentry-on-Machines memory; likewise no tracked twin.)
    // 3e5b8a9b (#137, ss#0023): Sentry boot marker — init_sentry now sends one
    // info capture_message("boot: monitoring active") per process, the direct
    // in-Sentry confirmation the gateway init fired (its INFO log is filtered by
    // Hermes' root=WARNING) + a per-boot restart signal. Range 50a6545e..3e5b8a9b
    // touches only shared/sentry_init.py + tests; no tracked twin moved.
    // c081223b (#138, ss#1742/#1744): digest.home_matter_id materializes as a
    // '## Digest home' SOUL section (unauthored = fail-closed, byte-identical);
    // mcp_smokeball_file_attachment_to_matter mapped INTERNAL_WRITE (the
    // credential-free cross-connector attachment transfer). Range
    // 3e5b8a9b..c081223b touches only translate.py + action_classes + tests;
    // no tracked twin moved.
    // 7c10fd61 (#140, ss#1758): caption provenance allowlist for the tier-2
    // citation gate — captions harvested from READ results into the session
    // register; only provenance-verified bare case-name hits are exempt
    // (reporter cites/statutes/rules/tier-1 never relax; empty register =
    // fail-closed). Vendored citation_filter synced from ss#1764 (allowlist +
    // Cal ordinal reporter false-negative fix) and now drift-tracked as a
    // SEC-32 pair in overlay-pairs.json.
    // 07c7a516 (#142 + #138): authored webhook-trigger exceptions — the gate
    // suppresses excluded (matter/actor) deliveries per customer.yaml
    // webhook_triggers[].exclude with a WEBHOOK_SUPPRESSED audit row, fail-open
    // to forward; plus the #1742 digest-home SOUL materializer +
    // mcp_smokeball_file_attachment_to_matter INTERNAL_WRITE mapping (#1744).
    // Range 7c10fd61..07c7a516 touches webhook_gate.py + shared/
    // gate_trigger_exclusions.py + translate.py + action_classes + tests; no
    // tracked twin moved.
    // 3294e909 (#143, ss#1758/#141): the provenance SESSION RESOLVER — core's
    // pre_tool_call fire sites drop session_id, so the register was never
    // consulted under its real key (111/111 tier3 rows empty-register); the
    // trust plugin now notes the real id (new pre_llm_call hook +
    // post_tool_call) and resolves at a single pre-hook choke point. Unblocks
    // the #140 caption exemption + A1 identifier gate. No tracked twin moved.
    // 95fc269f (#144): exclusion matching checks ANY candidate key — the live
    // Smokeball envelope carries a foreign top-level id that defeated first-
    // present-wins precedence (proven by signed probes against the running
    // gate); fail-open config reads now log. Range 3294e909..95fc269f touches
    // shared/gate_trigger_exclusions.py + tests; no tracked twin moved.
    // 6e685b03 (#145): exclusion matching searches the NESTED payload level —
    // the verbatim live envelope (session transcript, 2026-07-07) puts the
    // matter at payload.id with only userId top-level; both prior attempts
    // assumed flat. Regression test pins the verbatim envelope. Range
    // 95fc269f..6e685b03 touches shared/gate_trigger_exclusions.py + tests;
    // no tracked twin moved.
    // af895354 (#146, ss #1791): the webhook gate records WEBHOOK_SUPPRESSED via
    // a new uid-gated broker verb (webhook_suppressed_append) instead of the
    // gateway-PID-gated audit_append that was silently refusing the write —
    // proven live on pilot-smokeball (suppression stood, audit row never
    // persisted). Range 6e685b03..af895354 touches webhook_gate.py +
    // shared/audit_client.py + tests; no tracked twin moved.
    // 138c10a (#147, ss #1796): wire mcp:brave — shared web-search connector
    // (ADR 0070). Range af895354..138c10a touches bootstrap/mcp_registry.py +
    // shared/action_classes.py + tests; no tracked twin moved (every
    // overlaySha256 unchanged, only overlayRef).
    // b9391d8 (#148, ss #1796): fix the Brave runtime tool name to
    // mcp_brave_brave_web_search — live pilot-smokeball verification of #147
    // caught the single-brave name was unmapped -> REFUSED. Range
    // 138c10a..b9391d8 touches shared/action_classes.py + test; no tracked twin.
    // 9189224 (#149, ss #1822, ADR 0072): recipient-aware proactive send —
    // external_send_internal class + recipient_classifier (NEW byte-identical
    // pair) + outbound_recipient registry + evaluate_tool_call reclassification.
    // Range b9391d8..9189224 adds shared/recipient_classifier.py (tracked as a
    // new pair) and touches action_classes.py/enforce.py/__init__.py/validate.py/
    // translate.py (not tracked twins).
    // 539f42c7 (#150, ss #1796, ADR 0070 native cut): retire the mcp:brave
    // connector for Hermes' NATIVE web_search provider. translate._materialize_web_search
    // (native:<provider> -> web.search_backend), mcp_registry brave spec removed,
    // action_classes web_search READ, validate accepts native:. Range
    // 9189224..539f42c7 touches bootstrap/{translate,mcp_registry,validate}.py +
    // shared/action_classes.py + tests; no tracked twin moved (overlaySha256 unchanged).
    // 0c9d165 (#151, ss #1804, ADR 0071): add `confirm` ceiling value +
    // external_send enforcement to plugins/hermes-smd-trust/enforce.py (mirrors
    // the in-tree operator/adapter/trust_ceiling.py). Range 539f42c7..0c9d165
    // touches enforce.py + test; no tracked twin moved (overlaySha256 unchanged).
    // d5187194 (#152, ss ADR 0073): remove the law-firm external_send entry from
    // VERTICAL_FLOORS — outside-send is the firm's authored dial; the floor
    // machinery stays (empty) for future regulation-compelled floors. Range
    // 0c9d165..d5187194 touches shared/action_classes.py + enforce.py +
    // config_applier/safety.py + tests — NOT tracked twins, so every
    // overlaySha256 is unchanged; only overlayRef. Superset of 0c9d165.
    // 8806099 (#153, ADR 0028 §2 / #855 voice live-gate): fail-closed voice
    // gate on allowed autonomous OUTSIDE external_send for voice_library-authored
    // seats — samples + per-turn transform-applied marker (shared/voice_status.py)
    // required, else draft + VOICE_GATE_TRIGGERED. Range d5187194..8806099
    // touches plugins/hermes-smd-trust/{enforce.py,voice_gate.py} +
    // plugins/hermes-smd-voice/__init__.py + shared/voice_status.py + tests —
    // NOT tracked twins, so every overlaySha256 is unchanged; only overlayRef.
    // 78064d3 (#154, ss #1805, ADR 0071): bootstrap/validate.py accepts the
    // `confirm` exposure ceiling for external_send (rejects it off the send
    // classes) — keeps the on-box config_applier validator in lockstep with the
    // console. Range 8806099..78064d3 touches bootstrap/validate.py + contract
    // tests — NOT tracked twins, so every overlaySha256 is unchanged; only overlayRef.
    // 17d33d7 (#156, ADR 0075): recipient-class enrichment — RecipientClass gains
    // CLIENT/VENDOR, typed outbound roster, external_send_client/_vendor action
    // classes wired through enforce/validate/translate. Range 3724e78..17d33d7
    // moves ONE tracked twin (shared/recipient_classifier.py), so that pair's
    // overlaySha256 is re-recorded in overlay-pairs.json alongside this bump.
    // 5b7318cb (#158, ss ADR 0073): authored-exposure SOUL section — the agent
    // acts AT its authored ceiling instead of defaulting below it. translate.py
    // + tests only; no tracked twin moved. Superset of 17d33d7.
    // f3e48d6b (#157, ss #1806, ADR 0071): confirm-over-channel approval stamp —
    // shared/pending_send.py + plugins/hermes-smd-trust/{approval,enforce,__init__}.py
    // + tests. None are tracked twins, so every overlaySha256 is unchanged; only
    // overlayRef. Superset of 5b7318cb.
    // ba5b8179 (#159): stop tracking the repo's .worktrees/ gitlinks — #158 committed
    // live worktree checkouts with no .gitmodules, breaking `uv pip install git+@sha`
    // (git submodule update failed) on EVERY seat rebuild. Untracks + gitignores them.
    // 63a3bca (#162, ADR 0075): proactive outbound relay to rostered client/vendor.
    // fdf8870a (#163, ss #1806, ADR 0071 harden): out-of-band send of the approved
    // confirm payload — the overlay dispatches the send itself (the LLM does not
    // reliably re-invoke on "yes"), re-authorized through the same evaluate_tool_call
    // gate + CONFIRM_SEND_DISPATCHED/FAILED audit rows. Child of #162; carries it.
    // No tracked twin moved; overlayRef-only. Superset of 63a3bca (#162).
    // 36fa158d (#165 + #167, ss #1915/#1916): hermes-smd-escalation mediated
    // ledger tools (escalation_append via the broker verb + escalation_state
    // over the ledger twin — replaces the refused execute_code append snippet
    // found dead by the WP-D live proof) + durable-job tool mappings (the same
    // unmapped ⇒ REFUSED class). NEW tracked twin pair
    // shared/escalation_ledger.py <-> operator/workspace_broker/
    // escalation_ledger.py (sha c4882668). Superset of d6739132 (#164).
    // a16f9580 (#169, ss #1935/#1932/#1931): escalation_append derive_only
    // (real ACK codes before the alert sends) + recipient-aware reply floor
    // (INTERNAL recipients not content-floored, mirrors ADR 0072 send path) +
    // per-skill settings live-writable in the config applier. No tracked twin
    // moved; overlayRef-only. Superset of 4d0be7ec (#168).
    // 3ffc2d1f (#170, ss #1941): peer-memory capture nudge — the ADR 0048
    // learned lane's write side. pre_llm_call injects the
    // record_peer_preference capture instruction on every sender-attributed
    // turn (fleet-wide zero rows: the tool existed, nothing prompted its use).
    // No tracked twin moved; overlayRef-only. Superset of a16f9580 (#169).
    // aa7d78f2 (#171, ss #1941 probe find): peer-memory keys the peer on the
    // Svix-verified sender via a claim-once unbound-origin handoff (dispatch
    // session_id is empty on the live email path; Hermes threads the ROUTE as
    // sender_id — the first live capture keyed webhook:agentmail, a channel
    // not a person). overlayRef-only. Superset of 3ffc2d1f (#170).
    // eb17f3cb (#172, ss #1943): inbound taint/fence rendezvous — the
    // chokepoint claims the fresh dispatch-unkeyed PENDING bucket and fences +
    // taints under the turn's own session id; rostered senders classify
    // internal (no fence/taint), strangers now actually hit the wall.
    // overlayRef-only. Superset of aa7d78f2 (#171).
    // f8808c6c (#173, ss #1946): per-turn tool-surface trim — translate emits
    // agent.disabled_toolsets on every profile config (browser/computer_use/
    // media/social/session_search, + workspace when no google_auth) and
    // agentmail blocked_tools excludes 8 inbox-admin/destructive tools via
    // native mcp_servers.tools.exclude. No send/draft/read tool leaves the
    // menu (ADR 0025 unchanged). Measured -7,272 tokens/turn of prompt-cache
    // write on the live payload (vfy_01KXKJEEV1R4EPYFKA6J7YDH16).
    // overlayRef-only. Superset of eb17f3cb (#172).
    // 68ecea36 (#176): msgraph-mail tool classes + read fencing — the manifest's
    // named coordinated change for ss#1986; unblocks seat reprovisions (boot
    // probe FATALed on the unmapped baked connector).
    // 02d90917 (#175, ss work-liveness fix): heartbeat scheduler self-check —
    // every beat reports scheduler_ok / job_count / max_overdue from a scan of
    // profiles/*/cron/jobs.json, so a locked-out or wedged scheduler pages in
    // minutes instead of the 8 silent days of the 2026-07-16→24 incident.
    // 73a2df84 (#174, ss #1961): report emails render an html half at send
    // time — shared/report_render.py transforms the markdown the report skills
    // already author (## headings, numbered items) into an inline-styled html
    // body, and hermes-smd-trust attaches it in pre_tool_call AFTER every gate
    // allows (purity invariant: the html adds no content the fabrication/floor/
    // taint scans did not already see). Block structure gates the render, so
    // prose replies stay byte-identical. overlayRef-only (neither touched file
    // is a tracked twin). Superset of f8808c6c (#173).
    // e031c09 — mcp:msgraph-mail registry entry (overlay#180, ss#1978):
    // materializes the connector's outbound tool surface so an msgraph seat can
    // act, not just receive (the smd-staging live-fire found it baked but never
    // launched). Range also carries overlay#179 (connector-health, ss#1990).
    // overlayRef-only: git-diff-verified none of the 8 tracked twins changed
    // across 9b3f712..e031c09.
    // 6481ac81 — Graph mail-channel chokepoint instrumentation (overlay#181,
    // ss#1990/ADR 0080): every MsGraphClient.request() outcome lands in the
    // connector-health ledger under msgraph_mail with conn-class computed from
    // the real status code — the channel bypasses post_tool_call, so this is
    // where its outages become visible. overlayRef-only: git-diff-verified no
    // tracked twin changed across e031c09..6481ac81.
    // 167ebc50 — missing connector-ledger DIR pages instead of holding green
    // (overlay#182, ss#1990): the smd-staging live finding — /run is root-owned,
    // the dir was never boot-created, every record silently failed, and a real
    // Graph 401 outage read legit-empty green. Pairs with entrypoint.sh's
    // boot-contract mkdir (same trap /run/smd-mcp closed 2026-06-24).
    // overlayRef-only across 6481ac81..167ebc50.
    // cd213a54 — scheduler_check reports overdue=0 (not None) in the healthy
    // steady state (overlay#183): a work_overdue alert could never auto-resolve
    // because "nothing overdue" was unreported and NULL holds (pilot's 02:29Z
    // reprovision-window alert sat open forever). None now = unmeasurable only.
    // overlayRef-only across 167ebc50..cd213a54.
    // 293a0424 — bootstrap reconciles profile homes against the authored
    // persona set (overlay#185, ss#2009): the 2026-07-13 persona-slug rename
    // left the retired slug's home + frozen cron store on the volume; the
    // scheduler monitoring then raised work_overdue on a store nothing
    // serves. Orphaned homes are now deleted at translate, so renames are
    // self-cleaning. Range also carries overlay#184 (Sentry event throttle).
    // overlayRef-only across cd213a54..293a0424.
    // bed9ebdd — retired persona name purged from overlay fixtures + permanent
    // full-repo guard test (overlay#186, ss#2009 close-out): the fixture copies
    // shipped inside the overlay pack onto customer volumes; the volume-wide
    // negative scan found them. overlayRef-only across 293a0424..bed9ebdd.
    // 9d15c6a9 — operator pause (overlay#188, ss#2003): pin_hard_stops +
    // gate POST /sticky-stop/set (exact mirror of /sticky-stop/clear, same
    // console-proxy bearer, idempotent) + trust-plugin total tool wall at
    // HARD_STOP (the chokepoint covering cron-fired wakes, which no pre_run
    // gates). Range also carries overlay#187 (secret-scan fallback-recipients
    // exemption, ss#2004). overlayRef-only across bed9ebdd..9d15c6a9.
    // 12fea42b — the sustained-dialogue program, Phase 1 (ss#2070): overlay#196
    // authored send_policy (trust-class reply caps, internal dialogue
    // exemption, reply backstop — a rostered colleague is no longer rate-held
    // mid-conversation), #197 held-reply persistence + the auto-release
    // sweeper (a rate-held reply used to be audited and dropped, so the
    // Operator simply went silent), #198 deterministic session→origin binding
    // by message id (closes overlay#195: concurrent messages from one person
    // got their replies crossed onto the wrong thread), #199 the per-person
    // usage meter + usage_export runtime-read kind. overlayRef-only across
    // 23ff1575..12fea42 — no tracked twin moved.
    // 711310ff — overlay#200, the two defects the Phase-1 rehearsal exposed
    // (vfy_01KYTG0B88R3B5K0D7FKPACRZT). A create_draft that FAILED still put
    // mail in the client's inbox because the relay decided on the tool name
    // alone, and the agent's retry sent the same answer again; the relay now
    // requires a draft the tool confirmed plus one reply per inbound message
    // id. And the relay's fabrication re-check ran without the provenance
    // caption allowlist the drafting path passes, so a reply naming matters
    // the agent had just read from Smokeball was blocked as
    // fabrication:tier2_citation and dropped silently. overlayRef-only across
    // 12fea42..711310f — no tracked twin moved.
    // 7243d3a7 — the ADR 0083 W0-prime seam (ss#2079): overlay#201 makes `seat`
    // and `output_classes` live-writable, and pins `personas.*.tone` as NEVER
    // live-writable. The tone entry is the load-bearing one and reads backwards
    // at first glance: a merged persona register currently never reaches a seat
    // at all, so making it live-writable looks like the fix. It is not. `tone`
    // is rendered into SOUL.md by translate at BOOT and the config applier does
    // not re-run translate, so a live apply would update the volume yaml, record
    // APPLIED, and leave the agent's system prompt carrying the OLD register — a
    // silent partial apply, worse than an honest rejection because the ledger
    // would claim it landed. The register instead arrives on the next restart,
    // via the unconditional boot fetch. overlayRef-only across 12fea42b..7243d3a
    // — no tracked twin moved; 711310f is 7243d3a's parent.
    // 151d1340 - the ADR 0083 spec loader (ss#2084): overlay#202 installs the
    // customer's authored voice/format specs as a ROOT-OWNED tree and adds the
    // per-turn read mark. The ownership is the load-bearing half: read_file is
    // READ-class, unfenced, and does not taint the session, so a spec the agent
    // could write would be a persistent, untainted, self-authored injection
    // channel surviving restarts - the same self-loopback shape proven live on
    // hermes-smd-staging 2026-06-15, answered then and now with ownership rather
    // than policy. overlayRef-only across 7243d3a..151d134 - no tracked twin moved.
    // d28f3713 - the defect the FIRST live spec install exposed (overlay#204,
    // vfy_01KYWVR8PBBEP85W3F5SSNC9FD). mkdir(parents=True) creates intermediate
    // dirs with 0o777 & ~umask and does not apply the caller's mode; the applier
    // hardened spec_dir and the LEAF but not classes/, which kept root's 0o750.
    // No world execute, so the agent could not TRAVERSE to a spec whose own mode
    // was a correct 0644 - and since the gate passes only on a verified read,
    // every staff autonomous send would have downgraded to draft permanently
    // against a healthy-looking tree. The pre-existing mode test asserted
    // classes==0755 and PASSED: the runner's umask is 0o022, under which the
    // buggy code yields 0755 by accident. It measured the environment, not the
    // code, which is why the replacement parametrises the umask.
    // overlayRef-only across 151d134..d28f371 - no tracked twin moved.
    // a8bffaf6 - the ledger records what AUTHORIZED the call (overlay#210,
    // ss#2122). The trust gate computed the whole authorization trail on
    // pre_tool_call and the audit plugin wrote the row on post_tool_call with
    // nothing joining them, so ceiling_level was null on 100% of 4130 live pilot
    // rows and matter_ref on all of them: a per-matter compliance export filters
    // on matter_ref and therefore returned an empty audit section for every
    // matter, silently (vfy_01KYZADQ8H). shared/trust_decision.py carries the
    // decision across, and its fallback slot is THREAD-LOCAL: delegate_task runs
    // worker threads with their own event loops (/opt/hermes/model_tools.py:66-80,
    // vfy_01KYZC1WWG), so a process-global slot would hand one thread's ceiling to
    // another thread's row. A mis-attributed ceiling is worse than a null one - it
    // asserts that something authorized a call it did not. Every row also stamps
    // HOW it matched, because a compliance ledger may not present an inferred join
    // as a keyed one. UNLIKE every bump above, this one DOES move a tracked twin:
    // plugins/hermes-smd-audit/emit.py is a pair, overlaySha256 re-recorded
    // 0bbc831f -> b34e7440; the adapter twin operator/adapter/audit_log.py is
    // unchanged (new fields ride the existing metadata JSON, matter_ref column
    // already existed) so its sha256 holds. verify-overlay-pairs.py against the
    // real overlay at a8bffaf: all 8 pass.
    // 46de5c90 - pair-keyed provenance (overlay#211, ss#2127/#2128; supersedes
    // overlay#208). Atom provenance asks "was this value read?", which cannot see
    // a MISPAIRING - and a mispairing is what reached the firm: on 2026-08-01 the
    // Operator wrote "matter 2026-PI-105, deposition of plaintiff Alvarez, August
    // 6, 2026" when the event carried matterNumber=2026-PI-101. Both values had
    // been read that session, so every atom verified and the line passed clean
    // (vfy_01KYZBTMFRM72S7VF2W4ADJMVP). record_read now seeds (matter, date)
    // associations ONE RECORD AT A TIME - never per blob, because a tool result is
    // a collection and pairing everything in it registers the cross-product,
    // verifying precisely the defect this catches.
    // This bump ALSO adds a 9th pair: operator/safety-substrate/identifier_filter.py
    // <-> shared/identifier_filter.py. It should always have been one - its sibling
    // citation_filter.py is - and because it was not, the copies diverged in BOTH
    // directions unseen: ss-console ahead on _CASE_RE matter numbers, the
    // ISO-datetime fix and pair support; the overlay ahead on caption support
    // (#1758) that ss-console still lacks. The overlay's own "CONTRACT TEST"
    // cannot catch this: it imports shared.identifier_filter, its own copy, and
    // asserts the file agrees with itself. The caption gap is tracked in #2125.
    // No pre-existing tracked twin moved (a8bffaf..46de5c90 touches only
    // identifier_filter.py, provenance.py and their tests).
    // verify-overlay-pairs.py against the real overlay at 46de5c90: all 9 pass.
    //
    // 2026-08-01, 46de5c90 -> 1594f687 (ss #2094 / ADR 0083). Two commits, both
    // this work: overlay#212 gives the drafting lane a declared exit
    // (`smd_deliver_draft`), and overlay#213 fixes what the spec pointer told a
    // drafter about the consequence of not reading.
    //
    // #212 exists because the spec gate could not SEE a work_product draft.
    // `pre_tool_call` carries tool_name/args/task_id/session_id/tool_call_id and
    // nothing about what is being produced; `content_ceiling` — which
    // output-classes.yaml names as work_product's `declared_by` — has no runtime
    // counterpart at all; and the one skill-name resolver is dead code documented
    // "never an entitlement input" (vfy_01KYZF6CYFRQ9SJDWQF0FDNX7W). So
    // create_memo carrying a demand letter is indistinguishable from the same
    // call carrying a chronology row. The lane now names its class instead.
    //
    // #213 is the defect that surfaced from exercising the pointer against a
    // firm-voice class for the first time: it asserted "an unread spec means the
    // send is refused and routed to a draft", which is false for work_product
    // (external_send: forbidden, and the artifact already IS a draft) — a
    // drafter would read the consequence as benign.
    //
    // NONE of the 8 pre-existing tracked twins moved (git-diff-verified
    // 46de5c90..1594f687; the range is exactly those two commits). #212 relocates
    // check_spec_gate to shared/spec_gate.py because hyphenated plugin dirs are
    // not dotted module paths, and neither path is twinned.
    // verify-overlay-pairs.py against the real overlay at 1594f687: all 9 pass.
    //
    // #214 (ss #2091, ADR 0083 §4) gives the Operator a way to RECORD a
    // correction it was told. The broker's `correction_propose` verb has been
    // complete since it shipped — uid-gated, validated broker-side, status
    // stamped as a constant — and nothing called it: the verb's own comment
    // names an `execute_code` turn as the caller shape, which is the path WP-D
    // found dead for the escalation ledger (ss #1915), because `code_execution`
    // has no authored exposure on any Operator seat. The new
    // hermes-smd-corrections plugin is the same mediated-tool fix that worked
    // there, mapped INTERNAL_WRITE so it needs nothing widened. The taint
    // refusal sits in `pre_tool_call` rather than the handler because Hermes
    // hands a tool handler only task_id/user_task, never session_id.
    //
    // NONE of the 9 tracked twins moved (git-diff-verified 1594f687..11eca2c0:
    // the five changed files are the new plugin dir, root plugin.yaml,
    // shared/action_classes.py, and its test — zero intersection with the pair
    // overlayPaths), so every overlaySha256 is unchanged and only overlayRef
    // moves. verify-overlay-pairs.py against the real overlay at 11eca2c0: all
    // 9 pass.
    //
    // #216 + #217 (ss #2122, 2026-08-02) close the audit-ledger attribution and
    // vocabulary halves: #216 resolves a cron session's embedded job id against
    // the persona cron store's stable managed name AT EMISSION TIME, so
    // skill_name lands on the row while the id → name mapping is alive (job ids
    // rotate on re-materialization); #217 declares the eight action types the
    // unvalidated writer path was already persisting (plus broker-side
    // CORRECTION_PROPOSED) and adds the AST completeness guard that scans the
    // writer surfaces. ONE tracked twin moved (plugins/hermes-smd-audit/emit.py,
    // git-diff-verified 11eca2c0..3af998c3) — its overlaySha256 re-recorded; the
    // console twin audit_log.py moved separately in ss#2156 (sha re-recorded
    // there). Deliberately excludes overlay#215 (Sentry scrub, unmerged at pin
    // time) — that lands as its own bump + rebuild.
    //
    // 3af998c3 -> ea752ab5 (2026-08-02, the bump the line above announced):
    // overlay#215 Sentry identifier scrub (ss #2150 P0, DPA Exhibit B-1 —
    // matter-number + GUID shapes in redact_text, extra/contexts/
    // logentry.params walked, breadcrumb data recursive, consumes.yaml DSN
    // note reconciled). The range is exactly the one squash commit —
    // sentry_init.py + its test suite + consumes.yaml, NOT tracked twins
    // (GitHub compare 3af998c3...ea752ab5) — so every overlaySha256 is
    // unchanged and only overlayRef moves. verify-overlay-pairs.py against
    // the real overlay at ea752ab5: all 9 pass.
    //
    // ea752ab5 -> 3e40f0c0 (2026-08-02, ADR 0085 plan Deploy-0): overlay#218
    // read_file on webhook turns via a read-only custom toolset (ss #2145).
    // The spec read-mark is set only by a read_file call, and the webhook
    // platform's safe toolset deliberately carries no file tools — so every
    // voice-gated delivery on an inbound-email turn refused forever. Fix is
    // two-process (translate.py platform_toolsets emission + plugin-load
    // create_custom_toolset with exactly ["read_file"]) plus a FATAL-loud
    // boot assertion, because the config half alone fails SILENTLY
    // (quiet_mode suppresses the unknown-toolset warning). The range is
    // exactly the one squash commit — translate.py + webhook-router plugin +
    // activation handler + shared/webhook_read_surface.py (new) +
    // consumes.yaml + tests, NOT tracked twins (git-diff-verified
    // ea752ab5..3e40f0c0) — so every overlaySha256 is unchanged and only
    // overlayRef moves. verify-overlay-pairs.py at 3e40f0c0: all 9 pass.
    //
    // 3e40f0c0 -> 64918213 (2026-08-02, ss#2171 PR 1a / ss#2132): overlay#224
    // structured write args reach the identifier scan (REPORT mode — the soak
    // that authorizes the refuse-mode flip) + ordinal-date extraction/folding.
    // The range moves ONE tracked pair: shared/identifier_filter.py, whose
    // overlaySha256 is re-recorded in overlay-pairs.json; the ss-console side
    // deliberately does not move until the PR 2 substrate sync.
    // 64918213 -> fad5431b (2026-08-02, ADR 0085 Deploy-1 pin): overlay#223
    // per-person preferences (ss #2067 / ADR 0085 §6) — the person predicate +
    // person possession ceremony in the establishment plugin, the
    // spec_applier/preferences.py root-owned materializer, and
    // shared/person_prefs.py. Completes the establishment bundle on the seat
    // (with #219 intake, #220 additive gate repoint, #221 results-dir fix,
    // #222 admin possession). The range is exactly the one squash commit —
    // git-diff-verified zero intersection with pair overlayPaths — so every
    // overlaySha256 is unchanged and only overlayRef moves.
    // fad5431b -> 62da0504 (2026-08-02): overlay#225 moves the establishment
    // spool out of /opt/data. The gateway chmods its home to 0700 mid-boot
    // (the audit ledger works around the same behavior with a bind mount), so
    // the broker uid could not traverse to a spool whose own dirs were a
    // correct 0770 — live-caught at the first establishment call on the pilot.
    // One squash commit, no tracked twin moved.
    // 62da0504 -> 73007247 (2026-08-09, ss#2171): overlay#226 — the identifier
    // gate REFUSES. Report-only becomes blocking at all three call-site paths;
    // block set = every kind except NAME; ambient dates (utc today/yesterday)
    // verify against the clock; the empty-register carve applies to the DRAFT
    // gate only (the send gate blocks — autonomous send + nothing read =
    // "cannot verify"); SMD_IDENTIFIER_GATE_MODE=report is the operator-only
    // rollback lever, unset/garbage = block (fail-closed parse). Audit rows
    // keep action_type IDENTIFIER_UNVERIFIED and gain mode/blocked/
    // block_bypass/date_distance. The range moves ONE tracked pair:
    // shared/identifier_filter.py (posture docstring only — no code change),
    // overlaySha256 re-recorded d8c57385 -> d03192e8; the ss-console substrate
    // copy gets the identical docstring in this PR, sha256 re-recorded
    // 798dbc26 -> a573139f. verify-overlay-pairs.py at 73007247: all 9 pass
    // (run recorded in the PR body).
    // 73007247 -> ba6d116a (2026-08-09, ss#2148): overlay#227 — durable-
    // credential age rides the heartbeat. connector_check.token_ages() reads
    // the Smokeball refresh-token file's mtime age; the heartbeat ships it as
    // connector_token_age, a SEPARATE field from the health map (a synthesized
    // consecutive_failures=0 entry would falsely RESOLVE an open
    // connector_down alert — pinned by the overlay's
    // test_token_ages_never_synthesizes_health_entries). Feeds the console's
    // connector_token_expiring pre-expiry condition (migration 0103 + worker
    // branch, same PR). Touches shared/connector_check.py + shared/heartbeat.py
    // — NOT tracked pairs; every overlaySha256 unchanged, only overlayRef.
    // verify-overlay-pairs.py at ba6d116a: all 9 pass.
    // ba6d116a -> 64408467 (2026-08-10, ss#2222 gate 3): overlay#228 — authored
    // initiation authority. New plugin hermes-smd-initiation injects the
    // person-initiation disposition per sender-attributed turn (rostered direct
    // ask initiates manual skills; admin-reserved skills require scope.admins;
    // embedded content never initiates; no improvised skill reports). Closes
    // the card-rehearsal R1 finding (ss#2221). Range is the new plugin + root
    // plugin.yaml + README + test only — NOT tracked pairs (git-diff-verified
    // zero intersection); every overlaySha256 unchanged, only overlayRef.
    // 64408467 -> 8833b3fe (2026-08-10b, ss#2222 gate 3 second half):
    // overlay#230 — the injection never fired live because pre_llm_call's
    // sender_id on webhook-dispatched turns is the ROUTE (webhook:agentmail),
    // never the person (the ss#1941 shape). Initiation resolves the verified
    // sender via SESSION_INBOUND_ORIGIN with a cooperative re-key and now
    // registers before peer-memory. plugin.yaml + initiation plugin + test
    // only — NOT tracked pairs (git-diff-verified); every overlaySha256
    // unchanged, only overlayRef.
    // 8833b3fe -> df1dbb83 (2026-08-10c, ss#2222): overlay#233 classifies
    // mcp_smokeball_render_docx_template = INTERNAL_WRITE + pin coverage —
    // the coordinated half of ss#2241's renderer. Without it the boot
    // conformance probe refuses the unmapped tool and kills boot (live-caught
    // on pilot v116, rolled back to v115). Range also carries overlay#232
    // (jobs teardown-shaped broker fix). shared/action_classes.py +
    // shared/job_worker_runtime.py + tests — NOT tracked pairs
    // (compare-verified); every overlaySha256 unchanged, only overlayRef.
    // df1dbb83 -> 947cc2f3 (2026-08-10d, ss#2222): overlay#234 — establishment
    // classifies the VERIFIED sender, not the webhook route. Live-caught: an
    // authored admin was refused "only Operator admins can establish" and the
    // possession ceremony behind that predicate never fired, so no challenge
    // was sent. Same ss#1941 shape initiation fixed in overlay#230. Range also
    // carries the ss#2234 spec-control work. NOT tracked pairs
    // (compare-verified); every overlaySha256 unchanged, only overlayRef.
    // 947cc2f3 -> 44067ae1 (2026-08-10e, ss#2167): overlay#235 — the outbound
    // matter-identity gate. Stops case A's content reaching case B's recipient,
    // the one fabrication class with no control. Checks the body's own matter
    // identifiers against who is party to that matter; neither side is the
    // model's word, because a send that DECLARED its matter would be circular
    // (the model resolves the recipient's matter to address them, so it would
    // declare that one and always agree with itself). Placed above the
    // `decision.allowed` guard: on a draft_for_review seat every send is
    // withheld, so a check inside that block would never run on the seat it is
    // for. CORRECTION (ss#2252): this comment originally said "silent until
    // authored". That was false — the gate reads no customer.yaml posture and is
    // ON by default; SMD_MATTER_GATE_MODE is the only lever. The safety property
    // that does hold is that a mismatch downgrades to a human draft rather than
    // refusing, and an unresolved membership does not withhold at all.
    // plugins/hermes-smd-trust/{enforce,__init__,matter_gate}.py +
    // shared/matter_binding.py + consumes.yaml + tests — NOT tracked pairs
    // (the tracked twins are voice/transform, audit/emit and the shared/
    // filters; this change edits none of them); every overlaySha256 unchanged,
    // only overlayRef.
    // 44067ae1 -> 055f912e (2026-08-11, ss#2247): overlay#236 — establishment
    // corpus staging is exempt from the draft fabrication gate. That gate scans
    // text the AGENT composed; a staged document is the FIRM's own work product
    // copied byte for byte, so scanning it for fabrication asks whether the firm
    // fabricated its own letter. It protected nothing either: read_document had
    // already returned that text to the model earlier in the same turn. Live
    // cost on 08-11: it refused the firm's demand letter (dollar figures) and
    // trial binder (dates), two of three blessed exemplars, and the agent then
    // DELETED the figures so the letter would stage. A gate that cannot be
    // satisfied honestly teaches the model to satisfy it dishonestly, and the
    // edit is invisible where the refusal would have been visible.
    // establish_submit stays gated (its spec_body IS agent-composed), and the
    // real controls (spec_leak_check, digit invariant) both passed on that run.
    // plugins/hermes-smd-trust/outbound.py + tests — NOT tracked pairs; every
    // overlaySha256 unchanged, only overlayRef.
    // 055f912e -> 57818798 (2026-08-11b, ss#2247+ss#2222): six commits.
    // overlay#241 — establishment stages corpus documents BY REFERENCE: the
    // seat captures read_document results at post_tool_call (raw, pre-fence,
    // session-scoped) and establish_stage_document assembles the captured
    // windows; model-supplied text is refused UNCONDITIONALLY for connector
    // documents, because the live run retyped 19KB and drifted (48 chars
    // dropped, chars substituted) and instruction cannot fix a step the
    // mechanism makes impossible. Paged-to-the-end becomes a gate. Broker and
    // intake byte-identical. overlay#242 — operator_seat_facts: grounded seat
    // facts as a registered tool (the introduce card phrasings never fired on
    // email turns; the router body's skill_view instruction named a tool that
    // is NOT on the webhook surface — live probe, 15 tools). Three-state voice
    // status via manifest_state(); run-history stripped at the read boundary;
    // WEBHOOK_EXPECTED_TOOLS warn tier (heartbeat, not _die). Range also
    // carries overlay#237/#238 (docs/contract) and overlay#239 (ss#2151 item
    // identity, peer lane) — #239 goes LIVE with this bump. ONE tracked twin
    // moved: shared/escalation_ledger.py (overlay#239); its pair sha256s are
    // recomputed to the new byte-identical value (ss side moved in ss#2257).
    // Every other overlaySha256 unchanged.
    // 57818798 -> 658169eb (2026-08-11, ss#2167): overlay#240 — the matter gate
    // gains the two things it lacked. It recognised a matter only as a raw
    // UUID, so a letter citing "2026-PI-101" returned unresolved even against a
    // CLOSED party set; every shipped test seeded a UUID body, so the suite and
    // both kill-tests passed over it (vfy_01KZRRW59N6HS3DHVQJRNMKVHW). Matters
    // are now aliased by number, ambiguity withdrawn rather than guessed. And
    // it never ran on the reply lane at all — guarded by `is_send`, true only
    // for EXTERNAL_SEND*, while that lane calls create_draft (INTERNAL_WRITE)
    // and relays the draft out over REST (vfy_01KZRRW066Y70TFEYKGQX6ME76). It
    // now evaluates at the relay seam, with an exemption that deliberately does
    // NOT read `recipient_class is INTERNAL` — an inbound-roster match
    // classifies INTERNAL before the typed roster is consulted, so that
    // spelling would have exempted 100% of the lane (filed as ss#2263).
    // matter_gate.py moves to shared/ so the reply plugin can import it.
    // NOT tracked pairs; diff-verified zero intersection across the range;
    // every overlaySha256 unchanged, only overlayRef.
    // 658169eb -> 1d73e2c0 (2026-08-11d, ss#2247): overlay#243 — the read
    // capture unwraps the dispatcher envelope. First live reference-staging run
    // found capture silently dark: post_tool_call's result string is
    // {"result": "<connector JSON as a string>"}, not the connector's JSON, so
    // the capture parsed the wrapper, found no top-level `text`, and returned
    // through the silent guard — every stage refused no_capture after four
    // genuine reads. (The Operator's failure report was honest and it did NOT
    // fall back to retyping text; the unconditional refusal held.) The unwrap
    // peels the wrapper and the MCP content-block shape, at most twice, and
    // stops the moment the payload carries `text`. Regression tests pin the
    // LIVE string shape from the seat's session store — the gap that let
    // overlay#241's green suite miss this was testing the connector's
    // documented shape instead of the dispatcher's delivered one.
    // plugins/hermes-smd-establishment/__init__.py + tests — NOT tracked
    // pairs; every overlaySha256 unchanged, only overlayRef.
    // 1d73e2c0 -> e9bfe987 (2026-08-11b, ss#2269 + ss#2262): the matter gate can
    // SEE the number forms the firm's matters actually use. Alternation is
    // first-match-wins and the short branch sat ahead of the long one, so the
    // real matter PI-2026-0001 matched as PI-2026 and -0001 was left behind; the
    // truncated token resolved to nothing and the send was not withheld. Live on
    // the pilot (vfy_01KZRZH044CH4N5EEKHQ9A6KHW) that matter is the ONE of nine
    // with a complete party list, so the gate could not withhold a number-cited
    // send on that seat at all — ss#2167's join was correct and this regex
    // defeated it. Longest-alternative-first plus IGNORECASE, the latter safe
    // because _resolve_cited keeps only tokens that resolve to a matter the
    // session read, so a false positive cannot manufacture a verdict (which is
    // what let ss#2262 close in the same three lines). Also corrects the comment
    // claiming byte-compatibility with identifier_filter._CASE_RE — they had
    // diverged and the comment hid it (same shape as ss#2252).
    // shared/matter_gate.py + tests — NOT tracked pairs; diff-verified zero
    // intersection across the range; every overlaySha256 unchanged.
    // e9bfe987 -> ad647365 (2026-08-11c, ss#2258 + ss#2255): a seat sends from
    // ITS OWN inbox, or refuses. resolve_inbox_id took inboxes[0] on the
    // docstring reasoning "single tenant per Machine, so the first inbox is the
    // agent's own". AGENTMAIL_API_KEY is account-wide — provision-customer.sh
    // says so outright ("It can reach the shared account's OTHER inboxes
    // (cross-tenant)") — and the listing is newest-first: probed live it held
    // EIGHT inboxes with the pilot's own at index SIX and a probe inbox created
    // the previous afternoon at index 0. The caller is _dispatch_approved_send,
    // which fires the moment a human approves a draft, and a client seat's
    // day-one posture is external_send: draft_for_review — so every approved
    // letter went through it. The moment ashton-price's inbox is created it
    // becomes inboxes[0] and every OTHER seat starts sending as that client.
    // Latent only because CONFIRM_SEND_DISPATCHED has 0 rows ever, which is also
    // why nobody caught it. Now: authored AGENTMAIL_INBOX_ADDRESS else the
    // <slug>@agentmail.to convention, and the address MUST appear in the listing
    // — a miss RAISES rather than falling back, because sending from another
    // firm's mailbox is worse than not sending. Also sweeps overlay#246 (docs,
    // stranded 29 min after the last bump) and overlay#247 (webhook fallback
    // instructs read_file not the absent skill_view, ss#2255).
    // outbound_send.py + reply/__init__.py — NOT tracked pairs; every
    // overlaySha256 unchanged, only overlayRef.
    //
    // ad647365 -> 1b74e1cb (2026-08-11d): a three-commit range whose console
    // halves all landed first. overlay#249 (ss#2276) volume sentinel for durable
    // cron disable; overlay#250 (ss#2258) AgentMail transmit behind the workspace
    // broker — the two REST send paths call broker verbs, agent-side inbox
    // resolution is DELETED, the four MCP send tools leave the menu, and
    // smd_send_message replaces them carrying the same EXTERNAL_SEND class so the
    // authored external_send_internal:autonomous tier survives the change;
    // overlay#251 (ss#2220) seat_gate_binding_snapshot fixture sync. NO tracked
    // twin moves — recipient_classifier.py was READ during the ss#2258
    // canonicalization work but not modified — so every overlaySha256 is
    // unchanged and only overlayRef moves. The broker half is ss-console#2279;
    // neither half functions alone.
    //
    // ec3fb713 -> d8e0d767 (2026-08-13): a Captain-authorized SINGLE bump past
    // an eight-commit backlog. It is one bump rather than four because four
    // sessions had independently queued on this lever, and four separate
    // rollouts would mean four chances to meet a bad interaction with no way to
    // tell which commit caused it. Range: overlay#257 content floor reads the
    // html half of a send (ss#2297); #258 one name for the tool-call correlation
    // key (ss#2312); #259 ACK code bound to the ledger row it was written for
    // (ss#2304); #261 working rules become a READ section (ss#2338); #260 person
    // nudge covers work sent TO a person (ss#2151); #262 the installed voice
    // carries what it was learned from (ss#2339); #263 the gate can see a
    // delivery it suppressed (echo-guard Phase 1a); #264 the send tool can be
    // called the way Hermes calls it (ss#2348). ONE tracked twin moves —
    // hermes-smd-audit/emit.py at #258 — and it is one-sided: the console twin
    // operator/adapter/audit_log.py has no build_per_tool_metadata and carries
    // neither trace_id nor tool_call_id, so overlaySha256 moves and the ss-twin
    // sha256 does not. All nine overlaySha256 values were verified against their
    // files AT THE OLD PIN first, so the manifest was honest going in. Live
    // blast radius at bump time: pilot-smokeball and smd-staging only —
    // hermes-ashton-price is stopped with autostart disabled.
    //
    // d8e0d767 -> d567cfb (2026-08-13b): the second bump of the day, carrying
    // overlay#266 (classify render_docx_draft) and #268 (stop forbidding the
    // dollar figure the skill authorizes), both ss#2258. Nothing in the drafting
    // lane's four built phases reaches a seat until this lands. ONE tracked twin
    // moves — shared/identifier_filter.py at #268 — and it is one-sided: the
    // change adds MONEY_RE/canon_money/extract_money, and ss-console has no
    // consumer of any of them (grep returns nothing across the repo, while the
    // same command shape finds _DATE_STRPTIME_FORMATS in that very twin, so the
    // search works and the symbols are absent). overlaySha256 moves; the ss-twin
    // sha256 does not. Both parity snapshots were re-derived rather than
    // re-stamped: heartbeat.py (af3cf3f2) and hermes-smd-audit/schemas.py
    // (1696c30a) are byte-identical at both ends of the range, so neither
    // derived list can have moved.
    //
    // d567cfb -> 20518e8 (2026-08-13c, Captain-authorized convergence sweep):
    // the target moved BEFORE this bump landed. overlay#248, #265 and #267 all
    // merged after the d567cfb pin was written, so retargeting supersedes it
    // rather than following it — ONE bump and ONE reprovision instead of two.
    // A SECOND tracked twin moves: shared/recipient_classifier.py at #265,
    // one-sided the same way (ss-console counterpart unchanged). Both parity
    // snapshots were re-checked across the EXTENDED range: heartbeat.py and
    // hermes-smd-audit/schemas.py are untouched from d567cfb to 20518e8, so the
    // re-derivation recorded above still holds.
    // 20518e8 -> 0716dc1 (2026-08-18, hardening epic ss#2392 runtime pass):
    // overlay#270 (reply path reads the money register; a delivery-path hold is
    // appended to the draft tool's own result at transform_tool_result, ss#2367)
    // and overlay#271 (ADR 0086 matter-party seeding for the matter gate,
    // ss#2167). NO tracked twin moves in the range (compare API: reply plugin
    // internals, shared/matter_binding.py, docs, tests only), so every
    // overlaySha256 is unchanged. Both parity snapshots re-checked: heartbeat.py
    // and the audit emit surface are untouched 20518e8..0716dc1; re-stamps are
    // the identity. Bump merge is gated on the first armed shadow-firm run
    // (ss#2389 release gate); the run id is cited in the bump PR.
    // af0c8a0 -> 0088352 (2026-08-18f, overlay#275 / ss#2258 A&P bring-up):
    // overlay#278 — the msgraph delta poller holds its cursor on per-item
    // failure instead of orphaning the mail (the observed A&P first-boot loss),
    // with poison-vs-systemic dead-letter discrimination, a resync watermark,
    // Sentry page signals, and shared/msgraph_replay.py as the recovery path.
    // Single-commit range, msgraph poller/replay/consumes/tests only: NO
    // tracked twin moves, heartbeat.py and the audit emit surface untouched,
    // re-stamps are the identity. Shadow-firm gate note: the rehearsal
    // scenarios ride AgentMail, so the green run is whole-overlay regression
    // evidence — the revised poller's own runtime proof happens on the A&P
    // seat per the overlay#275 contract ACs.
    // overlay#281 + #282 + #283 (ss#2444, Hermes v0.18.0 -> v0.20.4 promotion PR-1): translate.py
    // pins the v0.18 behaviours that v2026.8.18 defaults flip (approvals.mode
    // manual, agent.max_turns 90, tools.tool_search off, delegation fan-out 3,
    // display.show_reasoning false) + the tests. Single-commit range, NO tracked
    // twin moves; overlaySha256 unchanged, only overlayRef. #283 canonicalizes the
    // v0.19 mcp__server__tool rename at the fan-out (without it a v0.20 seat refuses
    // every connector tool); #282 is the matter-mixing read fence, carried along
    // because it merged to overlay main between the two bumps.
    // d35e0b0 -> 4dbf415 (2026-08-21): five overlay merges. #296 is the ss#2511
    // fix after the A&P self-test wrote a sentinel case number onto a real
    // matter: the seat's own skill text, memory and scored drafts stop seeding
    // the identifier register (allowlist of tenant-source reads), identifiers
    // seen in the seat's own text block even with an empty register, add_file /
    // render_docx_draft are scanned report-only per tool, and the enforce reason
    // for an executed internal write no longer reads "routed to draft folder".
    // Riding along: #294 (ss#2497 audit joins), #292 (ss#2498 routine on/off
    // rows: ROUTINE_ENABLED/ROUTINE_DISABLED join the vocabulary), #295
    // (ss#2501 sent-reply digest), #297 (ss#2499 msgraph message identity).
    // ONE tracked twin moves: plugins/hermes-smd-audit/emit.py, re-recorded
    // one-sided in overlay-pairs.json.
    // 4dbf415 -> 4ca6682 (2026-08-21, overlay#298): the Captain's flip after four
    // pilot drafting lanes (0 false positives, 1 genuine catch: computed response
    // deadlines reached a filed Word draft while the same values were refused on
    // the memo and the email). render_docx_draft leaves the report-only carve and
    // blocks like every other draft tool; add_file stays report-only until a lane
    // exercises it. Single-commit range, NO tracked twin moves.
    // 4ca6682 -> 349d86b (2026-08-21, overlay#299 + #300): the A&P Operator's setup
    // reply to the firm's administrator hit the Tier-2 citation gate four to six
    // times in one turn on ordinary comparison prose, and the refusal named neither
    // the shape it saw nor a fix, so the model retried blind and then shipped a
    // trimmed reply that told the client a gate had blocked it. #299 gives the
    // refusal a per-pattern hint carrying the kind and the remedy, never the matched
    // text, and closes the hole the retries were finding: "Palsgraf versus Long
    // Island Railroad" matched nothing, because CASE_NAME_RE folds only "v" and
    // "vs". CASE_NAME_VERSUS_RE keeps its parties case-SENSITIVE so "apples versus
    // oranges" still passes, and provenance harvests versus-form captions. #300
    // folds "versus" in canonical_caption on both sides of the repo boundary so an
    // allowlist entry registered by one copy matches a hit canonicalized by the
    // other. ONE tracked twin moves: shared/citation_filter.py, re-recorded
    // one-sided in overlay-pairs.json (behaviour identical, prose and wrapping not).
    // 349d86b -> 991044a (2026-08-21, overlay#301 + #302, ss#2529): the firm
    // teaches the Operator by talking to it. Before this, a partner writing "in
    // client letters, be more formal and shorter, no pleasantries" had no route:
    // firm establishment needs a staged corpus, all four distillation compilers
    // refuse an empty one, so the sentence could only be captured and the person
    // told it was not in effect. Two rehearsal turns spoken by an Operator ADMIN
    // were answered exactly that way on 08-21, which is the opposite of what ADR
    // 0085 section 3 promised. #301 gives the sentence somewhere to live: a spec
    // property renders standing adjustments beside its distilled body, and the
    // intake installs one confirmed sentence with no corpus and no compilers
    // (they cannot run on it - every one of them refuses an empty corpus). #302
    // is the seat behaviour: establish_propose reads the rule back with a tag,
    // the reply must carry that block VERBATIM or the send is refused, the
    // affirmative is read from the sender's OWN words with quoted history
    // stripped (the block says "Reply yes to confirm", so reading the whole
    // message would let the Operator confirm its own proposal), and a submit
    // commits only the id the seat saw confirmed. The corrections nudge is gone
    // and the tool is not. VOCABULARY MOVES, 62 -> 65: RULE_PROPOSED,
    // ESTABLISHMENT_SUBMITTED, ESTABLISHMENT_RESULT. The last two are a
    // correction - they have reached client ledgers since establishment shipped
    // while neither vocabulary declared them. NO tracked twin moves: none of the
    // nine overlayPaths is in the range, re-hashed at the new ref rather than
    // assumed (verify-overlay-pairs.py PASS 9/9).
    // 991044a -> 07ed486 (2026-08-21, overlay#303 + #304 + #305): #304 is the ss#2529
    // hotfix found on the pilot's first live proof: the confirmation matcher read
    // the Operator's own prompt preamble as the person's words, and its "never"
    // made every real "yes" read as a refusal. It now reads only the email body
    // below the untrusted delimiter, with tests rendered from the real template.
    // #303 and #305 are the operator-own-matter commitment confirm (a peer
    // session's work, carried so the seats take one bump, not two). No tracked twin moves.
    // 07ed486 -> b782926c (2026-08-21, overlay#306, ss#2537 follow-up): the pilot
    // seat crash-looped at boot at 22:57Z because ss#2537 authored `commitment:
    // confirm` on pilot-smokeball and ashton-price while the on-box config
    // validator still refused `confirm` on every non-send class. The enforce
    // branch that gives it meaning shipped in #303, carried into the 07ed486
    // bump; the validator had not moved with it. #306 accepts it on `commitment`
    // in a persona's `exposure` and nowhere else, never on `exposure_ceiling`,
    // so it still cannot be authored where it would do nothing. This PR un-parks
    // both seat lines in the same change. No tracked twin moves: bootstrap/
    // validate.py and tests/test_customer_config.py are the whole range.
    // b782926c -> e9d9fae1 (2026-08-21, overlay#307, ss#2529): second hotfix from
    // the pilot proof. The plugin sent the model's own paraphrase as spec_body
    // beside the proposal id and the broker refused it (by design: only the
    // words the person saw may be committed); the Operator then claimed "in
    // effect" with nothing installed. Now a confirmed commit sends only the id,
    // and a reply cannot claim effect until establish_status says installed.
    // e9d9fae1 -> 2b0c786d (2026-08-22, overlay#308, ss#2537 follow-up): read
    // live on pilot-smokeball. SOUL.md rendered the persona's `commitment:
    // confirm` exposure with the generic confirm sentence, which was written for
    // sends: prepare the action and request explicit approval in the same turn.
    // The model did exactly that, described the matter it would create, asked for
    // a yes in its own words, and never called mcp_smokeball_create_matter, so
    // the gate never minted an act proposal and the administrator's yes had
    // nothing to bind to. #308 adds a per-(action, ceiling) SOUL override: at
    // (commitment, confirm) the line tells the model to call the tool as soon as
    // an administrator asks, relay the gate's returned act line verbatim, and on
    // the answer call again with the same values. Send classes keep the generic
    // sentence. No tracked twin moves: bootstrap/translate.py and
    // tests/test_bootstrap_translate.py are the whole range.
    //
    // 2026-08-22: 2b0c786 -> 241df3bc (overlay#309, ss#2547). A refusal that keeps a
    // routine from reaching a human now reaches us instead: the heartbeat carries
    // send_refusals / send_refusals_last_ts / send_refusals_json (refused cron
    // sends, CONFIRM_SEND_FAILED, and wakes with needs-you items that sent
    // nothing, trailing 24h); a pre-run script's handoff seeds the provenance
    // register with date atoms only, bound to one session and consumed once, and
    // a turn cannot write under $HERMES_HOME/.smd/; staff-class sends normalize
    // em and en dashes instead of being refused. No tracked pair moves; schemas.py
    // untouched. Retro-falsifier over the pilot ledger: vfy_01M0N81GHW5PHY0N453ZHN18RY.
    //
    // 2026-08-22b: 241df3bc -> 119f6bf2 (overlay#311, ss-console#2546 PR 2). ONE
    // merge in the range, checked rather than assumed: `git log
    // 241df3bc..119f6bf` on the overlay returns a single commit. A rule stated by
    // somebody who is NOT an Operator admin used to end in silence: it was
    // recorded, nobody was told, an admin's "no" did nothing, and the sweep
    // deleted it so its author could not even be told it lapsed. From this pin
    // the request is emailed to the administrators the firm names on
    // scope.rule_requests_to (traffic, not authority: an admin not named receives
    // nothing), the requester is told on apply, on decline and on lapse, a 30 s
    // daemon reports the lapse because the client seat's crons are all off, and a
    // routine/schedule/channel/memory/autonomy/on-off ask reaches SMD through the
    // new operations_request tool. Every one of those sends re-authorizes through
    // the same enforce.evaluate_tool_call a model's own send goes through, so a
    // tainted turn refuses it and the Operator says so rather than claiming
    // somebody was asked. VOCABULARY MOVED: 65 -> 68 types (RULE_DECLINED,
    // RULE_LAPSED, RULE_REQUEST_NOTIFIED), re-extracted by AST at both refs and
    // compared as sets; all three leave consoleOnly. No tracked pair moves, and
    // that was established by re-hashing all nine twins at the new ref rather
    // than by reading the range's file list.
    //
    // 2026-08-22c: 119f6bf2 -> 29536fcb (overlay#310, ss#2547 follow-up). A silent
    // wake is joined to its routine's OWN turn (skill_name + time, capped at 60
    // min; 30 min when no turn row exists) instead of by session id, which the
    // pre-run child never stamps on an EMITTED_WAKE row. Without it the unsent
    // fact fell back to a fixed 30-minute window on every live wake and would
    // have paged a false "unsent" on any escalator turn that sent after 30 min.
    // Range touches shared/heartbeat.py, tests/test_heartbeat.py and the retro
    // script only; no tracked pair moves; schemas.py untouched. Retro-falsifier
    // at this ref reproduces every muting day per kind on the pilot ledger.
    // 29536fcb -> 87fa8501 (2026-08-22, overlay#312 + #313). #313 is the ss#2546
    // fix from the pilot proof: a firm rule may attach only to one of the six
    // registry output classes (the model had filed rules under invented classes
    // no output ever reads), and the person who asked hears "in effect" when the
    // install is actually observed (the single immediate poll had missed it).
    // #312 is ss#2552 (a peer session): a colleague who states a preference hears
    // what will change, not that a profile was updated.
    // 87fa8501 -> cce3126e (2026-08-23, overlay#314, ss#2546 reopened). A request
    // only SMD can grant comes back answered: the operations email to SMD carries
    // an [ops XXXX] tag, SMD's "done" / "no, reason" reply from a listed SMD
    // address resolves the row (no inbound trust granted), the requester gets one
    // sweeper-sent letter, and the reply at request time can no longer describe
    // the future routine. Vocabulary 68 -> 71 (OPS_REQUEST_*); no tracked pair moves.
    // cce3126e -> fc8f88c1 (2026-08-23, overlay#315, ss#2546 pilot-proof fixes): the
    // requester's outcome letter is claimed once across the sweeper thread and the
    // in-turn path (it had arrived twice, 38 ms apart), and a reply to a sender who
    // cannot answer an ops request may no longer say it is closed. Vocabulary
    // identical (71); no tracked pair moves.
    // fc8f88c1 -> 2c4e8e92 (2026-08-23, overlay#316, ss#2546): the outcome letter is
    // claimed in the broker before it is sent; the seat runs the plugin in two
    // processes (gateway + webhook-gate), so the in-process claim of #315 could not
    // arbitrate. Pairs with console #2564. Vocabulary identical (71); no pair moves.
    // 2c4e8e92 -> 0f84a625 (2026-08-23, overlay#317, ss#2546): on the pass-on turn a
    // future description of the requested routine is withheld even when named only by
    // pronoun ("when it's set up, you'll start seeing it"), and curly apostrophes no
    // longer slip the patterns. Vocabulary identical (71); no pair moves.
    // 0f84a625 -> f16d4920 (2026-08-24, overlay#318, ss#2390 + the ss#2547 live
    // defects): the pre-run handoff actually binds in production (persona-home
    // reader, recency binding), seeds (matterNumber, dates) records as pair
    // associations, the empty-register refusal stops offering value removal, and
    // the heartbeat counts withheld degraded digests as the 'degraded' kind.
    // Vocabulary identical; no tracked pair moves.
    // bc9285bf -> 29eac2c7 (2026-08-28e, overlay#331): unwrap the inbound
    // fence before parsing a counted read. The round-5 trace (the #330 journal
    // in one turn): marking OK, evaluation OK, accumulation dead - the hook
    // receives nonce-FENCED text because Hermes v0.20.4 fires transform before
    // post (ss#2444); same cure establishment took 2026-08-20. read_volume +
    // tests only. Vocabulary identical; no tracked pair moves.
    // 6e4223d1 -> bc9285bf (2026-08-28d, overlay#330): shape-only trace journal
    // for the read-volume gate (/tmp/read_volume_trace.jsonl, 120-entry cap,
    // keys/types/counts never content) - three silent rehearsal rounds had no
    // discriminator between marking, accumulation, and evaluation failures.
    // read_volume + tests only. Vocabulary identical; no tracked pair moves.
    // 3f86737d -> 6e4223d1 (2026-08-28c, overlay#329): the read-volume
    // accumulator parses the dispatcher envelope. Round-3 rehearsal: session
    // marked, 30pp read against a 20pp threshold, gate silent - post_tool_call
    // hands {"result": "<connector JSON as a string>"} and the field walker
    // never descended nested JSON strings (the exact live-caught trap
    // _unwrap_read_result documents in hermes-smd-establishment). read_volume
    // + tests only. Vocabulary identical; no tracked pair moves.
    // c2668ce2 -> 3f86737d (2026-08-28b, overlay#328): the read-volume gate's
    // review marker watches skill_view in addition to read_file. The live pilot
    // rehearsal showed the model reads skill procedures via the gateway-native
    // skill_view (3 calls, zero read_file), so the spine-path marker was inert
    // and a 30-page review ran unfenced against a 20-page threshold; on-seat
    // selftest proved the rest of the deployed gate correct. read_volume.py +
    // tests only. Vocabulary identical; no tracked pair moves.
    // 671507b7 -> c2668ce2 (2026-08-28, overlay#327, agreement §2.8 routine 5):
    // the read-volume gate. A served opposing-response review is metered in
    // pages at the read seam (shared/read_volume.py; fence beside the
    // matter-mixing fence in trust enforce); past the client-authored
    // settings.review_threshold_pages the crossing read is refused with a
    // surface-only-note directive. Review sessions are marked by webhook route
    // OR by the session reading the gated skill's SKILL.md (the spine path).
    // Lever SMD_READ_VOLUME_GATE_MODE off|report|block (unset=block).
    // Vocabulary identical; no tracked pair moves.
    // 99c62699 -> 671507b7 (2026-08-25, overlay#320-#325): the message-structure floor
    // (#323, ss#2090) plus the pilot-law retirement (#324). The abandoned loop-arm boot
    // self-check is REMOVED again (#325) rather than carried forward. The runaway-loop
    // BRAKE (_meter_loop_arms) was already in the old pin and is unchanged.
    // f16d4920 -> 99c62699 (2026-08-25, overlay#319): the sticky-stop ladder's
    // runaway-loop arms get fed. record_tool_failure and record_refusal were
    // implemented, thresholded and audited but had NO caller in either repo, so a
    // seat looping on a failing tool or refusing every call stopped only on cost.
    // post_tool_call in hermes-smd-audit now feeds both, plus record_tool_success
    // (the ladder counts CONSECUTIVE failures, so feeding failures alone would
    // march every long-lived seat to HARD_STOP). Detection is positive-only: an
    // unrecognised `status` records nothing, so an envelope rename degrades to the
    // old unbraked behaviour rather than stopping a live seat. The controls stay
    // status: inert in runtime-controls.yaml until a boot probe proves the arm
    // fires on a provisioned seat. Vocabulary identical; no tracked pair moves.
    // 29eac2c7 -> 3c1b8e98 (2026-08-29f, overlay#333, ss#2614): the chronology-package
    // seam's agent side: hermes-smd-medchron (submit / status / allowance over the
    // broker), the medchron_jobs runtime-read kind, three action-class rows. The
    // runner, the verbs and the daemon land here in the same change. Vocabulary
    // identical (71); no tracked pair moves.
    // 3c1b8e98 -> c5846c68 (2026-08-31, overlay#334 + overlay#335, ss#2652/ss#2654):
    // the ledger-integrity pair (symmetric raise reset, release validation, hold
    // determinations via the escalation plugin) and the identifier pair
    // (register-anchored bare-digit matter numbers: pre_run_handoff seeding,
    // identifier_filter known-number scan, matter_gate chunked membership pass).
    // TWO tracked pairs move: escalation_ledger (byte-identical again at
    // 912bac29...) and identifier_filter (overlay side re-recorded at 71e598a5...).
    // c5846c68 -> 45cc19e7 (2026-08-31c, overlay#336, ss#2616): the handoff route
    // is real (translate materializes it on the MCP bearer; the gate retries any
    // non-2xx forward) and medchron submit gains the append file-id selection.
    // Vocabulary identical (71); no tracked pair moves.
    // 45cc19e7 -> 727f3f5b (2026-08-31d, overlay#337, ss#2664 WS-RENDER pair):
    // pre-rendered out-of-turn dispatch (consume-once envelope, full ->
    // skeleton -> failure-note ladder, post-dispatch ledger appends under the
    // witness session), the in-turn rendered-body slot check, CONFIRM-row
    // body-conformance stamps (rendered_body_sha256 / body_variant /
    // routing_leg, pre-mutation canonical hash), the text/plain down-render,
    // and the outbound scans wired on the out-of-turn path. Vocabulary
    // identical (71); no tracked pair moves.
    // 727f3f5b -> 799647d1 (2026-09-01, TWO merges: overlay#338 then overlay#339;
    // console halves ss#2674 and ss#2677, both already on main).
    // #338: the CONFIRM row gains a SECOND canonical hash, plain_body_sha256, over
    // the exact text/plain handed to the channel, because _attach_html_body
    // down-renders `text` before dispatch and the channel therefore never stores
    // the bytes rendered_body_sha256 names -- which graded every conformant
    // templated send channel_mismatch_hold. Omitted, never duplicated, when no
    // down-render happened.
    // #339: SUPERVISOR_STATES widens 5 -> 7 (`starting`, `never-healthy`). The set
    // is a closed vocabulary that drops unrecognised words to NULL, and the console
    // holds on NULL rather than paging -- so until this pin, both words entrypoint.sh
    // already writes were discarded in transit as silence.
    // Vocabulary identical (71, AST at both refs); no tracked pair moves;
    // shared/heartbeat.py byte-identical (a field VALUE vocabulary changed, not the
    // field set).
    // 799647d1 -> b16e32f7 (2026-09-01b, overlay#341; console half is migration
    // 0112 + the ingest + the alert label, same ss PR as this bump).
    // The sticky-stop ladder has FOUR meters (consecutive tool failures, refusal
    // cascade, runtime budget, cost threshold) and the beat carried only the
    // LEVEL, so every page read "Cost breaker HARD_STOP" whatever tripped it --
    // on 2026-09-01 ashton-price stopped on a bad credential and the SEV1 named
    // the wrong meter. The cause was never missing, only dropped:
    // sticky_stop_state has recorded `reason` and `condition` on the transition
    // all along and read_level did `SELECT level`. read_stop_state now returns
    // all three from the SAME row (worst level, latest stamp) and build_payload
    // carries them. Also fixes three cause-pairing defects found in review:
    // pin_hard_stops left a stale condition beside an operator-pause reason; an
    // OK row could hand back a cause; rows with no recognised level returned a
    // fabricated OK instead of unknown.
    // Vocabulary identical; no tracked pair moves (cost_breaker.py and
    // heartbeat.py have no ss-console twin). shared/heartbeat.py DID change --
    // the field SET grew by two, so heartbeat-fields.json is re-stamped in the
    // same change and its parity gate covers both new columns.
    // b16e32f7 -> c557f8c0 (2026-09-02b, overlay#343; Captain decision).
    // The sticky-stop ladder collapses to TWO states, OK and HARD_STOP. WARN
    // and SOFT_STOP are removed because they did nothing: SOFT_STOP was
    // specified to pin every skill's trust_ceiling to draft_for_review and no
    // caller ever did it, no reader compared against anything but HARD_STOP,
    // and the alerter never paged on it -- while the CLIENT portal told the
    // customer "the safety substrate has pinned the agent". pilot-smokeball
    // sat latched at SOFT_STOP for five days restricting nothing.
    // THE HARD_STOP THRESHOLDS DID NOT MOVE (8 tool failures / 600s, 20
    // refusals / 1800s, 200% of the cost cap), pinned by a test in both repos:
    // deleting two dead states must not be confusable with changing when a
    // client's Operator halts. The one meter with no hard threshold
    // (record_runtime_seconds) now stops nothing and records an observation
    // row -- exactly its prior effect, since SOFT_STOP restricted nothing.
    // Legacy WARN/SOFT_STOP rows read as OK, which also releases a seat
    // latched at SOFT_STOP with no Captain clear.
    // Vocabulary identical; the canonical twin
    // operator/safety-substrate/sticky_stop.py moves in the SAME change (this
    // repo is where it lands first per the vendoring header).
    // c557f8c0 -> 9951fbf8 (2026-09-02c, overlay#344; canonical half ss#2690).
    // Every fabrication marker now carries a `remedy` appended to the refusal
    // text the model reads. Measured cause: scoped to client-write tools, 132
    // of 150 refused (session, tool) pairs RECOVER in the same session -- the
    // gate refuses, the model corrects, the memo lands. The ones that STRAND
    // cluster on markers whose refusal names a rule instead of an action; the
    // em-dash marker lost a daily-needs-you-digest memo on 2026-08-19 and again
    // on 2026-09-02, each on ONE attempt with no retry. Remedies are narrow and
    // never restate the rule (the 2026-08-24 refusal that named its rule taught
    // the model to strip 38 matter numbers), enforced by a test in the overlay.
    // Vocabulary identical; fabrication_markers.json moves in BOTH repos with a
    // re-pinned sha256 on the overlay side.
    // 9951fbf8 -> e0288582 (2026-09-04, overlay#345 + #346; console half
    // ss#2697, B3 of claims-2026-09-04). Every out-of-turn prerendered send now
    // stamps `skill_name` (cron-resolved routine, not the envelope) into
    // audit_extra; the broker (#2697) writes it to the COLUMN, and the console's
    // verifier joins dispatch to wake by that column, hash second, never by
    // window proximity. Before this every templated send on a live seat held
    // at channel_mismatch_hold because the column was NULL. #345 only drops the
    // retired smd seat from the overlay's contract snapshot. No paired twin
    // moves (neither commit touches a tracked pair); vocabulary and heartbeat
    // fields re-read as identical (empty range diff on schemas.py/heartbeat.py).
    // e0288582 -> 5770b79f (2026-09-08, overlay#348; ss voice-establishment). An
    // Operator admin's email voice-establishment survey may now read the firm's
    // own letters ACROSS matters — the read-time matter-mixing fence
    // (shared/matter_gate.py content_read_refusal) is exempted for a
    // seat-classified admin establishment session; the send-time fence is
    // untouched and establishment never sends. The range e0288582..5770b79f is
    // overlay#347 (send-render.yaml contract mirror, ss#2700) + overlay#348
    // (matter_gate + hermes-smd-establishment), NEITHER a tracked .py twin, so
    // every overlaySha256 is unchanged (verify-overlay-pairs.py 9/9 PASS at the
    // new ref); only overlayRef moves.
    expect(DOCKERFILE).toContain('ARG OVERLAY_REF="5770b79fea3206f01df343489d232daf2fdc4a2b"')
  })

  it('does NOT swallow a failed plugin install (no fail-open `|| echo ... continuing`)', () => {
    // The specific regression: a `|| echo "WARN ...; continuing"` on the plugin
    // install line that let a harness-less image build green.
    expect(
      /plugins install[\s\S]{0,160}?\|\|\s*echo[^\n]*continuing/i.test(DOCKERFILE),
      'hermes plugins install must not fall through to a warning-and-continue'
    ).toBe(false)
  })

  it('hard-asserts the overlay policy core is importable at build time', () => {
    // The deterministic build gate that catches a stale/mis-pinned `shared`
    // package (the v0.1.1 ModuleNotFoundError) before the Machine ever boots.
    expect(
      DOCKERFILE.includes('import shared.outbound_gate'),
      'Dockerfile must assert `import shared.outbound_gate` succeeds in the venv'
    ).toBe(true)
    expect(DOCKERFILE).toMatch(/from shared\.outbound_gate import evaluate/)
  })
})

/**
 * Regression guard: the first-boot build/runtime fixes (first real boot,
 * 2026-05-30). Three stacked defects meant the Machine had never booted:
 *   1. `postgresql-16` is not in Debian 13 (trixie ships PG 17) — build exit 100.
 *   2. The uv-created venv has no `pip` binary; `.venv/bin/pip install` exits 127.
 *   3. The venv was not on PATH, so bare `python3 -m honcho.*` / `hermes-smd`
 *      resolved to /usr/bin and crashlooped at boot.
 */
describe('Operator Machine first-boot build/runtime fixes', () => {
  it('installs postgresql-17 (Debian 13 default), never postgresql-16', () => {
    expect(
      /apt-get install[\s\S]*?postgresql-17 postgresql-client-17/.test(DOCKERFILE),
      'apt must install postgresql-17 / postgresql-client-17'
    ).toBe(true)
    expect(
      /apt-get install[\s\S]*?postgresql-16 postgresql-client-16/.test(DOCKERFILE),
      'postgresql-16 is not in Debian 13 trixie repos — build fails with exit 100'
    ).toBe(false)
    expect(DOCKERFILE, 'Postgres bin PATH must target /17/, not /16/').not.toMatch(
      /usr\/lib\/postgresql\/16\/bin/
    )
  })

  it('installs into the venv via `uv pip`, never the absent `.venv/bin/pip`', () => {
    expect(
      /\.venv\/bin\/pip install/.test(DOCKERFILE),
      'uv venvs ship no pip binary; `.venv/bin/pip install` exits 127 — use `uv pip install`'
    ).toBe(false)
  })

  it('bootstrap.sh puts the Hermes venv on PATH', () => {
    expect(
      /export PATH="\/opt\/hermes\/\.venv\/bin:/.test(BOOTSTRAP),
      'bootstrap.sh must prepend the venv to PATH so bare hermes/hermes-smd resolve to it'
    ).toBe(true)
    // Postgres is no longer started in bootstrap (Honcho deferred), so the
    // PG_BIN/17 reference moved out of bootstrap.sh — see the Honcho-deferral
    // suite below. Postgres 17 stays INSTALLED in the Dockerfile (asserted above).
  })
})

/**
 * Regression guard: Honcho is deferred to Phase 2 (ADR 0016, revised
 * 2026-05-30). The never-booted Machine died at the fictional Honcho steps —
 * `python -m honcho.{migrations,server}` against `honcho-ai`, which is the
 * client SDK, not the server. These lock the deletion + the correct launch
 * verb so the boot path can't regress back into the fiction.
 *
 * Assertions run against comment-stripped content: the deferral comments
 * intentionally name the removed commands.
 */
describe('Operator Machine: Honcho deferred, flat-file core (ADR 0016 revised)', () => {
  it('Dockerfile does not install the fictional honcho-ai client SDK', () => {
    expect(
      /honcho-ai/.test(DOCKERFILE_CODE),
      'honcho-ai is the Honcho CLIENT SDK, not the server — it must not be installed'
    ).toBe(false)
    expect(/HONCHO_PIP_SPEC/.test(DOCKERFILE_CODE)).toBe(false)
  })

  it('bootstrap.sh does not run the fictional honcho migrations/server', () => {
    expect(
      /python3?\s+-m\s+honcho\.(migrations|server)/.test(BOOTSTRAP_CODE),
      '`python -m honcho.{migrations,server}` never existed — the boot died here'
    ).toBe(false)
  })

  it('bootstrap.sh launches the active-persona gateway daemon, not the default profile or the chat REPL', () => {
    // Must run `hermes gateway run` (daemon) targeting a persona profile via
    // `-p <slug>`. A bare `hermes gateway run` runs Hermes' built-in `default`
    // profile — no model, SOUL.md, skills, or connector wiring — i.e. not the
    // customer's agent. The `-p` flag is the regression guard for that bug.
    expect(
      /exec\s+\S*hermes\s+-p\s+\S+\s+gateway\s+run/.test(BOOTSTRAP_CODE),
      'an unattended Machine must `exec hermes -p <profile> gateway run`'
    ).toBe(true)
    expect(
      /exec\s+\S*hermes\s+chat\b/.test(BOOTSTRAP_CODE),
      '`hermes chat` is an interactive REPL — wrong for PID-1 with no TTY'
    ).toBe(false)
  })

  it('bootstrap.sh does not require the Honcho secrets at boot', () => {
    // HONCHO_API_KEY / SMD_D1_OBSERVATIONS_BINDING moved to OPTIONAL_ENV; a
    // Phase-1 boot must not fail-closed on their absence.
    const required = stripHashComments(BOOTSTRAP.match(/REQUIRED_ENV=\(([\s\S]*?)\n\)/)?.[1] ?? '')
    expect(/HONCHO_API_KEY/.test(required), 'HONCHO_API_KEY must not be required in Phase 1').toBe(
      false
    )
    expect(
      /SMD_D1_OBSERVATIONS_BINDING/.test(required),
      'SMD_D1_OBSERVATIONS_BINDING must not be required in Phase 1'
    ).toBe(false)
  })
})

describe('Operator Machine profile guards', () => {
  it('installs and enables the overlay inside the active profile Hermes home', () => {
    expect(
      BOOTSTRAP.includes('PROFILE_HERMES_HOME="${HERMES_HOME}/profiles/${ACTIVE_PROFILE}"'),
      'bootstrap.sh must name the Hermes home selected by `hermes -p <profile>`'
    ).toBe(true)
    expect(
      BOOTSTRAP.includes(
        'PROFILE_OVERLAY_PLUGIN_DIR="${PROFILE_HERMES_HOME}/plugins/hermes-smd-overlay"'
      ),
      'profiled plugin discovery must receive the pinned overlay pack'
    ).toBe(true)
    expect(
      BOOTSTRAP_CODE.includes('hermes -p "${ACTIVE_PROFILE}" plugins enable hermes-smd-overlay'),
      'the overlay must be enabled in the active profile config'
    ).toBe(true)
  })

  it('seeds and hard-gates the activation hook inside the active profile', () => {
    expect(
      BOOTSTRAP.includes('PROFILE_HOOKS_DIR="${PROFILE_HERMES_HOME}/hooks"'),
      'HookRegistry resolves hooks from the profiled Hermes home'
    ).toBe(true)
    expect(
      BOOTSTRAP.includes('ACTIVATION_HOOK_DIR="${PROFILE_HOOKS_DIR}/smd-overlay-activation"'),
      'the boot gate must validate the hook path the profiled gateway scans'
    ).toBe(true)
    expect(
      BOOTSTRAP.indexOf('ACTIVATION_HOOK_DIR="${PROFILE_HOOKS_DIR}') <
        BOOTSTRAP.indexOf('exec /opt/hermes/.venv/bin/hermes -p "${ACTIVE_PROFILE}" gateway run'),
      'the live activation hook must be installed before gateway startup'
    ).toBe(true)
  })

  it('packages and runs the disabled-skills guard before the gateway starts', () => {
    expect(
      DOCKERFILE.includes(
        'COPY operator/templates/ensure-disabled-skills.py /app/ensure-disabled-skills.py'
      ),
      'Dockerfile must package the disabled-skills guard'
    ).toBe(true)
    expect(
      BOOTSTRAP.includes('/app/ensure-disabled-skills.py "${CUSTOMER_YAML}" "${HERMES_HOME}"'),
      'bootstrap.sh must enforce persona skills_disabled after profile materialization'
    ).toBe(true)
    expect(
      BOOTSTRAP.indexOf('/app/ensure-disabled-skills.py') <
        BOOTSTRAP.indexOf('exec /opt/hermes/.venv/bin/hermes -p "${ACTIVE_PROFILE}" gateway run'),
      'disabled-skills guard must run before the Hermes gateway exec'
    ).toBe(true)
  })

  it('runs a convergent (not fixed-window) disabled-skills reconciler (ss#2230)', () => {
    expect(
      BOOTSTRAP.includes('ensure-disabled-skills.py --check'),
      'the reconciler must probe with --check until the prune has converged'
    ).toBe(true)
    expect(
      BOOTSTRAP.includes('[ "${_ticks}" -ge 24 ]'),
      'the reconciler must not exit on an early clean streak — a clean check before ' +
        'the gateway sync starts proves nothing (the 2026-08-10 race)'
    ).toBe(true)
    expect(
      BOOTSTRAP.includes('for _ in 1 2 3 4 5 6'),
      'the fixed 30s reconcile window is the ss#2230 defect; it must not return'
    ).toBe(false)
  })

  it('packages and runs the operator identity guard before the gateway starts', () => {
    expect(
      DOCKERFILE.includes(
        'COPY operator/templates/ensure-operator-identity.py /app/ensure-operator-identity.py'
      ),
      'Dockerfile must package the operator identity guard'
    ).toBe(true)
    expect(
      BOOTSTRAP.includes('/app/ensure-operator-identity.py "${CUSTOMER_YAML}" "${HERMES_HOME}"'),
      'bootstrap.sh must write customer-owned identity facts into SOUL.md'
    ).toBe(true)
    expect(
      BOOTSTRAP.indexOf('/app/ensure-operator-identity.py') <
        BOOTSTRAP.indexOf('exec /opt/hermes/.venv/bin/hermes -p "${ACTIVE_PROFILE}" gateway run'),
      'operator identity guard must run before the Hermes gateway exec'
    ).toBe(true)
  })
})
