#!/usr/bin/env node
// Refuse to publish a customer.yaml that names a skill the seat's running image
// does not carry (the 2026-10-13 A&P outage).
//
// Usage: check-config-against-image.mjs <customer.yaml> <image-manifest.json|""> <published.yaml|"">
//
// The seat re-fetches customer.yaml from R2 on EVERY boot, so publishing a
// config the running image cannot run is a latent seat change: the next
// reboot, crash-restart or rollback adopts it. On 2026-10-13 the merged config
// named an hourly cron whose pre_run.py shipped only in the newer image; the
// seat rebooted onto the older one and its boot died.
//
// What a config REQUIRES of an image:
//   skill:<name>            every enabled personas[].skills[] entry
//   pin:<name>@<6hex>       an enabled skill pinned to content (not "pending")
//   pre_run:<skill>/<file>  every pre_run_decides cron's script
//
// What the image PROVIDES comes from vaults/<slug>/image-manifest.json, which
// the seat's entrypoint uploads from the image (/app/image-manifest.json, baked
// by operator/templates/gen-image-manifest.py) on every boot: it describes the
// image that last booted, which is the image the next restart boots.
//
// Exit 0: publish. Exit 1: refuse, with the reason on stderr. Exit 2: usage.
//
// "Cannot determine" never passes on its own. With no readable manifest (a seat
// built before manifests existed, or an unreadable object) the config may still
// publish if it adds NO requirement over the object the seat already boots from:
// that claims nothing about the image, only that this publish cannot make the
// seat worse. Anything new blocks until the seat's rebuild publishes it.
import { readFileSync } from 'node:fs'

import { parse as parseYaml } from 'yaml'

const [cfgPath, manifestPath, publishedPath] = process.argv.slice(2)
if (!cfgPath) {
  process.stderr.write(
    'Usage: check-config-against-image.mjs <customer.yaml> <manifest|""> <published|"">\n'
  )
  process.exit(2)
}

const list = (value) => (Array.isArray(value) ? value : [])

function skillRequirements(skill, out) {
  if (!skill || skill.enabled !== true || typeof skill.name !== 'string' || !skill.name) return
  out.add(`skill:${skill.name}`)
  const version = String(skill.version ?? 'pending')
  if (version !== 'pending') out.add(`pin:${skill.name}@${version}`)
}

function cronRequirements(entry, out) {
  if (!entry || typeof entry !== 'object') return
  const skill = String(entry.skill ?? '').trim()
  const preRun = String(entry.pre_run ?? '').trim()
  if (skill && preRun && String(entry.wake_policy ?? '').trim() === 'pre_run_decides') {
    out.add(`pre_run:${skill}/${preRun}`)
  }
}

/** The set of image requirements a parsed customer.yaml carries. */
function requirements(config) {
  const out = new Set()
  for (const persona of list(config?.personas)) {
    for (const skill of list(persona?.skills)) skillRequirements(skill, out)
    for (const entry of list(persona?.cron)) cronRequirements(entry, out)
  }
  return out
}

/** The set an image manifest provides, or null when it is not a usable manifest. */
function provided(manifest) {
  if (!manifest || manifest.schema_version !== 1 || typeof manifest.skills !== 'object') return null
  const out = new Set()
  for (const [name, body] of Object.entries(manifest.skills)) {
    out.add(`skill:${name}`)
    if (typeof body?.pin === 'string') out.add(`pin:${name}@${body.pin}`)
    for (const file of Array.isArray(body?.files) ? body.files : [])
      out.add(`pre_run:${name}/${file}`)
  }
  return out
}

function readYaml(path) {
  return parseYaml(readFileSync(path, 'utf8'))
}

function refuse(lines) {
  for (const line of lines) process.stderr.write(`${line}\n`)
  process.exit(1)
}

let required
try {
  required = requirements(readYaml(cfgPath))
} catch (err) {
  refuse([`cannot read ${cfgPath}: ${err.message}`])
}

let have = null
let manifestNote = 'no image manifest for this seat'
if (manifestPath) {
  try {
    const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
    have = provided(manifest)
    if (have === null) manifestNote = 'the image manifest is not a schema_version 1 manifest'
    else manifestNote = `image built from ${manifest.built_from ?? 'unknown commit'}`
  } catch (err) {
    manifestNote = `the image manifest is unreadable (${err.message})`
  }
}

if (have !== null) {
  const missing = [...required].filter((r) => !have.has(r)).sort()
  if (missing.length > 0) {
    refuse([
      `config names what the seat's running image lacks (${manifestNote}):`,
      ...missing.map((m) => `  ${m}`),
      'Rebuild the seat (reprovision) to publish this config with an image that carries it.',
    ])
  }
  process.stdout.write(
    `every skill and pre_run this config names is in the running image (${manifestNote})\n`
  )
  process.exit(0)
}

// Cannot determine the image. Only a publish that adds nothing may pass.
let before
try {
  if (!publishedPath) throw new Error('no published object to compare against')
  before = requirements(readYaml(publishedPath))
} catch (err) {
  refuse([
    `cannot determine what the seat's running image carries: ${manifestNote}, and ${err.message}.`,
    'Refusing to publish; the seat rebuild (reprovision) publishes this config with its image.',
  ])
}
const added = [...required].filter((r) => !before.has(r)).sort()
if (added.length > 0) {
  refuse([
    `cannot determine what the seat's running image carries (${manifestNote}), and this config adds:`,
    ...added.map((a) => `  ${a}`),
    'Refusing to publish; the seat rebuild (reprovision) publishes this config with its image.',
  ])
}
process.stdout.write(
  `${manifestNote}; publishing because this config adds no skill or pre_run over the object the seat already boots from\n`
)
