#!/usr/bin/env node
/**
 * nosemgrep-audit.mjs -- every Semgrep suppression carries its reason.
 *
 * A `nosemgrep` annotation silences a security finding. The gate this replaces
 * was one grep regex in security.yml that meant to require twenty characters of
 * justification and in practice passed any rule-id-only suppression whose id
 * ran past nineteen characters, which is every real Semgrep id: the 2026-09-25
 * review counted 22 such lines and 0 flagged (review action item 9). A regex
 * that has to parse "marker, optional ids, optional separator, reason" is the
 * wrong instrument, so the rule is a function with fixtures instead
 * (tests/nosemgrep-audit.test.ts).
 *
 * THE RULE. An annotation passes when either
 *   - at least MIN_REASON characters of reason follow the rule id(s) on the
 *     same line (after an optional separator: an em or en dash, a hyphen, a
 *     colon, or an opening parenthesis), or
 *   - the line immediately above is a non-empty comment, which is where a
 *     reason too long for the line goes.
 *
 * Usage: node scripts/nosemgrep-audit.mjs   (exit 1 and a list on violations)
 */

import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'

export const MIN_REASON = 20

/** File types Semgrep's packs scan here. */
export const AUDITED_GLOBS = ['*.ts', '*.tsx', '*.js', '*.mjs', '*.cjs', '*.py']

const MARKER_RE = /(?:\/\/|#)\s*nosemgrep\b(.*)$/
/** `: id` or `: id1,id2`. Semgrep ids are dotted/hyphenated words with no spaces. */
const IDS_RE = /^\s*:\s*[\w.-]+(?:,[\w.-]+)*/
const SEPARATOR_RE = /^[\s—–:(-]+/
const COMMENT_RE = /^\s*(?:\/\/|#)(.*)$/

/**
 * The reason written on the annotation's own line, with the marker, the rule
 * ids and the separator removed.
 * @param {string} tail - the text after `nosemgrep`
 */
export function sameLineReason(tail) {
  return tail
    .replace(IDS_RE, '')
    .replace(SEPARATOR_RE, '')
    .replace(/\)\s*$/, '')
    .trim()
}

/** Is this line a comment with something in it that is not itself a suppression? */
function isReasonComment(line) {
  const m = COMMENT_RE.exec(line ?? '')
  if (!m) return false
  const content = m[1].trim()
  return content.length > 0 && !/^nosemgrep\b/.test(content)
}

/**
 * Every unjustified annotation in one file's text.
 * @param {string} text
 * @returns {{ line: number, text: string }[]}
 */
export function auditText(text) {
  const lines = text.split('\n')
  const bad = []
  lines.forEach((line, i) => {
    const m = MARKER_RE.exec(line)
    if (!m) return
    if (sameLineReason(m[1]).length >= MIN_REASON) return
    if (isReasonComment(lines[i - 1])) return
    bad.push({ line: i + 1, text: line.trim() })
  })
  return bad
}

/** The tracked files in scope, from git so vendored and generated trees are never read. */
export function trackedFiles(cwd = process.cwd()) {
  const out = execFileSync('git', ['ls-files', '-z', '--', ...AUDITED_GLOBS], {
    cwd,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  })
  return out.split('\0').filter(Boolean)
}

/** @returns {{ file: string, line: number, text: string }[]} */
export function auditRepo(cwd = process.cwd()) {
  return trackedFiles(cwd).flatMap((file) =>
    auditText(readFileSync(`${cwd}/${file}`, 'utf8')).map((v) => ({ file, ...v }))
  )
}

const invokedDirectly = process.argv[1]?.endsWith('nosemgrep-audit.mjs')
if (invokedDirectly) {
  const violations = auditRepo()
  if (violations.length > 0) {
    console.log(
      `nosemgrep annotations need ${MIN_REASON}+ characters of reason after the rule id, ` +
        'or a comment line directly above:'
    )
    for (const v of violations) console.log(`${v.file}:${v.line}: ${v.text}`)
    process.exitCode = 1
  } else {
    console.log('nosemgrep: every suppression carries its reason.')
  }
}
