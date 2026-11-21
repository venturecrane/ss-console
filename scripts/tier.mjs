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
 * Beside the tier it prints up to two `review:` lines: a bug review for release
 * and fleet tooling, and a replay of a past failure for a change to a check.
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

/**
 * The checks themselves: CI workflows and their path lists, the goldens, the
 * sprawl, size and type ratchets, the git hooks and the session hooks. A change
 * here can quietly drop a protection while every test stays green, because the
 * test that would have failed is the thing that changed. On 2026-11-18 a plan
 * to drop a golden check was rated as needing no design and turned out to lose
 * a protection. So it carries its own review line, printed whatever the tier
 * (a client tier does not replay the past failure for it), and beside the
 * release line when both apply. This is the one place that list lives.
 */
const CHECK_CHANGE = [
  /^\.github\/workflows\//, // CI itself
  /^\.github\/[^/]+-paths\.txt$/, // which suites a change triggers
  /^\.github\/CODEOWNERS$/,
  /^\.husky\//, // git hooks
  /^\.claude\/hooks\//, // session hooks and gates
  /^\.lintstagedrc\.json$/,
  /^eslint\.config\.js$/,
  /^scripts\/sprawl\//, // sprawl census, its baselines and allowlist
  /^scripts\/sprawl-audit\.ts$/,
  /^scripts\/runtime-ac-proof\.mjs$/, // the runtime-AC merge gate
  /^tests\/[^/]*ratchet[^/]*\.test\.ts$/,
  /^tests\/operator-module-size\.test\.ts$/,
  /^operator\/bin\/tests\/test_function_size_ratchet\.py$/,
  /^operator\/bin\/pyright-ratchet\.py$/,
  // the numbers a ratchet holds the tree to: size, type and dated-entry
  // baselines and the gate-coverage snapshots
  /^operator\/contracts\/[^/]*(-size|-baseline|-snapshot)[^/]*\.json$/,
  /^operator\/bin\/goldens\//, // golden comparison and its allowed lists
  /^operator\/bin\/lib\/golden_diff\.py$/,
  /^operator\/(.+\/)?tests\/[^/]*goldens?_kit\.py$/, // how goldens are recorded
  /^operator\/(.+\/)?[^/]*allowed[^/]*\.json$/, // every golden allowed-changes list
]
const CHECK_REVIEW_LINE =
  'this changes a check; show what it caught before still fails (replay a past failure)'

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
  const checkChanges = paths.filter((p) => CHECK_CHANGE.some((re) => re.test(p)))
  return {
    tier,
    review: tier === 'none' && releaseTooling.length > 0 ? REVIEW_LINE : null,
    checkReview: checkChanges.length > 0 ? CHECK_REVIEW_LINE : null,
    classes: byClass,
    releaseTooling,
    checkChanges,
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
  if (result.checkReview) out.push(`review: ${result.checkReview}`)
  for (const [cls, list] of Object.entries(result.classes)) {
    out.push(`  ${cls}:`, ...list.map((p) => `    ${p}`))
  }
  if (result.releaseTooling.length > 0) {
    out.push('  release tooling:', ...result.releaseTooling.map((p) => `    ${p}`))
  }
  if (result.checkChanges.length > 0) {
    out.push('  checks:', ...result.checkChanges.map((p) => `    ${p}`))
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
