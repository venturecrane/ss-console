---
title: Vertical Packs
section: product
order: 4
owner: captain
review_every: 90
last_reviewed: "2026-10-21"
summary: A pack is a quick-start template that makes the universal Operator recognizable as "exactly your thing" - not a claim that we are experts in the client's business
sources:
  - label: Law-firm base pack manifest
    href: operator/verticals/law-firm/vertical.yaml
  - label: Law-firm personal-injury add-on
    href: operator/verticals/law-firm/addons/pi/addon.yaml
  - label: Intake interest allow-list
    href: src/lib/booking/config.ts
  - label: Registered verticals
    href: src/lib/operator/customer-yaml/types.ts
  - label: ADR 0037 - The Operator Thesis (Tenet 5, packs)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0037-operator-thesis.md
  - label: ADR 0022 - Vertical Pack Architecture
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0022-vertical-pack-architecture.md
  - label: Pack Production Operating Model
    href: https://github.com/venturecrane/ss-console/blob/main/docs/pm/pack-production-operating-model.md
  - label: docs/specs/verticals/law-firm.md - worked reference pack
    href: https://github.com/venturecrane/ss-console/blob/main/docs/specs/verticals/law-firm.md
---

A pack is the industry starting point for a client's Operator, such as the law-firm pack: a ready set of skills, limits and connections that makes the Operator recognizable to that kind of business on day one. Twelve industries have a written pack and a public page, but only the law-firm pack serves a client today, so treat the others as offers SMD can build out when a client signs, not as finished products.

## What a pack is, and what it is not

The Operator is a configurable substrate with no fixed function ([ADR 0037](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0037-operator-thesis.md), Tenet 2). "All things to all people" is the capability. "Exactly your thing" is how we sell it. A **pack** is the bridge: it refines the substrate for a vertical with a starting set of skills, entitlements, voices, connectors, compliance floors, and fixtures (Tenet 5).

A pack is a **quick-start template, not a claim of expertise in the client's business.** Tenet 5 makes packs the entry, not the substance: we enter through a sharp, narrow vertical because the platform underneath is unlimited and we lose nothing by starting narrow. Beyond the pack, what a client's Operator does is what that engagement authored (Tenet 3), and the client is the expert in their own business (ADR 0040, section 4). The marketing word "package" describes a recognizable wedge; the capability underneath does not shrink to fit it.

This distinction is doctrine, not phrasing. A pack page that reads like we already know the prospect's business breaks the rule against claiming to know a prospect's business (rule 7 of the tone standard in `CLAUDE.md`; see [Positioning & Voice](/admin/playbook/positioning-voice)). The pack describes a *seat*, the coordinator-shaped role a business covers one way or another, and offers to cover it. It does not diagnose the specific firm.

## Packs compose and cluster

Per Tenet 5, packs **compose** (an accounting pack inside a law firm) and cluster into **families** that share DNA, so building one compounds the next. The magnitude is the strategy; the pack is the entry. This is why narrow is safe: the operating model describes packs as about 80% template, honed per client (`docs/pm/pack-production-operating-model.md`), and the families let later packs reuse the worked motion of earlier ones.

## The architecture: three layers

[ADR 0022](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0022-vertical-pack-architecture.md) (locked 2026-05-25) defines the pack as a three-layer model - **platform / vertical+addons / customer** - over an event-sourced state substrate. A base pack (for example `law-firm`) can carry add-ons (for example `law-firm/pi`, the personal-injury skin, at `operator/verticals/law-firm/addons/pi/`) that are additive on the base. The customer layer is the per-engagement authoring on top.

## A pack is five artifacts

The Pack Production Operating Model defines a pack as five concrete artifacts, all driven by the spec (build it first):

| # | Artifact | Lives in |
| --- | --- | --- |
| 1 | **Vertical spec** (the brief) | `docs/specs/verticals/<slug>.md` |
| 2 | **Technical pack** (manifest, registry, fixtures) | `operator/verticals/<slug>/` + `types.ts` |
| 3 | **N=0 proof** (hand-drafted connective artifacts) | `operator/verticals/<slug>/fixtures/` |
| 4 | **GTM surface** (landing page + intake hook) | `src/pages/packs/<slug>.astro` + the interest allow-list in `src/lib/booking/config.ts` |
| 5 | **Delivery SOP** (honed per client) | `docs/templates/delivery-sops/` + spec notes |

The vertical-specific skill **bodies** are not in this list. They live in this repo under `operator/skills/<skill>/SKILL.md` (72 skill directories on 2026-10-21, a count that includes the hosted-agent and couples skills), not in the overlay, and are baked into the fleet image; the vertical's `operator/verticals/<slug>/vertical.yaml` (and an add-on's `addon.yaml`) declares which of them the pack uses. The operating model document still says skill bodies go in the overlay; that line predates the move and is out of date. See [Operator Platform Architecture](/admin/playbook/operator-platform) for the overlay boundary.

## The shared spine

A pack is the shared spine plus a vertical skin. Generic connective skills already exist in `operator/skills/`: `inbox-triage`, `email-reply`, `status-report-assembler`. (Four more generic skills, ar-chaser, asset-collection-follower, retainer-hours-reconciler and scope-creep-flagger, were retired 2026-09-29: no client ran them.) Reuse them; do not rebuild them. The vertical contributes its specific skills and its connector map; the spine stays shared. For Law, the base pack's `vertical.yaml` lists eighteen law skills (its own comment still says the brief specified twelve; more were added since), and the personal-injury add-on's `addon.yaml` lists twenty-six more.

## The documented verticals

Twelve verticals are specified under `docs/specs/verticals/` (plus a `_template`), each with a matching `operator/verticals/<slug>/` directory. Law (personal-injury) is the one built end-to-end and the worked reference every later pack is skinned from.

- `accounting`
- `dental`
- `home-services`
- `insurance`
- `law-firm` (worked reference; base + `pi` add-on)
- `marketing-agency`
- `med-spa`
- `mortgage`
- `property-management`
- `ria`
- `title`
- `veterinary`

A documented spec is a brief, not a shipped pack. A pack page is published from its written spec, and building the pack out (its seat configuration and skills) starts with the first client in that vertical. The Captain amended the operating model to this on 2026-10-21 to match practice: all twelve pack pages are public, and only law has been delivered (`docs/pm/pack-production-operating-model.md`).

Current state, checked against the repo: all twelve pack pages are published (`src/pages/packs/<slug>.astro` exists for every vertical above), and only the law-firm pack has a live seat (`vertical: law-firm` is the only vertical any `operator/customers/*/customer.yaml` names; the other seats are `vertical: mixed`). The published pages therefore run ahead of the rule as written; which of the two should change is a decision for the Captain and is not recorded.

## How a pack is built (the motion, proven on Law)

From the operating model:

1. Write the vertical spec from the target landscape plus light voice-of-customer.
2. Register the vertical in `types.ts` if new; copy `operator/verticals/_template/` to `operator/verticals/<slug>/` and fill the manifest and addon.
3. Hand-draft the N=0 proof against synthetic inputs - it must read as sendable.
4. Clone `src/pages/packs/law-firm.astro`, reskin the copy (compete-with-a-hire, the seat, the connective tasks, stays-in-its-lane), and add the slug to `INTEREST_LABELS` in `src/lib/booking/config.ts` (the one allow-list both `/book` and `/api/intake/send` enforce) and to `landing-page.test.ts`. The operating model still names `book.astro` and `api/intake/send.ts` as the allow-lists; they now read the shared list.
5. Note the delivery specifics against the generic SOP.
6. Verify: `npx vitest run tests/forbidden-strings.test.ts tests/landing-page.test.ts` and `npm run typecheck`.
7. Author the vertical skill bodies in `operator/skills/`. Any connector the pack needs is an author-built MCP server in `operator/connectors/<name>/` (ADR 0053); its tools must also be classified in the overlay's `shared/action_classes.py`, which is the overlay hand-off (see `/admin/playbook/connectors-channels`).

## The overlay boundary and the hand-off

A pack touches two parts of this repo:

- **The pack itself:** the five artifacts plus the skill bodies - manifest, registry edit, marketing surface plus intake allow-list, vertical spec, N=0 fixtures, delivery SOP notes, the skills in `operator/skills/`, and any author-built connector in `operator/connectors/`.
- **The overlay (`operator/hermes-smd-overlay/`):** the action-class classification of any new connector tool (`shared/action_classes.py`), and any new runtime hook a pack genuinely needs. This is the hand-off a pack can trigger, and a predecessor to a live customer (not to authoring the pack or its marketing).

Each pack is handed to an agent team with the target-landscape entry, the spec template, the Law pack as the worked reference, and the hand-off prompt. The team returns one PR. Until 2026-10-22 the overlay was a separate repo and a pack that added connector tools needed a second PR there; ADR 0095 folded it into this repo.

**The collision warning is real:** parallel teams must not edit the shared files (`src/lib/booking/config.ts`, `src/lib/operator/customer-yaml/types.ts`, `tests/landing-page.test.ts`) at the same time, or they collide. Serialize those edits or assign one integrator. See [Operating Model & the Fleet](/admin/playbook/operating-model) for how the fleet coordinates parallel work.

## The one hard rule

No fabricated client-facing claims (the Pattern A/B prohibition), no "coming soon," no fixed timeframes or dollar amounts in marketing, no copy implying pre-knowledge of a prospect's business. Marketing copy passes `tests/forbidden-strings.test.ts` and `tests/landing-page.test.ts` (register every new pack page in the latter's `readMarketingFiles()`). When a claim is not yet true, the fix is to **build faster, not to gate the build.** See `/admin/playbook/positioning-voice` for the full content policy.
