# Venture Handbook (`docs/handbook/`)

The E-Myth franchise operations manual for SMD Services. Every `.md` file here is one page,
rendered into the admin portal at **`/admin/playbook`** (collection defined in
`src/content.config.ts`; routes in `src/pages/admin/playbook/`).

The purpose: a newcomer with zero context can run, build, and grow this venture from these
pages alone - what it is, why it exists, how it works, and where everything lives.

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
| An ADR / a locked decision | `decision-stack.md`, `adr-index.md`, and the topic page |
| Pricing, rate ladder, payment terms | `pricing-economics.md` |
| The Operator architecture / ceilings / memory | `operator-platform.md`, `autonomy-governance.md`, `knowledge-memory.md` |
| A connector or delivery channel | `connectors-channels.md` |
| An admin surface | `admin-console.md`, and `customer-lifecycle.md` if the motion changed |
| A consulting portal surface | `client-portal.md` |
| A client-facing Operator surface | `operator-console.md` |
| The deploy or secrets flow | `deployment-release.md` / `secrets-access.md` |
| The repo layout or a new top-level dir | `repository-map.md`, `architecture-map.md`, `docs-map.md` |
| A module that emits a manifest, completion, wired map, or done flag | Answer `docs/doctrine/report-is-a-probe-checklist.md` in the PR body (Law 14) |

## Authoring rules

1. **Frontmatter** (validated by `src/content.config.ts`):
   ```yaml
   ---
   title: <Page Title>
   section: business | product | system | operations | reference
   order: <number within the section>
   summary: <one-line lede>
   sources:                       # optional; the canonical docs this page mirrors
     - label: <short label>
       href: <github blob URL or repo path>
   status: <optional, e.g. "draft">
   ---
   ```
   `title` and `section` are required; the rest are optional. The slug is the filename
   (`overview.md` -> `/admin/playbook/overview`).

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

`business` (The Business) / `product` (The Product) / `system` (The System - where
everything lives) / `operations` (How We Work) / `reference` (Reference). Section display
order and labels are set in `_HandbookSidebar.astro` and `index.astro`.

`README.md` is excluded from the rendered collection (it is this guide, not a page).

## How the handbook is kept current

Three mechanisms, in order of strength:

1. **The maintenance contract (primary).** Update the adjacent page in the same PR that
   changes the venture. This is the only mechanism that keeps the *meaning* true; the other
   two only catch what it misses.

2. **The structural gate (hard, blocks merge).** `tests/handbook-integrity.test.ts` runs in
   `npm run verify` and CI. It fails the build on malformed frontmatter, a dead
   `/admin/playbook/<slug>` cross-link, a cited same-repo source file that no longer exists
   (the check that forces a doc update when a source is moved, renamed, or deleted), two pages
   colliding on one `(section, order)` slot, or an em dash. Deterministic, so it is safe to
   block on.

3. **The drift radar (advisory).** `npm run handbook:drift` compares each page's last-commit
   time to its cited sources' last-commit times and reports pages whose sources changed after
   them. Advisory, not a gate - a source edit does not always change what the page says - so a
   human reads the report and decides. Run it before a handbook review pass.
