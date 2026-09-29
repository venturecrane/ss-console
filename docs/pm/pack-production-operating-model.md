---
title: Operator Pack Production — Operating Model
date: 2026-06-02
status: draft
captain: Scott Durgan
related-adr: 0022-vertical-pack-architecture.md, 0037-operator-thesis.md, 0020-connector-strategy.md
---

# Operator Pack Production — Operating Model

How we turn a vertical from the target landscape into a built, marketed, deliverable Operator pack, and how we hand each one to an agent team. Extracted from the **Law pack** (the first one built end-to-end); Law is the worked reference every later pack is skinned from.

The frame is [ADR 0037](../adr/0037-operator-thesis.md): the Operator competes with a **hire**, not software; incumbent systems are **connection targets, not competitors**; the moat is the harness + the guide + the memory. Packs are ~80% templates, honed per client. The hard part (positioning) is done; this is the build.

## A pack is five artifacts

| #   | Artifact                                                  | Lives in                                               | Reference                                      |
| --- | --------------------------------------------------------- | ------------------------------------------------------ | ---------------------------------------------- |
| 1   | **Vertical spec** (the brief)                             | `docs/specs/verticals/<slug>.md`                       | `law-firm.md`                                  |
| 2   | **Technical pack** (manifest + addon, registry, fixtures) | `operator/verticals/<slug>/` + `types.ts`              | `operator/verticals/law-firm/`                 |
| 3   | **N=0 proof** (hand-drafted connective artifacts)         | `operator/verticals/<slug>/fixtures/`                  | `law-firm/fixtures/n0-deliverability-proof.md` |
| 4   | **GTM surface** (landing page + intake hook)              | `src/pages/packs/<slug>.astro` + allow-lists           | `src/pages/packs/law-firm.astro`               |
| 5   | **Delivery SOP** (honed per client)                       | `docs/templates/delivery-sops/` (generic) + spec notes | `operator-pack-delivery-sop.md`                |

The spec (1) drives all the others. Build it first.

## The shared spine (reuse, don't rebuild)

Generic connective skills already exist in `operator/skills/`: `inbox-triage`, `email-reply`, `status-report-assembler`. (Four more generic skills, ar-chaser, asset-collection-follower, retainer-hours-reconciler and scope-creep-flagger, were retired 2026-09-29: no client ran them.) A pack is the spine plus a vertical skin. The vertical-specific skill **bodies** live in `hermes-smd-overlay`; the manifest only declares their identifiers.

## Repo boundary

- **ss-console (this repo):** the five artifacts above minus skill bodies. Manifest, registry edit, marketing surface + intake allow-list, vertical spec, N=0 fixtures, delivery SOP notes.
- **hermes-smd-overlay:** vertical-specific skill bodies and any `build:` connector adapter. This is the **first hand-off** a pack triggers, and a predecessor to a live customer (not to authoring the pack or its marketing).
- **One infra item, in the path not a gate:** skill-body persistence + materialization history (ADR 0022 Streams 2-3) must be real before the _first live customer_ onboards.

## The one hard rule

No fabricated client-facing claims (Pattern A/B), no "coming soon," no fixed timeframes or dollar amounts in marketing, no copy implying pre-knowledge of a prospect's business. Marketing copy passes `tests/forbidden-strings.test.ts` + `tests/landing-page.test.ts` (register every new pack page in the latter's `readMarketingFiles()`). The fix when a claim is not yet true is to build faster, not to gate the build.

## Two front doors

General consulting (Phoenix, in-person, home-services-led per `docs/decisions/vertical-selection-phase-1.md`) and the Operator packs (national, productized) are **two coexisting doors**, not one superseding the other. The home page routes general -> `OperatorIntro` -> `/operator`; packs live under `/packs/<slug>`. Do not collapse them.

## Building a pack (the motion, proven on Law)

1. Write the vertical spec from the target landscape + light VoC.
2. Register the vertical in `types.ts` if new (Law was already registered); copy `operator/verticals/_template/` to `operator/verticals/<slug>/` and fill the manifest (+ addon).
3. Hand-draft the N=0 proof against synthetic inputs; it must read as sendable.
4. Clone `src/pages/packs/law-firm.astro` to `<slug>.astro`, reskin the copy (compete-with-a-hire, the seat, the connective tasks, stays-in-its-lane), and add `interest=<slug>` to the allow-lists in `book.astro` + `api/intake/send.ts` and to `landing-page.test.ts`.
5. Note the delivery specifics against the generic SOP.
6. Verify: `npx vitest run tests/forbidden-strings.test.ts tests/landing-page.test.ts` and `npm run typecheck`.
7. The vertical skill bodies + any `build:` adapter become the overlay hand-off.

## Hand-off to an agent team

Each pack is handed to a team with: the target-landscape entry, the spec template, the Law pack as worked reference, and the hand-off prompt (`docs/templates/packs/pack-handoff-prompt.md`). The team returns an ss-console PR (artifacts 1-5 minus skill bodies) and a `hermes-smd-overlay` PR (skill bodies, adapter). Note: parallel teams must not edit the shared allow-list files (`book.astro`, `api/intake/send.ts`, `types.ts`, `landing-page.test.ts`) at the same time, or they collide; serialize those edits or assign one integrator.

## Sequence

Law (done) proves the motion. Then the rest of the target landscape, leading with the Tier-A verticals (insurance, veterinary), then the others as demand pulls. A page goes live when the pack behind it can be delivered.
