import { defineCollection } from 'astro:content'
import { glob } from 'astro/loaders'
import { z } from 'zod'
import { HANDBOOK_OWNERS, HANDBOOK_SECTION_KEYS } from './lib/handbook/sections'

/**
 * Venture Handbook collection — the E-Myth franchise operations manual.
 *
 * Source of truth is markdown under docs/handbook/ (next to the rest of the
 * repo docs, so an agent updates the adjacent page in the same PR that changes
 * the venture). Rendered into the admin portal at /admin/playbook by
 * src/pages/admin/playbook/index.astro + [...slug].astro.
 *
 * The glob loader's `base` points outside src/content/ on purpose — that is the
 * documented usage. README.md is excluded (it is the authoring guide, not a
 * page). Slugs are the filename (flat tree): docs/handbook/overview.md →
 * /admin/playbook/overview.
 *
 * Schema is intentionally minimal and forgiving so a single mis-authored
 * frontmatter field never reds the build:
 *   - title, section   required (drive the page heading and sidebar grouping)
 *   - order            optional (sidebar sort within a section; defaults last)
 *   - summary          optional (one-line lede under the page title)
 *   - sources[]        optional ({ label, href }; rendered as the Sources footer
 *                      and the hook a future drift-check keys off)
 *   - status           optional (e.g. "draft" — surfaced as a badge)
 *   - owner, last_reviewed, review_every
 *                      freshness metadata, rendered on every page. Each falls back
 *                      to undefined on a malformed value instead of failing the
 *                      build; tests/handbook-integrity.test.ts is the gate that
 *                      requires owner and review_every and checks the date format.
 *
 * Sections come from src/lib/handbook/sections.ts, the one definition shared
 * with the sidebar, landing page, integrity test and ADR-index generator.
 */
const handbook = defineCollection({
  loader: glob({ base: './docs/handbook', pattern: ['**/*.md', '!**/README.md'] }),
  schema: z.object({
    title: z.string(),
    section: z.enum(HANDBOOK_SECTION_KEYS),
    order: z.number().default(999),
    summary: z.string().optional(),
    sources: z
      .array(
        z.object({
          label: z.string(),
          href: z.string(),
        })
      )
      .optional(),
    status: z.string().optional(),
    owner: z.enum(HANDBOOK_OWNERS).optional().catch(undefined),
    last_reviewed: z.coerce.date().optional().catch(undefined),
    review_every: z.number().int().positive().optional().catch(undefined),
  }),
})

export const collections = { handbook }
