import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // Exclude git worktrees from test discovery. `.claude/worktrees/*` are
    // checkouts of other feature branches — their test expectations drift
    // relative to main and cause spurious failures during `npm run verify`.
    // Tests that belong to this branch live in `tests/`; anything else under
    // `.claude/` is not ours to validate.
    exclude: ['**/node_modules/**', '**/dist/**', '.claude/worktrees/**'],
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
      // Thresholds are set at the 2026-04-16 baseline (rounded down 1-2 pts)
      // to act as a regression guardrail, not an aspirational target.
      // Branches/functions are the meaningful signals; lines/statements are
      // dragged down by .astro templates which v8 instruments but cannot
      // exercise in a unit-test environment.
      //
      // To raise thresholds: run `npm run test:coverage`, note new numbers,
      // bump here, and open a PR. Never set a threshold you can't currently
      // meet.
      thresholds: {
        lines: 22,
        branches: 67,
        functions: 52,
        statements: 22,
      },
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
      ],
    },
  },
})
