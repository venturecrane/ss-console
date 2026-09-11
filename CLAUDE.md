# CLAUDE.md - SMD Services

This file provides guidance for Claude Code agents working in this repository.

## About This Venture

**SMD Services** is a solutions consulting venture under SMDurgan, LLC. We sell scope-based consulting engagements to growing businesses. This is NOT a SaaS product. It is a services business.

**Objective:** Launch the venture and reach profitability.

**Core offering:** We work alongside business owners to understand where they're trying to go, figure out what's slowing them down, and build the right solution together. Solutions range from process documentation and tool configuration to custom internal tools, system integrations, and operational dashboards. Engagement length and pricing are scoped per project.

**Geography:** Phoenix-based, in-person default for Phase 1 (first 5 clients), remote-capable.

**Positioning:** The client is the hero, we are the guide. Collaborative, objectives-first. The value is enterprise operational discipline applied to businesses that have never had access to it, delivered at speed and pricing that works for their stage. AI & automation is a named capability. We do AI work when AI is the right answer, and we say so plainly. We do not brand the firm, the method, or non-AI engagements as "AI-powered." A chef isn't hired for his knife, but he names the knife when it matters.

## What This Repo Is For

This repo is the operational hub for the SMD Services venture. It holds:

- Business collateral (assessment scripts, proposals, SOW templates, pricing)
- Client delivery templates (SOP templates, checklist frameworks, communication playbooks)
- Marketing materials (landing page copy, outreach templates, case study frameworks)
- The venture website (smd.services) when built
- Process documentation for how we run engagements

This is NOT a product codebase. There is no app to build (yet). The primary output of agent work here is **documents, templates, and strategy** — not code.

## Session Start

Every session must begin with:

1. Call the `crane_preflight` MCP tool (no arguments)
2. Call the `crane_sos` MCP tool with `venture: "ss"`

This creates a session, loads documentation, and establishes handoff context.

## Session Mechanics (2026-08-01 trust-restoration mechanisms)

Four deterministic mechanisms, built after the 2026-08-01 autopsy (14 sessions read end to end; six root causes). Each produces or checks an artifact — none asks an agent to remember to behave. Prose here is the semantic half; the hooks enforce the shape.

**1. Reply contract** (`.claude/hooks/reply-contract.mjs`, Stop hook). Replies over 25 prose lines must lead with `MISSION:` / `STATUS:` / `DID:` / `NEXT:` (STATUS ∈ `OK | BLOCKED | DECISION-NEEDED | DEFECT-FOUND`) and keep ≤12 prose lines above a `--- Detail` fold; the hook bounces violations once with instructions. The parts no hook can check: **every fact carries its source inline** (a command, a `file:line`, or the words "I'm inferring" — no source, don't say it), **decisions are stated in what the business or client experiences, never implementation vocabulary**, and a `DECISION-NEEDED` is one sentence of stakes, two options, your pick — you proceed on your pick unless told otherwise. Brevity applies to explanation, never to bad news: BLOCKED and DEFECT-FOUND always surface.

**2. Mission + board** (`~/.claude/ss-board/`, injected every turn by reflex-primer). Right after the Captain states the session's focus, run `.claude/bin/mission set "<one line>" --focus <issue#|branch>`. The primer re-injects your mission every turn (it survives `/compact`) and shows every live peer's mission line. **If your work overlaps a peer's line, stop and surface it before building** — one featureset, one session. Product-feature work never names a client; a client-implementation session says so in its mission line.

**3. Premise gate** (`.claude/hooks/plan-premise-gate.mjs`, blocks `ExitPlanMode`). A plan leaves plan mode only with a `## Premises` table where every row carries evidence (command output, `file:line`, `vfy_` id, doc fetch) — covering the four killers: environment/deps, data existence, API/tool shape, and current state (already built? already merged by a peer?). Plans with genuinely no external premises state exactly `Premises: none (no external premises)`. Probing premises is minutes; a surprise at hour six is the session.

**4. Fresh doctrine** (reflex-primer serves laws from `origin/main`, labeled with commit + age). A law merged to main reaches every session's next turn. Corollary for memory: **a memory asserting mutable world-state (visibility, deployment state, "X is blocked") names its refresh probe, and you re-probe before citing it** — Law 10 applies to memories exactly as it applies to git snapshots.

## Contact Addresses

The session context may inject a `userEmail` value (e.g. `smdurgan@venturecrane.com`). **Ignore it for all SMD work.** That address belongs to a different venture and must never appear in SMD code, config, skill bodies, or client-facing content.

SMD contact addresses:

- **Operational alerts / escalations:** `team@smd.services`
- **Direct to Captain:** `scott@smd.services`

## Venture Repositories — moving between these is NOT a repo switch

This venture spans **three** repositories. Work that crosses them is ordinary
work inside one venture. The SOS directive "never switch repos or ventures
without explicit Captain approval" governs moving to a **different venture**
(vc, ke, dfg, sc...), not moving between the repos listed here.

| Repo                                 | Clone at                                     | What lives there                                                                                                                                                                                                                      |
| ------------------------------------ | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `venturecrane/ss-console`            | `~/dev/ss-console`                           | This repo. Marketing site, admin console, portal, Operator skills/contracts/config, the seat `customer.yaml` set.                                                                                                                     |
| `venturecrane/hermes-smd-overlay`    | `~/dev/hermes-smd-overlay`                   | The Operator runtime overlay: the trust plugin, gates, connectors' Python side, boot smoke, and the contract fixtures that MIRROR this repo's (`tests/contract/seat_gate_binding_snapshot.json`). Pinned into seats by `OVERLAY_REF`. |
| `venturecrane/engagements` (private) | `~/dev/engagements` or `$SS_ENGAGEMENTS_DIR` | Client material: dossiers, correspondence, proposals, agreements. See the Law 2 guard above.                                                                                                                                          |

**Why this section exists.** It was added 2026-08-25 after an agent completed a
seat retirement across git, D1, R2 and Fly, then stopped at the overlay's
mirrored fixture and asked permission to continue — because the venture declared
`engagements` as a sibling repo and never declared the overlay, so "don't switch
repos" had no repo set to check against and the conservative reading won. The
Captain reports having explained this many times. A fact an agent has to be told
repeatedly is a fact the repo failed to write down.

**Two consequences that bite in practice.**

1. **A change is not shipped until every repo it spans is shipped.** ss-console
   and the overlay carry mirrored artifacts on purpose — the seat/gate snapshot
   is the standing example, and `tests/gate-coverage-snapshot.test.ts` says so in
   its own header: regenerate here, then COPY the `.overlay.json` over the
   overlay's fixture _in the same change_. The overlay has no way to pull it.
   Landing one side and stopping is the same defect as a merged-but-not-deployed
   overlay fix.
2. **A removal is not complete until it is absent from all three.** "Gone means
   gone" enumerates runtime layers; the repo layer is plural here.

## Enterprise Rules

- **All changes through PRs.** Never push directly to main. Branch, PR, CI, QA, merge.
- **Worktree discipline: the primary checkout is read-only.** All repo mutations happen in an isolated worktree (`EnterWorktree`). A PreToolUse hook (`.claude/hooks/worktree-guard.mjs`) rejects Edit/Write into the primary tree (paths under `.claude/` are exempt — the worktrees themselves live there), and a SessionStart hook (`.claude/hooks/sync-primary.sh`) fast-forwards a clean primary checkout to origin/main so it never drifts stale. Do not work around the guard with Bash writes. Captain-only escape hatch: `SS_ALLOW_PRIMARY_WRITES=1`. Guard tests: `tests/worktree-guard.test.ts`.
- **Your session-start snapshot expires (Law 10).** The `gitStatus` block in a session's context, the worktree list, what had merged, and what is installed are all captured once at startup, and under concurrency none of it survives contact: on 2026-07-31, five sessions and 22 merges in 36 hours. Note that `sync-primary.sh` is itself a cause, because it moves the primary's source at one session's startup without moving `node_modules`, on behalf of a session that started later. Two surfaces close this. `.claude/hooks/reflex-primer.sh` re-checks every turn and reports main moving, the tree changing against a per-session baseline, and dependencies content-drifting from `package-lock.json` (it gates on mtime but speaks only on content, because `git stash`, `git restore`, and rebases all touch the lockfile without changing it). `.claude/hooks/session-peers.sh` reports live peer worktrees at startup, since sessions are otherwise invisible to one another until `/eos`. Tests: `tests/staleness-detection.test.ts`, which is the only execution coverage any bash hook here has.
- **Operating doctrine.** The distilled agent operating laws live in `docs/doctrine/agent-operating-doctrine.md` (registry: each law with its incidents, enforcement tier, and pointer). The always-on surface is `.claude/hooks/reflex-primer.sh` (UserPromptSubmit, every turn); `tests/doctrine-integrity.test.ts` pins primer/doctrine parity. Maintenance contract: a correction from the Captain that changes a law updates doctrine + primer in the same PR. Engagement work is gated by Law 2: read `operator/customers/<slug>/dossier.md` before touching that engagement (`.claude/hooks/engagement-guard.mjs` blocks unread-engagement writes; Bash writes are not intercepted — do not work around the guard; Captain-only escape hatch, path-scoped and audited: `SS_ALLOW_UNREAD_ENGAGEMENT_WRITES="<path-fragment>"`).
- **Client material lives in `venturecrane/engagements` (private), not here.** Dossiers, correspondence archives, proposals, scoping and entitlement write-ups, rehearsal reports, and agreements all live in that repo, under the **same** `operator/customers/<slug>/` prefix. What stays in ss-console is the operational config — `customer.yaml`, `routine-grid.yaml` — plus the `_template/` provisioning scaffold. Clone it to `~/dev/engagements` (or set `SS_ENGAGEMENTS_DIR`); the Law 2 guard **fails closed** when it is missing, because "cannot evaluate" must not read as "permitted". Never write a dossier, a letter, or a client document into ss-console: `tests/doctrine-integrity.test.ts` fails if a `correspondence/` archive reappears here. Rationale and history: ADR 0081, decision #52, and the engagements repo README.
- **Never echo secret values.** Transcripts persist in ~/.claude/ and are sent to API providers.
- **Verify secret VALUES, not just key existence.**
- **Never auto-save to VCMS** without explicit Captain approval.
- **Scope discipline: do the work, do not file it.** Work discovered mid-task gets **done now** if it is within reach — that is what a platoon of agents is for. Finish the current scope first, then do it. File an issue only when it is genuinely not doable now: blocked on something that does not yet exist, or a decision only the Captain can make. "I noticed something" is not a reason to file. **Standing target: zero open issues.** Automated reconcilers (`unaudited-send-reconcile`, `terminal-state-reconcile`) are exempt — their issues are alerts, and the target must never become a reason to silence a monitor.
- **Escalation triggers.** Credential not found in 2 min, same error 3 times, blocked >30 min — stop and escalate.

### Gone means gone (removal discipline)

"Done means wired" has an inverse, and it is enforced the same way: **a removal, rename, or retirement is complete only when the artifact is absent from every layer it ever lived in, proven by a probe of each RUNTIME layer — not by the diff that deleted it from git.**

The lesson (the quinn incident, 2026-07-02 → 2026-07-26): a persona name was "removed" four separate times — display name (07-02), D1 projection (07-09), repo slug + CI guard (07-13) — and each completion report was honest about the layer it touched while wrong about the job, because state the repo materializes outlives the repo: the Fly volume kept the retired slug's profile home and its frozen cron store until monitoring paged on it 12 days later. Same failure class as built-but-not-wired: the claim was scoped to the artifact the agent could see, not the mission.

The rule, mechanically:

1. **Inventory before claiming.** Before reporting a removal complete, enumerate the layers the artifact can live in. For this venture that list is: git (source, fixtures, docs), D1 projections (`customer_configs` — no auto-sync, #1308), R2 (skills, vaults, config), the Fly volume (`/opt/data` — profile homes, cron stores, tokens, the Smokeball extraction cache at `/opt/data/smokeball-extract-cache/` which holds transcribed matter-document TEXT; survives reprovision BY DESIGN), the running Machine (env, loaded config, skills_list), monitoring surfaces (heartbeat fields, alert sinks, Sentry), and external records (GitHub issues/PRs, mailboxes, calendars, vendor dashboards).
2. **Negative probe per runtime layer.** Each runtime layer gets a probe showing absence, recorded via `crane_verify` with the probe output. Repo layers are covered by CI guards (`tests/forbidden-strings.test.ts`); runtime layers are never covered by CI guards — that is the whole point.
3. **Prefer structural fixes over sweeps.** A one-time cleanup leaves the class alive. When a removal keeps resurfacing, the fix is a reconciler that makes the layer converge on authored state (the overlay#185 profile-home reconciler is the template) plus a boot/smoke assertion that the convergence held (`boot-smoke-test.sh` step 6b).
4. **The completion report cites the probes.** "Removed X" without verify IDs for the runtime layers is a repo-layer claim and must be worded as one.

### Done means the client can do it (reachability discipline)

The positive twin of "gone means gone", and the same enumeration in the other direction. **A feature is done when a real client can perform the act on the deployment they use, proven by an observation of the running system.** Not when the PR merged, not when tests passed, not when the component exists.

The lesson (the entitlement-control incident, 2026-07-28): four PRs, each individually honest, each defining "done" as the artifact it added. One wrote "Next slices, unbuilt and not implied here." Nobody lied. The artifacts summed to less than the feature, the epic closed green, and a Named Administrator could not change a routine's level. That is a definition problem, not a diligence problem, which is why asking for more care would not have caught it.

Three claims, kept distinct:

- **Built** — the code exists and its own tests pass. The weakest of the three and the easiest to mistake for done, because it produces the most visible evidence.
- **Wired** — every gate between a real client's finger and the effect is open on the deployment that client uses. Configured, not configurable. Secrets and config authoring are part of the deliverable, not prerequisites belonging to someone else.
- **Tested** — someone performed the act as the client, on the real seat, and observed the far end change. A green unit test against a fake token is not this.

**Run `/wired` before planning any work whose effect is observable outside this repo** (a client, the Captain on a live surface, an Operator seat, a prospect on a marketing page). It produces the contract: the act as a sentence, the terminal seam, the gate chain enumerated backwards from that seam, and a feasibility probe that escalates unclosable gates **before** the closable ones get built. Then plan against the contract, then `/critique`. Internal refactors, tests, and docs skip it.

**Enforcement.** Law 9 in `docs/doctrine/agent-operating-doctrine.md` (primer tier, always on). Merge gate is `.github/workflows/runtime-ac-proof.yml`: an AC tagged `(runtime)` cannot be marked `met` without a `crane_verify` ID in the PR's Evidence column. That gate exists because `tick-acs-on-merge` ticks whatever the merging PR declares about itself and `unmet-ac-on-close` skips PR-driven closes, so without it the system certifies the author's own definition of done.

### No fabricated client-facing content

Any information displayed to a client (timelines, schedules, deliverables, pricing, deposit terms, guarantees, consultant names, dates, scope language, post-signing promises, first-person sentences about future business behavior) MUST come from data authored for that specific engagement. That means database columns populated by a human-reviewed admin flow, CMS content, or source files explicitly reviewed by Captain.

**Two violation patterns are prohibited:**

- **Pattern A (committed template sentences that imply uncontracted commitments).** Hardcoded sentences in source, even ones that interpolate authored values, that promise specific business behavior the engagement has not contracted. Real examples from the 2026-04-15 audit:
  - `'We'll reach out to schedule kickoff.'` (`src/lib/portal/states.ts:138`)
  - `'Work begins within two weeks of signing.'` (`src/pages/portal/quotes/[id].astro:72`)
  - `'Replies within 1 business day.'` (`src/components/portal/ConsultantBlock.astro:136` — file since removed)
  - `'A 2-week stabilization period follows the final handoff.'` (`src/lib/pdf/sow-template.tsx:529`)

- **Pattern B (runtime fabrication from non-authoritative fields).** Values rendered from sources never authored as client-facing content: placeholder defaults, parsed or derived text, brief-borrowed copy. Real examples from the audit:
  - The 3-week schedule constant (`'We shadow and observe.'` / `'We redesign together.'` / `'Training and handoff.'`) at `src/pages/portal/quotes/[id].astro:79-83`, stripped by hotfix #378
  - `overview: 'Operations cleanup engagement as discussed during assessment.'` injected into every SOW PDF at `src/pages/api/admin/quotes/[id].ts:110`
  - `contactName: primaryContact?.name ?? 'Business Owner'` at `src/pages/api/admin/quotes/[id].ts:101` (a SOW signed as "Business Owner" is a compliance risk)

**If authored data is missing:** render nothing or an explicit "TBD in SOW" marker. See `docs/style/empty-state-pattern.md`. Never invent plausible content.

**Visual + component patterns:** see `docs/style/UI-PATTERNS.md`. Six rules covering status display, redundancy, button hierarchy, heading skip, typography scale, and spacing rhythm — authored to raise UI quality to professional level. Same enforcement shape as empty-state-pattern: narrow, cited to NN/g / Material 3 / WCAG 2.2, anti-patterns with file paths, merge gate per shipped rule. Produced by the `ui-drift-audit` skill (run by `.github/workflows/ui-drift-audit.yml`), which emits a surfaces × rules matrix at `.stitch/audits/ui-drift-{date}.md`.

**Enforcement.** Violations are P0. Merge gate is `.github/workflows/scope-deferred-todo.yml` (blocks TODO-deferred ACs without the `scope-deferred` label). Issue-close gate is `.github/workflows/unmet-ac-on-close.yml` (reopens issues closed with unchecked ACs).

**Fabrication and drift guardrails.** Pattern A/B is not the full policy. The repo also blocks three adjacent failure modes:

- Shipped user-facing copy must not pick up banned style markers or placeholder copy. No em dashes. No "coming soon" on prospect or client surfaces.
- Enrichment prompts must stay extractive and evidence-bound. They must not ask for management style, personality, communication preferences, likely objections, or other private-condition inference.
- Shared product flows must stay shared once canonicalized. If `/book` and `/get-started` drift back into duplicate intake implementations, that is a regression.

**Tests linked to this policy**

- `tests/forbidden-strings.test.ts` - historical Pattern A/B phrases, user-facing style-marker checks, and portal registry guardrails
- `tests/intake-questionnaire.test.ts` - shared-surface regression coverage for the canonical intake questionnaire

## Tone & Positioning Standard

**These rules apply to ALL external-facing content: website copy, outreach, proposals, collateral, and any client-facing language. They also apply to internal content that may inform external copy (e.g., one-liners, scripts).**

### 1. Objectives over problems

Frame engagements around understanding business objectives, not just diagnosing problems. Recognizing a problem is a start, but without knowing the objective — what the business is trying to achieve — we can't truly solve it. The owner often knows the pain but hasn't articulated the goal. Part of our value is helping them discover the real objective through conversation. Don't give them a faster horse.

- **Do:** "We start by understanding where you're trying to go, then figure out what's in the way."
- **Don't:** "We diagnose your top problems and fix them."

### 2. Collaborative, not diagnostic

We are a peer working alongside the owner, not an expert arriving to audit them. The owner has the vision. We have operational experience. We figure it out together.

- **Do:** "We work alongside you," "Let's figure out what needs to change," "together"
- **Don't:** "We audit your operations," "We tell you what to fix," "We come in and pick the things that matter"

### 3. No fixed timeframes in external content

Timeframes are scoped per engagement, just like pricing. Do not publish specific durations for any phase of the engagement — the call, implementation, training, or support. Internally, use timeframes for planning, but never commit them in client-facing content.

- **Do:** "We start with a conversation," "Hands-on training with your team"
- **Don't:** "1-hour call," "10-day sprint," "60-minute training," "2-week support window"

**Applies to marketing content only.** This rule does not apply to signed contractual documents (SOW PDFs, invoices, countersigned agreements). A signed SOW is a contract where specific timeframes are the product of the conversation, not marketing copy. Timeframes in signed documents stay as authored.

### 4. No published dollar amounts

No dollar amounts appear on the website or in marketing materials. The client sees a project price in their proposal — never on a public page.

### 5. "Solution" not "systems" in marketing contexts

"Systems" sounds scary to business owners — it implies software and one more thing to learn. Not all solutions are software; sometimes it's a better process, a clearer role, or a simpler workflow. Use "solution" in positioning contexts. "System" is fine when referring to a specific literal tool (e.g., "data migration and system setup").

- **Do:** "Build the right solution," "the right solution to get you there"
- **Don't:** "Build better systems," "Your systems should keep up"

### 6. Voice standard (from Decision #20)

Always "we" / "our team." Never "I" / "the consultant." See Decision Stack #20 for full details.

**Practitioner-firm exception (added 2026-05-03):** §07 "Who We Are" on the marketing home page is the one place where Scott speaks in first person. SMD is positioned as a practitioner firm — like a lawyer's office, a doctor's practice, or a craftsman's shop — where the founder _is_ the firm and there is a real team behind him. In that model, forced "we" voice in the founder bio reads insincere; first person is the only sincere voice. About speaks as Scott; every other section ships in firm-level "we" voice. The voice-standard test in `tests/landing-page.test.ts` excludes `About.astro` for this reason. Do not rewrite About to "we" voice without Captain explicitly reversing this call.

**Identity-marker rule (added 2026-05-03):** Words that describe an aspirational self-quality (Captain's examples: "magic," "artist," "creative") must SHAPE the voice without being STATED on the page. Stating them reads as overclaim or self-flattery. Convey wonder via concrete language and what the work does, not by calling it magic. Convey creativity by showing it, not by calling it artistry. The rule applies to all marketing surfaces, not just About.

### 7. No claim to know the prospect's business

We do not write copy that implies pre-knowledge of a specific prospect's business. We are collaborators who learn the situation through conversation, not diagnosticians who arrive with answers. This rule covers both first-person ("I know what's wrong with your business") and implied ("This is what your business needs"). See `feedback_no_pretend_to_know_business.md`.

---

## The Business Model and Domain Context

The problem framework, the six-category delivery taxonomy, pain clusters by vertical, engagement phases, the pricing ladder, the assessment-call thesis, the pre-launch priority checklist, and the buyer / competition / referral context live in `.claude/rules/venture-model.md`. It loads automatically when a session touches marketing pages, components, the handbook, ADRs, or vertical packs; for content, collateral, scoping, or pricing work that touches none of those paths, read it first.

One rule from it applies everywhere: the six-category list is the **delivery taxonomy** and the marketing source of truth ([ADR 0001](docs/adr/0001-taxonomy-two-layer-model.md)); the five-category schema in `src/portal/assessments/extraction-schema.ts` is the **client-assessment extraction taxonomy**, a different layer. Agents editing either side must not silently change the other.

## Three-Subdomain Architecture

One Astro app on Cloudflare Workers, one Worker (`ss-web`), three custom domains, routed by `src/middleware.ts`. Its header comment documents the rewrite, the Clerk-primary auth model, and the legacy magic-link fallback; `src/lib/auth/admin-session-shim.ts` is the admin bridge.

| Host                  | Serves                                   | Auth role |
| --------------------- | ---------------------------------------- | --------- |
| `smd.services`        | Marketing pages                          | Public    |
| `admin.smd.services`  | Admin console (rewritten to `/admin/*`)  | `admin`   |
| `portal.smd.services` | Client portal (rewritten to `/portal/*`) | `client`  |

**Env vars.** `APP_BASE_URL` (marketing, SignWell webhooks), `ADMIN_BASE_URL` (OAuth redirect URI, outbound admin links — strict, no fallback), `PORTAL_BASE_URL` (portal links, falls back to `APP_BASE_URL`). See `src/lib/config/app-url.ts`.

**Env access in code.** Adapter v13 removed `Astro.locals.runtime`; import `env` from `cloudflare:workers` (typed by augmenting `Cloudflare.Env` in `src/env.d.ts`).

## Local Dev

`.mcp.json` is user-local and gitignored; create it with at least the `crane` entry. Also register Sentry so agents can read `smd-operator` issues directly (remote server, OAuth 2.1 + PKCE, no token to vault; the first call opens a browser consent):

```bash
claude mcp add --transport http -s project sentry https://mcp.sentry.dev/mcp/smdurgan-llc
```

Subdomain routing does not fire at `localhost:4321`; hit `/admin/*` and `/portal/*` directly. For full-fidelity testing add `127.0.0.1 admin.localhost` and `127.0.0.1 portal.localhost` to `/etc/hosts` and set matching `ADMIN_BASE_URL` / `PORTAL_BASE_URL` in `.dev.vars`.

Build layout, deploy, and secret rotation are documented in `wrangler.toml`, `docs/handbook/deployment-release.md`, and `docs/handbook/secrets-access.md`.

## Instruction Modules

Fetch the relevant module when working in that domain.

| Module                | Key Rule                                                                                                                                | Fetch for details                                                         |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `coding-standards.md` | Parse external inputs (never cast); no floating Promises; no module-level state in Workers; 500/75/15 file/function/complexity ceilings | Portable TypeScript directives, agent-context arithmetic, per-stack notes |
| `secrets.md`          | Verify secret VALUES, not just key existence                                                                                            | Infisical, vault, API keys                                                |
| `content-policy.md`   | Never auto-save to VCMS; agents ARE the voice                                                                                           | VCMS tags, storage rules, editorial, style                                |
| `team-workflow.md`    | All changes through PRs; never push to main                                                                                             | Full workflow, escalation triggers                                        |
| `guardrails.md`       | The full guardrail set the SOS Directives block summarizes                                                                              | Complete guardrails, rationale                                            |
| `operating-ethos.md`  | Mission first; confidence, not anxiety; no corporate theater                                                                            | Full operating ethos                                                      |
| `tooling.md`          | The toolkit catalog (MCP, fleet, subagents, browser)                                                                                    | Tool selection, capabilities                                              |
| `session-reflexes.md` | Name the source before any factual claim                                                                                                | The four reflexes, redirect decoding                                      |

Fetch with: `crane_doc('global', '<module>')`

## Design System

Load the enterprise pattern + component catalog before any UI work — design briefs, wireframes, component generation, design-related PR review:

- Patterns (cross-venture UX problem/solution pairs): `crane_doc('global', 'design-system.patterns.index.md')`
- Components (per-venture catalog of atoms, molecules, organisms): `crane_doc('global', 'design-system.components.index.md')`

Then load this venture's spec for palette and tone: `crane_doc('ss', 'design-spec.md')`.

The catalog is the shared vocabulary across all eight ventures — eight named patterns (status display by context, redundancy ban, button hierarchy, heading skip ban, typography scale, spacing rhythm, shared primitives, actions and menus) plus the components map (atoms / molecules / organisms with per-venture implementations). The catalog is a map, not a library — each venture maintains its own source. Cite a pattern by its file slug (`patterns/03-button-hierarchy.md`, etc.) when referencing it in PRs and skill output.

## Operator Context (moved to operator/CLAUDE.md)

The Operator Thesis (ADR 0037), the locked Operator architecture, and the
ADR load-lists live in `operator/CLAUDE.md`, which auto-loads whenever a
session works with files under `operator/`. For Operator strategy work that
touches no operator/ file, read `operator/CLAUDE.md` first — the thesis is
to be built upon, not re-derived.

## Captain-Side Skills (docs/skills/)

Venture-local slash commands (currently `/medchron`), authored under `docs/skills/<name>/SKILL.md` and installed with `bash scripts/install-captain-skills.sh`, which symlinks them into the gitignored `.agents/skills/` and `.claude/commands/`. Edit the tracked file only; run the installer on a fresh checkout. These are not enterprise skills; nothing here syncs with crane-console.

The governing constraint: the Captain cannot see any artifact a run produces, so every decision point arrives as prose carrying the counts, the exclusions and their reasons, the money, a recommendation, and a specific question. See `feedback_captain_cannot_see_artifacts_gates_must_be_prose.md`.

## Venture Handbook

The franchise operations manual lives in `docs/handbook/` and renders in the admin console at `admin.smd.services/admin/playbook`. It is the E-Myth handoff manual: what the venture is, why it exists, how it works, and where everything lives, organized so a zero-context successor could run, build, and grow the venture. Source of truth is the markdown in `docs/handbook/` (rendered by `src/content.config.ts` + `src/pages/admin/playbook/`).

**Maintenance contract:** when a change to the venture changes what a handbook page says, update that page in the same PR. The docs live next to the code so each page is edited in the same breath as the thing it documents. See `docs/handbook/README.md` for the page map and the "if you change X, update Y" table.

**Enforcement:** `tests/handbook-integrity.test.ts` runs in `npm run verify` and CI - it blocks merge on malformed frontmatter, a dead `/admin/playbook/<slug>` cross-link, a cited same-repo source file that no longer exists, a `(section, order)` collision, or an em dash. The advisory `npm run handbook:drift` reports pages whose cited sources changed after the page (git-mtime), for a periodic review pass. Both are documented in `docs/handbook/README.md`.

## Key Reference

- **Decision Stack:** `docs/adr/decision-stack.md` (37 active decisions across 6 layers, numbered through #55 (3 superseded: #2, #12, #43) — buy box, scope, pricing, assessment, distribution, delivery. Source of truth for all collateral and processes.)
- **Operator ADRs:** `docs/adr/0004-*.md` through `docs/adr/0061-*.md`. Always cite the ADR number when referencing an architectural decision. The Operator Thesis (ADR 0037) is the positioning frame the rest hang from.
- **Package 2 Deep Dive:** `~/Desktop/services-package-2-deep-dive.md` (full problem analysis, delivery model, positioning)
- `docs/` — Venture documentation as it develops

---

_Update this file as the venture evolves. This is the primary context for AI agents._
