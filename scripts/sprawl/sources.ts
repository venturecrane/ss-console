/**
 * The files the sprawl audit reads, shared by the CLI (scripts/sprawl-audit.ts)
 * and the CI ratchet (tests/sprawl-ratchet.test.ts), so the weekly report and
 * the merge gate count the same tree the same way.
 */

import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import type { SourceFile } from './analyze'

const CODE_EXT = /\.(ts|tsx|mjs|cjs|js|astro|py|sh|bash|ya?ml|toml|md)$/
const SKIP = /(^|\/)(node_modules|dist|\.venv|__pycache__|fixtures)\/|package-lock\.json$/
const EXTENSIONLESS_SCRIPT = /^(operator\/bin|\.claude\/bin)\/[^/.]+$/

export const LITERAL_BASELINE = 'scripts/sprawl/literal-baseline.json'

export function trackedFiles(cwd = '.'): string[] {
  const raw = execFileSync('git', ['ls-files', '-z'], { cwd, encoding: 'utf8', maxBuffer: 1 << 28 })
  return raw.split('\0').filter((p) => p && !SKIP.test(p))
}

export function readSources(paths: string[], cwd = '.'): SourceFile[] {
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
 * Parse the committed baseline: a flat map of literal to allowed file count.
 *
 * @public Merge-gate check. tests/sprawl-ratchet.test.ts imports it to read scripts/sprawl/literal-baseline.json.
 */
export function parseLiteralBaseline(text: string): Record<string, number> {
  const raw: unknown = JSON.parse(text)
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    throw new Error(`${LITERAL_BASELINE}: expected an object of literal -> count`)
  }
  const out: Record<string, number> = {}
  for (const [k, v] of Object.entries(raw)) {
    if (k.startsWith('$')) continue
    if (typeof v !== 'number' || !Number.isInteger(v) || v < 0) {
      throw new Error(`${LITERAL_BASELINE}: ${k} must be a non-negative integer`)
    }
    out[k] = v
  }
  return out
}
