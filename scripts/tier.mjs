/**
 * Build tier: how much build process a change gets, decided from the paths it
 * touches, so neither the owner nor a model decides it. Run through
 * `.claude/bin/tier`.
 *
 *   tier <path> [<path> ...] [--new] [--json]
 *   git diff --name-only main | tier [--new] [--json]
 *
 * The classes come from the touch map (scripts/lib/touch-map.mjs):
 *
 *   none        no path is on a client surface
 *   fix         a path is on a client surface, and the change is to something
 *               that exists
 *   capability  a path is on a client surface, and the caller says `--new`:
 *               the change adds something new there
 *
 * `--new` is the one bit the caller states; everything else is computed.
 * Exit 0 for every valid call, 2 when no paths were given.
 */
import { readFileSync, realpathSync } from 'node:fs'
import { dirname, isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { classifyPaths } from './lib/touch-map.mjs'

/** The repo this script lives in; an absolute path is read relative to it. */
const CHECKOUT = resolve(dirname(fileURLToPath(import.meta.url)), '..')

/**
 * Release and fleet tooling. The touch map classes these `none` on purpose (no
 * client request runs through them), yet release tooling drew more fixes after
 * merge than any other kind of work, so a change to them still gets a bug
 * review. This is the one place that list lives.
 */
const RELEASE_TOOLING_RE =
  /^(operator\/bin\/lib\/fleet\/|operator\/bin\/(fleet|reprovision\.sh)$|operator\/bin\/lib\/(select-release-image|fly-transport|render-fly-toml|machine-size)\.sh$|\.github\/workflows\/(release-tick|deploy|hermes-release-watch)\.yml$)/
const REVIEW_LINE = 'bug review required (release and fleet tooling)'

/** A path as the touch map reads it: relative to the repo root, no leading `./`. */
function repoRelative(p) {
  const rel = isAbsolute(p) ? relative(CHECKOUT, p) : p
  return rel.replace(/^\.\//, '')
}

const USAGE = 'usage: tier <path> [<path> ...] [--new] [--json]   (or paths on stdin, one per line)'

/**
 * The tier for a set of changed paths.
 * @param {string[]} paths
 * @param {{ isNew?: boolean }} [opts]
 */
export function tierFor(rawPaths, { isNew = false } = {}) {
  const paths = rawPaths.map(repoRelative)
  const { clientSurface, byClass } = classifyPaths(paths)
  const tier = !clientSurface ? 'none' : isNew ? 'capability' : 'fix'
  const releaseTooling = paths.filter((p) => RELEASE_TOOLING_RE.test(p))
  return {
    tier,
    review: tier === 'none' && releaseTooling.length > 0 ? REVIEW_LINE : null,
    classes: byClass,
    releaseTooling,
  }
}

function main(argv) {
  const flags = new Set(argv.filter((a) => a.startsWith('--')))
  const unknown = [...flags].filter((f) => f !== '--new' && f !== '--json')
  if (unknown.length > 0) {
    process.stderr.write(`tier: unknown flag ${unknown.join(', ')}\n${USAGE}\n`)
    return 2
  }
  let paths = argv.filter((a) => !a.startsWith('--'))
  if (paths.length === 0 && !process.stdin.isTTY) {
    try {
      paths = readFileSync(0, 'utf8').split('\n')
    } catch {
      paths = [] // stdin open but with nothing to read: the same as no paths
    }
  }
  paths = paths.map((p) => p.trim()).filter(Boolean)
  if (paths.length === 0) {
    process.stderr.write(USAGE + '\n')
    return 2
  }
  const result = tierFor(paths, { isNew: flags.has('--new') })
  if (flags.has('--json')) {
    process.stdout.write(JSON.stringify(result) + '\n')
    return 0
  }
  const out = [`tier: ${result.tier}`]
  if (result.review) out.push(`review: ${result.review}`)
  for (const [cls, list] of Object.entries(result.classes)) {
    out.push(`  ${cls}:`, ...list.map((p) => `    ${p}`))
  }
  if (result.releaseTooling.length > 0) {
    out.push('  release tooling:', ...result.releaseTooling.map((p) => `    ${p}`))
  }
  if (Object.keys(result.classes).length === 0 && result.releaseTooling.length === 0) {
    out.push(`  no client surface in ${paths.length} path(s)`)
  }
  process.stdout.write(out.join('\n') + '\n')
  return 0
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  process.exit(main(process.argv.slice(2)))
}
