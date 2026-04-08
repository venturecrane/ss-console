import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
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
