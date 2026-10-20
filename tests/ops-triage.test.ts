/**
 * ops-triage emails the agent's own Subject line, not whatever ss-env printed.
 *
 * `operator/ops-machine/bin/ops-triage` runs the triage agent through
 * `ss-env`, which wraps `infisical run`. Infisical writes
 * `INF Injecting N Infisical secrets into your application process` to STDERR
 * even under `--silent`. The first version captured the agent with `2>&1`, so
 * that line became line 1 of the report, `head -1` never matched
 * "[SMD Ops] Triage", and from at least 2026-10-17 every triage email went out
 * titled "report (format not followed)", hiding "N need you" from the subject
 * the Captain reads. These tests run the real script against a fake HOME whose
 * `ss-env` reproduces that stderr line, and read what would have been sent.
 *
 * GIT_* IS STRIPPED and `git` is a stub: the script fetches and detaches its
 * repo clone, and this fixture must never touch the real repository.
 */
import { execFileSync } from 'node:child_process'
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const SCRIPT = join(REPO_ROOT, 'operator', 'ops-machine', 'bin', 'ops-triage')

const INFISICAL_LINE =
  '\u001b[90m3:47PM\u001b[0m \u001b[32mINF\u001b[0m Injecting 93 Infisical secrets into your application process'

const WORK_HEALTH_BODY = `Subject: [SMD Work] 2 need attention (1 new)

- smd-staging: something new
`

const homes: string[] = []
afterEach(() => {
  while (homes.length) rmSync(homes.pop()!, { recursive: true, force: true })
})

/** Run ops-triage once against a fake HOME. `agentOut`/`agentErr` are what the
 *  fake `claude` prints. Returns the subject and body handed to the sender. */
function runTriage(
  agentOut: string,
  agentErr = ''
): { subject: string; body: string; home: string } {
  const home = mkdtempSync(join(tmpdir(), 'ops-triage-'))
  homes.push(home)
  for (const d of ['bin', 'fakebin', 'dev/ss-console/operator'])
    mkdirSync(join(home, d), { recursive: true })
  writeFileSync(join(home, 'wh-body.txt'), WORK_HEALTH_BODY)
  writeFileSync(join(home, 'agent-out.txt'), agentOut)
  writeFileSync(join(home, 'agent-err.txt'), agentErr)

  // ss-env: infisical's stderr line first, then the wrapped command, faked.
  const ssEnv = `#!/bin/bash
printf '%s\\n' "$(cat "$HOME/infisical-line")" >&2
if [ "$1" = python3 ] && [ "$2" = bin/work-health.py ]; then cat "$HOME/wh-body.txt"; exit 0; fi
if [ "$1" = claude ]; then cat "$HOME/agent-out.txt"; cat "$HOME/agent-err.txt" >&2; exit 0; fi
if [ "$1" = python3 ] && [ "$2" = - ]; then
  cat >/dev/null
  printf '%s' "$3" > "$HOME/sent-subject"; cp "$4" "$HOME/sent-body"; echo "resend 200"; exit 0
fi
echo "fake ss-env: unexpected $*" >&2; exit 99
`
  writeFileSync(join(home, 'infisical-line'), INFISICAL_LINE)
  writeFileSync(join(home, 'bin', 'ss-env'), ssEnv)
  chmodSync(join(home, 'bin', 'ss-env'), 0o755)
  writeFileSync(join(home, 'fakebin', 'git'), '#!/bin/bash\nexit 0\n')
  chmodSync(join(home, 'fakebin', 'git'), 0o755)

  const env: Record<string, string> = {}
  for (const [k, v] of Object.entries(process.env))
    if (v !== undefined && !k.startsWith('GIT_')) env[k] = v
  env.HOME = home
  env.PATH = `${join(home, 'fakebin')}:${process.env.PATH ?? '/usr/bin:/bin'}`
  execFileSync('bash', [SCRIPT], { env, stdio: 'pipe' })

  const subjectPath = join(home, 'sent-subject')
  if (!existsSync(subjectPath)) {
    throw new Error(
      `nothing sent; log:\n${execFileSync('bash', ['-c', 'cat "$1"/logs/*', '_', home]).toString()}`
    )
  }
  return {
    subject: readFileSync(subjectPath, 'utf8'),
    body: readFileSync(join(home, 'sent-body'), 'utf8'),
    home,
  }
}

const REPORT = `Subject: [SMD Ops] Triage: 1 handled, 2 need you

NEEDS YOU (2)
- a&p: one.
`

describe('ops-triage report subject', () => {
  it('uses the agent Subject line even though ss-env writes to stderr', () => {
    const { subject, body } = runTriage(REPORT)
    expect(subject).toBe('[SMD Ops] Triage: 1 handled, 2 need you')
    expect(body).not.toContain('Infisical')
    expect(body).not.toContain('\u001b[')
    expect(body).not.toMatch(/^Subject:/m)
    expect(body.startsWith('NEEDS YOU (2)')).toBe(true)
  })

  it('finds the Subject line when the agent prints something before it', () => {
    const { subject, body } = runTriage(`Done investigating.\n\n${REPORT}`)
    expect(subject).toBe('[SMD Ops] Triage: 1 handled, 2 need you')
    expect(body).toContain('NEEDS YOU (2)')
    expect(body).not.toMatch(/^Subject:/m)
  })

  it('still flags a report with no Subject line, and keeps the whole text', () => {
    const { subject, body } = runTriage('I could not finish.\n')
    expect(subject).toBe('[SMD Ops] Triage: report (format not followed)')
    expect(body).toContain('I could not finish.')
  })

  it('keeps agent stderr out of the email, naming the file only when it holds more than infisical', () => {
    const quiet = runTriage(REPORT)
    expect(quiet.body).not.toContain('agent stderr')
    const noisy = runTriage(REPORT, 'Error: tool budget exceeded\n')
    expect(noisy.subject).toBe('[SMD Ops] Triage: 1 handled, 2 need you')
    expect(noisy.body).not.toContain('tool budget exceeded')
    expect(noisy.body).toMatch(/agent stderr: 1 line\(s\) in .*triage-report-.*\.err/)
  })
})
