#!/usr/bin/env node
/**
 * Create the two staging portal identities in the Clerk DEVELOPMENT instance.
 *
 * Staging (`ss-web-staging`) points at the Clerk development instance, which
 * has its own user pool — nothing from production exists in it. Without these
 * identities nobody can sign in to staging, so no authenticated journey can be
 * rehearsed before a real client walks it.
 *
 * Why password auth: `POST /v1/users` marks the email verified on creation, so
 * this path needs no inbox. That matters because staging deliberately has no
 * RESEND_API_KEY (email must stay fail-closed there) and the staging-* mailboxes
 * may not receive mail at all. The password is the convenience credential for a
 * scripted run; it is NOT the mechanism a real client uses — A&P's principal
 * signs in with email + code.
 *
 * Usage (never echo the key — pipe it via infisical):
 *   infisical run --env=dev --path=/ -- node scripts/staging-clerk-users.mjs
 *
 * Requires both passwords in the environment. They are NEVER generated here and
 * never printed: a generated-and-echoed password would put a live credential in
 * the terminal transcript, which persists. Mint them out of band and store them
 * with `crane_secret_set` at Infisical path `/` env `dev`:
 *   STAGING_PORTAL_CLIENT_PASSWORD, STAGING_PORTAL_ADMIN_PASSWORD
 *
 * Idempotent: an existing identity is PATCHed back to the known password rather
 * than erroring, so a partial run or a password rotation is recoverable.
 *
 * Exit codes: 0 ok · 1 refused (not a dev key) · 2 Clerk API error
 */

const API = 'https://api.clerk.com/v1'

// ---------------------------------------------------------------------------
// The single safety layer. A production secret key here would mint identities
// in the LIVE Clerk instance alongside real client logins. Nothing downstream
// re-checks this, so it is the first statement in the file.
// ---------------------------------------------------------------------------
const SECRET = process.env.CLERK_SECRET_KEY
if (!SECRET || !SECRET.startsWith('sk_test_')) {
  console.error(
    'Refusing to run: CLERK_SECRET_KEY is absent or is not a development-instance key.\n' +
      'This script only ever targets the Clerk DEVELOPMENT instance.\n' +
      'Run it as: infisical run --env=dev --path=/ -- node scripts/staging-clerk-users.mjs'
  )
  process.exit(1)
}

const IDENTITIES = [
  {
    email: 'staging-client@smd.services',
    first_name: 'Staging',
    last_name: 'Client',
    passwordEnv: 'STAGING_PORTAL_CLIENT_PASSWORD',
  },
  {
    email: 'staging-admin@smd.services',
    first_name: 'Staging',
    last_name: 'Admin',
    passwordEnv: 'STAGING_PORTAL_ADMIN_PASSWORD',
  },
]

const missingPasswords = IDENTITIES.filter((i) => !process.env[i.passwordEnv]).map(
  (i) => i.passwordEnv
)
if (missingPasswords.length > 0) {
  console.error(
    `Missing required password env var(s): ${missingPasswords.join(', ')}\n` +
      'These are never generated here — a generated password would have to be printed to be\n' +
      'usable, and stdout persists in the transcript. Store them at Infisical path / env dev\n' +
      'with crane_secret_set, then re-run under infisical run.'
  )
  process.exit(1)
}

async function clerk(path, init = {}) {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${SECRET}`,
      'Content-Type': 'application/json',
      ...(init.headers ?? {}),
    },
  })
  const text = await res.text()
  let body
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = text
  }
  if (!res.ok) {
    const detail = typeof body === 'string' ? body : JSON.stringify(body?.errors ?? body)
    throw new Error(`Clerk ${init.method ?? 'GET'} ${path} -> ${res.status}: ${detail}`)
  }
  return body
}

async function findByEmail(email) {
  const found = await clerk(`/users?email_address=${encodeURIComponent(email)}&limit=1`)
  const list = Array.isArray(found) ? found : (found?.data ?? [])
  return list[0] ?? null
}

async function main() {
  // Confirm which instance we are about to write to, and prove it is a dev one.
  const instance = await clerk('/instance')
  if (instance?.environment_type && instance.environment_type !== 'development') {
    console.error(`Refusing: instance environment_type is "${instance.environment_type}".`)
    process.exit(1)
  }
  console.log(`Clerk instance: ${instance?.id} (${instance?.environment_type})`)

  for (const identity of IDENTITIES) {
    const password = process.env[identity.passwordEnv]
    const existing = await findByEmail(identity.email)

    if (existing) {
      await clerk(`/users/${existing.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ password, skip_password_checks: true }),
      })
      console.log(`  exists  ${identity.email} -> ${existing.id} (password reset)`)
    } else {
      const created = await clerk('/users', {
        method: 'POST',
        body: JSON.stringify({
          email_address: [identity.email],
          password,
          first_name: identity.first_name,
          last_name: identity.last_name,
          // A long random password is otherwise rejected by the breached-password
          // check, failing the run for a reason unrelated to staging.
          skip_password_checks: true,
          // Marks the identity as a fixture in Clerk's own dashboard and gives
          // a filter for later cleanup.
          public_metadata: { staging_fixture: true },
        }),
      })
      console.log(`  created ${identity.email} -> ${created.id}`)
    }
  }

  console.log(
    '\nDone. These identities exist only in the Clerk development instance and ' +
      'are bound to staging D1 rows by email — see scripts/seeds/staging-portal-access.sql.'
  )
}

main().catch((err) => {
  console.error(String(err.message ?? err))
  process.exit(2)
})
