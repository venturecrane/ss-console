# /auth-setup - Set up Clerk + Playwright auth bootstrap for a venture

Wires up `@clerk/testing/playwright` so E2E tests (and any agent driving Playwright) authenticate against Clerk-protected routes **without manual login**.

Solves the recurring pain: "agent gets hung up needing manual login to test."

## When to use

- Run **inside a venture console repo** that uses Clerk (`dc`, `dfg`, `ke`, `sc`)
- After verifying the venture's Clerk integration is working in dev (`npm run dev` reaches a protected page)
- One-time setup per venture; re-run only to update the template

## Prerequisites (Manual)

Before running this command, the Captain must have completed:

1. Created a Clerk **test user** in the venture's Clerk Dashboard
   - Email: `agent-test+clerk_test@venturecrane.com` (the `+clerk_test` is required — Clerk recognizes it as a test identity)
   - Password: anything strong, stored in Bitwarden
   - Roles/permissions: same as a typical authenticated user
2. Confirmed the venture's Infisical secrets include working `CLERK_SECRET_KEY` and `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` (or `PUBLIC_CLERK_PUBLISHABLE_KEY` for Astro)
3. (If running against deployed CI) added the same env vars to GitHub Actions secrets

## Execution

### Step 1: Detect Context

1. Confirm cwd is a venture console repo. Walk up to the `.git` root and read `package.json`. If the repo is not in `{dc,dfg,ke,sc}-console`, stop and explain.
2. Detect framework from `package.json`:
   - `@clerk/nextjs` → Next.js
   - `@clerk/astro` → Astro
3. Detect existing Playwright config: `playwright.config.{ts,js}`. If absent, stop — this skill does not bootstrap Playwright itself.
4. Read the canonical runbook for reference:
   ```bash
   cat ~/dev/crane-console/docs/runbooks/clerk-playwright-auth-setup.md
   ```

### Step 2: Add Infisical secret

Add `E2E_CLERK_USER_EMAIL` if not present:

```bash
infisical secrets set --path=/{venture} E2E_CLERK_USER_EMAIL=agent-test+clerk_test@venturecrane.com
```

Verify the venture's existing Clerk keys are present:

```bash
infisical secrets list --path=/{venture} | grep -E "CLERK_(SECRET_KEY|PUBLISHABLE_KEY)"
```

If `CLERK_SECRET_KEY` is missing, stop — the test instance must be configured first.

### Step 3: Install dev deps

```bash
npm install -D @clerk/testing
```

(`@playwright/test` should already be installed; verify with `npm list @playwright/test`.)

### Step 4: Copy template files

From `~/dev/crane-console/templates/clerk-playwright-auth/`:

```bash
TEMPLATE=~/dev/crane-console/templates/clerk-playwright-auth

mkdir -p playwright
cp "$TEMPLATE/auth.setup.ts" playwright/auth.setup.ts
```

For Astro projects (`sc`), edit `playwright/auth.setup.ts` and verify env-var names match `PUBLIC_CLERK_PUBLISHABLE_KEY` (Clerk's testing pkg reads from `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` by default — Astro repos may need to copy the value).

### Step 5: Update playwright.config.ts

Read the existing `playwright.config.ts` and merge the `projects` block from `templates/clerk-playwright-auth/playwright.config.snippet.ts`:

- Add a `setup-clerk` project that runs `auth.setup.ts`
- Make authenticated browser projects depend on `setup-clerk` and load `storageState: 'playwright/.clerk/user.json'`
- Leave any existing public/unauthenticated projects unchanged

Use the Edit tool. Show the diff before applying.

### Step 6: .gitignore

Ensure `playwright/.clerk/` is gitignored (the captured `user.json` is a session secret):

```bash
grep -q "playwright/.clerk" .gitignore || echo "playwright/.clerk/" >> .gitignore
```

### Step 7: .env.example

Append from the template:

```bash
cat ~/dev/crane-console/templates/clerk-playwright-auth/.env.example.snippet >> .env.example
```

### Step 8: Verify

```bash
npx playwright test --project=setup-clerk
```

Expected output: two tests pass (`global clerk setup`, `authenticate and save state`). A `playwright/.clerk/user.json` is created.

If the second test fails with `E2E_CLERK_USER_EMAIL not set`, re-run `crane vc {venture}` (or whatever launches with secrets) so Infisical injects the env.

If the second test fails with a redirect to `/sign-in`, the test user wasn't found in Clerk Dashboard — re-check Step 1 of Prerequisites.

### Step 9: Smoke test the authenticated flow

Add a minimal smoke test that hits a protected page:

```ts
// e2e/smoke.spec.ts
import { test, expect } from '@playwright/test'

test('authenticated user reaches protected route', async ({ page }) => {
  await page.goto('/')
  // Adjust the URL/selector to a real protected page in this venture
  await expect(page).not.toHaveURL(/sign-in/)
})
```

Run with `npx playwright test --project=chromium-authed` (or whatever the authed project is named). Expect green.

### Step 10: Commit

```bash
git checkout -b chore/clerk-playwright-auth-bootstrap
git add playwright/auth.setup.ts playwright.config.ts .gitignore .env.example package.json package-lock.json
git commit -m "$(cat <<'EOF'
chore: clerk + playwright auth bootstrap

Solves recurring pain where E2E runs (and agents driving Playwright)
hit the Clerk login wall and require manual sign-in. Uses
@clerk/testing/playwright to issue a server-side sign-in token
(bypasses password/OTP/2FA), captures storageState once, and reuses
it across workers.

Runbook: ~/dev/crane-console/docs/runbooks/clerk-playwright-auth-setup.md
EOF
)"
git push -u origin chore/clerk-playwright-auth-bootstrap
gh pr create --title "chore: clerk + playwright auth bootstrap" --body "..."
```

## What NOT to do

- Don't commit `playwright/.clerk/user.json` — it's a session secret
- Don't use the production `sk_live_*` Clerk key in CI — testing tokens only work on dev instances
- Don't reuse a real (human) user as the test identity — use the `+clerk_test` pattern
- Don't add this to `ss-console` (workers only, no UI auth) or `crane-console` (backend, no UI auth)

## Related

- Runbook: `docs/runbooks/clerk-playwright-auth-setup.md`
- Template: `templates/clerk-playwright-auth/`
- Memory: `reference_browser_automation_tools.md`
- Tooling catalog: `docs/instructions/tooling.md`
