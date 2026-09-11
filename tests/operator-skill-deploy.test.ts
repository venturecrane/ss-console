/**
 * Skill-delivery pipeline coverage (#1206).
 *
 * Two halves of the same gap — a repo skill bound in customer.yaml must reach
 * the Machine, and a missing body must be caught BEFORE boot, not as a runtime
 * crash-loop:
 *
 *  1. The author-time guardrail in `scripts/validate-customer-yaml.ts` — a
 *     schema-valid customer.yaml that binds a skill with no deployable body
 *     fails the pre-provision gate (exit 1), while the real customer-zero config
 *     still passes.
 *  2. The boot-time seed in `operator/templates/bootstrap.sh` — the
 *     `/app/skills -> ${HERMES_HOME}/skills` overlay is ADDITIVE: it lands repo
 *     skills on the volume without clobbering agent-authored skills that live
 *     only there (ADR 0017).
 */
import { execFileSync } from 'node:child_process'
import {
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { parse as parseYaml, stringify as stringifyYaml } from 'yaml'
import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const SMD_CUSTOMER_YAML = join(REPO_ROOT, 'operator', 'customers', 'smd-staging', 'customer.yaml')
const BOOTSTRAP_SH = join(REPO_ROOT, 'operator', 'templates', 'bootstrap.sh')

interface ValidatorResult {
  code: number
  stdout: string
  stderr: string
}

/** Run the real validator CLI exactly as `provision-customer.sh` does. */
function runValidator(customerYamlPath: string): ValidatorResult {
  try {
    const stdout = execFileSync(
      'npx',
      ['--quiet', 'tsx', 'scripts/validate-customer-yaml.ts', customerYamlPath],
      { cwd: REPO_ROOT, encoding: 'utf8' }
    )
    return { code: 0, stdout, stderr: '' }
  } catch (err) {
    const e = err as { status?: number; stdout?: Buffer | string; stderr?: Buffer | string }
    return { code: e.status ?? 1, stdout: String(e.stdout ?? ''), stderr: String(e.stderr ?? '') }
  }
}

const tmpDirs: string[] = []
function makeTmpDir(): string {
  const dir = mkdtempSync(join(tmpdir(), 'skill-deploy-'))
  tmpDirs.push(dir)
  return dir
}

afterEach(() => {
  while (tmpDirs.length > 0) {
    const dir = tmpDirs.pop()
    if (dir) rmSync(dir, { recursive: true, force: true })
  }
})

/**
 * ss#2331. Every test in this file shells out — `npx --quiet tsx` to run the
 * real validator CLI, or `bash` to run the real bootstrap seed — so its duration
 * tracks machine load, not the code under test. Against vitest's 5000ms default
 * that produced a flake four separate sessions stopped to diagnose as their own
 * regression (measured 4.57s and 8.4s for the file on a loaded box, passing in
 * isolation and in CI every time).
 *
 * The timeout is raised rather than the subprocess mocked: running the REAL
 * validator and the REAL seed script is the point of these tests, and a mock
 * would leave a guard that cannot observe the thing it guards.
 *
 * A flake trains people to re-run rather than read, which is how a genuine
 * failure gets waved through.
 */
const SUBPROCESS_TIMEOUT_MS = 30_000

describe('validate-customer-yaml: author-time skill-body guardrail (#1206)', () => {
  it(
    'passes the real customer-zero config (no false positive)',
    () => {
      const result = runValidator(SMD_CUSTOMER_YAML)
      expect(result.code).toBe(0)
      expect(result.stdout).toContain('OK')
    },
    SUBPROCESS_TIMEOUT_MS
  )

  it(
    'fails a schema-valid config that binds a skill with no deployable body',
    () => {
      // Keep the real (cron/webhook-referenced) skill so the config stays
      // schema-valid; add one extra enabled skill whose body does not exist.
      const cfg = parseYaml(readFileSync(SMD_CUSTOMER_YAML, 'utf8')) as {
        personas: {
          skills: { name: string; version: string; enabled: boolean; initiation: unknown }[]
        }[]
      }
      cfg.personas[0].skills.push({
        name: 'nonexistent-skill-xyz',
        version: 'pending',
        enabled: true,
        initiation: { manual: true, scheduled: false, webhook: false },
      })
      const dir = makeTmpDir()
      const bad = join(dir, 'customer.yaml')
      writeFileSync(bad, stringifyYaml(cfg))

      const result = runValidator(bad)
      expect(result.code).toBe(1)
      expect(result.stderr).toContain('skill-body-missing')
      expect(result.stderr).toContain('nonexistent-skill-xyz')
    },
    SUBPROCESS_TIMEOUT_MS
  )

  it(
    'accepts a customer-local skill body (skills/<name>/SKILL.md beside customer.yaml)',
    () => {
      // Bind a skill that exists only in the customer-local catalog, not the
      // shared repo catalog — the second lookup location must satisfy the gate.
      const cfg = parseYaml(readFileSync(SMD_CUSTOMER_YAML, 'utf8')) as {
        personas: {
          skills: { name: string; version: string; enabled: boolean; initiation: unknown }[]
        }[]
      }
      cfg.personas[0].skills.push({
        name: 'customer-local-skill',
        version: 'pending',
        enabled: true,
        initiation: { manual: true, scheduled: false, webhook: false },
      })
      const dir = makeTmpDir()
      mkdirSync(join(dir, 'skills', 'customer-local-skill'), { recursive: true })
      writeFileSync(join(dir, 'skills', 'customer-local-skill', 'SKILL.md'), '# local\n')
      const yamlPath = join(dir, 'customer.yaml')
      writeFileSync(yamlPath, stringifyYaml(cfg))

      const result = runValidator(yamlPath)
      expect(result.code).toBe(0)
      expect(result.stdout).toContain('OK')
    },
    SUBPROCESS_TIMEOUT_MS
  )
})

describe('bootstrap.sh: skill-catalog seed is additive (#1206)', () => {
  it(
    'replaces stale aliases additively, preserving agent-authored skills, never touching the source',
    () => {
      const dir = makeTmpDir()
      const appSkills = join(dir, 'app', 'skills')
      const volSkills = join(dir, 'opt', 'data', 'skills')
      // Repo (image) catalog: two skills.
      mkdirSync(join(appSkills, 'inbox-triage'), { recursive: true })
      writeFileSync(join(appSkills, 'inbox-triage', 'SKILL.md'), '# repo body v2\n')
      mkdirSync(join(appSkills, 'proposal-drafter'), { recursive: true })
      writeFileSync(join(appSkills, 'proposal-drafter', 'SKILL.md'), '# proposal repo body\n')
      // Volume preexisting state:
      mkdirSync(volSkills, { recursive: true })
      //  (a) a stale SYMLINK alias to the image copy — the exact shape that made a
      //      bare `cp -a /app/skills/. .../skills/` abort with "are the same file"
      //      and crash-loop the boot on the customer-zero redeploy.
      symlinkSync(join(appSkills, 'inbox-triage'), join(volSkills, 'inbox-triage'))
      //  (b) a skill that lives ONLY on the volume — the agent-authored case (ADR 0017).
      mkdirSync(join(volSkills, 'agent-authored-skill'), { recursive: true })
      writeFileSync(join(volSkills, 'agent-authored-skill', 'SKILL.md'), '# authored at runtime\n')

      // The exact per-skill replace idiom from bootstrap.sh step 6b.
      const seed = `
      set -eu
      HERMES_HOME='${join(dir, 'opt', 'data')}'
      for _src in '${appSkills}'/*/; do
        [ -e "$_src" ] || continue
        _name=$(basename "$_src")
        _dst="$HERMES_HOME/skills/$_name"
        if [ -L "$_dst" ]; then rm -f "$_dst"; else rm -rf "$_dst"; fi
        cp -a "$_src" "$_dst"
      done
    `
      execFileSync('bash', ['-c', seed])

      // The stale alias is now a REAL dir carrying the repo body (no "same file" error).
      expect(lstatSync(join(volSkills, 'inbox-triage')).isSymbolicLink()).toBe(false)
      expect(readFileSync(join(volSkills, 'inbox-triage', 'SKILL.md'), 'utf8')).toContain(
        'repo body v2'
      )
      // The other repo skill landed on the volume.
      expect(readFileSync(join(volSkills, 'proposal-drafter', 'SKILL.md'), 'utf8')).toContain(
        'proposal repo body'
      )
      // The agent-authored skill was preserved, not wiped.
      expect(readFileSync(join(volSkills, 'agent-authored-skill', 'SKILL.md'), 'utf8')).toContain(
        'authored at runtime'
      )
      // Clearing the alias must NOT have mutated the image source it pointed at.
      expect(readFileSync(join(appSkills, 'inbox-triage', 'SKILL.md'), 'utf8')).toContain(
        'repo body v2'
      )
    },
    SUBPROCESS_TIMEOUT_MS
  )

  it(
    'bootstrap.sh seeds per-skill additively and never destructively wipes the catalog',
    () => {
      const script = readFileSync(BOOTSTRAP_SH, 'utf8')
      // The per-skill replace idiom must be present: scoped to one repo skill at a
      // time so agent-authored skills (present only on the volume) survive.
      expect(script).toContain('for _src in /app/skills/*/')
      expect(script).toContain('cp -a "${_src}" "${_dst}"')
      // Symlink-safety: an aliasing entry (the "are the same file" crash-loop) is
      // removed as a LINK, never recursed through into the read-only /app/skills.
      expect(script).toContain('[ -L "${_dst}" ]')
      expect(script).toContain('[ -L "${HERMES_HOME}/skills" ]')
      // Regression guard: the clear stays scoped to a single named skill — the
      // catalog ROOT is never wiped (that would delete agent-authored skills), and
      // its children are never globbed away.
      expect(script).not.toMatch(/rm\s+-rf\s+["']?\$\{HERMES_HOME\}\/skills["'\s]/)
      expect(script).not.toMatch(/rm\s+-rf\s+["']?\$\{HERMES_HOME\}\/skills\/\*/)
    },
    SUBPROCESS_TIMEOUT_MS
  )
})
