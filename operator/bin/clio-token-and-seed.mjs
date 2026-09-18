#!/usr/bin/env node
// Mint a Clio Manage access token from the encrypted token blob, then run the
// Clio dev seed with that token in the child process env. The token is NEVER
// printed.
//
// Safe for the live pilot-law integration: Clio Manage refresh tokens are
// reusable and NOT rotated (https://docs.developers.clio.com/faq), so refreshing
// out-of-band mints a fresh access token and leaves the stored refresh token
// untouched.
//
// Decryption mirrors oktopeak/clio-mcp src/auth/tokenStorage.ts exactly:
//   aes-256-gcm, key = Buffer.from(ENCRYPTION_KEY,'hex'),
//   file layout = [iv(16)][authTag(16)][ciphertext], base64 in CLIO_TOKENS_ENC_B64.
//
// Run under Infisical so the secrets are in env, never in the transcript:
//   infisical run --env=prod --path=/ss --silent -- \
//     node operator/bin/clio-token-and-seed.mjs [--dry-run]

import crypto from 'node:crypto'
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const TOKEN_URL = process.env.CLIO_TOKEN_URL || 'https://app.clio.com/oauth/token'

function need(name) {
  const v = process.env[name]
  if (!v) {
    console.error(
      `Missing ${name}. Run under: infisical run --env=prod --path=/ss --silent -- node ...`
    )
    process.exit(2)
  }
  return v
}

function decryptRefreshToken() {
  const combined = Buffer.from(need('CLIO_TOKENS_ENC_B64'), 'base64')
  const iv = combined.subarray(0, 16)
  const authTag = combined.subarray(16, 32)
  const encrypted = combined.subarray(32)
  const key = Buffer.from(need('CLIO_ENCRYPTION_KEY'), 'hex')
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv)
  decipher.setAuthTag(authTag)
  let pt
  try {
    pt = Buffer.concat([decipher.update(encrypted), decipher.final()]).toString('utf8')
  } catch (e) {
    console.error(
      `Token blob decryption failed (${e.message}). Wrong ENCRYPTION_KEY or corrupt blob.`
    )
    process.exit(2)
  }
  const tokens = JSON.parse(pt)
  const rt = tokens.refresh_token || tokens.refreshToken
  if (!rt) {
    console.error(
      `Decrypted blob has no refresh_token. Keys present: ${Object.keys(tokens).join(', ')}`
    )
    process.exit(2)
  }
  return rt
}

async function mintAccessToken(refreshToken) {
  const body = new URLSearchParams({
    client_id: need('CLIO_CLIENT_ID'),
    client_secret: need('CLIO_CLIENT_SECRET'),
    grant_type: 'refresh_token',
    refresh_token: refreshToken,
  })
  const resp = await fetch(TOKEN_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })
  if (!resp.ok) {
    const t = await resp.text()
    console.error(`Token refresh failed: HTTP ${resp.status} ${t.slice(0, 300)}`)
    process.exit(2)
  }
  const json = await resp.json()
  if (!json.access_token) {
    console.error('Token refresh response had no access_token')
    process.exit(2)
  }
  return json.access_token
}

const here = path.dirname(fileURLToPath(import.meta.url))
const seedScript = path.join(here, 'seed-clio-demo.py')
const passthrough = process.argv.slice(2)

const refreshToken = decryptRefreshToken()
const accessToken = await mintAccessToken(refreshToken)
console.log('Minted a fresh Clio access token (refresh token reused, pilot-law untouched).\n')

const child = spawn('python3', [seedScript, ...passthrough], {
  stdio: 'inherit',
  env: { ...process.env, CLIO_ACCESS_TOKEN: accessToken },
})
child.on('exit', (code) => process.exit(code ?? 0))
