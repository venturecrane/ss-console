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
import importPlugin, { createNodeResolver } from 'eslint-plugin-import-x'
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
// Warn-tier is capped at zero: `npm run lint` runs with `--max-warnings 0`
// (the 2026-08-09 baseline of 13 ratcheted to 0 by 2026-09-25). A warning is
// a failure; fix the boundary rather than raising the cap.
//
// The count assumes `.astro/types.d.ts` exists. Without it the Clerk-derived
// types go unresolved and the tree reports warnings that are not real.
// `npm run verify` and verify.yml both run `astro check` (which syncs those
// types) before lint. On a fresh clone, run `npx astro sync` first.
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

// A value-import cycle between two modules. ES modules tolerate one while every
// use sits inside a function, which is how the one the 2026-09-25 review found
// (customer-yaml sections-scope <-> sections-staff-send-as) went unnoticed;
// the first top-level use turns it into an undefined at load time. `import
// type` edges are erased and ignored. Scoped to src/lib, where the layers are:
// over all of src/ it cost 8.5% of rule time (`TIMING=all npx eslint src`,
// 2026-09-25), scoped 5.5% (1.2 s of 21.9 s), and a page or component is a
// leaf nothing imports back.
const IMPORT_CYCLE_RULES = { 'import-x/no-cycle': ['error', { ignoreExternal: true }] }

const IMPORT_CYCLE_FILES = ['src/lib/**/*.ts']

// Resolve relative TypeScript imports the way the bundler does: extensionless
// specifiers find .ts/.tsx, and a `.js` specifier finds its .ts source.
const IMPORT_RESOLVER = createNodeResolver({
  extensions: ['.ts', '.tsx', '.mjs', '.js', '.json'],
  extensionAlias: { '.js': ['.ts', '.js'] },
})

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

/**
 * Declarations that have one home, and the parse-then-cast pattern. Each
 * entry is a `no-restricted-syntax` guard; `name` ties it to its home module
 * in GUARD_HOMES, which is exempt from that guard alone.
 *
 * Helpers: the 2026-06-30 and 2026-09-10 reviews found byte-identical private
 * copies of each (up to eight per helper; several jsonResponse copies had
 * inverted the (status, data) argument order). The 2026-09-25 review added
 * the Stripe request headers (three copies) and the signed-token codec (four).
 *
 * Casts: `JSON.parse(x) as T` and `(await r.json()) as T` assert a shape
 * nothing checked. The tree was brought to zero on 2026-09-25 (review, Code
 * Quality 2): parse into `unknown` and narrow field by field, as
 * src/lib/db/quote-content.ts and src/lib/api/helpers.ts parseJsonRecord do.
 * `as unknown` is allowed; it is the first step of that narrowing.
 */
/** @typedef {{ name: string, selector: string, message: string }} Guard */

/**
 * @param {string[]} names
 * @param {string} message
 * @returns {Guard}
 */
const declaredOnce = (names, message) => {
  const pattern = `/^(${names.join('|')})$/`
  return {
    name: names.join(','),
    selector: `FunctionDeclaration[id.name=${pattern}], VariableDeclarator[id.name=${pattern}]`,
    message,
  }
}

/** @type {Guard[]} */
const SRC_GUARDS = [
  declaredOnce(
    ['jsonResponse'],
    'Import jsonResponse from src/lib/api/helpers instead of re-declaring it — local copies drift (several inverted the canonical (status, data) arg order).'
  ),
  declaredOnce(
    ['escapeHtml', 'trimString', 'isRecord', 'isValidEmail'],
    'Import this helper from src/lib/api/helpers instead of re-declaring it — the 2026-09-10 review found up to eight byte-identical private copies per helper.'
  ),
  declaredOnce(
    ['machineBaseUrl'],
    'Import machineBaseUrl from src/lib/operator/machine-url — it addresses the live Operator Machine and had five identical copies (2026-09-10 review).'
  ),
  declaredOnce(
    ['jsonError'],
    'Call errorResponse(status, message) from src/lib/api/helpers directly — jsonError was a one-line alias of it in seven files (2026-09-10 review).'
  ),
  declaredOnce(
    ['stripeHeaders', 'STRIPE_API_BASE'],
    'Import stripeHeaders and STRIPE_API_BASE from src/lib/stripe/client: there were three byte-identical copies until the 2026-09-25 review.'
  ),
  declaredOnce(
    [
      'base64UrlEncode',
      'base64UrlDecode',
      'importSigningKey',
      'signPayload',
      'verifySignedPayload',
    ],
    'Import the signed-token codec from src/lib/security/signed-payload: OAuth state, booking links and assessment sessions each hand-rolled it until the 2026-09-25 review.'
  ),
  {
    name: 'json-cast',
    selector: [
      ":matches(TSAsExpression, TSTypeAssertion)[expression.callee.object.name='JSON'][expression.callee.property.name='parse']:not([typeAnnotation.type='TSUnknownKeyword'])",
      ":matches(TSAsExpression, TSTypeAssertion)[expression.type='AwaitExpression'][expression.argument.callee.property.name='json']:not([typeAnnotation.type='TSUnknownKeyword'])",
      "TSAsExpression[expression.type='TSAsExpression'][expression.expression.callee.property.name=/^(parse|json)$/]",
      "TSAsExpression[expression.type='TSAsExpression'][expression.expression.argument.callee.property.name='json']",
      "CallExpression[callee.property.name='json'][typeArguments]",
    ].join(', '),
    message:
      'Parse external or stored JSON into `unknown` and narrow it field by field (parseJsonRecord / isRecord in src/lib/api/helpers, parseLineItems in src/lib/db/quote-content); never cast it to a type (review 2026-09-25, Code Quality 2).',
  },
]

/**
 * The rule's options take only selector + message; `name` is ours.
 * @param {Guard[]} guards
 */
const guardOptions = (guards) => guards.map(({ selector, message }) => ({ selector, message }))

/**
 * Each guard's home module, exempt from that guard only.
 * @type {Record<string, string[]>}
 */
const GUARD_HOMES = {
  'src/lib/api/helpers.ts': ['jsonResponse', 'escapeHtml,trimString,isRecord,isValidEmail'],
  'src/lib/operator/machine-url.ts': ['machineBaseUrl'],
  'src/lib/stripe/client.ts': ['stripeHeaders,STRIPE_API_BASE'],
  'src/lib/security/signed-payload.ts': [
    'base64UrlEncode,base64UrlDecode,importSigningKey,signPayload,verifySignedPayload',
  ],
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
  {
    files: IMPORT_CYCLE_FILES,
    ignores: ['**/*.test.ts'],
    settings: {
      'import-x/resolver-next': [IMPORT_RESOLVER],
      // Without these the rule resolves a .ts import and then declines to
      // parse it, so it walks no edges and passes every cycle (probed).
      'import-x/extensions': ['.ts', '.tsx', '.mjs', '.js'],
      'import-x/parsers': { '@typescript-eslint/parser': ['.ts', '.tsx'] },
    },
    rules: IMPORT_CYCLE_RULES,
  },
  // One home per shared helper, and no parse-then-cast. The guards live in
  // SRC_GUARDS above; each home module is exempt only from its own guard.
  {
    files: ['src/**/*.ts', 'src/**/*.tsx'],
    rules: { 'no-restricted-syntax': ['error', ...guardOptions(SRC_GUARDS)] },
  },
  ...Object.entries(GUARD_HOMES).map(([home, names]) => ({
    files: [home],
    rules: {
      'no-restricted-syntax': [
        'error',
        ...guardOptions(SRC_GUARDS.filter((g) => !names.includes(g.name))),
      ],
    },
  })),
  // API routes must build JSON responses through the shared helpers so error
  // body shape and Content-Type stay uniform (code review 2026-07-02 §1.7).
  // This block overrides the src/** no-restricted-syntax config for API files,
  // so it carries SRC_GUARDS forward and adds the two route-only rules.
  {
    files: ['src/pages/api/**/*.ts'],
    rules: {
      'no-restricted-syntax': [
        'error',
        ...guardOptions(SRC_GUARDS),
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
  // The tooling trees: the CI scripts under scripts/ and the Claude Code hooks
  // and CLIs under .claude/. Both were ignored wholesale until 2026-09-25, and
  // the window's two largest new modules grew over the ceiling there unseen
  // (review 2026-09-25, Architecture 2: the obligation reconciler's `main` at
  // 249 lines and complexity 50, the register CLI at 627 logical lines). They
  // are live code: obligation-reconcile.yml runs the first on a schedule and
  // `.claude/bin/register` execs the second, and the enforcement hooks
  // (worktree-guard, engagement-guard) are the controls the doctrine leans on.
  //
  // Every structural ceiling applies at error, as everywhere else. What is
  // relaxed is only what cannot work: plain JavaScript carries no types, and
  // the `.claude/` files sit outside tsconfig (its default include skips dot
  // directories), so the type-aware rules are turned off for JS files in these
  // trees. TypeScript under scripts/ is inside tsconfig and keeps them.
  {
    files: [
      'scripts/**/*.js',
      'scripts/**/*.mjs',
      'scripts/**/*.cjs',
      '.claude/**/*.js',
      '.claude/**/*.mjs',
      '.claude/**/*.cjs',
    ],
    ...tseslint.configs.disableTypeChecked,
  },
  {
    ignores: [
      '**/dist/**',
      '**/node_modules/**',
      '**/.wrangler/**',
      '**/.astro/**',
      // Agent worktrees: full checkouts of other branches, each with its own
      // lint run. Only the worktrees are skipped; the tracked hooks and CLIs
      // under .claude/ are linted (block above). This was `**/.claude/**`
      // until 2026-09-25, which exempted every enforcement hook as a side
      // effect of excluding the worktrees.
      '**/.claude/worktrees/**',
      // Stale git worktrees from a pre-`.claude/worktrees/` tooling
      // convention. Real registered worktrees (`git worktree list`) but
      // not committed; pre-push verify failed locally on dirty checkouts
      // that have nothing to do with the branch being pushed. Treat the
      // same as `.claude/worktrees/**` above.
      '**/.worktrees/**',
      'coverage/**',
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
