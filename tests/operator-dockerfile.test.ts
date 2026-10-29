/**
 * Regression guard: the customer-Machine Dockerfile must build the overlay from
 * this tree (operator/hermes-smd-overlay, ADR 0095) and fail CLOSED on a broken
 * harness install.
 *
 * Two stacked bugs once shipped a silently harness-less Machine (first-boot
 * audit, 2026-05-29, when the overlay was still a separately pinned repo):
 *   1. The overlay pin predated `shared/outbound_gate.py`, so the trust plugin's
 *      `from shared.outbound_gate import evaluate` would ImportError at runtime.
 *   2. The `hermes plugins install` step ended in `|| echo "WARN ...; continuing"`,
 *      swallowing that failure so the image built green and booted an agent with
 *      no trust/inbound/outbound harness.
 *
 * This is the exact fail-open antipattern the venture forbids (stub/NoOp default
 * reaches the live path and reports success). These assertions lock the fix, and
 * since the fold they also pin that there is no pin: the overlay is COPYed from
 * the build context and the ss-console commit is its version.
 *
 * @see operator/templates/Dockerfile
 * @see docs/runbooks/operator/first-boot.md
 * @see docs/adr/0028-outbound-integrity-gates-provenance-and-voice.md
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const DOCKERFILE = readFileSync(resolve('operator/templates/Dockerfile'), 'utf8')
const BOOTSTRAP = readFileSync(resolve('operator/templates/bootstrap.sh'), 'utf8')

// Strip `#`-comment lines so NEGATIVE assertions (e.g. "no honcho.server")
// match executable content only — the Honcho-deferral comments deliberately
// NAME the removed commands to explain why they are gone.
const stripHashComments = (s: string): string =>
  s
    .split('\n')
    .filter((l) => !/^\s*#/.test(l))
    .join('\n')
const DOCKERFILE_CODE = stripHashComments(DOCKERFILE)
const BOOTSTRAP_CODE = stripHashComments(BOOTSTRAP)

describe('Operator customer Machine Dockerfile', () => {
  it('builds the overlay from this tree: no pin, no git URL, no plugin install (ADR 0095)', () => {
    // Until 2026-10-22 the overlay was a separate repo pinned by ARG OVERLAY_REF
    // (203 bumps in 90 days). It is operator/hermes-smd-overlay now, COPYed into
    // the build context; the ss-console commit (SS_CONSOLE_SHA) is its version.
    expect(DOCKERFILE_CODE).not.toMatch(/^\s*ARG\s+OVERLAY_REF\b/m)
    expect(DOCKERFILE_CODE).not.toMatch(/^\s*ARG\s+OVERLAY_REPO\b/m)
    expect(DOCKERFILE_CODE).not.toMatch(/git\+https?:\/\//)
    expect(DOCKERFILE_CODE).not.toMatch(/hermes plugins install/)
    expect(BOOTSTRAP_CODE).not.toMatch(/hermes plugins install/)
    expect(DOCKERFILE_CODE).toMatch(/^COPY operator\/hermes-smd-overlay\/ \/app\/overlay-src\/$/m)
    expect(DOCKERFILE_CODE).toMatch(/^COPY operator\/hermes-smd-overlay\/ \/app\/overlay-pack\/$/m)
  })

  it('stages the pack with its two gates and never from the pip build directory', () => {
    // The pack Hermes loads is a second COPY of the tree, gated on the fan-out
    // and the activation hook (ss-console#1285); the pip source copy is removed.
    const flat = DOCKERFILE_CODE.replace(/\\\n/g, ' ')
    expect(flat).toMatch(/test -f \/app\/overlay-pack\/__init__\.py/)
    expect(flat).toMatch(/test -f \/app\/overlay-pack\/hooks\/smd-overlay-activation\/handler\.py/)
    expect(flat).toMatch(/rm -rf \/app\/overlay-src/)
    expect(flat.indexOf('rm -rf /app/overlay-src')).toBeLessThan(
      flat.indexOf('COPY operator/hermes-smd-overlay/ /app/overlay-pack/')
    )
  })

  it('refuses to build without the 40-hex ss-console commit, and bakes it as the version', () => {
    // An image whose version cannot be matched to a heartbeat is an image
    // `fleet release` can never verify. The sentinel file is what
    // resolve_overlay_ref() falls back to for a path install.
    const flat = DOCKERFILE_CODE.replace(/\\\n/g, ' ')
    expect(flat).toMatch(/grep -Eq '\^\[0-9a-f\]\{40\}\$'[^\n]*SS_CONSOLE_SHA must be the 40-hex/)
    expect(flat).toMatch(/> \/app\/OVERLAY_REF/)
    expect(DOCKERFILE_CODE).toMatch(/^ENV SMD_OVERLAY_REF=\$\{SS_CONSOLE_SHA\}$/m)
    // Root-owned with the pack: the agent uid must not be able to rewrite the
    // version a seat reports.
    const chown = /RUN chown -R root:root[\s\S]*?&& chown root:root/.exec(DOCKERFILE_CODE)
    expect(chown, 'no root-owned block').not.toBeNull()
    expect(chown![0]).toContain('/app/OVERLAY_REF')
    expect(chown![0]).toContain('/app/overlay-pack')
  })

  it('bootstrap dies without the image pack: no runtime install fallback, no older-image branch', () => {
    expect(BOOTSTRAP_CODE).toMatch(/\|\| die "overlay pack missing from the image/)
    expect(BOOTSTRAP_CODE).not.toMatch(/unpinned fallback/)
    expect(BOOTSTRAP_CODE).not.toMatch(/no image pack to refresh from/)
    // The root-home selection is a hard gate like the profile-home one, not `|| true`.
    expect(BOOTSTRAP_CODE).toMatch(
      /-m bootstrap\.plugin_selection "\$\{HERMES_HOME\}\/config\.yaml" \\\n\s*\|\| die/
    )
    expect(BOOTSTRAP_CODE).not.toMatch(/plugin_selection[^\n]*\|\| true/)
  })

  it('hard-asserts the overlay policy core is importable at build time', () => {
    // The deterministic build gate that catches a stale/mis-pinned `shared`
    // package (the v0.1.1 ModuleNotFoundError) before the Machine ever boots.
    expect(
      DOCKERFILE.includes('import shared.outbound_gate'),
      'Dockerfile must assert `import shared.outbound_gate` succeeds in the venv'
    ).toBe(true)
    expect(DOCKERFILE).toMatch(/from shared\.outbound_gate import evaluate/)
  })

  it('stamps the Hermes install with the pinned tag and sha, and proves it reads back as the gateway uid', () => {
    // 2026-10-28: every v0.22.5 seat reported `hermes --version` as
    // "vunknown (2026.9.24)". No install stamp, and the git fallback dies on
    // "dubious ownership" because /opt/hermes is root-owned and Hermes runs as
    // uid 10000. The stamp is the version; the build check makes it unshippable
    // to lose.
    const lines = DOCKERFILE_CODE.split('\n')
    const editable = lines.findIndex((l) => /uv pip install .*-e "\."/.test(l))
    const stamp = lines.findIndex((l) => /scripts\/write_install_stamp\.py/.test(l))
    expect(editable, 'the Hermes editable install line').toBeGreaterThan(-1)
    expect(stamp, 'the install stamp is written after Hermes is installed').toBeGreaterThan(
      editable
    )
    const block = lines.slice(stamp, stamp + 20).join('\n')
    expect(block).toMatch(/--output \/opt\/hermes\/install-stamp\.json/)
    expect(block).toMatch(/--commit "\$\{HERMES_UPSTREAM_SHA\}"/)
    expect(block).toMatch(/--base-version "\$\{HERMES_UPSTREAM_TAG#v\}"/)
    expect(block).toMatch(/--update-mechanism external/)
    // docker distribution makes Hermes demand a pm-runtime this image never builds.
    expect(block).not.toMatch(/--distribution/)
    expect(block).toMatch(/runuser -u hermes -- \/opt\/hermes\/\.venv\/bin\/python -c/)
    expect(block).toMatch(/assert i\.derived_version == want_version/)
    expect(block).toMatch(/assert i\.commit == want_sha/)
    const argDecls = lines.slice(editable, stamp).join('\n')
    expect(argDecls).toMatch(/^ARG HERMES_UPSTREAM_TAG$/m)
    expect(argDecls).toMatch(/^ARG HERMES_UPSTREAM_SHA$/m)
  })
})

/**
 * Regression guard: the first-boot build/runtime fixes (first real boot,
 * 2026-05-30). Three stacked defects meant the Machine had never booted:
 *   1. `postgresql-16` is not in Debian 13 (trixie ships PG 17) — build exit 100.
 *   2. The uv-created venv has no `pip` binary; `.venv/bin/pip install` exits 127.
 *   3. The venv was not on PATH, so bare `python3 -m honcho.*` / `hermes-smd`
 *      resolved to /usr/bin and crashlooped at boot.
 */
describe('Operator Machine first-boot build/runtime fixes', () => {
  it('installs postgresql-17 (Debian 13 default), never postgresql-16', () => {
    expect(
      /apt-get install[\s\S]*?postgresql-17 postgresql-client-17/.test(DOCKERFILE),
      'apt must install postgresql-17 / postgresql-client-17'
    ).toBe(true)
    expect(
      /apt-get install[\s\S]*?postgresql-16 postgresql-client-16/.test(DOCKERFILE),
      'postgresql-16 is not in Debian 13 trixie repos — build fails with exit 100'
    ).toBe(false)
    expect(DOCKERFILE, 'Postgres bin PATH must target /17/, not /16/').not.toMatch(
      /usr\/lib\/postgresql\/16\/bin/
    )
  })

  it('installs into the venv via `uv pip`, never the absent `.venv/bin/pip`', () => {
    expect(
      /\.venv\/bin\/pip install/.test(DOCKERFILE),
      'uv venvs ship no pip binary; `.venv/bin/pip install` exits 127 — use `uv pip install`'
    ).toBe(false)
  })

  it('bootstrap.sh puts the Hermes venv on PATH', () => {
    expect(
      /export PATH="\/opt\/hermes\/\.venv\/bin:/.test(BOOTSTRAP),
      'bootstrap.sh must prepend the venv to PATH so bare hermes/hermes-smd resolve to it'
    ).toBe(true)
    // Postgres is no longer started in bootstrap (Honcho deferred), so the
    // PG_BIN/17 reference moved out of bootstrap.sh — see the Honcho-deferral
    // suite below. Postgres 17 stays INSTALLED in the Dockerfile (asserted above).
  })

  it('bootstrap.sh launches the front-door gate on WEBHOOK_SECRET_MCP, not only on the AgentMail secret', () => {
    // 2026-09-16: removing a stale WEBHOOK_SECRET_AGENTMAIL from the first
    // client seat (msgraph, no agentmail) took public :8643 down with it —
    // Fly health check critical, Smokeball OAuth callback and every console
    // control unreachable — while the gateway on :8644 ran on. The gate serves
    // the whole public surface, so it launches whenever the seat carries any
    // secret it verifies. WEBHOOK_SECRET_MCP is staged on every seat by the
    // provisioner. The falsifier: revert the condition to the AgentMail-only
    // form and the first assertion fails.
    const launch = BOOTSTRAP_CODE.match(
      /if \[ -n "\$\{WEBHOOK_SECRET_MCP:-\}" \] \|\| \[ -n "\$\{WEBHOOK_SECRET_AGENTMAIL:-\}" \]; then/
    )
    expect(
      launch,
      'gate launch must key on WEBHOOK_SECRET_MCP or WEBHOOK_SECRET_AGENTMAIL'
    ).not.toBeNull()
    expect(BOOTSTRAP_CODE).not.toMatch(/if \[ -n "\$\{WEBHOOK_SECRET_AGENTMAIL:-\}" \]; then/)
    // The launch sits before the gateway exec, as a supervised child.
    const launchAt = BOOTSTRAP_CODE.indexOf(launch![0])
    const execAt = BOOTSTRAP_CODE.indexOf('hermes -p "${ACTIVE_PROFILE}" gateway run')
    expect(execAt, 'gateway exec missing').toBeGreaterThan(-1)
    expect(launchAt).toBeLessThan(execAt)
  })
})

/**
 * Regression guard: Honcho is deferred to Phase 2 (ADR 0016, revised
 * 2026-05-30). The never-booted Machine died at the fictional Honcho steps —
 * `python -m honcho.{migrations,server}` against `honcho-ai`, which is the
 * client SDK, not the server. These lock the deletion + the correct launch
 * verb so the boot path can't regress back into the fiction.
 *
 * Assertions run against comment-stripped content: the deferral comments
 * intentionally name the removed commands.
 */
describe('Operator Machine: Honcho deferred, flat-file core (ADR 0016 revised)', () => {
  it('Dockerfile does not install the fictional honcho-ai client SDK', () => {
    expect(
      /honcho-ai/.test(DOCKERFILE_CODE),
      'honcho-ai is the Honcho CLIENT SDK, not the server — it must not be installed'
    ).toBe(false)
    expect(/HONCHO_PIP_SPEC/.test(DOCKERFILE_CODE)).toBe(false)
  })

  it('bootstrap.sh does not run the fictional honcho migrations/server', () => {
    expect(
      /python3?\s+-m\s+honcho\.(migrations|server)/.test(BOOTSTRAP_CODE),
      '`python -m honcho.{migrations,server}` never existed — the boot died here'
    ).toBe(false)
  })

  it('bootstrap.sh launches the active-persona gateway daemon, not the default profile or the chat REPL', () => {
    // Must run `hermes gateway run` (daemon) targeting a persona profile via
    // `-p <slug>`. A bare `hermes gateway run` runs Hermes' built-in `default`
    // profile — no model, SOUL.md, skills, or connector wiring — i.e. not the
    // customer's agent. The `-p` flag is the regression guard for that bug.
    expect(
      /exec\s+\S*hermes\s+-p\s+\S+\s+gateway\s+run/.test(BOOTSTRAP_CODE),
      'an unattended Machine must `exec hermes -p <profile> gateway run`'
    ).toBe(true)
    expect(
      /exec\s+\S*hermes\s+chat\b/.test(BOOTSTRAP_CODE),
      '`hermes chat` is an interactive REPL — wrong for PID-1 with no TTY'
    ).toBe(false)
  })

  it('bootstrap.sh does not require the Honcho secrets at boot', () => {
    // HONCHO_API_KEY / SMD_D1_OBSERVATIONS_BINDING moved to OPTIONAL_ENV; a
    // Phase-1 boot must not fail-closed on their absence.
    const required = stripHashComments(BOOTSTRAP.match(/REQUIRED_ENV=\(([\s\S]*?)\n\)/)?.[1] ?? '')
    expect(/HONCHO_API_KEY/.test(required), 'HONCHO_API_KEY must not be required in Phase 1').toBe(
      false
    )
    expect(
      /SMD_D1_OBSERVATIONS_BINDING/.test(required),
      'SMD_D1_OBSERVATIONS_BINDING must not be required in Phase 1'
    ).toBe(false)
  })
})

describe('Operator Machine profile guards', () => {
  it('installs and selects the overlay inside the active profile Hermes home', () => {
    expect(
      BOOTSTRAP.includes('PROFILE_HERMES_HOME="${HERMES_HOME}/profiles/${ACTIVE_PROFILE}"'),
      'bootstrap.sh must name the Hermes home selected by `hermes -p <profile>`'
    ).toBe(true)
    expect(
      BOOTSTRAP.includes(
        'PROFILE_OVERLAY_PLUGIN_DIR="${PROFILE_HERMES_HOME}/plugins/hermes-smd-overlay"'
      ),
      'profiled plugin discovery must receive the pinned overlay pack'
    ).toBe(true)
    expect(
      BOOTSTRAP_CODE.includes('-m bootstrap.plugin_selection "${PROFILE_HERMES_HOME}/config.yaml"'),
      'the overlay must be selected in the active profile config'
    ).toBe(true)
  })

  it('never runs `hermes plugins enable` (#3564: it commits a PM env that strips the venv)', () => {
    // From Hermes v0.22 `plugins enable` runs package-manager admission, which
    // records a dependency generation under /opt/data/installs; every later
    // hermes process then boots off the image venv (the reverted #3567).
    // FALSIFIER: put either enable back and this fails.
    expect(BOOTSTRAP_CODE).not.toMatch(/hermes\b[^\n]*\bplugins enable\b/)
  })

  it('purges and asserts the PM dependency state right before the gateway exec, fail-closed (#3564)', () => {
    const purge = BOOTSTRAP_CODE.indexOf('-m bootstrap.pm_state purge "${HERMES_HOME}"')
    const check = BOOTSTRAP_CODE.indexOf('-m bootstrap.pm_state check "${HERMES_HOME}"')
    const tmp = BOOTSTRAP_CODE.indexOf('export TMPDIR=/tmp TMP=/tmp TEMP=/tmp')
    const gateway = BOOTSTRAP_CODE.indexOf(
      'exec /opt/hermes/.venv/bin/hermes -p "${ACTIVE_PROFILE}" gateway run'
    )
    expect(purge).toBeGreaterThan(-1)
    expect(check).toBeGreaterThan(purge)
    expect(tmp).toBeGreaterThan(-1)
    expect(gateway).toBeGreaterThan(check)
    expect(gateway).toBeGreaterThan(tmp)
    // The first open of each state.db is taken before the gateway, isolated (#3564).
    const preopen = BOOTSTRAP_CODE.indexOf('python3 -I -m bootstrap.state_db_open "${_state_db}"')
    expect(preopen, 'bootstrap.sh must pre-open every state.db').toBeGreaterThan(-1)
    expect(gateway).toBeGreaterThan(preopen)
    expect(BOOTSTRAP_CODE).toMatch(/-m bootstrap\.state_db_open "\$\{_state_db\}" \\\n\s*\|\| die/)
    expect(BOOTSTRAP_CODE).toMatch(
      /-m bootstrap\.pm_state check "\$\{HERMES_HOME\}" \\\n\s*\|\| die/
    )
  })

  it('re-asserts the authored webhook tool surface after the profile selection, fail-closed (#3564)', () => {
    // Hermes v0.22 `plugins enable` appends the plugin's toolset to every saved
    // platform_toolsets list. bootstrap no longer runs it; the re-assertion stays
    // as the guard. FALSIFIER: move it before the selection, or swallow its
    // failure, and a widened inbound email surface could ship.
    const enable = BOOTSTRAP_CODE.indexOf(
      '-m bootstrap.plugin_selection "${PROFILE_HERMES_HOME}/config.yaml"'
    )
    const reassert = BOOTSTRAP_CODE.indexOf('-m bootstrap.webhook_toolsets "${CUSTOMER_YAML}"')
    const gateway = BOOTSTRAP_CODE.indexOf(
      'exec /opt/hermes/.venv/bin/hermes -p "${ACTIVE_PROFILE}" gateway run'
    )
    expect(enable).toBeGreaterThan(-1)
    expect(reassert, 'bootstrap.sh never re-asserts the webhook toolsets').toBeGreaterThan(enable)
    expect(gateway).toBeGreaterThan(reassert)
    expect(BOOTSTRAP_CODE).toMatch(
      /-m bootstrap\.webhook_toolsets "\$\{CUSTOMER_YAML\}" "\$\{PROFILE_HERMES_HOME\}\/config\.yaml" \\\n\s*\|\| die/
    )
  })

  it('seeds and hard-gates the activation hook inside the active profile', () => {
    expect(
      BOOTSTRAP.includes('PROFILE_HOOKS_DIR="${PROFILE_HERMES_HOME}/hooks"'),
      'HookRegistry resolves hooks from the profiled Hermes home'
    ).toBe(true)
    expect(
      BOOTSTRAP.includes('ACTIVATION_HOOK_DIR="${PROFILE_HOOKS_DIR}/smd-overlay-activation"'),
      'the boot gate must validate the hook path the profiled gateway scans'
    ).toBe(true)
    expect(
      BOOTSTRAP.indexOf('ACTIVATION_HOOK_DIR="${PROFILE_HOOKS_DIR}') <
        BOOTSTRAP.indexOf('exec /opt/hermes/.venv/bin/hermes -p "${ACTIVE_PROFILE}" gateway run'),
      'the live activation hook must be installed before gateway startup'
    ).toBe(true)
  })

  it('packages and runs the disabled-skills guard before the gateway starts', () => {
    expect(
      DOCKERFILE.includes(
        'COPY operator/templates/ensure-disabled-skills.py /app/ensure-disabled-skills.py'
      ),
      'Dockerfile must package the disabled-skills guard'
    ).toBe(true)
    expect(
      BOOTSTRAP.includes('/app/ensure-disabled-skills.py "${CUSTOMER_YAML}" "${HERMES_HOME}"'),
      'bootstrap.sh must enforce persona skills_disabled after profile materialization'
    ).toBe(true)
    expect(
      BOOTSTRAP.indexOf('/app/ensure-disabled-skills.py') <
        BOOTSTRAP.indexOf('exec /opt/hermes/.venv/bin/hermes -p "${ACTIVE_PROFILE}" gateway run'),
      'disabled-skills guard must run before the Hermes gateway exec'
    ).toBe(true)
  })

  it('runs a convergent (not fixed-window) disabled-skills reconciler (ss#2230)', () => {
    expect(
      BOOTSTRAP.includes('ensure-disabled-skills.py --check'),
      'the reconciler must probe with --check until the prune has converged'
    ).toBe(true)
    expect(
      BOOTSTRAP.includes('[ "${_gateway_seen}" -eq 1 ] && [ "${_clean_streak}" -ge 3 ]'),
      'the reconciler must not exit on a clean streak until the gateway heartbeat shows ' +
        'its skill sync has run — a clean check before the sync proves nothing ' +
        '(the 2026-08-10 race, and the 2026-10-28 pilot rollback)'
    ).toBe(true)
    expect(
      BOOTSTRAP.includes('for _ in 1 2 3 4 5 6'),
      'the fixed 30s reconcile window is the ss#2230 defect; it must not return'
    ).toBe(false)
  })

  it('packages and runs the operator identity guard before the gateway starts', () => {
    expect(
      DOCKERFILE.includes(
        'COPY operator/templates/ensure-operator-identity.py /app/ensure-operator-identity.py'
      ),
      'Dockerfile must package the operator identity guard'
    ).toBe(true)
    expect(
      BOOTSTRAP.includes('/app/ensure-operator-identity.py "${CUSTOMER_YAML}" "${HERMES_HOME}"'),
      'bootstrap.sh must write customer-owned identity facts into SOUL.md'
    ).toBe(true)
    expect(
      BOOTSTRAP.indexOf('/app/ensure-operator-identity.py') <
        BOOTSTRAP.indexOf('exec /opt/hermes/.venv/bin/hermes -p "${ACTIVE_PROFILE}" gateway run'),
      'operator identity guard must run before the Hermes gateway exec'
    ).toBe(true)
  })
})

/**
 * The seat image's third-party Python is pinned and hash-checked (2026-09-10
 * code review, Dependencies HIGH, third review running). Every `uv pip install`
 * in the Dockerfile must be one of exactly two shapes:
 *
 *   1. a hash-locked requirements file under operator/requirements/, installed
 *      with --require-hashes (third-party packages);
 *   2. `--no-deps` over local paths only (our own packages, whose dependencies
 *      shape 1 already pinned; a path cannot carry a hash). The overlay is one
 *      of these since the fold (ADR 0095): `/app/overlay-src`, COPYed from
 *      operator/hermes-smd-overlay. Until 2026-09-25 its install line resolved
 *      the overlay's dependencies at build time (boto3 and sentry-sdk from PyPI,
 *      unlocked and unaudited; code review Dependencies 3). Now its third-party
 *      dependencies Hermes' lock does not already install come from
 *      hermes-overlay.txt (shape 1, installed first), and `uv pip check`
 *      runs after it so an unmet requirement fails the build.
 *
 * A bare package name on an install line is what this guards against: it is
 * how the broker venv shipped three unversioned packages for three months.
 */
describe('Operator Machine Python is pinned and hash-checked', () => {
  // Join backslash-continued lines so a multi-line RUN reads as one command,
  // then split on newlines, `&&` and `;` so each `uv pip install` is judged
  // on its own.
  const commands = DOCKERFILE_CODE.replace(/\\\n/g, ' ')
    .split(/\n|&&|;/)
    .map((c) => c.trim().replace(/^RUN\s+/, ''))
    .filter((c) => /^uv pip install\b/.test(c))

  /** The package operands of an install line: every token that is not a flag or a flag's value. */
  function operands(cmd: string): string[] {
    const tokens = cmd
      .replace(/^uv pip install\b/, '')
      .trim()
      .split(/\s+/)
    const out: string[] = []
    for (let i = 0; i < tokens.length; i++) {
      const t = tokens[i]
      if (!t) continue
      if (t === '--python' || t === '-p' || t === '-r' || t === '-e') {
        i++ // the flag's value
        if (t === '-e') out.push(`-e ${tokens[i]}`)
        continue
      }
      if (t.startsWith('-')) continue
      out.push(t)
    }
    return out
  }

  it('finds the install lines it is judging (the check can fail)', () => {
    expect(commands.length).toBeGreaterThanOrEqual(6)
  })

  it('every uv pip install is a hashed requirements file, a --no-deps local install, or the pinned overlay', () => {
    const offenders: string[] = []
    for (const cmd of commands) {
      const hashed =
        /--require-hashes/.test(cmd) &&
        /-r\s+("?\/app\/requirements\/[^\s"]+\.txt"?|"\$reqs")/.test(cmd) &&
        operands(cmd).length === 0
      const localOnly =
        /--no-deps/.test(cmd) &&
        operands(cmd).length > 0 &&
        operands(cmd).every((t) => /^(\/app\/|"\$cdir"|-e "\.")/.test(t))
      if (!hashed && !localOnly) offenders.push(cmd)
    }
    expect(offenders, 'an install line names a package without a hash-locked file').toEqual([])
  })

  it('the classifier refuses a bare package name (the check can fail)', () => {
    const bare = 'uv pip install --python /opt/x/.venv/bin/python google-auth pyyaml'
    expect(operands(bare)).toEqual(['google-auth', 'pyyaml'])
    expect(/--require-hashes/.test(bare) || /--no-deps/.test(bare)).toBe(false)
  })

  it('every requirements file the Dockerfile installs from exists, is hashed, and carries no ranges', () => {
    const referenced = new Set<string>()
    for (const m of DOCKERFILE_CODE.matchAll(/\/app\/requirements\/([a-z0-9_.-]+\.txt)/g)) {
      referenced.add(m[1])
    }
    // The connector loop references its file by name at build time.
    for (const dir of [
      'smokeball',
      'msgraph-mail',
      '_reference',
      'wordpress',
      'youtube-public',
      'ga4',
    ])
      referenced.add(`connector-${dir}.txt`)
    expect(referenced.size).toBeGreaterThanOrEqual(3)
    for (const file of referenced) {
      const text = readFileSync(resolve('operator/requirements', file), 'utf8')
      expect(/^[a-z0-9][a-z0-9._-]*==/m.test(text), `${file} pins nothing`).toBe(true)
      expect(/--hash=sha256:/.test(text), `${file} carries no hashes`).toBe(true)
      expect(/^[a-z0-9][a-z0-9._-]*\s*(>=|<=|~=|<|>)/m.test(text), `${file} carries a range`).toBe(
        false
      )
    }
  })

  it('imports the SMD note lane and command at build', () => {
    // FALSIFIER: drop either module from the build-time import smoke and an
    // image whose note lane cannot import boots, and no note wake is ever sent.
    expect(DOCKERFILE_CODE).toContain(
      'medchron.negotiation.run, medchron.note_lane, medchron.note_by_smd, medchron.litigation_lane'
    )
  })

  it('ships what the litigation status job reads court papers with, and asserts it at build', () => {
    // FALSIFIER: drop antiword from the apt line, striprtf from the import
    // line, or the entrypoint COPY, and a seat boots a lane that cannot read
    // a .doc or .rtf court paper or never makes its queue.
    expect(DOCKERFILE_CODE).toMatch(/poppler-utils fonts-dejavu-core antiword/)
    expect(DOCKERFILE_CODE).toContain(
      'medchron.litigation_lane, medchron.litigation.run, striprtf.striprtf'
    )
    expect(DOCKERFILE_CODE).toContain('command -v antiword >/dev/null')
    expect(DOCKERFILE_CODE).toContain(
      'COPY operator/templates/entrypoint-litigation.sh /app/entrypoint-litigation.sh'
    )
    expect(readFileSync(resolve('operator/requirements/medchron.txt'), 'utf8')).toMatch(
      /^striprtf==[0-9.]+ \\$/m
    )
  })

  it('imports the case file manager at build and ships its entrypoint', () => {
    // FALSIFIER: drop the casefile modules from the build-time import smoke or
    // the entrypoint COPY, and a seat boots a lane that cannot import, or whose
    // queue and inputs were never made.
    expect(DOCKERFILE_CODE).toContain(
      'medchron.casefile_lane, medchron.casefile.run, medchron.casefile.firm'
    )
    expect(DOCKERFILE_CODE).toContain(
      'COPY operator/templates/entrypoint-casefile.sh /app/entrypoint-casefile.sh'
    )
  })

  it('the requirements directory is copied into the image before any venv installs from it', () => {
    const copyAt = DOCKERFILE_CODE.indexOf('COPY operator/requirements/ /app/requirements/')
    const firstUse = DOCKERFILE_CODE.indexOf('/app/requirements/')
    expect(copyAt).toBeGreaterThan(-1)
    expect(copyAt).toBeLessThanOrEqual(firstUse)
  })

  it('the overlay installs --no-deps from the build context after its hashed dependencies, and uv pip check follows it', () => {
    const idx = (re: RegExp) => commands.findIndex((c) => re.test(c))
    const deps = idx(/-r\s+\/app\/requirements\/hermes-overlay\.txt/)
    const overlay = idx(/--no-deps \/app\/overlay-src\b/)
    expect(deps, 'hermes-overlay.txt is never installed').toBeGreaterThan(-1)
    expect(overlay, 'the overlay path install line is missing').toBeGreaterThan(-1)
    expect(deps).toBeLessThan(overlay)
    expect(commands[deps]).toMatch(/--python \/opt\/hermes\/\.venv\/bin\/python/)
    expect(commands[deps]).toMatch(/--no-config/)
    expect(operands(commands[overlay])).toEqual(['/app/overlay-src'])
    const flat = DOCKERFILE_CODE.replace(/\\\n/g, ' ')
    const overlayAt = flat.indexOf('--no-deps /app/overlay-src')
    const checkAt = flat.search(
      /python3 -I \/app\/requirements\/hermes_pip_check\.py\s+\/opt\/hermes\/\.venv\/bin\/python\s+\/app\/requirements\/hermes-security\.txt\s+\/opt\/hermes\/uv\.lock/
    )
    expect(checkAt, 'no hermes_pip_check.py over the Hermes venv').toBeGreaterThan(overlayAt)
  })

  /**
   * DP-1 (2026-10-27 code review): hermes-security.txt overrides named
   * Hermes-locked packages, hash-locked, into the Hermes venv after `uv sync`;
   * the check that follows tolerates only hermes-agent's own pin on each.
   * Since the v0.22.5 promotion (#3564) the one override is brotlicffi
   * (PYSEC-2026-4323); v0.22.5's lock carries the certifi and urllib3 fixes.
   */
  it('the Hermes venv gets hermes-security.txt hash-locked after uv sync and before the check', () => {
    const idx = (re: RegExp) => commands.findIndex((c) => re.test(c))
    const security = idx(/-r\s+\/app\/requirements\/hermes-security\.txt/)
    const overlayDeps = idx(/-r\s+\/app\/requirements\/hermes-overlay\.txt/)
    expect(security, 'hermes-security.txt is never installed').toBeGreaterThan(-1)
    expect(commands[security]).toMatch(/--python \/opt\/hermes\/\.venv\/bin\/python/)
    expect(commands[security]).toMatch(/--no-config/)
    expect(commands[security]).toMatch(/--require-hashes/)
    expect(commands[security]).toMatch(/--no-deps/)
    expect(security).toBeLessThan(overlayDeps)
    const flat = DOCKERFILE_CODE.replace(/\\\n/g, ' ')
    expect(flat.indexOf('uv sync --frozen')).toBeLessThan(
      flat.indexOf('-r /app/requirements/hermes-security.txt')
    )
    // No bare `uv pip check` over the Hermes venv survives beside the
    // tolerant one: it would fail the build on the brotlicffi override.
    expect(flat).not.toMatch(/uv pip check --python \/opt\/hermes\/\.venv\/bin\/python/)
    const pinned = readFileSync(resolve('operator/requirements/hermes-security.txt'), 'utf8')
    const names = [...pinned.matchAll(/^([a-z0-9][a-z0-9._-]*)==/gm)].map((m) => m[1]).sort()
    // tornado 6.6 (2026-10-28): three advisories on 6.5.10, inside
    // python-telegram-bot's `tornado~=6.5` range, so no pip-check exception.
    expect(names, 'hermes-security.txt overrides only brotlicffi and tornado').toEqual([
      'brotlicffi',
      'tornado',
    ])
  })

  it("compile.sh exports Hermes' lock with the same extras the image's uv sync installs", () => {
    const compile = readFileSync(resolve('operator/requirements/compile.sh'), 'utf8')
    const extras = (text: string) => [...text.matchAll(/--extra\s+([a-z0-9_-]+)/g)].map((m) => m[1])
    const syncLine = DOCKERFILE_CODE.split('\n').find((l) => /uv sync --frozen/.test(l))
    const exportAt = compile.indexOf('uv export --frozen')
    expect(syncLine, 'no uv sync --frozen line').toBeDefined()
    expect(exportAt).toBeGreaterThan(-1)
    const exportLines = compile.slice(exportAt, compile.indexOf(')', exportAt))
    expect(extras(exportLines).sort()).toEqual(extras(syncLine ?? '').sort())
    expect(extras(exportLines).length).toBeGreaterThan(0)
  })
})

/**
 * npm installs in the seat image go through a lockfile (2026-09-25 code
 * review, Dependencies 3). `npm install <name>@<version>` pins one package and
 * floats its transitive closure; `npm install` in a tree re-resolves where the
 * lock disagrees. Every install must be `npm ci`, which installs the lock or
 * fails the build.
 */
describe('Operator Machine npm installs are lock-pinned', () => {
  const npmCommands = DOCKERFILE_CODE.replace(/\\\n/g, ' ')
    .split(/\n|&&|;|\(|\)/)
    .map((c) => c.trim().replace(/^RUN\s+/, ''))
    .filter((c) => /^npm (install|i|ci|add)\b/.test(c))

  it('finds the npm install lines it is judging (the check can fail)', () => {
    expect(npmCommands.length).toBeGreaterThanOrEqual(2)
  })

  it('every npm install is npm ci', () => {
    expect(npmCommands.filter((c) => !/^npm ci\b/.test(c))).toEqual([])
  })

  it('the clio-mcp lock the image installs from exists and pins @oktopeak/clio-mcp exactly', () => {
    expect(DOCKERFILE_CODE).toContain(
      'COPY operator/npm/clio-mcp/package.json operator/npm/clio-mcp/package-lock.json /opt/clio-mcp/'
    )
    const pkg = JSON.parse(readFileSync(resolve('operator/npm/clio-mcp/package.json'), 'utf8')) as {
      dependencies: Record<string, string>
    }
    expect(pkg.dependencies['@oktopeak/clio-mcp']).toMatch(/^\d+\.\d+\.\d+$/)
    const lock = JSON.parse(
      readFileSync(resolve('operator/npm/clio-mcp/package-lock.json'), 'utf8')
    ) as { packages: Record<string, { version?: string; integrity?: string }> }
    const entry = lock.packages['node_modules/@oktopeak/clio-mcp']
    expect(entry?.version).toBe(pkg.dependencies['@oktopeak/clio-mcp'])
    const unhashed = Object.entries(lock.packages)
      .filter(([k, v]) => k !== '' && !v.integrity)
      .map(([k]) => k)
    expect(unhashed, 'lock entries without an integrity hash').toEqual([])
  })
})

describe('Dockerfile: one image for every seat, layers cached across releases (fleet release, 2026-10-19)', () => {
  const lines = DOCKERFILE_CODE.split('\n')
  const runtimeStart = lines.findIndex((l) => /^FROM debian:/.test(l))
  const runtime = lines.slice(runtimeStart)
  const firstIndex = (re: RegExp): number => runtime.findIndex((l) => re.test(l))

  it('takes no per-seat CUSTOMER_SLUG build arg (the slug reaches the Machine only as fly.toml [env])', () => {
    expect(DOCKERFILE_CODE).not.toMatch(/^\s*(ARG|ENV)\s+CUSTOMER_SLUG\b/m)
    expect(DOCKERFILE_CODE).not.toMatch(/\$\{?CUSTOMER_SLUG/)
  })

  it('declares SS_CONSOLE_SHA only after every heavy layer and every venv (apt, npm, uv, the Hermes build, the overlay install)', () => {
    // Every RUN after an ARG declaration in a stage is invalidated when it
    // changes. FALSIFIER: move `ARG SS_CONSOLE_SHA` back to the top of the stage
    // and every release rebuilds apt, npm, uv and both pip installs from scratch.
    // Since the fold it is the only per-release ARG: the overlay is COPYed from
    // the tree, so a code change invalidates from its COPY down and nothing
    // before it.
    const shaArg = firstIndex(/^ARG SS_CONSOLE_SHA\b/)
    const heavy = [
      /^RUN apt-get update/,
      /^RUN npm ci/,
      /^RUN uv sync/,
      /^RUN cd web && npm run build/,
      /^COPY operator\/hermes-smd-overlay\/ \/app\/overlay-src\//,
      /^COPY operator\/hermes-smd-overlay\/ \/app\/overlay-pack\//,
    ].map(firstIndex)
    expect(shaArg).toBeGreaterThan(0)
    for (const idx of heavy) {
      expect(idx).toBeGreaterThan(0)
      expect(shaArg).toBeGreaterThan(idx)
    }
    // No `uv venv` or `npm ci` after it: every venv is built before the sha lands.
    const after = runtime.slice(shaArg).join('\n')
    expect(after).not.toMatch(/^RUN[^\n]*(uv venv|npm ci|uv sync)/m)
    // ...and immediately before its first use.
    expect(runtime.slice(shaArg, shaArg + 2).join('\n')).toMatch(/^RUN[^\n]*\$\{SS_CONSOLE_SHA\}/m)
    expect(firstIndex(/ENV SMD_OVERLAY_REF=\$\{SS_CONSOLE_SHA\}/)).toBeGreaterThan(shaArg)
  })

  it('bakes the capability file a skills release is checked against, after every COPY into /app and /opt', () => {
    const gen = firstIndex(/gen-image-capabilities\.py \/app\/image-capabilities\.json/)
    expect(gen).toBeGreaterThan(0)
    const lastCopy = runtime.reduce(
      (acc, l, i) => (/^COPY\s+(?!--from)/.test(l) && !/gen-image-capabilities/.test(l) ? i : acc),
      -1
    )
    expect(gen).toBeGreaterThan(lastCopy)
  })

  it('copies nothing from operator/customers (no client content in the shared image)', () => {
    const copies = DOCKERFILE_CODE.split('\n').filter((l) => /^COPY\s/.test(l))
    expect(copies.length).toBeGreaterThan(10)
    expect(copies.filter((l) => /operator\/customers/.test(l))).toEqual([])
  })

  it('the Hermes pin lives here, not in customer.yaml', () => {
    expect(DOCKERFILE).toMatch(/^ARG HERMES_UPSTREAM_SHA=[0-9a-f]{40}$/m)
    expect(DOCKERFILE_CODE).not.toMatch(/from customer\.yaml hermes_ref/)
  })
})
