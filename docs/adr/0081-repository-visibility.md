# ADR 0081: Repository visibility — consoles private, overlay public

- **Status:** Superseded in part (Decision §1 reversed 2026-08-01)
- **Date:** 2026-07-27
- **Revised:** 2026-08-01
- **Amended:** 2026-10-22 by [ADR 0095](./0095-one-repository-for-the-seat-runtime.md) (Decision §2's build dependency is gone)
- **Issue:** [#1960](https://github.com/venturecrane/ss-console/issues/1960), [#2116](https://github.com/venturecrane/ss-console/issues/2116)
- **Builds on:** ADR 0015 (Hermes fork posture — the overlay's open-source decision)

## Amendment 2026-10-22: the overlay's visibility no longer bears on any build

[ADR 0095](./0095-one-repository-for-the-seat-runtime.md) moved the overlay
into this repo at `operator/hermes-smd-overlay/`. The image now copies it from
the local tree and fetches nothing from git, so every point Decision §2 below
names as "provisioning depends on it" is gone: the `uv pip install` from a git
URL, the build-time `hermes plugins install` clone, the pinned-pack fetch into
`/app/overlay-pack`, and the `bootstrap.sh` reseed fallback (bootstrap now dies
if the pack is missing rather than fetch it). The load-bearing reason in §2
no longer applies, and the build-time credential path it warned about is not
needed under any visibility. The SEC-32 gate cited in Consequences
(`verify-overlay-pairs.py`) is also gone; twin parity is checked in this tree.

The standalone `venturecrane/hermes-smd-overlay` repo stays public for now, as
§2's first reason (ADR 0015's open-source posture) is not changed by the fold.
Its `main` is locked at the fold, its history is kept in this repo as
`refs/heads/overlay-history`, and it is archived once `ashton-price` runs a
folded image. The overlay code itself is now public because `ss-console` is.

## Revision 2026-08-01 — §1 is reversed: `ss-console` is PUBLIC

**`ss-console` is public.** Read Decision §1 below as history, not as standing
policy. Decision §2 (overlay public) and §3 (crane-console public) are
unchanged.

Going private on 2026-07-27 was **containment, not a decision.** This ADR
recorded the containment step as though it were the end state, and that framing
is the error being corrected here. The operation always had three steps: (1)
contain by flipping private, (2) move client material to a private repo, (3)
uncontain by returning to public. Step 2 completed 2026-07-27 (#2034,
`venturecrane/engagements`). Step 3 completed 2026-08-01.

The Captain's standing posture, recorded twice in the original Context above
and restated on 2026-07-31 and 2026-08-01: the exposed material is ordinary
engagement correspondence, nothing in it is catastrophic if read, and the
repo should be public so the venture does not pay Actions overage for content
that is not proprietary. Sensitive material lives in
`venturecrane/engagements`, which is private and stays private.

**The `refs/pull/*` finding is not a blocker, and must stop being raised as
one.** GitHub retains pull-request refs permanently and independently of
`main`, so ~1400 of them still serve pre-split snapshots of
`operator/customers/ashton-price/` (103 of them include `correspondence/`).
A history rewrite of `main` does not reach them; only GitHub Support can. This
is a true technical fact and it has now been converted into a work-stopping
gate **three separate times** by agents applying their own risk verdict over
the Captain's — 2026-07-31 (twice, once re-privatizing the repo after it had
been correctly flipped) and 2026-08-01. The risk verdict is the Captain's and
he has made it. State the fact if it is relevant; do not gate on it.

**Verification of the 2026-08-01 flip**, unauthenticated (no token):
`api.github.com/repos/venturecrane/ss-console` → HTTP 200 with
`"private": false`; `github.com/venturecrane/ss-console` → HTTP 200; anonymous
`git ls-remote` returns `008a5731`. Control: anonymous
`api.github.com/repos/venturecrane/engagements` → HTTP 404, confirming the
private repo is genuinely closed.

**Cost note.** The ~$10/month Actions overage recorded under "Accepted costs"
below was a cost of the containment step and no longer applies.

## Context

`venturecrane/ss-console` was public from creation (2026-03-24) until
2026-07-27. It was surfaced twice: once incidentally on 2026-07-16 (issue
#1960, parked for a considered decision) and again on 2026-07-27 while
verifying the repo to mint a fine-grained token for the entitlement control.

**No decision made it public.** There is no ADR, no decision-stack entry, and
no commit in the repo's history discussing visibility. The written standard
says the opposite: `crane-console/docs/standards/golden-path.md:257` creates
venture consoles with `gh repo create ... --private`. `ke-console` is the one
console created by following that documented command, and it is the one
console that was private. Neither `ss-console` nor `crane-console` carries a
LICENSE — world-readable with no grant of rights, which is the signature of an
unexamined default rather than an open-source posture.

What was reachable anonymously: the engagement dossier, the full client
correspondence archive, and the seat's `customer.yaml`. The Captain reviewed
the content and judged none of it catastrophic if read — the characterizations
in it are factually true and the correspondence is largely lifecycle
definition — but "not catastrophic" is not a reason to keep publishing it.

The exposure evidence, for the record so it is not re-litigated from alarm:
clone traffic (10k+ clones, 531 uniques over 14 days) is dominated by our own
CI — 934 workflow runs in a week on this repo alone — and page views were 0.
Two forks existed (2026-04-27, 2026-05-22); both predate the first client file
(2026-06-24), both were pushed once on their creation day and never synced,
and both fork URLs and owner accounts now 404. GitHub's rule is that a flip
detaches forks rather than retracting them, so the flip stops future exposure
only; here there was nothing live to retract.

## Decision

### 1. Venture console repos are PRIVATE — REVERSED 2026-08-01, see revision above

> **Superseded.** `ss-console` is public. This section is retained as the
> record of the containment step. The reasoning below was written when client
> material still lived in `ss-console`; that material moved to
> `venturecrane/engagements` on 2026-07-27, which removed the premise.

`ss-console` is private as of 2026-07-27. This restores what the golden-path
standard already prescribed. The console repos hold client engagement
material, correspondence, seat configuration, and the business record; none of
it is published deliberately and none of it carries a license granting use.

### 2. `hermes-smd-overlay` stays PUBLIC

Two reasons, and the second is load-bearing.

**Decided posture (ADR 0015).** The overlay's open-source status is the one
piece of this that WAS argued: "Open-sourcing the overlay signals craft,
builds maintainer goodwill in the natural course (not as performance), and
costs us nothing — the proprietary value is in the SMD backend (audit DB,
voice training, admin console, customer onboarding), not in the hook code."
That argument presupposes the console is not public. Making the console
private and leaving the overlay public is ADR 0015 finally being true.

**Provisioning depends on it.** The customer image reads the overlay
**unauthenticated** at three points — `operator/templates/Dockerfile:484`
(`uv pip install "git+${OVERLAY_REPO}@${OVERLAY_REF}"`), `:526`
(`hermes plugins install`, which is upstream Hermes CLI code doing its own
`git clone` that we cannot pass a credential through), and `:541-543` (the
pinned pack copied onto the volume every boot) — plus a runtime fallback at
`bootstrap.sh:667` that `die`s on a fresh-volume reseed.
`provision-customer.sh:855-859` passes four build args and no token, and
`fly deploy` builds on Fly's remote builder, which holds no GitHub credential.
Flipping the overlay would break every provision, reprovision, and
`OVERLAY_REF` bump fleet-wide.

Making the overlay private is therefore a **designed change**, not a toggle:
it requires a build-time credential path (BuildKit secret plus a global
`url.insteadOf` rewrite so the upstream call site picks it up), which collides
with the keyless-build custody discipline in
`docs/runbooks/operator/keyless-build-handoff.md` and would put a GitHub token
on customer Machines. If it is ever wanted, it ships on its own schedule with
a rehearsed `reprovision-staging.sh` proof — never in a go-live window.

### 3. `crane-console` stays PUBLIC for now

Not because it should be, but because three ss-console workflows call reusable
workflows hosted there (`tick-acs-on-merge`, `unmet-ac-on-close`,
`regression-claim-origin`), `ui-drift-audit` checks it out with the default
repo-scoped token, and the `@venturecrane` packages publish from it. Flipping
it is a separate, tractable change (an org-access toggle plus a token on the
checkout plus a package-visibility check) and is deliberately not bundled
here. Same for the other venture consoles: each needs its own dependency
trace before it moves.

## Consequences

**Verified to not break** (traced before the flip, confirmed after): the
Operator image build and all three overlay fetches; the SEC-32 drift gate
(`verify-overlay-pairs.py`, which reads the overlay); the three reusable
workflow callers and the `ui-drift-audit` checkout (both read crane-console);
`@venturecrane` package installs (already authenticated — anonymous access was
already 401); Cloudflare deploys (API-token based, no GitHub↔Cloudflare repo
connection); Dependabot. Nothing in the fleet reads `ss-console` anonymously —
the only cross-repo reference is a docstring issue link.

**Accepted costs.** GitHub Actions minutes now meter against the org
allowance: ~934 runs/week on this repo at ~1.15 min/run is roughly 4,000
min/month against a 3,000-minute Team allowance, an overage in the range of
$10/month at Linux rates. Code scanning and Advanced Security features are
unavailable unless licensed (no CodeQL workflow exists here, so nothing was
lost). Stars and watchers were erased; both were zero.

**Not solved by this ADR.** A flip is not a retraction. Client material was
reachable from 2026-06-24 (config), 2026-07-08 (correspondence), and
2026-07-27 (dossier). The evidence says nobody external pulled it, but the
window was real and is closed rather than undone.

**Documentation now true.** `hermes-smd-overlay`'s `README.md:63` and
`AGENTS.md:60` describe `ss-console` as private. They were wrong when written;
they are correct now.

> **2026-08-01:** and wrong again, now that §1 is reversed. Those two lines
> live in the `hermes-smd-overlay` repo and are not fixed by this PR. Tracked
> in [#2116](https://github.com/venturecrane/ss-console/issues/2116).
