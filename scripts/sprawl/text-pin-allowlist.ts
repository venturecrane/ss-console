/**
 * The test files that read repo source as text ON PURPOSE, left out of the
 * source-text pin census (scripts/sprawl/analyze.ts textPinCensus, gated by
 * tests/text-pin-ratchet.test.ts). Four kinds, and only these:
 *
 * - POLICY SCAN: a rule over a tree or a class of files (no em dashes, no
 *   fabricated client content, no dollar amounts, no secret in source, every
 *   route binds the tenant). It can only do that by reading the text.
 * - CONTRACT GUARD: two artifacts must agree (a generated file and its
 *   generator, an enum in Python and its copy in TypeScript, a code constant and
 *   the skill that must restate it).
 * - SECURITY OR ORDER GUARD: a boot, provision or CI script whose order or
 *   privilege handling is the safety property (secrets unset before the drop,
 *   ownership before the swap, auth middleware before the routes). Running a
 *   seat boot in a test is not cheap; the text is the cheapest faithful probe,
 *   and removing an access control is the Captain's call.
 * - MODEL-READ RULE: a SKILL.md, router, reply-shape or prompt the Operator's
 *   model reads IS the program. A test that the text states a hold, route,
 *   send, fence, money or reply-content rule is a behaviour guard.
 *
 * A test that pins incidental wording of one file (a heading, an example, an
 * export name, a comment) is none of these and does not belong here, however
 * important the sentence. Each entry says what it enforces; adding one is a
 * reviewed decision, and the /code-review sprawl pass treats a new entry like a
 * new pin.
 */
export const TEXT_PIN_POLICY_SCANS: Readonly<Record<string, string>> = {
  // ---- Policy scans named in CLAUDE.md, the doctrine or the handbook
  'tests/forbidden-strings.test.ts':
    'Policy: Pattern A/B phrases and style markers stay out of shipped client copy (CLAUDE.md).',
  'tests/doctrine-integrity.test.ts':
    'Contract: doctrine and primer agree; no correspondence archive in ss-console (Law 2).',
  'tests/handbook-integrity.test.ts':
    'Policy: handbook frontmatter, links, cited files and generated lists agree with the tree.',
  'tests/adr-integrity.test.ts': 'Contract: the ADR index is complete against docs/adr/.',
  'tests/docs-facts.test.ts':
    'Contract: counts stated in CLAUDE.md agree with the files they count.',
  'tests/landing-page.test.ts':
    'Policy: marketing pages carry no dollar amounts, keep we-voice, no mailto, no overclaim (CLAUDE.md tone standard).',
  'tests/intake-questionnaire.test.ts':
    'Policy: /get-started and /book share one intake questionnaire (CLAUDE.md shared-flow rule).',
  'tests/extraction-prompt.test.ts':
    'Model-read rule: the extraction prompt stays extractive and evidence-bound, no dollar amounts (CLAUDE.md).',
  'tests/staleness-detection.test.ts':
    'Contract: every doctrine primer line survives every staleness state (CLAUDE.md: the bash-hook coverage).',
  'tests/typography-tokens.test.ts':
    'Policy: font tokens only across src/, the retired face stays gone, no hardcoded font URLs.',
  'tests/operator-section-badges.test.ts':
    'Policy: across marketing pages the retired section badge stays gone and each page keeps an eyebrow.',
  'tests/operator-pack-kit-grammar.test.ts':
    'Policy: every pack page frames the walk as illustrative and binds no finished-capability verb.',
  'tests/pricing-figures.test.ts':
    'Policy: locked internal price figures never appear in the public repo (ADR 0063).',
  'tests/api-inventory.test.ts':
    'Contract: the generated API page matches src/pages/api, with no em dash.',
  'tests/incidents-register.test.ts':
    'Contract: the incident index matches disk; class vocabulary defined; no em dash.',
  'tests/coverage-config-honesty.test.ts':
    'Policy: no coverage threshold without a workflow that runs coverage.',
  'tests/hooks-registered.test.ts':
    'Contract: every hook is registered or exempt with a verified reason.',
  'tests/workflow-permissions.test.ts':
    'Security: workflows that call a reusable workflow declare their permissions.',
  'tests/public-waivers.test.ts': 'Contract: every public-export waiver names a real consumer.',
  'tests/single-owner-registry.test.ts':
    'Contract: single-owner ADR signatures stay inside their allowed paths.',
  'tests/admin-astro-boundaries.test.ts':
    'Policy: the heavy admin pages do no direct DOM wiring and stay under their line caps.',
  'tests/smd-session-skills.test.ts':
    'Contract: the forked session skills live where crane cannot overwrite them; /wired READY matches docs/doctrine/wired-contract.md.',

  // ---- Security, auth and access control (removing one is the Captain's call)
  'tests/middleware.test.ts':
    'Security order: Clerk runs before the SS middleware; 404 is server-rendered so no path skips it.',
  'tests/security-headers.test.ts':
    'Security: headers are applied around the whole request, redirects and denials included.',
  'tests/oauth-callback.test.ts':
    'Security: every OAuth callback audit emit passes the D1 binding.',
  'tests/auth.test.ts':
    'Security: session cookie flags, admin role gate, middleware protection and redirects, admin/portal decoupling.',
  'tests/canonical-app-url.test.ts':
    'Security: client links are built from configured base URLs, never the request host.',
  'tests/sentry-gating.test.ts': 'Secret: the Sentry DSN is read from env, never hardcoded.',
  'tests/claude-extract.test.ts':
    'Secret: the extract route reads its API key from env and refuses without it.',
  'tests/scaffold.test.ts':
    'Security order: 404, /book and / stay server-rendered (a prerendered page bypasses the middleware).',
  'tests/follow-ups.test.ts': 'Privacy: the admin layout is noindex.',
  'tests/assessments.test.ts':
    'Security: assessment SQL is parameterized and org-scoped, R2 keys sanitized, every route requires an admin session.',
  'src/lib/db/engagement-contacts.test.ts':
    'Security: engagement-contact SQL is parameterized (no interpolation into prepare).',
  'tests/booking/holds.test.ts':
    'Security: booking-hold SQL is parameterized (no interpolation into prepare).',
  'tests/portal-routes-bind-tenant.test.ts':
    'Security: every portal API and page route binds the tenant.',
  'tests/portal-operator-nav.test.ts':
    'Access: the principal can reach the Activity audit record and Settings by link (#2176).',
  'tests/booking-manage-token-not-tracked.test.ts':
    'Privacy: the booking manage page opts out of analytics and both redactors strip opaque tokens.',
  'tests/operator-write-form-targets.test.ts':
    'Access control: authority and governance forms post to their /api handlers, not GET-only pages.',
  'tests/customer-commitments.test.ts':
    'Money: trust_ceiling never runs a commitment action without approval in the current turn.',
  'tests/trust-signing-keys.test.ts':
    'Security: published key fingerprints are recomputed from the keys; the trust page hardcodes none.',
  'tests/staging-isolation.test.ts': 'Security: staging never shares a production resource.',
  'tests/deploy-seat-spec-gate.test.ts':
    'Secret: the seat-spec signing key is referenced only under its enable gate.',
  'tests/agentmail-webhook-secret-fence.test.ts':
    'Secret: per-seat webhook secret fence, no global fallback, stale AgentMail secrets unset.',
  'tests/agentmail-webhook-read-fence.test.ts':
    'Secret: the org-scoped webhook-read key never falls back to the inbox key.',
  'tests/msgraph-two-app-fence.test.ts': 'Secret: the read and send Graph apps stay fenced apart.',
  'tests/runtime-read-transport.test.ts':
    'Secret: runtime-read and MCP handoff webhook secrets are derived per slug and staged by name.',
  'tests/provisioner-gates-parse-not-grep.test.ts':
    'Secret: credential-staging channel gates read the parsed config; facts defined before first use.',
  'operator/bin/tests/test_secret_custody.py': 'Secret: every staged secret is classified.',
  'operator/bin/tests/test_provision_smokeball_region.py':
    'Secret: the Smokeball region is guarded and staged with its environment.',
  'operator/bin/tests/test_decommission_backends.py':
    'Security: the scripts that sign evidence run with cryptography.',
  'operator/bin/tests/test_pip_audit_gate.py': 'Security: the pip-audit gate retries its fetches.',
  'operator/bin/tests/test_chain_rehearsal.py':
    'Security: the drill never writes the object-locked audit archive.',
  'operator/bin/tests/test_clio_mcp_version_gate.py':
    'Privacy: no seat enables a clio-mcp version that logs client data.',
  'operator/bin/tests/test_fleet_tick.py':
    'Security: fleet tools make no ssh or org-wide fly calls.',
  'operator/runners/jobs/tests/test_note_by_smd.py':
    'Secret: the command never names the MCP webhook secret.',
  'operator/runners/jobs/tests/test_casefile_no_firm_tokens.py':
    'Privacy: no firm names in public code.',
  'operator/hermes-smd-overlay/tests/test_no_real_addresses_in_source.py':
    'Privacy: no real addresses in overlay source.',
  'operator/hermes-smd-overlay/tests/test_lanes_plugin.py':
    'Secret: the lanes plugin never reads os.environ.',
  'operator/hermes-smd-overlay/tests/test_seat_log_level_not_debug.py':
    'Privacy: translate never authors a debug logging level.',
  'operator/hermes-smd-overlay/tests/test_calendar_tools.py':
    'Security: the calendar plugin has no transmit path.',
  'operator/hermes-smd-overlay/tests/test_brief_taint_exemption.py':
    'Security: only casework mints CodeFixedRecipients.',
  'operator/hermes-smd-overlay/tests/test_advertising_gate_outbound.py':
    'Security: the image ships the advertising checker where the outbound gate loads it.',
  'operator/hermes-smd-overlay/tests/test_couples_surface.py':
    'Security: the router wires the couples toolset and the activation gate runs its check.',
  'operator/templates/drafting/tests/test_corpus_measure.py':
    'Money: corpus measurement never references the billable vision path.',

  // ---- Boot, provision and release order (the order is the safety property)
  'tests/operator-dockerfile.test.ts':
    'Order/security: hash-locked installs, users and privilege drops, build-check order, no client content in the image (ADR 0095).',
  'tests/operator-entrypoint.test.ts':
    'Order/security: broker forked before the gateway drop; spool, queue and run dir owners and modes; inputs adopted before the swap.',
  'tests/operator-entrypoint-supervisor.test.ts':
    'Order: the root audit-loss dir is made first and entrypoint.sh wires the supervisor.',
  'tests/operator-workspace-broker.test.ts':
    'Secret custody (ADR 0045/0044): distinct non-root users, send secrets unset before the drop, passed by path at 0600.',
  'tests/operator-lane-inputs-fetch.test.ts':
    'Order: the retrying fetch helper is sourced before lane files; inputs land at the right owner and mode.',
  'tests/operator-litigation-textcache.test.ts':
    'Privacy: the litigation text cache is child-only 0700 and its env name agrees across boot, lane and cache.',
  'tests/operator-skill-deploy.test.ts':
    'Order: the boot skill overlay is additive and never rm -rf the agent-authored skills (ADR 0017).',
  'tests/operator-spec-loader.test.ts':
    'Security order: the spec dir is root-owned, fetched before the privilege drop, ownership-checked.',
  'tests/operator-self-test-step4.test.ts':
    'Security: the refusal self-test names a mail draft tool and no practice-management write tool (ss#2511).',
  'tests/shipped-runtime-paths.test.ts':
    'Contract: every path a shipped skill cites ships in the image.',
  'tests/compile-seat-spec.test.ts':
    'Contract: spec tables match the code they mirror (fleet build, Dockerfile).',
  'tests/config-publish-guards.test.ts':
    'Order: customer config is never created, keyed by a bounded slug, validated by the entrypoint before adoption.',
  'tests/pending-config.test.ts':
    'Order: PENDING markers refuse provisioning before the compiler and the R2 upload.',
  'tests/provisioner-medchron-controls.test.ts':
    'Order: controls staged after the firm config and before the fly.toml render; missing controls is fatal.',
  'tests/provisioner-firm-config-currency.test.ts':
    'Order: the firm config must match engagements main, checked before validate and upload.',
  'tests/provisioner-fleet-status-seed.test.ts':
    'Contract: the seed SQL runs against the real migration; wrangler stderr is kept.',
  'tests/publish-firm-inputs.test.ts':
    'Order: one publisher of firm inputs, after the engagements-main gate.',
  'tests/r2-key-collision.test.ts':
    'Security: document keys are collision-safe and routes use the shared key helper.',
  'tests/config-reconcile.test.ts':
    'Invariant: the config reconciler closes the push-range sync gap and reports drift.',
  'tests/r2-config-reconcile.test.ts': 'Invariant: the R2 reconciler writes nothing.',
  'tests/merge-queue-triggers.test.ts':
    'Invariant: every required check reports on merge_group, against the queue base.',
  'tests/fleet-alerts-staging-seats.test.ts':
    'Contract: the alert worker staging seat list equals the customer.yaml staging set.',
  'tests/connector-observability.test.ts':
    'Contract: token-expiry alerting uses the deployed token lifetime.',
  'tests/demo-seat.test.ts':
    'Policy: the demo runbook names every act skill; no placeholder or client names in the demo seat.',
  'workers/fleet-alerts/src/shortfalls.test.ts':
    'Contract: the digest cron in code matches the wrangler.toml schedule.',
  'operator/bin/tests/test_deploy_ordering.py':
    'Order: no fly secrets deploy before the image roll; secret strips before the gateway starts.',
  'operator/bin/tests/test_active_persona_env_export.py':
    'Order: the active persona profile is exported before the gateway starts (ADR 0056 trust gate).',
  'operator/bin/tests/test_image_own_sweep.py':
    'Order/security: the ownership sweep chowns the trees with -h, early.',
  'operator/bin/tests/test_seat_timezone.py':
    'Boot: the timezone export is guarded on a non-empty value.',
  'operator/bin/tests/test_provision_image_selection.py':
    'Order: provision deploys the chosen release image.',
  'operator/bin/tests/test_jobs_runtime.py':
    'Order: the image build installs requirements before the runner.',
  'operator/bin/tests/test_reconcile_sends.py':
    'Invariant: the scheduled unaudited-send reconcile never narrows to one channel.',
  'operator/bin/tests/test_reconcile_outcomes.py':
    'Contract: the workflow marker sed matches the renderer marker.',
  'operator/bin/tests/test_ci_coverage_conformance.py':
    'Contract: CI runs every operator test; release workflows carry no forbidden flags or secrets.',
  'operator/bin/tests/test_ci_publish_bundle.py':
    'Contract: the bundle layout and paths match the seat installer.',
  'operator/bin/tests/test_fleet_parts.py':
    'Contract: the generated bootstrap env arrays declare the required keys the fleet reads.',
  'operator/templates/tests/test_gateway_liveness_supervisor.py':
    'Order: the boot liveness supervisor.',
  'operator/templates/tests/test_broker_socket_wait.py':
    'Order: the broker socket wait fails closed (fatal, kills the supervisor).',
  'operator/templates/tests/test_seat_script_find_counts.py':
    'Policy: no find|wc -l in a seat script can kill the boot.',
  'operator/templates/tests/test_seat_layout_shell_names.py':
    'Contract: seat-layout names are assigned and sourced before use.',
  'operator/templates/tests/test_selftest.py': 'Contract: the reserved selftest- prefix.',
  'operator/hermes-smd-overlay/tests/test_root_pollers.py':
    'Order: one root poller; synchronous boot fetches.',
  'operator/hermes-smd-overlay/tests/test_activation_completion_log.py':
    'Boot: the completion line names every fail-closed gate.',
  'operator/hermes-smd-overlay/tests/test_voice_mechanism_b_gone.py':
    'Policy: the retired voice mechanism stays gone from boot.',

  // ---- Contract guards (two artifacts must agree)
  'tests/operator-action-class-parity.test.ts':
    'Contract: TS action classes agree with the Python ActionClass enum (ADR 0075).',
  'tests/operator-audit-producers.test.ts':
    'Contract: every accepted audit action has a producer; aliveness union matches the substrate enum.',
  'tests/operator-connector-materializer.test.ts':
    'Contract: every connector backend prefix is classified in the materializer contract.',
  'tests/operator-fixture-contract.test.ts':
    'Contract: the matter-memo source-to-phrase mapping agrees with the fixtures (ss#2360).',
  'tests/audit-vocabulary-parity.test.ts':
    'Contract: overlay ACCEPTED_ACTION_TYPES matches the contract snapshot.',
  'tests/portal-operator-audit.test.ts':
    'Contract: TS AUDIT_ACTION_TYPES equals Python ACCEPTED_ACTION_TYPES.',
  'tests/register-cli.test.ts':
    'Contract: the register CLI and the reconciler write obligations only through upsertObligation.',
  'tests/broker-verb-registry.test.ts':
    'Contract: broker verb registry and its untested-verb ratchet.',
  'tests/document-kinds-registry.test.ts':
    'Contract: every copy of the document-kind list agrees with operator/contracts/document-kinds.yaml.',
  'tests/library-setup-activity.test.ts':
    'Contract: portal activity lines match ACTIVITY in library_setup_wording.py.',
  'tests/reply-log.test.ts': "Contract: the dialog's options match the endpoint's vocabulary.",
  'tests/format-assertions.test.ts':
    'Contract: the client-authored rule vocabulary, its refusal banner, and controls withheld where the seat would not check.',
  'tests/customer-yaml-people.test.ts':
    'Contract: every placeholder in the couples template is filled.',
  'operator/bin/tests/test_decommission_inventory.py':
    'Contract: seat tables classified; bucket names match provisioning.',
  'operator/bin/tests/test_env_contract_conformance.py':
    'Contract: the env contract agrees with the bootstrap arrays and strips.',
  'operator/bin/tests/test_seat_layout_generation.py':
    'Contract: generated seat-layout modules agree with operator/contracts/seat-layout.yaml.',
  'operator/bin/tests/test_runtime_control_conformance.py':
    'Contract: every runtime control is bound to a seam or a stage.',
  'operator/bin/tests/test_skills_manifest.py':
    'Contract: the runtime-read set equals the TypeScript policy.',
  'operator/runners/jobs/tests/test_lane_registries.py':
    'Contract: lane registries agree with holds.classify.',
  'operator/runners/jobs/tests/test_uncovered_why.py':
    'Contract: one uncovered-why token vocabulary.',
  'operator/runners/jobs/tests/test_drafting_firm_rules.py':
    'Contract: firm-rule refusal codes agree.',
  'operator/runners/jobs/tests/test_drafting_run.py':
    'Contract: every reason literal leads with a known code; no direct writes in the runner.',
  'operator/runners/jobs/tests/test_update_decide.py': 'Contract: update hold prefixes agree.',
  'operator/runners/jobs/tests/test_update_illegible.py':
    'Model-read rule: the house note reaches the prompt through its WORDING token.',
  'operator/runners/jobs/tests/test_update_already_current.py':
    'Contract: every update outcome code is restated in the skill.',
  'operator/runners/jobs/tests/test_skill_load.py':
    'Policy: every wake builder writes the skill load line.',
  'operator/workspace_broker/tests/test_delivery_proof.py':
    'Contract: one delivery-proof code vocabulary.',
  'operator/workspace_broker/tests/test_medchron_verbs.py':
    'Contract: hold prefixes match the runner.',
  'operator/workspace_broker/tests/test_held_wording_parity.py':
    'Model-read rule: DELIVER held wording agrees between the broker and every skill (ADR 0104).',
  'operator/safety_substrate/tests/test_guard_hook_parity.py':
    'Contract: the hook-surface contract.',
  'operator/tests/test_skill_helpers_sync.py':
    'Contract: vendored helper copies are byte-identical.',
  'operator/tests/test_marketing_skill_examples.py':
    'Policy: worked examples pass the advertising gate and style rules.',
  'operator/hermes-smd-overlay/tests/test_rep_desk.py':
    "Model-read rule: the guide's not-covered sentence is the gap report's own constant.",
  'operator/hermes-smd-overlay/tests/test_training_report.py':
    'Privacy: the training report reads records only through the progress store; the purge clears the field it names.',
  'operator/hermes-smd-overlay/tests_e2e/test_guide_desk_rehearsal.py':
    'Contract: every tool the desk procedure names is offered on the email surface.',
  'tests/ops-requests.test.ts':
    'Contract: every job table is covered by the request index queries.',
  'operator/hermes-smd-overlay/tests/test_heartbeat.py':
    'Contract: the sender-enrollment action constant agrees.',
  'operator/hermes-smd-overlay/tests/test_dispatch_body_stamp.py':
    "Contract: the send-render mirror is the send verifier's stamp contract.",
  'operator/hermes-smd-overlay/tests/test_rule_reader_registry.py':
    'Model-read rule: a client is told a rule is in effect only where a lane applies it.',
  'operator/connectors/smokeball/tests/test_render_docx_template.py':
    'Policy: shipped document skeletons pass the content gate.',
  'operator/skills/post-intake-daily-list/tests/test_post_intake_daily_list.py':
    'Contract: the journal dir constant agrees with the connector.',
  'operator/skills/statute-watch/tests/test_statute_watch.py':
    'Contract: render phrases are restated in output-format.md.',
  'operator/skills/deadline-miss-escalator/test_render_routing.py':
    'Model-read rule: FAILURE_NOTE is restated in the SKILL.md.',
  'operator/skills/client-verification-tracker/test_render_envelope.py':
    'Model-read rule: FAILURE_NOTE is restated in the SKILL.md.',
  'operator/skills/deadline-miss-escalator/test_escalator_pre_run.py':
    'Contract: the envelope supplies the keys the dispatcher requires.',
  'operator/skills/client-verification-tracker/test_verification_pre_run.py':
    'Contract: the envelope supplies the keys the dispatcher requires.',

  // ---- Model-read rules: the skill or prompt text is the program
  'tests/router-class-parity.test.ts':
    'Model-read rule: routing, admin-reserved skills, roster fence and rubric parity in the router.',
  'tests/operator-help.test.ts':
    'Model-read rule: help answers and the admin_only reservation agree with the router.',
  'tests/hold-class-wording.test.ts':
    'Model-read rule: every hold class is worded or silenced; no send-back, no machinery words.',
  'tests/skill-reply-rules.test.ts': 'Model-read rule: reply rules each skill must state.',
  'tests/output-class-specs.test.ts':
    'Model-read rule: one write key space, no write control in the portal view, no fabricated provenance.',
  'tests/matter-records-procedure.test.ts':
    'Model-read rule: records queued not done in the turn; files to staff only; matter number never a Smokeball id (ADR 0102).',
  'tests/medchron-skill-hold-routing.test.ts':
    'Model-read rule: DELIVER binds by job id; nothing sent on a refused bind or held job; no partial to the attorney.',
  'tests/medchron-skill-update-into-delivered.test.ts':
    'Model-read rule: an update goes into the delivered chronology; only the runner removes the old one.',
  'tests/medchron-skill-update-reply.test.ts':
    'Model-read rule: identity from the delivered record; never ask the firm to confirm a DOB; no-charge only with a credit.',
  'tests/medchron-skill-dob-from-documents.test.ts':
    'Model-read rule: DOB read from documents in a bounded search; never submitted before the requester confirms.',
  'tests/medchron-skill-allowance-unit.test.ts':
    'Model-read rule: allowance stated in pages, never dollars; a delivered reply states what remains.',
  'tests/litigation-people-skill.test.ts':
    'Model-read rule: "my cases" means the asker\'s own files, never firm-wide.',
  'tests/send-as-routed-skills.test.ts':
    'Model-read rule: every mail-routed skill names the send-as call and its approval reading (ADR 0089).',
  'tests/chase-voice-signature.test.ts':
    'Model-read rule: the signature comes from authored data, never a hardcoded firm name.',
  'tests/case-alert-routing.test.ts':
    'Model-read rule: case alerts route per matter, fail closed, never grow the roster.',
  'tests/skill-note-read-through.test.ts':
    'Model-read rule: client file notes carry no record ids, routine names or training text.',
  'tests/skill-file-note-header.test.ts':
    'Model-read rule: one file-note shape across every output-format and voice file.',
  'tests/couples-role.test.ts':
    'Model-read rule: persona safety rules (988, no monitoring, no relaying) (ADR 0097).',
  'tests/guide-seat.test.ts':
    'Model-read rule: guide seat answers listed senders only, one send class (ADR 0105).',
  'tests/product-guide-library.test.ts':
    'Model-read rule: the guide library carries no dollar amounts, names, em dashes or runtime vocabulary.',
  'tests/guide-code-capability-not-disclosed.test.ts':
    'Model-read rule: the guide never tells a non-administrator who issues invite codes (ADR 0106).',
  'tests/role-persona-delivery.test.ts':
    'Model-read rule: every role persona reaches the image with its safety section.',
  'tests/initiation-card-router-coverage.test.ts':
    'Model-read rule: each card phrase is in the router skill body read on an email turn.',
  'tests/scheduled-scan-memo-fence.test.ts':
    'Model-read rule: every scheduled skill that walks matters states the matter-content fence.',
  'tests/vendor-invoice-intake.test.ts':
    'Model-read rule: ledger re-read before reply, no dollar figure on flag lines, never finalizes.',
  'tests/open-house-visitor-capture.test.ts':
    'Model-read rule: follow-up timing never reads protected-trait notes; a visitor is never contacted.',
  'tests/digest-pointer-no-ledger-date.test.ts':
    'Model-read rule: the digest escalation line names no person, routine or ledger date.',
  'tests/voice-corrections.test.ts':
    'Model-read rule: no path from a captured correction to a spec file; promoted text is authored.',
  'operator/skills/date-prep-brief/tests/test_date_prep_pre_run.py':
    'Model-read rule: run exactly the approved job; finalized lists are a captioned .docx.',
  'operator/runners/jobs/tests/test_allowance_decide.py':
    'Model-read rule: never name one client of a joint delivery alone.',
  'operator/runners/jobs/tests/test_update_page_context.py':
    'Model-read rule: never cite a context block; never a home street address (privacy).',
  'operator/runners/jobs/tests/test_update_parity_f.py':
    "Model-read rule: a legend's meaning, never the bare code.",
  'operator/runners/jobs/tests/test_update_note_heading.py':
    'Model-read rule: never the date or physician of a page banner.',
  'operator/runners/jobs/tests/test_update_parity_p.py':
    'Model-read rule: as-of dates, never year-less.',
  'operator/runners/jobs/tests/test_update_select_template.py':
    'Model-read rule: never invent or infer a provider name.',
  'operator/runners/jobs/tests/test_deliver_by_smd.py':
    'Model-read rule: the DELIVER reply wording for an update.',

  // ---- Client content and money (CLAUDE.md "No fabricated client-facing content", P0)
  'tests/sow-template.test.ts':
    'Client content: SOW payment terms, no hourly rate, exclusions, we-voice, no fixed stabilization period.',
  'tests/portal-start-subscription-cta.test.ts':
    'Money: start gates, principal-only role, server re-checks, real Stripe link only, charged total shown.',
  'tests/portal-quotes.test.ts':
    'Client content: no hourly rate or "will reach out" on proposals; scope only from authored deliverables.',
  'tests/quotes-authored-content.test.ts':
    'Client content: Pattern A/B fallbacks gone; SOW and portal read authored fields only.',
  'tests/invoices.test.ts':
    'Money: no legacy ach_debit; no fabricated fallback or second Pay control.',
  'tests/admin-entities-proposing.test.ts':
    'Client content: an empty proposing state renders nothing, never a placeholder sentence.',
  'tests/advanced-settings-surface.test.ts':
    'Honesty: no success state for a write that did not happen; one reconstruction across src.',
}
