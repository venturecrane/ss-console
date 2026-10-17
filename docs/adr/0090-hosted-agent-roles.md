# ADR 0090: Hosted-agent roles: a packaged agent job, selected by `seat.role`

- **Status:** Accepted (2026-10-17)
- **Decider:** Captain (decision recorded on [#3264](https://github.com/venturecrane/ss-console/issues/3264), 2026-10-17)
- **Builds on:** ADR 0067 (Hosted Agent self-serve SKU), ADR 0071 (confirm ceiling and the Hosted Agent tier ladder), ADR 0084 (agent-native applications; products are surfaces on a shared seat), ADR 0011 (persona is a Hermes profile)
- **Related:** ADR 0091 (privacy by space for multi-person seats), ADR 0092 (calendar and documents for hosted agents)
- **Scope:** the concept, its home in the tree, and how a seat selects it. The first role (couples) is named; its build is tracked on #3264 and is not authorized by this record alone.

## Context

The Captain asked for a hosted agent a couple shares: a shared calendar, travel, date ideas, lists and expenses, dates to remember, a memory journal, gratitude prompts, and private check-in nudges on topics the couple authors together. The first instance is SMD's own proof of concept, `captain-mark`, on the same footing as Brokery: SMD pays, and the customer may take it on once it is proven (#3264, Captain decision 1).

The Captain framed the class in one sentence: "a hosted agent that has a dedicated predefined role", the same class as Brokery and Draft Crane, and asked that the role concept be formalized (#3264, Captain decision 3). The ask is a product, not a one-off seat: a predefined job with an onboarding process that can be sold or given to the next couple without re-authoring it.

The tree has two existing axes, and neither fits:

1. **`seat.product`** answers how a seat is sold and governed: `hosted-agent` (self-serve, light governance) or `operator` (concierge, firm buyer). ADR 0084 decision 3 keeps it meaning posture.
2. **Surfaces** (ADR 0084 decisions 2 and 4) answer what interface a seat exposes: a workbench with its own portal route, skills, connectors and layout vocabulary.

A third word is already taken. **Pack** names the vertical packs under `operator/verticals/` (`accounting`, `law-firm`, `dental`, and so on) that configure an Operator seat for an industry. ADR 0084 decision 4 records why reusing a word for a second concept is a failure this venture has paid for before.

What the couple seat needs is neither posture nor interface nor an industry. It is a job: a persona with safety rules, a skill set, default routines, a privacy model, the connections the job requires, an onboarding schema, and a seat template. Today every hosted seat is authored by hand from `operator/customers/_hosted-template/` (ADR 0067 concierge seams), so each new instance of the same job would repeat the authoring and drift.

## Decision

**1. A role is a packaged hosted-agent job.** It lives at `operator/roles/<role>/` and contains:

- `role.yaml`: the persona reference, the skills, the default routines, the privacy model (for a multi-person role, the spaces of ADR 0091), the required connections (for calendar and documents, ADR 0092), and the onboarding schema (the fields intake must collect, and which of them need every person's confirmation);
- the persona prompt and its safety rules;
- a seat template, from which a seat's `customer.yaml` is generated.

**2. A seat selects a role with `seat.role: <role>`.** `seat.product` keeps meaning posture (the couples seat is `seat.product: hosted-agent`), and ADR 0084 surfaces keep meaning interface. The three are orthogonal: posture says how the seat is sold and governed, the surface says where the person works, and the role says what job the agent does.

**3. "Pack" stays reserved for `operator/verticals/`.** A role is not a pack. A pack tailors the Operator to an industry; a role defines a job a hosted agent does for a person or a household. The word "role" here means the agent's job. It does not mean a permission role such as `product_roles` or the PRD's Principal/Operator/Compliance roles, and code and docs must not use it for those.

**4. A role carries its onboarding, and onboarding produces authored config.** Intake collects the role's onboarding schema, and its output becomes the seat's `customer.yaml` through a reviewed PR. That is the ADR 0067 "guided generator from intake fields" seam, built once per role rather than once per seat. The existing validator and provisioning path then run unchanged. Anything the role's schema marks joint is not final until every person on the seat confirms it.

**5. Couples is the first role.** `operator/roles/couples/` is built under #3264, and `captain-mark` is its first instance. Its persona rules are authored, not inferred: never relay one partner's private words to the other, answer a crisis disclosure with support resources, refuse a request to monitor the other partner, never take sides, and support the relationship without counseling it (#3264, Captain decision 5).

**6. Brokery (open-house) and Draft Crane are later conformers.** Both are the same class, but neither is migrated by this record. Each moves under `operator/roles/` when work on it next touches its packaging, so the concept is proven on one role before two live products are rearranged around it.

## Consequences

- The seat descriptor gains a `role` key and the validator learns it. A seat with no `role` behaves exactly as today, so no live seat changes when this lands.
- The ss-console validator and the overlay's mirrored contract fixture (`tests/contract/seat_gate_binding_snapshot.json` in `hermes-smd-overlay`) change in the same PR when `seat.role` is added, per the venture-repositories rule in `CLAUDE.md`.
- Each role owns its onboarding schema, so the portal's hosted intake becomes role-driven. The hosted access gate is principal-only today (`src/lib/portal/hosted-agent-access.ts:42-44`); a multi-person role widens it, and that widening is part of the role's build.
- Pricing and checkout for a role are not decided here. The first instance is SMD-paid.
- The vocabulary is now posture, surface, role and pack. The glossary and any future product ADR should use the four words for these four things only.

## Rejected alternatives

- **Make the couple seat a pack.** A pack configures the Operator for an industry; reusing the word would put two concepts under one name in the same tree (ADR 0084 decision 4).
- **Make it a surface.** A surface is an interface on a shared seat. The couple seat's distinguishing property is the job and its privacy model, which hold across every interface it uses (Telegram, email, portal).
- **Extend `seat.product` with a third value such as `couples`.** That would fuse posture with job. A role could in principle be sold under either posture, just as ADR 0084 decision 3 allows for surfaces.
- **Hand-author each seat from `_hosted-template`.** That is today's path, and it makes the second couple a copy of the first that drifts. The Captain asked for a repeatable product.
- **Migrate Brokery and Draft Crane now.** It would move two live products to prove a concept that has no instance yet. They conform later.
