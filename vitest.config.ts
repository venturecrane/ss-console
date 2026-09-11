/// <reference types="vitest/config" />
import { getViteConfig } from 'astro/config'
import { resolve } from 'node:path'

// Stub for Clerk Astro's virtual config module. Clerk generates this
// virtual module during the Astro build to expose runtime config to its
// components. AstroContainer-based tests run outside the build pipeline
// and can't resolve it, so SSR tests for any layout importing Clerk
// components (e.g., AdminLayout's <SignOutButton />) fail to load
// without this shim. The stub returns the SSR posture the SS app uses
// in production.
const clerkAstroConfigVirtualPlugin = {
  name: 'stub-virtual-clerk-astro-config',
  resolveId(id: string) {
    if (id === 'virtual:@clerk/astro/config') return '\0virtual:@clerk/astro/config'
    return null
  },
  load(id: string) {
    if (id === '\0virtual:@clerk/astro/config') {
      return 'export const isStaticOutput = false;'
    }
    return null
  },
}

export default getViteConfig(
  {
    plugins: [clerkAstroConfigVirtualPlugin],
    resolve: {
      alias: {
        // `cloudflare:workers` is a runtime-only module; Node can't resolve it.
        // Tests that import route handlers (which now pull `env` from it via
        // the adapter v13 pattern) get a mutable stub they can populate with
        // mock bindings. See tests/_stubs/cloudflare-workers.ts.
        'cloudflare:workers': resolve(__dirname, 'tests/_stubs/cloudflare-workers.ts'),
      },
    },
    test: {
      // Exclude git worktrees from test discovery. Worktrees under
      // `.claude/worktrees/*` and `.worktrees/*` are checkouts of other
      // feature branches — their test expectations drift relative to main
      // and cause spurious failures during `npm run verify`. Tests that
      // belong to this branch live in `tests/`; anything inside a worktree
      // dir belongs to whatever branch is checked out there.
      exclude: ['**/node_modules/**', '**/dist/**', '.claude/worktrees/**', '.worktrees/**'],
      // The crane-test-harness package imports from `node:sqlite`. The
      // vitest 1.x + vite 6 combo trips on bare `node:` imports inside
      // transformed-then-loaded modules, so we externalize the harness
      // entirely — vitest loads it via plain Node require/import without
      // running it through Vite's import-analysis pipeline. The harness
      // ships pre-compiled JS in dist/, so this is correct anyway.
      server: {
        deps: {
          external: ['@venturecrane/crane-test-harness', /node:/],
        },
      },
      coverage: {
        provider: 'v8',
        // NO THRESHOLDS. There were four here — lines 22, branches 67,
        // functions 52, statements 22 — recorded at a 2026-04-16 baseline and
        // described as "a regression guardrail". The 2026-08-23 review found
        // no workflow ran them. Probing that (2026-08-24) found something
        // worse: `@vitest/coverage-v8` was never a dependency, so
        // `npm run test:coverage` had never executed at all, and with the
        // provider installed the run reports `0/0` files — under Astro's
        // `getViteConfig` wrapper the v8 provider collects nothing, with the
        // default include and with an explicit `src/**/*.ts` one alike.
        //
        // So the thresholds were not a lapsed guardrail. They were four
        // numbers that had never been evaluated once, over an instrument that
        // measures zero files, and their presence read to every subsequent
        // reader as a coverage floor the repo was holding. A check that cannot
        // fail has measured nothing; a threshold that cannot be evaluated is
        // worse, because it also misinforms.
        //
        // They are removed rather than fixed because making v8 coverage work
        // through `getViteConfig` is its own piece of work, and leaving the
        // claim standing while it waits is the failure mode being corrected.
        // `tests/coverage-config-honesty.test.ts` fails if thresholds return
        // without a workflow that runs them.
        //
        // The `exclude` list below is retained: it is the authored knowledge
        // of what should not count, and it costs nothing to keep for whenever
        // the provider is made to work.
        exclude: [
          // Test files themselves
          'tests/**',
          '**/*.test.ts',
          '**/*.spec.ts',
          // Astro page templates — v8 instruments them but unit tests cannot
          // exercise their request/response lifecycle, leading to misleading
          // zero-coverage numbers.
          'src/pages/**/*.astro',
          'src/components/**/*.astro',
          // Generated and build artifacts
          '.astro/**',
          'dist/**',
          // DB migrations — SQL-only, nothing to instrument
          'migrations/**',
          // Config files
          '*.config.*',
          '.claude/worktrees/**',
          '.worktrees/**',
        ],
      },
    },
  },
  {
    // Keep Astro's Vite pipeline for `.astro` imports in Vitest, but avoid
    // loading the full Cloudflare adapter stack from `astro.config.mjs`.
    configFile: false,
    output: 'server',
    site: 'https://smd.services',
  }
)
