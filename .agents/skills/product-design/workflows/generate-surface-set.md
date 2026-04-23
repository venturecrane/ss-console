# Generate a surface set (batch mode)

Invoked via `/product-design --set <surface-set>`. Default mode when the Captain says "run this on the entire client portal" or similar.

## Surface-set resolution

A "set" is a named group of surfaces sharing a surface class. The resolver:

1. Read NAVIGATION.md §1 (task model) for the venture
2. Match the `--set` argument against declared surface classes:
   - `portal` → all `session-auth-client` surfaces
   - `admin` → all `session-auth-admin` surfaces
   - `public` → all `public` surfaces
   - `auth` → all `auth-gate` surfaces
   - `<custom>` → match any surface class named exactly that
3. If the resolver can't find matching surfaces, stop and ask the Captain which set they mean

List the resolved surfaces to the Captain before generating:

```
Generating 7 surfaces for set `portal`:
  - /portal/           (home, dashboard archetype)
  - /portal/quotes/    (list archetype)
  - /portal/quotes/[id]/    (detail archetype)
  - /portal/invoices/       (list archetype)
  - /portal/invoices/[id]/  (detail archetype)
  - /portal/documents/      (list archetype)
  - /portal/engagement/     (detail archetype)

Proceed? (Y/n)
```

If the Captain confirms, proceed. If not, ask for a narrower set.

## Execution

**Serial, not parallel.** Max plan billing means everything runs in this session. Parallel spawning is Phase 2+ territory.

**Revise-aware by default.** For each surface in the resolved set, check whether a component file already exists at the target path. If it does, load it as prior-version context (same behavior as `--revise <path>` for a single surface) before generating. This makes `--set` the right tool for both greenfield batch generation AND identity-reset sweeps across shipped surfaces. No separate `--revise-all` flag.

For each surface in the resolved set:

1. Resolve the target component path via the adapter convention (e.g., `src/components/portal/QuoteDetail.astro`).
2. **If the file exists:** read it, include it in the prompt as "Current version of the file — reproduce the rendering intent against the updated spec (DESIGN.md / NAVIGATION.md / ux-brief)." This is the revise path.
3. **If the file does not exist:** proceed fresh, no prior-version block in the prompt.
4. Run [generate-single-surface.md](generate-single-surface.md) for that surface with the prompt assembled per step 2 or 3.
5. Capture outcome: path, iterations used, violations caught, final status (pass / build-fail / validator-fail / refused), and **whether it was a revise or a fresh generation** (affects summary display).
6. Move to the next surface — **do not stop the batch on a single failure.** Failures go into the summary; the Captain decides what to do after.

## Exception: structural dependency order

If the set contains both a detail surface (e.g., `portal-quotes-detail`) and a list surface that links to it (e.g., `portal-quotes-list`), generate the detail first. The list's prompt benefits from seeing the detail's component so it can match the card/row styling.

The resolver should order surfaces by archetype: detail → form → list → dashboard → wizard → modal. Emit the order in the pre-generation list so the Captain sees what's happening.

## Summary report

After all surfaces complete, print a table:

```
Batch complete. Summary:

| Surface                      | Mode    | Status         | Iterations | Notes |
|------------------------------|---------|----------------|------------|-------|
| portal-quotes-detail         | revise  | pass           | 1          | -     |
| portal-invoices-detail       | revise  | pass           | 2          | build-fix: missing import |
| portal-quotes-list           | revise  | validator-fail | 2          | R17 reachability violation — see report |
| portal-invoices-list         | fresh   | pass           | 1          | -     |
| portal-documents             | fresh   | pass           | 1          | -     |
| portal-home                  | revise  | refused        | -          | brief does not cover dashboard surface |
| portal-engagement            | fresh   | pass           | 1          | -     |

Preview routes live. Run the venture's dev command (`npm run dev` / `pnpm dev` / `yarn dev` — detected from lockfile) and navigate to:
  http://localhost:<port>/design-preview/portal-quotes-detail
  http://localhost:<port>/design-preview/portal-invoices-detail
  ...
```

Offer drill-down: "Show me surface X" opens the component file + its preview URL + any violation report.

## Exemplar opt-out (gate 0 test)

For the first gate 0 run on a venture with `.design/designs/` baselines, generate ONE surface in the batch WITHOUT those designs in prompt context. The prompt-assembly reference covers how to disable exemplars. After the batch, compare Captain verdicts:

- If the no-exemplar surface is comparable or better → leave exemplars out going forward, note in the plan that baselines are not needed
- If the no-exemplar surface is visibly worse → add exemplars back with a sunset date (remove when nav-spec + DESIGN.md + ux-brief are rich enough to stand alone)

## When to stop mid-batch

- If three or more surfaces in a row fail to converge in their 2-iteration budget → stop. Something is systemically wrong with the adapter, the spec, or the prompt assembly. Report to the Captain.
- If a surface refuses (brief doesn't cover it) → continue with the rest; note the refusal in the summary.
- If the dev server fails to start or the validator crashes → stop, surface to the Captain.
- If the Captain signals "stop" (direct message, Ctrl-C) → stop cleanly, emit the partial summary.

## Kill criteria — same as single-surface mode

2-iteration budget per surface. No polishing on iteration 3. Surface the diagnostic, move on or stop.
