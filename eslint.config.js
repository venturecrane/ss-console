// ss-console ESLint config — implements the Venture Crane portfolio coding standard.
// Source of truth for the rule set: docs/instructions/coding-standards.md in
// venturecrane/crane-console. When the standard changes, update this file to match.
//
// This is a self-contained config — no @venturecrane/eslint-config dependency,
// no private registry auth needed in CI. Drift between ventures is preferred over
// the cross-repo coupling a shared package would introduce.

import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import globals from 'globals'
import importPlugin from 'eslint-plugin-import-x'
import eslintPluginAstro from 'eslint-plugin-astro'
import { fileURLToPath } from 'node:url'
import { dirname } from 'node:path'

const tsconfigRootDir = dirname(fileURLToPath(import.meta.url))

const STRUCTURAL_RULES = {
  'max-lines': ['error', { max: 500, skipBlankLines: true, skipComments: true }],
  'max-lines-per-function': [
    'error',
    { max: 75, skipBlankLines: true, skipComments: true, IIFEs: true },
  ],
  complexity: ['error', { max: 15 }],
  'max-depth': ['error', 4],
  'max-params': ['error', 5],
}

const TYPE_SAFETY_RULES = {
  '@typescript-eslint/no-explicit-any': 'error',
  '@typescript-eslint/no-unused-vars': [
    'error',
    {
      argsIgnorePattern: '^_',
      varsIgnorePattern: '^_',
      caughtErrorsIgnorePattern: '.*',
      ignoreRestSiblings: true,
    },
  ],
  '@typescript-eslint/no-require-imports': 'error',
  'no-useless-assignment': 'error',
  'preserve-caught-error': 'error',
}

const TYPE_AWARE_ERROR_RULES = {
  '@typescript-eslint/no-floating-promises': 'error',
  '@typescript-eslint/no-misused-promises': 'error',
  '@typescript-eslint/await-thenable': 'error',
  '@typescript-eslint/switch-exhaustiveness-check': [
    'error',
    { considerDefaultExhaustiveForUnions: true },
  ],
}

// Sequenced at warn until Zod boundary validation rolls out portfolio-wide.
// Warn-tier is capped: `npm run lint` runs with `--max-warnings 13`, the
// 2026-08-09 baseline. That number only ever ratchets DOWN. Raising it to
// admit new warnings defeats the cap; fix the boundary instead.
//
// The count assumes `.astro/types.d.ts` exists. Without it the Clerk-derived
// types go unresolved and the same tree reports 75. `npm run verify` and
// verify.yml both run `astro check` (which syncs those types) before lint, so
// 13 is the number both see. On a fresh clone, run `npx astro sync` first.
const TYPE_AWARE_WARN_RULES = {
  '@typescript-eslint/no-unsafe-assignment': 'warn',
  '@typescript-eslint/no-unsafe-member-access': 'warn',
  '@typescript-eslint/no-unsafe-call': 'warn',
  '@typescript-eslint/no-unsafe-return': 'warn',
  '@typescript-eslint/no-unsafe-argument': 'warn',
  '@typescript-eslint/restrict-template-expressions': [
    'warn',
    { allowNumber: true, allowBoolean: true, allowNullish: true },
  ],
}

const HYGIENE_RULES = {
  eqeqeq: ['error', 'always', { null: 'ignore' }],
  'no-throw-literal': 'error',
  'import-x/no-default-export': 'error',
}

const TEST_FILE_OVERRIDES = {
  '@typescript-eslint/no-explicit-any': 'off',
  '@typescript-eslint/no-unused-vars': 'off',
  '@typescript-eslint/no-require-imports': 'off',
  'preserve-caught-error': 'off',
  'max-lines': 'off',
  'max-lines-per-function': 'off',
  complexity: 'off',
  'max-depth': 'off',
  'max-params': 'off',
  '@typescript-eslint/no-floating-promises': 'off',
  '@typescript-eslint/no-misused-promises': 'off',
  '@typescript-eslint/await-thenable': 'off',
  '@typescript-eslint/no-unsafe-assignment': 'off',
  '@typescript-eslint/no-unsafe-member-access': 'off',
  '@typescript-eslint/no-unsafe-call': 'off',
  '@typescript-eslint/no-unsafe-return': 'off',
  '@typescript-eslint/no-unsafe-argument': 'off',
  '@typescript-eslint/restrict-template-expressions': 'off',
  // Test stubs often declare async to satisfy an interface signature without
  // needing a real await expression.
  '@typescript-eslint/require-await': 'off',
  '@typescript-eslint/no-base-to-string': 'off',
  // Vitest spy/mock patterns regularly pass bound methods as arguments.
  '@typescript-eslint/unbound-method': 'off',
}

const DEFAULT_EXPORT_ALLOW_PATTERNS = [
  // TypeScript declaration files legitimately use `export default` in module
  // augmentations (e.g. WASM module imports in env.d.ts).
  '**/*.d.ts',
  '**/vitest.config.{ts,js,mjs}',
  '**/playwright.config.{ts,js,mjs}',
  '**/astro.config.{ts,js,mjs}',
  '**/next.config.{ts,js,mjs}',
  '**/tailwind.config.{ts,js,mjs}',
  '**/postcss.config.{ts,js,mjs}',
  '**/eslint.config.{ts,js,mjs}',
  '**/sentry.{client,server,edge}.config.{ts,js,mjs}',
  '**/workers/*/src/index.ts',
  '**/page.{tsx,jsx,ts,js}',
  '**/layout.{tsx,jsx,ts,js}',
  '**/loading.{tsx,jsx,ts,js}',
  '**/error.{tsx,jsx,ts,js}',
  '**/not-found.{tsx,jsx,ts,js}',
  '**/route.{ts,js}',
  '**/template.{tsx,jsx}',
  '**/default.{tsx,jsx}',
  '**/middleware.{ts,js}',
  '**/*.astro',
]

export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  ...eslintPluginAstro.configs.recommended,
  {
    languageOptions: {
      globals: { ...globals.node, ...globals.browser },
      parserOptions: { projectService: true, tsconfigRootDir },
    },
    plugins: { 'import-x': importPlugin },
    rules: {
      ...STRUCTURAL_RULES,
      ...TYPE_SAFETY_RULES,
      ...TYPE_AWARE_ERROR_RULES,
      ...TYPE_AWARE_WARN_RULES,
      ...HYGIENE_RULES,
    },
  },
  {
    files: [
      '**/*.test.ts',
      '**/*.test.tsx',
      '**/*.spec.ts',
      '**/*.spec.tsx',
      '**/test/**/*.ts',
      '**/test/**/*.tsx',
      '**/__tests__/**/*.ts',
      '**/__tests__/**/*.tsx',
      '**/__fixtures__/**/*.ts',
    ],
    rules: TEST_FILE_OVERRIDES,
  },
  {
    files: DEFAULT_EXPORT_ALLOW_PATTERNS,
    rules: { 'import-x/no-default-export': 'off' },
  },
  // The API JSON helper must be imported from src/lib/api/helpers, never
  // re-declared locally. Local copies drifted — several inverted the canonical
  // (status, data) arg order, a latent copy-paste bug hazard (2026-06-30 code
  // review). Scoped to src/ so the workers/* subprojects (separate tsconfig,
  // cannot import the shared helper) are exempt; helpers.ts itself is ignored.
  {
    files: ['src/**/*.ts', 'src/**/*.tsx'],
    ignores: ['src/lib/api/helpers.ts', 'src/lib/operator/machine-url.ts'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector:
            "FunctionDeclaration[id.name='jsonResponse'], VariableDeclarator[id.name='jsonResponse']",
          message:
            'Import jsonResponse from src/lib/api/helpers instead of re-declaring it — local copies drift (several inverted the canonical (status, data) arg order).',
        },
        // The 2026-09-10 review found byte-identical private copies of these
        // in up to eight files each. One home per helper: helpers.ts for the
        // small guards, machine-url.ts for the function that addresses the
        // live Machine. jsonError was seven one-line aliases of errorResponse.
        {
          selector:
            'FunctionDeclaration[id.name=/^(escapeHtml|trimString|isRecord|isValidEmail)$/], VariableDeclarator[id.name=/^(escapeHtml|trimString|isRecord|isValidEmail)$/]',
          message:
            'Import this helper from src/lib/api/helpers instead of re-declaring it — the 2026-09-10 review found up to eight byte-identical private copies per helper.',
        },
        {
          selector:
            "FunctionDeclaration[id.name='machineBaseUrl'], VariableDeclarator[id.name='machineBaseUrl']",
          message:
            'Import machineBaseUrl from src/lib/operator/machine-url — it addresses the live Operator Machine and had five identical copies (2026-09-10 review).',
        },
        {
          selector:
            "FunctionDeclaration[id.name='jsonError'], VariableDeclarator[id.name='jsonError']",
          message:
            'Call errorResponse(status, message) from src/lib/api/helpers directly — jsonError was a one-line alias of it in seven files (2026-09-10 review).',
        },
      ],
    },
  },
  // API routes must build JSON responses through the shared helpers so error
  // body shape and Content-Type stay uniform (code review 2026-07-02 §1.7).
  // This block overrides the src/** no-restricted-syntax config for API files,
  // so it re-declares the jsonResponse-redeclaration guard above.
  {
    files: ['src/pages/api/**/*.ts'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector:
            "FunctionDeclaration[id.name='jsonResponse'], VariableDeclarator[id.name='jsonResponse']",
          message:
            'Import jsonResponse from src/lib/api/helpers instead of re-declaring it — local copies drift (several inverted the canonical (status, data) arg order).',
        },
        {
          selector:
            'FunctionDeclaration[id.name=/^(escapeHtml|trimString|isRecord|isValidEmail)$/], VariableDeclarator[id.name=/^(escapeHtml|trimString|isRecord|isValidEmail)$/]',
          message:
            'Import this helper from src/lib/api/helpers instead of re-declaring it — the 2026-09-10 review found up to eight byte-identical private copies per helper.',
        },
        {
          selector:
            "FunctionDeclaration[id.name='machineBaseUrl'], VariableDeclarator[id.name='machineBaseUrl']",
          message:
            'Import machineBaseUrl from src/lib/operator/machine-url — it addresses the live Operator Machine and had five identical copies (2026-09-10 review).',
        },
        {
          selector:
            "FunctionDeclaration[id.name='jsonError'], VariableDeclarator[id.name='jsonError']",
          message:
            'Call errorResponse(status, message) from src/lib/api/helpers directly — jsonError was a one-line alias of it in seven files (2026-09-10 review).',
        },
        // Error bodies are built only by errorResponse(status, code, message?,
        // extra?), so `error` is always a code from src/lib/api/errors.ts and
        // `message` is always the prose. Before 2026-09-11 the same key carried
        // 36 codes and 33 sentences (review 2026-09-10, Architecture 5);
        // tests/api-error-vocabulary.test.ts pins the catalog membership, this
        // rule stops a hand-built `{ error: ... }` body from reappearing.
        {
          selector:
            "CallExpression[callee.name='jsonResponse'] > ObjectExpression > Property[key.name='error']",
          message:
            'Build error bodies with errorResponse(status, code, message?, extra?) from src/lib/api/helpers; `error` must be a code from src/lib/api/errors.ts and the prose goes in `message` (2026-09-11 vocabulary).',
        },
        {
          selector:
            "NewExpression[callee.name='Response'] CallExpression[callee.object.name='JSON'][callee.property.name='stringify']",
          message:
            'Use jsonResponse(status, data) or errorResponse(status, message) from src/lib/api/helpers instead of new Response(JSON.stringify(...)) — keeps API error and JSON bodies and their Content-Type uniform (code review 2026-07-02 §1.7). If you genuinely need extra headers, build on the helper and set them, or add a scoped eslint-disable with a reason.',
        },
      ],
    },
  },
  // astro-eslint-parser does not support projectService; type-aware rules that
  // require a full TS program crash or produce false positives in .astro files.
  // Structural and type-safety rules still apply.
  {
    files: ['**/*.astro'],
    rules: {
      '@typescript-eslint/no-floating-promises': 'off',
      '@typescript-eslint/no-misused-promises': 'off',
      '@typescript-eslint/await-thenable': 'off',
      '@typescript-eslint/switch-exhaustiveness-check': 'off',
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
      '@typescript-eslint/no-unsafe-call': 'off',
      '@typescript-eslint/no-unsafe-return': 'off',
      '@typescript-eslint/no-unsafe-argument': 'off',
      '@typescript-eslint/restrict-template-expressions': 'off',
      '@typescript-eslint/no-base-to-string': 'off',
      '@typescript-eslint/no-unnecessary-type-assertion': 'off',
    },
  },
  {
    files: ['**/*.js', '**/*.cjs', '**/*.mjs'],
    rules: { '@typescript-eslint/no-require-imports': 'off' },
  },
  // Dev-only component galleries are HTML-heavy template pages with no logic ceiling.
  // Line limits are a proxy for complexity; markup repetition is not complexity.
  {
    files: ['**/pages/dev/**/*.astro'],
    rules: { 'max-lines': 'off' },
  },
  // The customer.yaml contract is one authored schema: every accepted value
  // set, every section interface, and the doc comment that says what each
  // field means to the seat. Splitting it by section would scatter one
  // contract across files with no cohesion gained, and two thirds of its raw
  // lines are those doc comments, which the ceiling already skips. It carries
  // no logic beyond a handful of one-line predicates; the line ceiling is a
  // complexity proxy, and a type module has none to bound (review 2026-09-10,
  // Architecture 3: the file sat at 497 logical lines, met by trimming).
  {
    files: ['src/lib/operator/customer-yaml/types.ts'],
    rules: { 'max-lines': 'off' },
  },
  {
    ignores: [
      '**/dist/**',
      '**/node_modules/**',
      '**/.wrangler/**',
      '**/.astro/**',
      '**/.claude/**',
      // Stale git worktrees from a pre-`.claude/worktrees/` tooling
      // convention. Real registered worktrees (`git worktree list`) but
      // not committed; pre-push verify failed locally on dirty checkouts
      // that have nothing to do with the branch being pushed. Treat the
      // same as `.claude/**` above.
      '**/.worktrees/**',
      'coverage/**',
      'scripts/**',
      // Python virtualenvs. `operator/`'s own test instructions create one
      // (`uv venv .venv` under operator/), it is gitignored, and site-packages
      // ships .js assets (matplotlib's web backend) that projectService cannot
      // resolve — so following the documented Python setup made `npm run
      // verify` fail with three parsing errors that had nothing to do with the
      // branch. Added 2026-09-10.
      '**/.venv/**',
      // Worker vitest configs are excluded from the root tsconfig; projectService
      // cannot resolve them. Each worker has its own tsconfig that covers these.
      'workers/*/vitest.config.ts',
      // Public JS files served directly to browsers — not TypeScript-compiled,
      // type-safety rules produce false positives on the raw DOM/gtag calls.
      'public/js/**',
    ],
  }
)
