# Venture Handbook (`docs/handbook/`)

The E-Myth franchise operations manual for SMD Services. Every `.md` file here is one page,
rendered into the admin portal at **`/admin/playbook`** (collection defined in
`src/content.config.ts`; routes in `src/pages/admin/playbook/`).

The purpose: a new administrator who runs the business day to day, and is not an engineer,
can run SMD from these pages, directing AI agents and contractors for the technical work.
Everything is judged against that reader (the handbook assessment of 2026-10-21). The
Engineering Appendix serves the agents and engineers and is reached by link.

## The maintenance contract

The handbook only works if it stays true. The source of truth is the venture itself, so:

> **When a change to the venture changes what a page here says, update that page in the same
> PR.** Renamed a product, moved a surface, changed pricing, added a connector, shipped a new
> admin page, locked a new ADR - the adjacent handbook page is part of the change, not a
> follow-up.

This is why the content lives in the repo next to the code rather than in a database: the
doc is edited in the same breath as the thing it documents.

Rough "if you change X, update Y":

| You changed... | Update... |
|---|---|
| An ADR / a locked decision | `docs/adr/index.md` (its entry is the summary), then regenerate `adr-index.md` with `npm run handbook:adr-index -- --write`; update `decision-stack.md` and the topic page. `tests/handbook-integrity.test.ts` blocks merge while `adr-index.md` and the ADR corpus disagree |
| Pricing, rate ladder, payment terms | `pricing-economics.md` |
| The Operator architecture / ceilings / memory | `operator-platform.md`, `autonomy-governance.md`, `knowledge-memory.md` |
| A connector or delivery channel | `connectors-channels.md` |
| A seat folder in `operator/customers/` (added, retired, or its `seat.kind` / `seat.product` / `seat.role` / `vertical` changed) | Regenerate the roster block in `clients-and-commitments.md` with `npm run handbook:roster -- --write`; `tests/handbook-integrity.test.ts` blocks merge until it matches the tree |
| A signal sent to team@ (a new alert, a changed subject, a new scheduled control) | `administrators-week.md` and, if it needs a response, `when-something-goes-wrong.md` |
| Who may approve or decide something | `who-does-what.md` |
| A tool in `operator/connectors/<name>/manifest.toml` (added, removed, reclassified) or a new `*_tools.py` module | Regenerate the inventory block in `connectors-channels.md` with `npm run handbook:connectors -- --write`, and update the prose around it in the same PR; `tests/handbook-integrity.test.ts` blocks merge until the block matches the tree |
| An admin surface | `admin-console.md`, and `customer-lifecycle.md` if the motion changed |
| A consulting portal surface | `client-portal.md` |
| A client-facing Operator surface | `operator-console.md` |
| The deploy or secrets flow | `deployment-release.md` / `secrets-access.md` |
| The repo layout or a new top-level dir | `repository-map.md`, `architecture-map.md`, `docs-map.md` |
| A route under `src/pages/api` (added, moved, retired, re-gated) | Regenerate `docs/api/README.md` with `npx tsx scripts/api-inventory.ts --write`; `tests/api-inventory.test.ts` blocks merge until the page matches the tree |
| A migration | `data-model.md` (the page cites `migrations/` as a source, so `npm run handbook:drift` flags it until the page catches up) |
| A module that emits a manifest, completion, wired map, or done flag | Answer `docs/doctrine/report-is-a-probe-checklist.md` in the PR body (Law 14) |

## Authoring rules

1. **Frontmatter** (validated by `src/content.config.ts`):
   ```yaml
   ---
   title: <Page Title>
   section: running | business | product | engineering
   order: <number within the section>
   owner: captain | administrator | engineering
   review_every: <days: running 30, product 90, engineering 90, business 180>
   last_reviewed: "YYYY-MM-DD"   # only after a deliberate check of the body against its sources
   summary: <one-line lede>
   sources:                       # optional; the canonical docs this page mirrors
     - label: <short label>
       href: <github blob URL or repo path>
   status: <optional, e.g. "draft">
   ---
   ```
   `title`, `section`, `owner` and `review_every` are required (the schema renders a bad
   value as unknown; `tests/handbook-integrity.test.ts` is the gate). The slug is the
   filename (`overview.md` -> `/admin/playbook/overview`).

   **`last_reviewed` means someone checked the page against its sources, not that someone
   edited it.** Set it only after reading the body against every cited source and fixing what
   disagreed. A page without it renders "Not yet reviewed against its sources", which is the
   honest state; a date nobody earned is not. Each page renders its owner, its review date
   and an overdue badge, computed when the page is read. Being overdue never fails the build.

   `owner` is the role answerable for the page staying true. Running, business and product
   pages are `captain` until an administrator is named, then `administrator` for the
   Running SMD pages; the appendix is `engineering`.

   `status` is a visible badge for a known gap, such as `Rewrite pending`.

2. **Body starts at `##`.** The page `<h1>` is the title, rendered by the route.

3. **No em dashes.** Use spaced hyphens or restructure. House style.

4. **Cite, do not invent.** Ground claims in an ADR or file path. If you cannot reconstruct
   the *why* of something, write `> TODO(why): ...` rather than guessing. A visible gap is
   correct; fabrication is a P0 violation (see the no-fabrication policy in `CLAUDE.md`).

5. **Cross-link** with `/admin/playbook/<slug>`. Each page owns specific facts; link rather
   than re-narrate.

6. **Secrets/DR pages** (`secrets-access.md`, `disaster-recovery.md`) carry pointers and
   procedures only - never secret values or working recovery commands.

## Sections

`running` (Running SMD) / `business` (The Business) / `product` (The Product) /
`engineering` (Engineering Appendix). The first three are written for the administrator; the
appendix for engineers and agents, so a page's audience follows from its section. Keys,
labels, blurbs and display order are defined once in `src/lib/handbook/sections.ts`, read by
the schema, the sidebar, the landing page, the integrity test and the ADR-index generator.
Pages read in that order (section, then `order`), and each page links to the one before and
after it.

`README.md` is excluded from the rendered collection (it is this guide, not a page).

## How the handbook is kept current

Four mechanisms, in order of strength:

1. **The maintenance contract (primary).** Update the adjacent page in the same PR that
   changes the venture. This is the only mechanism that keeps the *meaning* true; the others
   only catch what it misses.

2. **The structural gate (hard, blocks merge).** `tests/handbook-integrity.test.ts` runs in
   `npm run verify` and CI. It fails the build on:
   - malformed frontmatter, including a missing `owner` or `review_every` or an impossible
     `last_reviewed` date;
   - a dead `/admin/playbook/<slug>` cross-link, or any link the renderer cannot resolve (a
     relative `./` or `../` path);
   - a `sources` entry that is not a URL or a path in this repo, or a cited same-repo source
     file that no longer exists;
   - two pages on one `(section, order)` slot, or an em dash;
   - a new `TODO(why)`. They only go down: resolve the gap, or write what is known as
     content. When one is resolved, lower `TODO_CEILING` in the test in the same PR.

3. **Generated and completeness-checked content (hard).** The facts that drifted were the
   lists of what exists, so the tree holds them to the pages:
   - `adr-index.md` must equal what `docs/adr/` renders (`npm run handbook:adr-index`);
   - the connector inventory in `connectors-channels.md` (`npm run handbook:connectors`);
   - the seat roster in `clients-and-commitments.md` (`npm run handbook:roster`);
   - every scheduled job in `scheduled-jobs.md` (`npm run handbook:inventories`);
   - `disaster-recovery.md` must name every runbook, `repository-map.md` every deployed
     worker, and `data-model.md` every live table. These are not regenerated, because the
     pages group them by hand; `npm run handbook:inventories` names what is missing.

4. **The weekly review (advisory, read every Monday).** `npm run handbook:drift`
   (`scripts/handbook-review.ts`) keys on `last_reviewed`, not on commit times, so editing a
   page never hides that its sources moved. It reports pages whose cited sources changed after
   their review, pages past their review window, and the never-reviewed backlog. "After the
   review" means a commit after the one that last changed the page's `last_reviewed` line, so
   a review is recorded by changing that date; a second review on the same calendar day changes
   nothing git can see and is recorded at the next review.
   `.github/workflows/handbook-review.yml` runs it every Monday and keeps one rolling issue,
   "Handbook: pages due for review", closed when nothing is due.
