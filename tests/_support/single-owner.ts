/**
 * The scanner behind tests/single-owner-registry.test.ts.
 *
 * An ADR that decides "this capability lives only in X" is enforced here, not
 * by a list of the files we already know about. Each registry entry
 * (docs/adr/single-owner-registry.yaml) carries a code-level SIGNATURE of the
 * capability: a call, a prompt line, a config key it reads, an import it
 * needs. The scanner reads every tracked source file (TypeScript, JavaScript,
 * Python, shell) and reports every place the signature appears. A hit outside
 * the entry's allowed paths is a second copy of the capability, whatever the
 * file is called, which is the failure the drafting lane (#3093) was: a
 * second house style built beside ADR 0083's "nowhere else", that no list of
 * known producers would have named.
 */
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { parse } from 'yaml'

export interface SourceFile {
  path: string
  text: string
}

export interface Signature {
  /** A line (full-line comments blanked) matching any of these is a hit. */
  match: string[]
  /** The file must ALSO contain a match for each of these (co-occurrence). */
  requires?: string[]
  /** Regex flags for match and requires; default none. */
  flags?: string
}

export interface PathNote {
  path: string
  why: string
}

export interface RegistryEntry {
  id: string
  adr: string
  decision: string
  signature: Signature
  allowed: PathNote[]
  known_violation?: PathNote[]
  new_copy: { path: string; code: string }
}

export interface Registry {
  phrasing: string
  entries: RegistryEntry[]
  reviewed_not_code: { adr: string; why: string }[]
}

export interface Hit {
  path: string
  line: number
  text: string
}

export const REGISTRY_PATH = 'docs/adr/single-owner-registry.yaml'

const SOURCE_EXT = /\.(py|ts|tsx|mts|cts|js|mjs|cjs|sh|bash)$/

/**
 * Test code, fixtures and schema migrations are not producers: a test that
 * builds a house style to assert on it is not a second house style.
 */
const NOT_PRODUCTION = [
  /(^|\/)tests?\//,
  /(^|\/)__tests__\//,
  /(^|\/)fixtures?\//,
  /(^|\/)node_modules\//,
  /(^|\/)migrations\//,
  /\.(test|spec)\.[cm]?[jt]sx?$/,
  /(^|\/)test_[^/]*\.py$/,
  /_test\.py$/,
  /(^|\/)conftest\.py$/,
  /\.d\.ts$/,
]

export function isScannable(path: string): boolean {
  return SOURCE_EXT.test(path) && !NOT_PRODUCTION.some((re) => re.test(path))
}

/** Every tracked file under `root`, by git, with the caller's GIT_* stripped. */
export function trackedFiles(root: string): string[] {
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('GIT_')))
  const out = execFileSync('git', ['-C', root, 'ls-files', '-z'], {
    env,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  })
  return out.split('\0').filter(Boolean)
}

export function loadSources(root: string): SourceFile[] {
  return trackedFiles(root)
    .filter(isScannable)
    .map((path) => ({ path, text: readFileSync(join(root, path), 'utf8') }))
}

export function loadRegistry(root: string): Registry {
  return parse(readFileSync(join(root, REGISTRY_PATH), 'utf8')) as Registry
}

/**
 * Full-line comments are blanked (line numbers kept): a comment that names a
 * capability is not a copy of it. Docstrings are not comments and stay.
 */
export function stripCommentLines(text: string): string {
  return text
    .split('\n')
    .map((line) => {
      const t = line.trimStart()
      if (t.startsWith('#!')) return line
      return /^(#|\/\/|\/\*|\*)/.test(t) ? '' : line
    })
    .join('\n')
}

/**
 * The one place a pattern string becomes a RegExp. Every source is a reviewed
 * line of docs/adr/single-owner-registry.yaml or a glob built from one, never
 * user input, and it runs only over this repo's own files in a test.
 */
export function compile(source: string, flags = ''): RegExp {
  // nosemgrep: javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp -- patterns come from the reviewed in-repo registry, run only in tests over repo files
  return new RegExp(source, flags)
}

/** `**` crosses directories, `*` does not; everything else is literal. */
export function globToRegExp(glob: string): RegExp {
  let re = ''
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i]
    if (c === '*' && glob[i + 1] === '*') {
      re += '.*'
      i++
      if (glob[i + 1] === '/') i++
    } else if (c === '*') re += '[^/]*'
    else re += c.replace(/[.+?^${}()|[\]\\]/g, '\\$&')
  }
  return compile(`^${re}$`)
}

export function pathMatches(path: string, globs: PathNote[] | undefined): boolean {
  return (globs ?? []).some((g) => globToRegExp(g.path).test(path))
}

export function scanEntry(entry: RegistryEntry, files: SourceFile[]): Hit[] {
  const flags = entry.signature.flags ?? ''
  const match = entry.signature.match.map((s) => compile(s, flags))
  const requires = (entry.signature.requires ?? []).map((s) => compile(s, flags))
  const hits: Hit[] = []
  for (const file of files) {
    const code = stripCommentLines(file.text)
    if (!requires.every((re) => re.test(code))) continue
    code.split('\n').forEach((text, i) => {
      if (match.some((re) => re.test(text)))
        hits.push({ path: file.path, line: i + 1, text: text.trim() })
    })
  }
  return hits
}

/** Hits that are neither the owner nor a recorded, pre-existing violation. */
export function strayHits(entry: RegistryEntry, files: SourceFile[]): Hit[] {
  return scanEntry(entry, files).filter(
    (h) => !pathMatches(h.path, entry.allowed) && !pathMatches(h.path, entry.known_violation)
  )
}
