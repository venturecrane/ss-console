# Preview route pattern

Dev-only routes that let the Captain view a generated component without production auth or real data. Lives at `src/pages/design-preview/<surface-name>.astro` in the venture repo.

## Why this pattern (not middleware)

Auth-gated surfaces can't screenshot without a fake session. The elaborate version of this is to inject a fake session via middleware toggled by an env var, with fixture data loaded from `.design/product-design/fixtures/`. That's a cross-cutting infra change disguised as a skill — parallel auth path to production apps, standing security surface, and it needs reimplementing in every venture that adopts the skill.

The simpler version: **product-design produces components, not pages.** Pages stay hand-wired with data fetching. A separate dev-only route imports the component with hand-authored fixture JSON — no middleware, no session, no env var. The fixture is co-located with the preview route, ~20 lines per surface.

Trade-off: the preview renders component output identical to production (same props → same HTML), but doesn't exercise the data-loading path. That's fine — data-loading isn't what product-design is producing.

## File layout

```
src/pages/design-preview/
├── portal-home.astro           # preview route
├── portal-home.fixture.json    # fixture data co-located
├── portal-quotes-list.astro
├── portal-quotes-list.fixture.json
├── portal-quotes-detail.astro
└── portal-quotes-detail.fixture.json
```

The `design-preview` segment keeps these routes distinct from real product routes. **Production exclusion comes from the runtime `import.meta.env.DEV` guard in the route file — not from any path-based convention.** An earlier draft of this doc suggested an underscore-prefixed directory (literally `\_design-preview/`) for "production exclusion," but that was wrong: Astro's underscore-prefix rule excludes files from routing _entirely_ (dev and prod), which defeats the preview purpose. Keep the path non-underscored; the DEV guard is what prevents production serving.

## Preview route template (Astro)

<!-- prettier-ignore -->
```astro
---
// src/pages/design-preview/<surface-name>.astro
// Dev-only preview for /product-design output. Not served in production.

import fixtureData from './<surface-name>.fixture.json'
import <ComponentName> from '@/components/<area>/<ComponentName>.astro'

if (!import.meta.env.DEV) {
  return Astro.redirect('/404')
}
---

<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>[preview] <ComponentName></title>
  <link rel="stylesheet" href="/src/styles/global.css" />
</head>
<body class="bg-background">
  <<ComponentName> {...fixtureData} />
</body>
</html>
```

Notes:

- Replace `<ComponentName>`, `<area>`, `<surface-name>` placeholders with real values
- Path alias `@/` assumes `tsconfig.json` has it configured; if the venture uses a different alias (or none), use the relative path
- The `<link>` to global.css ensures `@theme` tokens are applied (Astro's dev server normally handles this for pages, but being explicit avoids surprises)
- `bg-background` matches the body background declared in most venture design systems

## Fixture template

```json
{
  "propName": "value",
  "listProp": [
    {
      "id": "fixture-1",
      "name": "Sample item",
      "status": "active",
      "createdAt": "2026-04-01T10:00:00Z"
    },
    {
      "id": "fixture-2",
      "name": "Another item (longer name to test wrapping behavior)",
      "status": "pending",
      "createdAt": "2026-03-15T14:22:00Z"
    }
  ],
  "nestedProp": {
    "field": "value",
    "count": 42
  }
}
```

Rules for good fixtures:

- Shape matches exactly what the shipped page's data fetch returns
- At least one "happy path" item AND at least one edge case (long text, empty field, extreme value)
- Synthetic but plausible — use real-looking names, dates, statuses
- For list components, include 2-3 items minimum so the component exercises its rendering loop
- For detail components, include one item with representative depth

## Viewing the preview

After generation:

```bash
# In the venture repo — use npm, pnpm, or yarn depending on lockfile
npm run dev   # or: pnpm dev, yarn dev

# In another terminal or browser
open http://localhost:<port>/design-preview/<surface-name>
```

The port is whatever the venture's dev server chose (typically 4321 for Astro, 3000 for Next.js).

## Running the validator against the preview

```bash
# Capture the rendered HTML
curl -s http://localhost:<port>/design-preview/<surface-name> > /tmp/pd-<surface>.html

# Validate
python3 ~/.agents/skills/nav-spec/validate.py \
  --file /tmp/pd-<surface>.html \
  --surface <surface-tag> \
  --archetype <archetype-tag> \
  --viewport <viewport-tag> \
  --task <task-tag> \
  --pattern <pattern-tag> \
  --spec <venture-repo>/.design/NAVIGATION.md
```

## Ventures without Astro (Phase 2+)

The pattern adapts. For Next.js:

```
src/app/design-preview/[surface]/page.tsx
src/app/design-preview/[surface]/fixture.json
```

Same idea: distinct path segment, `import.meta.env.DEV` (or Next.js equivalent) as the production exclusion, fixture co-located. The Next.js adapter will document its exact convention when it lands in Phase 2.
