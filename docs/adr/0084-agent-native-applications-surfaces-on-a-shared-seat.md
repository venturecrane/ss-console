# ADR 0084: Agent-native applications — products are surfaces on a shared seat

- **Status:** Accepted (2026-07-31)
- **Decider:** Captain (this session)
- **Builds on:** ADR 0037 (Operator thesis — configurable substrate, no imposed defaults), ADR 0067 (Hosted Agent self-serve SKU), ADR 0071 (confirm ceiling and the Hosted Agent tier ladder), ADR 0007 (per-customer Machine isolation), ADR 0083 (authorship model — output classes)
- **Scope:** how SMD Services builds products from here. Draft Crane is the first, and moves from the `dc` venture into `ss`.
- **Not in scope:** pricing, the layout vocabulary, and any build. This ADR records a model and a home, not a plan.

> **Amended 2026-10-31 by [ADR 0100](./0100-one-operator-core-and-authored-capabilities.md) W6.** The "Hosted Agent" this ADR builds on is not a separate product or base: there is one Operator, and what a seat is for is the capabilities it authors (ADR 0100). Read "the Hosted Agent" below as the Operator, and its self-serve terms as an offering (ADR 0067, as amended). What changed for this ADR's model: a product built here is still a surface on a shared seat, and the seat it sits on is an Operator started from a preset of the one template (`operator/customers/_template/presets/`), not from a separate hosted-agent template; the client reaches it in the Operator section of the portal. The rest of this ADR stands.

## Context

The question that opened this was whether the Hosted Agent could serve as the base for an application — specifically Draft Crane, a shelved `dc` venture product for nonfiction authors — instead of building a conventional web application with a fixed set of screens and forms.

Research over one session established four things that bear on the answer.

**The architecture is not ours to invent.** An agent that selects from a catalog of authored, deterministic layouts and never emits markup was standardized twice within six weeks: Google's **A2UI** (2025-12-15, v0.8) and **MCP Apps** (2026-01-26, the first official MCP extension, production-live in Claude, ChatGPT, VS Code, and Goose). Both landed independently on the same security argument — UI as data, not code; the agent may only reference a client-controlled catalog. Microsoft shipped the same shape as Adaptive Cards in 2017. The negative signal is equally clear: Vercel's AI SDK RSC, the most ambitious attempt to stream model-generated React, is documented as unsuitable for stable production use. The industry tried code generation and settled on catalog selection.

**Two parts are genuinely unexplored.** Every shipped catalog is component-grained (Card, Row, Button). No evidence was found of a small closed set of coarse _semantic_ layouts (a passage list with sources, a comparison, a draft awaiting approval). And the idea of a repeated question becoming a durable affordance has no LLM-era implementation with published results — Findlater & McGrenere proposed it as future work in 2004 and never tested it.

**Where the adaptive-interface literature constrains the design.** Findlater & McGrenere (CHI 2004, N=27) measured system-controlled adaptation as significantly slower than static in all three orders and rated most frustrating by 15 of 27; user-controlled adaptation matched an oracle-optimal static layout and was preferred. Findlater et al. (CHI 2009) then isolated the cause: the failure was **spatial instability**, not adaptation. Adaptation that changes emphasis while preserving position wins; adaptation that moves things loses. Two design rules follow and are binding: the system **proposes and the user confirms**, and new affordances **append in stable positions and never reorder**. A third: it will not bootstrap itself, and must be seeded by example.

Caveat on the evidence base, recorded because it will matter later: every modern generative-UI study measures one-shot pairwise preference. No longitudinal or timed study exists, and the older literature found the two measures diverge.

**Draft Crane is a solved-problem library, not just a candidate.** Its storage architecture (Drive canonical, R2 write-through cache, metadata in D1, instant local saves), its trust loop (propose, show original and proposal together, accept or discard, undoable), its chunking parameters (empirically justified, with provenance carried inside the chunk), its export path, its verbatim-extraction prompt discipline, and its documented personas all transfer. One thing does not: its retrieval was specified in ADR-009 and **never wired** — `prompt-builder.ts` and `context-window.ts` are dead code imported only by their own tests, and the path that runs takes the first few chunks of each document with no ranking at all.

## Decisions

**1. Agent-native application is the model for products from here.** The engine is an always-on agent on a per-customer Machine. Interaction is natural language across the portal and the messaging channels. The interface is a vocabulary of authored layouts the agent selects among and populates; it never invents a layout at runtime. The distinguishing property is not that there is a chat box — it is that the product does not have to anticipate a question in order to answer it. The Captain's expectation is that future products follow this model; each one remains its own decision.

**2. Products are surfaces on a shared seat, not separate Machines.** A customer has one Machine. A **surface** is a workbench enabled on that seat, carrying its own portal route, skills, connectors, and layout vocabulary. Buying a surface provisions the seat if the customer has none; buying a second surface adds to the same seat rather than standing up another Machine. This is what makes a portfolio of products economically possible — three products would otherwise mean three Machines per customer.

**3. `seat.product` keeps meaning posture; `surfaces` is the new axis.** `product` continues to answer how a seat is sold and governed — `hosted-agent` (self-serve, bring-your-own key, light governance) or `operator` (concierge, firm buyer, heavy governance). Surfaces answer what the seat can do. The two are independent: a surface could in principle be offered under either posture.

**4. "Surface" is not "pack."** `pack` already names the vertical packs under `operator/verticals/` that configure an Operator seat for an industry — same product, different domain knowledge. A surface is a different product on the same seat. Reusing the word would give the tree two things under one name, a failure this venture has paid for before.

**5. Draft Crane becomes an SS product and keeps its name.** The name, `draftcrane.com`, and the `dc-marketing` copy are assets worth carrying. `dc-console` is not written to again; it will be archived, and remains readable after archiving since it is public (ADR 0081 governs console visibility and does not cover it).

**6. Nothing here authorizes a build.** The product shape is recorded at `docs/design/draft-crane/product-shape.md` and the supporting research at `docs/research/draft-crane/`. The next step is design, not implementation.

## Consequences

- A third product means the `product_slug` surface (`src/lib/portal/billing.ts`, `offerings.ts`, `home-cards.ts`, portal routes at `/portal/products/<slug>/`) grows a case, and the seat descriptor grows a `surfaces` concept. Neither is designed here.
- Search over a customer's own material is the one capability neither side has. It is ordinary design and engineering work rather than a research risk, but it must be built rather than inherited, and the DraftCrane retrieval design must not be adopted on the strength of its ADR.
- The layout vocabulary is deliberately left unpopulated. It is expected to grow, shrink, and change over the product's life; what is fixed is only that the agent chooses from the set rather than inventing outside it. The exercise that populates it — enumerate the questions an author would really ask, categorize, distill — is scheduled work and does not block design.
- Long-running work has an answer on both sides already: DraftCrane's `analysis_jobs` async map-reduce pipeline with batch progress, and the agent's durable job runtime whose `job_status`/`job_cancel` exist but are not publicly routed (deferred in ADR 0057). The 55-second figure in the current transport is our own constant (`webhook_gate.py:604`), set below typical **MCP client** tool timeouts, and it never truncates work — on expiry it returns "still working" while the turn continues.
- Kid Expenses and Durgan Field Guide are expected to follow this model. Neither is decided here.

## Rejected

- **A separate product with its own seat.** Cleanest match to the existing pattern, but a customer wanting two products would carry two Machines and pay twice for the substrate.
- **An add-on to the Hosted Agent, sharing its subscription.** Cheapest to build, but the buyer is different: an author is trying to finish a book, not to own a personal assistant.
- **Calling the class something proprietary.** Descriptive naming matches the venture's habit (output class, seat descriptor, trust ceiling) and costs nothing to teach.
