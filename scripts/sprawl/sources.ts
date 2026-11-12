/**
 * The files the sprawl audit reads, shared by the CLI (scripts/sprawl-audit.ts)
 * and the CI ratchet (tests/sprawl-ratchet.test.ts), so the weekly report and
 * the merge gate count the same tree the same way.
 */

import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { parse as parseYaml } from 'yaml'
import { type SourceFile, isGenerated } from './analyze'
import { pinLangOf } from './text-pins'

const CODE_EXT = /\.(ts|tsx|mjs|cjs|js|astro|py|sh|bash|ya?ml|toml|md)$/
const SKIP = /(^|\/)(node_modules|dist|\.venv|__pycache__|fixtures)\/|package-lock\.json$/
const EXTENSIONLESS_SCRIPT = /^(operator\/bin|\.claude\/bin)\/[^/.]+$/

export const LITERAL_BASELINE = 'scripts/sprawl/literal-baseline.json'

export function trackedFiles(cwd = '.'): string[] {
  const raw = execFileSync('git', ['ls-files', '-z'], { cwd, encoding: 'utf8', maxBuffer: 1 << 28 })
  return raw.split('\0').filter((p) => p && !SKIP.test(p))
}

function readAll(paths: string[], cwd: string): SourceFile[] {
  const out: SourceFile[] = []
  for (const path of paths) {
    const wanted =
      CODE_EXT.test(path) || EXTENSIONLESS_SCRIPT.test(path) || path.endsWith('Dockerfile')
    if (!wanted) continue
    try {
      out.push({ path, text: readFileSync(`${cwd}/${path}`, 'utf8') })
    } catch {
      // A tracked symlink to a directory, or a file deleted in the working tree: nothing to read.
    }
  }
  return out
}

/**
 * The single declarations a literal is meant to live in. Naming a value in its
 * one home is the cure, not the sprawl, so these files are not counted.
 */
const DECLARATIONS = new Set([
  'operator/contracts/seat-layout.yaml',
  'operator/contracts/env-consumption.yaml',
])

/**
 * The manifest of stamped Python copies (operator/bin/gen-skill-shared.py). A
 * copy it declares is the generator's byte-for-byte projection of one source,
 * like a file with a GENERATED header, but it carries no header because a
 * header would change what a seat runs. The manifest names it instead.
 */
export const STAMPED_COPIES_MANIFEST = 'operator/contracts/skill-shared-modules.yaml'

/**
 * Every copy path the manifest declares: `operator/skills/<skill>/<copy_as>`
 * for each listed skill, and each path under `copies`. Sources are never in it.
 * A manifest that does not parse declares nothing, so nothing is left out.
 */
export function declaredCopies(manifestText: string): Set<string> {
  const out = new Set<string>()
  let doc: unknown
  try {
    doc = parseYaml(manifestText)
  } catch {
    return out
  }
  const modules = (doc as { modules?: unknown } | null)?.modules
  if (modules === null || typeof modules !== 'object') return out
  for (const body of Object.values(modules as Record<string, unknown>)) {
    if (body === null || typeof body !== 'object') continue
    const { copy_as: copyAs, skills, copies } = body as Record<string, unknown>
    if (typeof copyAs === 'string' && Array.isArray(skills)) {
      for (const skill of skills)
        if (typeof skill === 'string') out.add(`operator/skills/${skill}/${copyAs}`)
    }
    if (Array.isArray(copies))
      for (const path of copies) if (typeof path === 'string') out.add(path)
  }
  return out
}

function stampedCopies(cwd: string): Set<string> {
  try {
    return declaredCopies(readFileSync(`${cwd}/${STAMPED_COPIES_MANIFEST}`, 'utf8'))
  } catch {
    return new Set()
  }
}

/**
 * The hand-written sources: generated files (analyze.ts isGenerated), the
 * stamped copies the generator manifest declares, and the declarations are
 * left out.
 */
export function readSources(paths: string[], cwd = '.'): SourceFile[] {
  const tracked = new Set(paths)
  const stamped = stampedCopies(cwd)
  return readAll(paths, cwd).filter(
    (f) => !DECLARATIONS.has(f.path) && !stamped.has(f.path) && !isGenerated(f.text, tracked)
  )
}

/** The generated files among `paths` (stamped copies included), for the clone and dead-code reports to leave out too. */
export function generatedPaths(paths: string[], cwd = '.'): Set<string> {
  const tracked = new Set(paths)
  const stamped = stampedCopies(cwd)
  return new Set(
    readAll(paths, cwd)
      .filter((f) => stamped.has(f.path) || isGenerated(f.text, tracked))
      .map((f) => f.path)
  )
}

/**
 * Parse a committed baseline: a flat map of key to allowed count (`$`-keys are comments).
 *
 * @public Merge-gate check. tests/sprawl-ratchet.test.ts and tests/text-pin-ratchet.test.ts import it to read their baselines.
 */
export function parseLiteralBaseline(
  text: string,
  name = LITERAL_BASELINE
): Record<string, number> {
  const raw: unknown = JSON.parse(text)
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    throw new Error(`${name}: expected an object of key -> count`)
  }
  const out: Record<string, number> = {}
  for (const [k, v] of Object.entries(raw)) {
    if (k.startsWith('$')) continue
    if (typeof v !== 'number' || !Number.isInteger(v) || v < 0) {
      throw new Error(`${name}: ${k} must be a non-negative integer`)
    }
    out[k] = v
  }
  return out
}

export const TEXT_PIN_BASELINE = 'scripts/sprawl/text-pin-baseline.json'

/** The tracked test files (TypeScript and Python), read, for the source-text pin census. */
export function readTestFiles(paths: string[], cwd = '.'): SourceFile[] {
  const out: SourceFile[] = []
  for (const path of paths) {
    if (!pinLangOf(path)) continue
    try {
      out.push({ path, text: readFileSync(`${cwd}/${path}`, 'utf8') })
    } catch {
      // Deleted in the working tree: nothing to count.
    }
  }
  return out
}
