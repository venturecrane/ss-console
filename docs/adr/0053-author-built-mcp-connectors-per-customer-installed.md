---
title: Author-Built MCP Connectors Are Per-Customer-Installed Units, Not Baked Into the Overlay
date: 2026-06-22
status: accepted
captain: Scott Durgan
related-adr: 0020-connector-strategy.md, 0015-hermes-fork-vs-upstream.md, 0007-per-customer-machine-isolation.md, 0021-leverage-hermes-native-primitives.md, 0022-vertical-pack-architecture.md, 0037-operator-thesis.md
---

# ADR 0053 — Author-Built MCP Connectors Are Per-Customer-Installed Units, Not Baked Into the Overlay

**Status:** Accepted (Captain decision, 2026-06-22). **Amended 2026-06-23** — the physical mechanism was corrected during implementation; see **§ Amendment** below before reading the original Decision. **Amended again 2026-10-22** by [ADR 0095](./0095-one-repository-for-the-seat-runtime.md); see the amendment directly below.

## Amendment (2026-10-22): the overlay is a directory of this repo

[ADR 0095](./0095-one-repository-for-the-seat-runtime.md) moved the overlay from the separate repo `venturecrane/hermes-smd-overlay` into `operator/hermes-smd-overlay/`. Three statements in this record change with it:

1. **Where the overlay lives.** The Context's "one pinned image" and Decision 3's "the single pinned overlay image (ADR 0015)" describe an overlay pinned into the image by `OVERLAY_REF`. There is no overlay pin now: the image is built from one ss-console commit, and the governed deploy unit named in the 2026-06-23 amendment (item 2, "`OVERLAY_REF` + image tag") is the image tag alone, whose label carries that commit.
2. **The action-class literal map** (2026-06-23 amendment, item 4) is `operator/hermes-smd-overlay/shared/action_classes.py`. A connector and its classification now land in one PR rather than one PR per repo; the map stays the governance authority and the manifest stays an oracle.
3. **Drift tracking** (Decision 5) cited `overlay-ref-drift.py` as the model. That script is removed; per-seat version drift is what `fleet status` shows against the newest completed release.

The substrate-only boundary of Decision 3 stands: connector servers live in `operator/connectors/`, not inside the overlay directory.

## Amendment (2026-06-23) — baked into the shared image, activated on bind

Implementation corrected the physical model. The original Decisions 2, 3, and 5 proposed standalone, separately-packaged units (own repo/package) installed onto a Machine only when bound. That solved a distribution problem that does not exist and contradicted the proven precedents already in this repo:

- **The skill-catalog precedent.** The entire skill catalog is baked into every image (`COPY operator/skills/`); per-customer **selection is pure config** in `customer.yaml`. Nothing ships per-customer; the binding selects.
- **The activation rail.** `translate.py::_materialize_mcp_servers` already launches an MCP server **only** when a `customer.yaml` binds it. A baked-but-unbound connector is inert — never launched, no tools, no secrets.

**Corrected mechanism:**

1. **Home = in-repo.** An author-built connector is a Python stdio MCP server at `operator/connectors/<name>/` (the Dockerfile's "Tier-1 custom MCP wrappers" dir), built on the shared `operator/connectors/_sdk/`. **No separate repo, org, release pipeline, or runtime fetch.**
2. **Baked into the shared image, one isolated venv per connector.** The Dockerfile installs each connector into its own venv (`/opt/connectors/<dir>/.venv`, the proven workspace-broker pattern); the overlay registry launches it by the **absolute venv console-script path**. The image stays the single governed deploy unit (`OVERLAY_REF` + image tag).
3. **Activated per-customer by binding — not per-customer installed.** A connector is present-but-inert on every Machine; it launches, surfaces tools, and receives secrets only when a `customer.yaml` binds `mcp:<name>` — identical posture to a catalog skill no persona enables. This supersedes the original "install only on bound Machines" and "the fleet never rebuilds to add a connector": adding a connector is an image change like adding a skill, shipped on the next reprovision. The only cost of a dormant connector is disk for inert code, paid once — never a running process.
4. **Governance authority is the overlay literal map.** Each connector's `mcp_<server>_<tool>` action classes are hand-authored **literal lines** in `shared/action_classes.py`, reviewed in the overlay PR; the connector's `manifest.toml` `tool_classes` is only a conformance **oracle** (checked against the map, never a runtime input). The connector cannot self-certify trust. An unclassified tool fails closed to `REFUSED`.
5. **Future hardening (not now): per-customer image bake.** If baked connector count / image size ever makes the shared image too heavy, move to per-customer images carrying only bound connectors. Deferred until weight demands it — it would fracture the single-image trust surface.

**Unchanged:** Decision 1 (a tool-surfacing connector we author is an MCP server, not a `build:` CLI) and Decision 4 (runs in-Machine, scoped to per-customer credentials, never a shared hosted service) hold as written. The Context and the named-shape framing stand.

---

**Source:** A walk-through of how a connector reaches a live Operator Machine, prompted by the Smokeball connector build for the Ashton & Price pilot. Smokeball has no first-party MCP and no vetted-community MCP, so it is the **first connector we must author ourselves that surfaces runtime tools.** That exposed a question the existing connector strategy (ADR 0020) does not answer: where does connector code _we write_ physically live, and how does it reach only the Machines that need it — without rebuilding the shared overlay image for every new client connector.

---

## Context

### How a connector reaches a Machine today

Every Operator capability resolves at runtime through a `customer.yaml.connectors{}.backend:` prefix (ADR 0020):

- **`mcp:<server>`** — a Model Context Protocol server. The overlay's `translate.py::_materialize_mcp_servers` reads the binding and writes that profile's `mcp_servers` config; the server's tools surface to the agent. Materialized **per-customer, only for what the seat binds.** Today every live `mcp:` server is **external** — vendor-hosted or vetted community (`mcp:clio-oktopeak`, `mcp:agentmail`, the M365/Stripe/QuickBooks servers in the ADR 0020 table). None of their code lives in our image.
- **`build:<vendor>`** — a Python CLI adapter under `operator/connectors/<vendor>/`, reached via `execute_code`. Code is ours, but a `build:` adapter **does not surface tools** — there is no tool-registration bridge (per `operator/contracts/connector-backend-materializers.json`). The current `build:` allowlist is empty (the former Google family was retired to the ADR 0045 broker).
- **`synthetic:<name>`** — in-process demo substrate. No live tools.

The result is a clean per-customer model: a connector is a **binding plus credentials**, not baked code. Credentials are per-customer (`token_ref: infisical:/operator/<slug>/...`), and `provision-customer.sh` stages a connector's secrets **only when it greps that binding out of the seat's customer.yaml.** A Machine with no Smokeball line has no Smokeball server configured, launched, or credentialed. Overlay drift is a surfaced fact, not a thing to remember — `operator/bin/overlay-ref-drift.py` diffs each Machine's running overlay commit against the pinned ref.

### The shape ADR 0020 does not place

ADR 0020 says: when no acceptable MCP exists, **BUILD**. But it equates "BUILD" with a `build:` execute_code CLI — and those **do not surface tools.** A connector whose capability must be callable by the agent as tools (a practice-management system the agent reads matters and writes memos against) cannot be a `build:` CLI; it must be an `mcp:` server so its tools materialize.

For a vendor with a vendor/community MCP, that is solved — bind `mcp:<theirs>`. For a vendor **without one** — Smokeball, and every long-tail system after it — the only way to get a tool-surfacing connector is to **write the MCP server ourselves.** That is a fourth, previously-unnamed shape: _code we author_ (like `build:`) that _surfaces tools_ (like `mcp:`). It is the right tool for the job, and ADR 0020's matrix should have named it.

The open question is purely physical: that server's code has to live somewhere on the Machine that runs it. The shared overlay is **one pinned image deployed to every Machine** (ADR 0015, pin-only fork). If we fold author-built servers into that image, then at N author-built connectors every Machine's base image carries N server packages — 95 of them dormant on a firm that uses one. That is the bloat the per-customer binding model otherwise avoids, and it reintroduces a coupling we explicitly do not want: _adding a connector for one new client becomes a change to the artifact every client runs._

We are at N = 1. This is the moment to set the pattern correctly.

## Decision

> **Read § Amendment (2026-06-23) first.** Decisions 2, 3, and 5 below describe the original pre-implementation mechanism (standalone per-customer-installed units); the Amendment supersedes their physical model with **in-repo + baked-into-the-shared-image + activated-on-bind**. Decisions 1 and 4 stand as written.

### 1. A tool-surfacing connector we author is an MCP server, not a `build:` CLI

Clarifying and extending ADR 0020: when no acceptable vendor or community MCP exists for a capability that must surface runtime tools, **we build an MCP server** (bound as `mcp:<vendor>`), not a `build:` `execute_code` adapter. `build:` remains reserved for non-tool-surfacing CLI work (and is empty today). "BUILD" in ADR 0020's decision order, for a tool-surfacing capability, means: build the MCP server.

### 2. Author-built MCP servers are standalone, per-customer-installed units

An MCP server we author is **not** folded into the shared overlay image. It is packaged as a **standalone, version-pinned unit** (its own repository/package), and `provision-customer.sh` installs it onto a Machine **only when that seat's customer.yaml binds it** — the same conditional shape already used to stage connector secrets, and the same per-customer model as skill bodies (ADR 0022 Stream 2: one `ss-operator-<slug>-skills` bucket per seat, not all skills to all Machines).

A connector is therefore present on a Machine **for exactly one reason: that seat asked for it.** Author-built servers behave identically to external `mcp:` servers from the fleet's perspective — bindings and credentials are per-customer, and the code follows the binding.

### 3. The shared overlay stays substrate-only

The single pinned overlay image (ADR 0015) holds the **stable substrate**: Hermes plugins, bootstrap, the governance/broker surface, and the `translate.py` materializer that turns bindings into config. It does **not** accumulate a catalog of per-client connector code. Adding a connector a single new client needs **never** requires an overlay release or a fleet-wide reprovision — only the one Machine that binds it is touched. This preserves the lean, slow-moving overlay the pin-only fork posture depends on.

### 4. Author-built servers run in-Machine, scoped to per-customer credentials

An author-built MCP server runs **inside the customer's own Machine** (stdio, launched by the materializer with that customer's credentials), **not** as a shared hosted service. Per-customer Machine isolation (ADR 0007) is the point: the firm's API token and the data flowing through the connector stay on that firm's isolated Machine. A single shared connector service that all firms' Operators call would re-pool isolated data and widen the blast surface — rejected.

### 5. Per-customer connector versions are pinned and drift-tracked

Each seat records the pinned version of any author-built connector it installs (alongside its other per-customer config), and connector-version drift is tracked the same way overlay drift is (`overlay-ref-drift.py` is the model). Upgrading one client's connector is a per-seat reprovision; other seats keep their pin. There is no "remember to update every Machine" step — by construction, there is nothing fleet-wide to update.

## Consequences

- **The fleet never rebuilds to add a client's connector.** New connector = publish/pin the unit (if author-built) + author the binding + stage creds + reprovision **that one Machine.** External `mcp:` connectors are already pure config; author-built ones now match them.
- **The overlay stays lean and stable.** It is substrate, not a connector warehouse. The pin-only fork posture (ADR 0015) holds as the connector count grows.
- **ADR 0020's matrix gains a named shape.** "No acceptable MCP → BUILD" now resolves, for tool-surfacing capabilities, to _build an MCP server packaged as a per-customer unit_, not a `build:` CLI. The `connector-backend-materializers.json` contract should record that author-built `mcp:` servers are a maintained-by-us subclass of the `mcp:` prefix, installed per-customer.
- **Isolation is preserved end-to-end.** Connector code, credentials, and the data it moves all live on the binding customer's Machine.
- **First instance: Smokeball.** The `pilot-smokeball` seat already binds `PracticeManagement → mcp:smokeball` with a per-customer `token_ref`. The Smokeball MCP server is built as the first standalone per-customer-installed unit under this ADR, establishing the pattern every subsequent author-built connector follows. (The staging document-write blocker is an unrelated external matter and does not affect this decision.)

## What this does NOT change

- External vendor/community `mcp:` connectors are unchanged — they were already per-customer config with no code in our image.
- `build:` and `synthetic:` prefixes and their materialization are unchanged.
- The customer.yaml authoring surface is unchanged: a connector is still a binding. This ADR governs where the _code behind an author-built binding_ lives and how it is installed, which is invisible at the authoring layer.
