#!/usr/bin/env node
// Allowlist-aware npm audit gate.
//
// Runs `npm audit --json` for each given directory and fails (exit 1) if any
// HIGH or CRITICAL advisory is present that is NOT in the time-boxed allowlist
// at .github/audit-allowlist.json. Allowlisted advisories are tolerated only
// until the allowlist's `expires` date; on or after that date the allowlist is
// ignored entirely, so a lingering exception re-fails the build and forces the
// tracked fix to land. Moderate/low advisories never fail the gate (parity with
// the previous `npm audit --audit-level=high`).
//
// Usage: node scripts/audit-allowlist.mjs [dir ...]   (default: ".")
//
// This exists so a security advisory whose only fix is blocked by the
// .npmrc supply-chain cooldown (min-release-age) does not freeze all merges to
// main via the required Security Summary gate. It is a scoped, self-expiring
// exception with per-advisory justification, not a blanket suppression. See
// .github/audit-allowlist.json and the issue it tracks.

import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const allowlistPath = join(repoRoot, '.github', 'audit-allowlist.json')

/** Load the allowlist, tolerating its absence (treated as "no exceptions"). */
function loadAllowlist() {
  try {
    const raw = JSON.parse(readFileSync(allowlistPath, 'utf8'))
    const expires = raw.expires ? new Date(`${raw.expires}T23:59:59Z`) : null
    const expired = expires ? new Date() > expires : true
    return {
      allow: raw.allow ?? {},
      expires: raw.expires ?? null,
      expired,
      tracking: raw.tracking ?? null,
    }
  } catch (err) {
    if (err.code === 'ENOENT') {
      return { allow: {}, expires: null, expired: true, tracking: null }
    }
    throw err
  }
}

/** Collect the GHSA IDs of every high/critical advisory reported for `dir`. */
function highSeverityGhsas(dir) {
  // npm audit exits non-zero when vulnerabilities exist; capture stdout anyway.
  const res = spawnSync('npm', ['audit', '--json', '--prefix', dir], {
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  })
  if (!res.stdout) {
    throw new Error(`npm audit produced no output for ${dir}: ${res.stderr || res.error}`)
  }
  const report = JSON.parse(res.stdout)
  const found = new Map() // ghsa -> { title, severity }
  for (const vuln of Object.values(report.vulnerabilities ?? {})) {
    if (vuln.severity !== 'high' && vuln.severity !== 'critical') continue
    for (const via of vuln.via) {
      if (typeof via !== 'object' || !via.url) continue
      const m = via.url.match(/GHSA-[0-9a-z-]+/i)
      if (m) found.set(m[0], { title: via.title ?? '', severity: vuln.severity })
    }
  }
  return found
}

/** Count non-allowlisted advisories (printing each) and collect the suppressed ones. */
function classify(dirs, allowIds, expired) {
  let blocking = 0
  const suppressed = new Set()
  for (const dir of dirs) {
    for (const [ghsa, info] of highSeverityGhsas(dir)) {
      if (!expired && allowIds.has(ghsa)) {
        suppressed.add(ghsa)
        continue
      }
      blocking += 1
      console.log(
        `::error::${dir}: ${info.severity} advisory ${ghsa} not allowlisted — ${info.title}`
      )
    }
  }
  return { blocking, suppressed }
}

function reportSuppressed(suppressed, allow, expires, tracking) {
  if (suppressed.size === 0) return
  console.log(`\nAllowlisted (tolerated until ${expires}, tracked in ${tracking ?? 'n/a'}):`)
  for (const ghsa of suppressed) {
    console.log(`  - ${ghsa}: ${allow[ghsa]}`)
  }
}

// Hygiene: flag allowlist entries that no longer match any live advisory so
// stale exceptions get pruned rather than lingering silently.
function warnStaleEntries(dirs, allowIds) {
  const live = new Set()
  for (const dir of dirs) for (const g of highSeverityGhsas(dir).keys()) live.add(g)
  for (const ghsa of allowIds) {
    if (!live.has(ghsa)) {
      console.log(
        `::warning::allowlist entry ${ghsa} matches no current advisory — remove it from .github/audit-allowlist.json`
      )
    }
  }
}

function main() {
  const dirs = process.argv.slice(2)
  if (dirs.length === 0) dirs.push('.')

  const { allow, expires, expired, tracking } = loadAllowlist()
  const allowIds = new Set(Object.keys(allow))

  if (expired && allowIds.size > 0) {
    console.log(
      `::warning::npm-audit allowlist expired on ${expires}; all advisories now enforced. Land the tracked fix (${tracking ?? 'see .github/audit-allowlist.json'}).`
    )
  }

  const { blocking, suppressed } = classify(dirs, allowIds, expired)
  reportSuppressed(suppressed, allow, expires, tracking)
  warnStaleEntries(dirs, allowIds)

  if (blocking > 0) {
    console.log(`\n${blocking} non-allowlisted high/critical advisory(ies) — failing.`)
    process.exit(1)
  }
  console.log('\nnpm audit gate passed (no un-allowlisted high/critical advisories).')
}

main()
