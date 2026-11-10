/**
 * Touch map: which changed paths reach a client.
 *
 * The runtime-AC merge gate (scripts/runtime-ac-proof.mjs) used to read only
 * the PR body, so a PR that changed a client lane and wrote no `(runtime)` row
 * at all passed: there was nothing in the body to check. This module answers
 * the one question the body cannot: does the diff touch something a client
 * reaches? It reads only the changed paths, which a machine can see, never the
 * author's account of the change.
 *
 * Four classes, most specific first:
 *
 *   send-path    code that composes, binds or transmits a message a client
 *                receives (the reply plugin, the broker's send verbs and
 *                fences, the mail connector, the send-render contract)
 *   client-lane  code a client's request runs through on a seat (the job
 *                runners, connectors, skills, the broker, the overlay's
 *                plugins and shared modules, the drafting templates)
 *   seat         what a seat is built and booted from (the image templates,
 *                the overlay's bootstrap and appliers, a live customer.yaml,
 *                provisioning and boot smoke)
 *   none         everything else: docs, tests, CI, hooks, the web app,
 *                workers, laptop-side fleet and ops tooling
 *
 * Test files never count. A change confined to tests changes no behavior on
 * a seat, and counting it would mark every test-hardening PR as client work.
 *
 * DERIVED FROM THE REPLAY, not from guesswork: the globs were read off the
 * changed-path distribution of the 1,120 PRs merged 2026-09-01 to 2026-11-09,
 * and tests/fixtures/touch-map/replay.json pins the classification of named
 * PRs on both sides (tests/touch-map.test.ts). Laptop-side fleet tooling
 * (operator/bin/lib/fleet) is deliberately `none`: it builds and releases
 * seats but no client request runs through it, and the release itself is
 * already proven by its own checks.
 */

/** A path segment or filename that marks a test, fixture or test kit. */
const TEST_PATH_RE =
  /(^|\/)(tests?|__tests__|fixtures|testdata)\/|(^|\/)(test_[^/]*|[^/]*_test\.py|[^/]*\.(test|spec)\.[cm]?[jt]sx?|conftest\.py|[^/]*testkit[^/]*)$/

/**
 * Ordered rules. The first match wins, so the narrow send-path rules sit above
 * the broad client-lane rules that would otherwise swallow them.
 * @type {{ cls: 'send-path' | 'client-lane' | 'seat', re: RegExp }[]}
 */
export const TOUCH_RULES = [
  // send-path
  { cls: 'send-path', re: /^operator\/hermes-smd-overlay\/plugins\/hermes-smd-reply\// },
  {
    cls: 'send-path',
    re: /^operator\/workspace_broker\/(reply_binding|transmit_verbs|send_as_acts|participant_fence|participant_lookup|delivery_proof|msgraph_ops|agentmail_ops)\.py$/,
  },
  { cls: 'send-path', re: /^operator\/connectors\/msgraph-mail\// },
  { cls: 'send-path', re: /^operator\/contracts\/send-render\.yaml$/ },

  // client-lane
  { cls: 'client-lane', re: /^operator\/runners\// },
  { cls: 'client-lane', re: /^operator\/connectors\// },
  { cls: 'client-lane', re: /^operator\/skills\// },
  { cls: 'client-lane', re: /^operator\/workspace_broker\// },
  { cls: 'client-lane', re: /^operator\/templates\/drafting\// },
  { cls: 'client-lane', re: /^operator\/hermes-smd-overlay\/(plugins|shared)\// },

  // seat
  { cls: 'seat', re: /^operator\/templates\// },
  { cls: 'seat', re: /^operator\/hermes-smd-overlay\// },
  { cls: 'seat', re: /^operator\/customers\/(?!_template\/)[^/]+\// },
  { cls: 'seat', re: /^operator\/bin\/(provision-customer|boot-smoke-test)\.sh$/ },
  { cls: 'seat', re: /^operator\/bin\/lib\/(stage-[^/]+|medchron-vendor-[^/]+)\.sh$/ },
]

/** The classes that make a PR a client-surface PR. */
export const CLIENT_SURFACE_CLASSES = ['send-path', 'client-lane', 'seat']

/**
 * The class of one changed path.
 * @param {string} path - repo-relative, forward slashes
 * @returns {'send-path' | 'client-lane' | 'seat' | 'none'}
 */
export function classifyPath(path) {
  const p = String(path ?? '').replace(/^\.\//, '')
  if (p === '' || TEST_PATH_RE.test(p)) return 'none'
  for (const rule of TOUCH_RULES) if (rule.re.test(p)) return rule.cls
  return 'none'
}

/**
 * The classes a PR's changed paths touch, and the paths behind each.
 *
 * `cls` is the most specific class touched (send-path over client-lane over
 * seat), or `none`. `clientSurface` is true when any path is a client surface.
 *
 * @param {string[]} paths
 * @returns {{ cls: 'send-path' | 'client-lane' | 'seat' | 'none', clientSurface: boolean, byClass: Record<string, string[]> }}
 */
export function classifyPaths(paths) {
  /** @type {Record<string, string[]>} */
  const byClass = {}
  for (const p of paths ?? []) {
    const c = classifyPath(p)
    if (c === 'none') continue
    ;(byClass[c] ??= []).push(p)
  }
  const cls = CLIENT_SURFACE_CLASSES.find((c) => byClass[c]) ?? 'none'
  return { cls, clientSurface: cls !== 'none', byClass }
}
