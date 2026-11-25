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
  readdirSync,
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
 *  fake `claude` prints. Returns the fake HOME, whether or not anything was sent. */
/** What the fake workspace size check prints and its exit status. */
interface SizeCheck {
  out: string
  rc: number
}
const SIZE_OK: SizeCheck = {
  out: 'PASS size ops-x m1 (started): performance-8x, 16384 MB as authored',
  rc: 0,
}

function runTriageSilently(
  agentOut: string,
  agentErr = '',
  opts: { size?: SizeCheck; home?: string } = {}
): string {
  const home = opts.home ?? mkdtempSync(join(tmpdir(), 'ops-triage-'))
  if (!opts.home) homes.push(home)
  for (const d of ['bin', 'fakebin', 'dev/ss-console/operator'])
    mkdirSync(join(home, d), { recursive: true })
  writeFileSync(join(home, 'wh-body.txt'), WORK_HEALTH_BODY)
  writeFileSync(join(home, 'agent-out.txt'), agentOut)
  writeFileSync(join(home, 'agent-err.txt'), agentErr)
  const size = opts.size ?? SIZE_OK
  writeFileSync(join(home, 'size-out.txt'), size.out)
  writeFileSync(join(home, 'size-rc.txt'), String(size.rc))

  // ss-env: infisical's stderr line first, then the wrapped command, faked.
  // Every send appends its subject to sent-all; sent-subject is the last one.
  const ssEnv = `#!/bin/bash
printf '%s\\n' "$(cat "$HOME/infisical-line")" >&2
if [ "$1" = python3 ] && [ "$2" = bin/work-health.py ]; then cat "$HOME/wh-body.txt"; exit 0; fi
if [ "$1" = bash ] && [ "\${2##*/}" = ops-workspace-size ] && [ "$3" = check ]; then
  cat "$HOME/size-out.txt"; exit "$(cat "$HOME/size-rc.txt")"
fi
if [ "$1" = claude ]; then cat "$HOME/agent-out.txt"; cat "$HOME/agent-err.txt" >&2; exit 0; fi
if [ "$1" = python3 ] && [ "$2" = - ]; then
  cat >/dev/null
  printf '%s' "$3" > "$HOME/sent-subject"; cp "$4" "$HOME/sent-body"; printf '%s\\n' "$3" >> "$HOME/sent-all"
  echo "resend 200"; exit 0
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
  return home
}

/** Run ops-triage and return the subject and body handed to the sender. */
function runTriage(
  agentOut: string,
  agentErr = ''
): { subject: string; body: string; home: string } {
  const home = runTriageSilently(agentOut, agentErr)
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

  it('emails only NEEDS YOU and keeps FIXES for a session', () => {
    const report = [
      'Subject: [SMD Ops] Triage: 1 handled, 1 need you',
      '',
      'NEEDS YOU (1)',
      '- a&p: should we tell the firm.',
      '',
      'HANDLED (1)',
      '- a&p: nothing to do.',
      '',
      'FIXES (1)',
      '- change a file.',
      '  second line of it.',
      '',
    ].join('\n')
    const { subject, body, home } = runTriage(report)
    expect(subject).toBe('[SMD Ops] Triage: 1 handled, 1 need you')
    expect(body).toContain('should we tell the firm')
    expect(body).not.toContain('nothing to do')
    expect(body).not.toContain('change a file')
    const dir = join(home, 'state', 'fixes')
    const saved = readdirSync(dir).filter((f) => f.endsWith('.txt'))
    expect(saved).toHaveLength(1)
    expect(readFileSync(join(dir, saved[0]), 'utf8')).toContain('second line of it')
  })

  it.each(['0 need you', '0 needs you'])('sends nothing when the subject says %s', (count) => {
    const report = `Subject: [SMD Ops] Triage: 4 handled, ${count}\n\nHANDLED (4)\n- done.\n\nFIXES (1)\n- a fix.\n`
    const home = runTriageSilently(report)
    expect(existsSync(join(home, 'sent-subject'))).toBe(false)
    expect(readdirSync(join(home, 'state', 'fixes'))).toHaveLength(1)
    expect(readFileSync(join(home, 'state', 'triage-since'), 'utf8')).toMatch(/^20/)
  })

  it('sends nothing and commits the window when the subject omits the count and the body has no NEEDS YOU', () => {
    const report = 'Subject: [SMD Ops] Triage: 2 handled\n\nHANDLED (2)\n- one.\n- two.\n'
    const home = runTriageSilently(report)
    expect(existsSync(join(home, 'sent-subject'))).toBe(false)
    expect(readFileSync(join(home, 'state', 'triage-since'), 'utf8')).toMatch(/^20/)
  })

  it('reads the count from the NEEDS YOU heading when the subject omits it', () => {
    const report =
      'Subject: [SMD Ops] Triage: 2 handled\n\nNEEDS YOU (1)\n- seat: should we tell them.\n\nHANDLED (2)\n- one.\n'
    const { subject, body } = runTriage(report)
    expect(subject).toBe('[SMD Ops] Triage: 2 handled')
    expect(body).toContain('should we tell them')
  })

  it('skips an email whose body would be empty, and commits the window', () => {
    const report = 'Subject: [SMD Ops] Triage: 1 handled, 1 need you\n\nHANDLED (1)\n- one.\n'
    const home = runTriageSilently(report)
    expect(existsSync(join(home, 'sent-subject'))).toBe(false)
    expect(readFileSync(join(home, 'state', 'triage-since'), 'utf8')).toMatch(/^20/)
  })

  it('does not read 10 need you as 0', () => {
    const report = 'Subject: [SMD Ops] Triage: 0 handled, 10 need you\n\nNEEDS YOU (10)\n- x.\n'
    expect(runTriage(report).subject).toContain('10 need you')
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

// 2026-11-24: the A&P workspace ran at 2 shared CPUs and 4 GB all afternoon,
// authored 8 performance CPUs and 16 GB, and nothing said so. The triage runs
// bin/ops-workspace-size every hour and emails a wrong or unreadable size once.
describe('ops-triage workspace size', () => {
  const QUIET = 'Subject: [SMD Ops] Triage: 1 handled, 0 need you\n\nHANDLED (1)\n- one.\n'
  const WRONG: SizeCheck = {
    out:
      'FAIL size ops-x m1 (started): runs shared/2 CPU/4096 MB, authored performance-8x/16384 MB ' +
      '(performance/8 CPU); fix: operator/ops-machine/bin/ops-workspace resize x',
    rc: 1,
  }
  const sentAll = (home: string) =>
    existsSync(join(home, 'sent-all'))
      ? readFileSync(join(home, 'sent-all'), 'utf8').trim().split('\n')
      : []

  it('sends nothing when every workspace is at its authored size', () => {
    expect(sentAll(runTriageSilently(QUIET))).toEqual([])
  })

  it('emails a wrong size once, with the fix line, and not again while it stays the same', () => {
    const home = runTriageSilently(QUIET, '', { size: WRONG })
    expect(sentAll(home)).toEqual(['[SMD Ops] Workspace size: 1 wrong or unread'])
    expect(readFileSync(join(home, 'sent-body'), 'utf8')).toContain('ops-workspace resize x')
    runTriageSilently(QUIET, '', { size: WRONG, home })
    expect(sentAll(home)).toHaveLength(1)
  })

  it('emails again after the size was fixed and went wrong again', () => {
    const home = runTriageSilently(QUIET, '', { size: WRONG })
    runTriageSilently(QUIET, '', { home })
    expect(existsSync(join(home, 'state', 'workspace-size-reported'))).toBe(false)
    runTriageSilently(QUIET, '', { size: WRONG, home })
    expect(sentAll(home)).toHaveLength(2)
  })

  it('treats a check that fails without a FAIL line as unread, never as all clear', () => {
    const home = runTriageSilently(QUIET, '', { size: { out: '', rc: 2 } })
    expect(sentAll(home)).toEqual(['[SMD Ops] Workspace size: 1 wrong or unread'])
    expect(readFileSync(join(home, 'sent-body'), 'utf8')).toContain('exited 2 and said nothing')
  })

  it('still runs and emails the work-health triage beside a size email', () => {
    const home = runTriageSilently(REPORT, '', { size: WRONG })
    expect(sentAll(home)).toEqual([
      '[SMD Ops] Workspace size: 1 wrong or unread',
      '[SMD Ops] Triage: 1 handled, 2 need you',
    ])
  })
})
