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
  },
})
