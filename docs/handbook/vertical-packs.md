---
title: Vertical Packs
section: product
order: 5
summary: A pack is a quick-start template that makes the universal Operator recognizable as "exactly your thing" - not a claim that we are experts in the client's business
sources:
  - label: ADR 0037 - The Operator Thesis (Tenet 5, packs)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0037-operator-thesis.md
  - label: ADR 0022 - Vertical Pack Architecture
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0022-vertical-pack-architecture.md
  - label: Pack Production Operating Model
    href: https://github.com/venturecrane/ss-console/blob/main/docs/pm/pack-production-operating-model.md
  - label: docs/specs/verticals/law-firm.md - worked reference pack
    href: https://github.com/venturecrane/ss-console/blob/main/docs/specs/verticals/law-firm.md
---

## What a pack is, and what it is not

The Operator is a configurable substrate with no fixed function ([ADR 0037](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0037-operator-thesis.md), Tenet 2). "All things to all people" is the capability. "Exactly your thing" is how we sell it. A **pack** is the bridge: it refines the substrate for a vertical with a starting set of skills, entitlements, voices, connectors, compliance floors, and fixtures.

A pack is a **convenient quick-start template, not a claim of expertise in the client's business.** Per Tenet 5, packs turn the universal into the recognizable - they are the entry, not the substance. We owe every pack genuine excellence (it has to deliver tangible value out of the box), but beyond that the operator is the **client's**, and they configure it as they will. The marketing word "package" describes a recognizable wedge; the universal capability underneath does not shrink to fit the wedge. We enter through a sharp, narrow vertical precisely because the platform is unlimited and we lose nothing by starting narrow.

This distinction is doctrine, not phrasing. A pack page that reads like we already know the prospect's business violates the venture's no-pretend-to-know-their-business rule (see `/admin/playbook/positioning-voice`). The pack describes a *seat* - the coordinator-shaped role a business covers one way or another - and offers to cover it. It does not diagnose the specific firm.

## Packs compose and cluster

Per Tenet 5, packs **compose** (an accounting pack inside a law firm) and cluster into **families** that share DNA, so building one compounds the next. The magnitude is the strategy; the pack is the entry. This is why narrow is safe: each pack is roughly 80% shared template honed per client, and the families let later packs reuse the worked motion of earlier ones.

## The architecture: three layers

[ADR 0022](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0022-vertical-pack-architecture.md) (locked 2026-05-25) defines the pack as a three-layer model - **platform / vertical+addons / customer** - over an event-sourced state substrate. A base pack (for example `law-firm`) can carry add-ons (for example `law-firm/pi`, the personal-injury skin) that are additive on the base. The customer layer is the per-engagement authoring on top.

## A pack is five artifacts

The Pack Production Operating Model defines a pack as five concrete artifacts, all driven by the spec (build it first):

| # | Artifact | Lives in |
| --- | --- | --- |
| 1 | **Vertical spec** (the brief) | `docs/specs/verticals/<slug>.md` |
| 2 | **Technical pack** (manifest, registry, fixtures) | `operator/verticals/<slug>/` + `types.ts` |
| 3 | **N=0 proof** (hand-drafted connective artifacts) | `operator/verticals/<slug>/fixtures/` |
| 4 | **GTM surface** (landing page + intake hook) | `src/pages/packs/<slug>.astro` + allow-lists |
| 5 | **Delivery SOP** (honed per client) | `docs/templates/delivery-sops/` + spec notes |

The vertical-specific skill **bodies** are not in this list - they live in the `venturecrane/hermes-smd-overlay` repo. The manifest in `operator/verticals/<slug>/` only declares their identifiers; the bodies are the first hand-off a pack triggers. See `/admin/playbook/operator-platform` for the overlay boundary.

## The shared spine

A pack is the shared spine plus a vertical skin. Generic connective skills already exist in `operator/skills/`: `inbox-triage`, `email-reply`, `status-report-assembler`. (Four more generic skills, ar-chaser, asset-collection-follower, retainer-hours-reconciler and scope-creep-flagger, were retired 2026-09-29: no client ran them.) Reuse them; do not rebuild them. The vertical contributes its specific skills (the Law pack adds twelve) and its connector map; the spine stays shared.

## The documented verticals

Thirteen verticals are specified under `docs/specs/verticals/` (plus a `_template`). Law (personal-injury) is the one built end-to-end and the worked reference every later pack is skinned from.

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

A documented spec is a brief, not a shipped pack. A pack page goes live only when the pack behind it can actually be delivered - per the operating model, "a page goes live when the pack behind it can be delivered."

> TODO(why): which of the thirteen specs beyond `law-firm` have status past `draft` (a shipped technical pack + GTM surface) is not reconstructable from the spec directory alone - the law-firm spec itself is marked `status: draft`. The roadmap (sequence: Law done, then Tier-A insurance and veterinary, then demand-pull) is in the operating model, but live-vs-spec'd state needs a check against `operator/verticals/` manifests and `src/pages/packs/`. Checked: docs/pm/pack-production-operating-model.md §Sequence; docs/specs/verticals/ frontmatter.

## How a pack is built (the motion, proven on Law)

From the operating model:

1. Write the vertical spec from the target landscape plus light voice-of-customer.
2. Register the vertical in `types.ts` if new; copy `operator/verticals/_template/` to `operator/verticals/<slug>/` and fill the manifest and addon.
3. Hand-draft the N=0 proof against synthetic inputs - it must read as sendable.
4. Clone `src/pages/packs/law-firm.astro`, reskin the copy (compete-with-a-hire, the seat, the connective tasks, stays-in-its-lane), and add `interest=<slug>` to the intake allow-lists and to `landing-page.test.ts`.
5. Note the delivery specifics against the generic SOP.
6. Verify: `npx vitest run tests/forbidden-strings.test.ts tests/landing-page.test.ts` and `npm run typecheck`.
7. The vertical skill bodies and any `build:` adapter become the overlay hand-off.

## The repo boundary and the hand-off

Two repos build a pack:

- **ss-console (this repo):** the five artifacts minus skill bodies - manifest, registry edit, marketing surface plus intake allow-list, vertical spec, N=0 fixtures, delivery SOP notes.
- **hermes-smd-overlay:** the vertical-specific skill bodies and any `build:` connector adapter. This is the first hand-off a pack triggers, and a predecessor to a live customer (not to authoring the pack or its marketing).

Each pack is handed to an agent team with the target-landscape entry, the spec template, the Law pack as the worked reference, and the hand-off prompt. The team returns one ss-console PR and one overlay PR.

**The collision warning is real:** parallel teams must not edit the shared allow-list files (`book.astro`, `api/intake/send.ts`, `types.ts`, `landing-page.test.ts`) at the same time, or they collide. Serialize those edits or assign one integrator. See `/admin/playbook/operating-model` for how the fleet coordinates parallel work.

## The one hard rule

No fabricated client-facing claims (the Pattern A/B prohibition), no "coming soon," no fixed timeframes or dollar amounts in marketing, no copy implying pre-knowledge of a prospect's business. Marketing copy passes `tests/forbidden-strings.test.ts` and `tests/landing-page.test.ts` (register every new pack page in the latter's `readMarketingFiles()`). When a claim is not yet true, the fix is to **build faster, not to gate the build.** See `/admin/playbook/positioning-voice` for the full content policy.
