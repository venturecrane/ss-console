# Generate a single surface

Invoked via `/product-design --surface <name>` or `/product-design --revise <path>`.

## Inputs

The SKILL.md pre-flight has already validated:

- You're in a venture repo with NAVIGATION.md v3+
- The UX brief covers the requested surface (or the negative-case refusal already fired)
- DESIGN.md or @theme tokens are discoverable
- An adapter exists for the venture's stack

## Steps

### 1. Resolve the surface's classification

Read NAVIGATION.md §1 (task model) and §4 (pattern catalog) to look up the surface. Extract the five-tag classification:

- `surface` (public / auth-gate / token-auth / session-auth-client / session-auth-admin)
- `archetype` (dashboard / list / detail / form / wizard / empty / error / modal / drawer / transient)
- `viewport` (mobile / desktop)
- `task` (short-name from §1 task model)
- `pattern` (name from §4 pattern catalog)

If any tag cannot be resolved from the spec, stop and ask the Captain. Do not guess.

### 2. Resolve the target path

The adapter (e.g., `adapters/astro-component.md`) specifies the target path convention. For the Astro adapter on ss-console:

- Component: `src/components/<area>/<ComponentName>.astro`
- Preview route: `src/pages/design-preview/<surface-name>.astro`
- Fixture: `src/pages/design-preview/<surface-name>.fixture.json`

Where `<area>` matches the surface class (e.g., `portal`, `admin`) and `<ComponentName>` is PascalCase derived from the surface name (`portal-quotes-detail` → `QuoteDetail`).

If the component path already exists and `--revise` was not passed, confirm with the Captain before overwriting.

### 3. Assemble the prompt

See [../references/prompt-assembly.md](../references/prompt-assembly.md) for the exact block order. Briefly:

1. Intent + surface target
2. Five-tag classification
3. Adapter template (from `adapters/<name>.md`)
4. Nav-contract block (from NAVIGATION.md §surface-class appendix, using the injection-snippet-template from nav-spec)
5. Design system tokens (from DESIGN.md or @theme extraction)
6. UX brief excerpt (the brief section covering this surface)
7. Existing component source (raw content of all `.astro`/`.tsx` files under `src/components/**`)
8. For `--revise`: the prior version of the file as context, plus the revision request

### 4. Generate

Write the component file with the Write tool.

If this is the first time generating this surface, also write the preview route and fixture file. See [../references/preview-route-pattern.md](../references/preview-route-pattern.md) for the template.

### 5. Build check

First detect the venture's package manager by lockfile:

- `pnpm-lock.yaml` present → use `pnpm build` (or `pnpm --filter <workspace> build` for monorepos)
- `yarn.lock` present → use `yarn build`
- `package-lock.json` present, or no lockfile → use `npm run build`

Run the build (pipe through `tail -30` to bound log output). If the build fails:

- Read the compile errors
- Append them to the prompt with instruction: "The build failed with these errors — regenerate to fix"
- Generate once more (iteration 2)
- If iteration 2 also fails: stop, report the failure and the remaining errors to the Captain

### 6. Structural validate (if preview route is wired)

Start the dev server (if not running) on a managed port. Curl or navigate to `http://localhost:<port>/design-preview/<surface>` to render. Extract the HTML response and save to a temp file.

```bash
python3 ~/.agents/skills/nav-spec/validate.py \
  --file /tmp/pd-<surface>.html \
  --surface <surface-tag> \
  --archetype <archetype-tag> \
  --viewport <viewport-tag> \
  --task <task-tag> \
  --pattern <pattern-tag> \
  --spec <venture-repo>/.design/NAVIGATION.md
```

Exit 0 = pass. Exit 1 = structural violations in the JSON output.

If violations: append the violation JSON to the prompt and regenerate (iteration 2). If iteration 2 still has structural violations: stop, report.

If no preview route exists for this surface yet and the Captain did not request one, skip validation. It will run after the Captain promotes the component into a real page.

### 7. Report

Tell the Captain:

- The file(s) written (component path, preview route path, fixture path if new)
- The dev-server URL to preview: `<venture's dev command> → http://localhost:<port>/design-preview/<surface>` (use the same package-manager detection as step 5: `npm run dev` / `pnpm dev` / `yarn dev`)
- Iterations used (1 or 2)
- Any violations caught and fixed between iterations
- If anything was skipped (e.g., "structural validation skipped — no preview route")

That's the deliverable. The Captain runs the venture's dev command and eyeballs it.

## Revision mode

With `--revise <path>`:

1. Read the existing file
2. Ask the Captain for the revision request (or parse it from invocation args if provided)
3. Include both in the prompt at step 3
4. Otherwise run the same 7 steps

The build-check and structural-validate gates still apply. A revision that breaks the build or fails the validator gets one retry, then surfaces.
