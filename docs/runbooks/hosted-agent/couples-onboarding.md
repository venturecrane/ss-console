# Couples onboarding runbook

How a couple goes from nothing to a seat config under review, for the couples
hosted-agent role (ADR 0090, ADR 0091). SMD-paid proof of concept: there is no
checkout and no price.

Email is the default channel. Telegram is optional and can be connected any
time later; nothing below waits on it.

## 1. Create the instance (admin)

`admin.smd.services/admin/hosted-agent/couples` -> New instance: couple name,
agent name, each partner's name and email. This writes the entity, a
`hosted-agent` subscription at `provisioning` (no Stripe), the intake row with
`role = 'couples'`, and one partner row each. Nothing is sent.

On the instance page, assign the customer slug (the Fly app is
`hermes-<slug>`).

## 2. Invite each partner (admin)

Instance page -> Send invitation, once per partner. The first invitation
creates a Clerk Organization for the couple (created by you) and binds it to the
entity; each invitation is a Clerk org invitation (`org:member`) that lands on
`portal.smd.services/portal/products/hosted-agent/couples`. On first sign-in the
partner's verified email claims their partner row and grants the `partner`
role. An address SMD did not author is refused.

## 3. Each partner, in the portal

1. Their details and consent (recorded with time and wording version).
2. Settings they choose together: timezone, check-in topics, each partner's
   check-in and gratitude schedule, the daily reminder and weekly check-in
   times, quiet hours, and how the agent reaches each of them (email by
   default). A save is a new version that the saver agreed to; it is final only
   when the other partner confirms that same version.

The instance page shows, per partner: invited, signed in, consent, Telegram,
and confirmation of the current joint version, plus what still blocks the
generator.

## 4. Generate the seat config (reviewed PR)

From an ss-console checkout:

    npx tsx scripts/couples-seat.ts --intake <intake-id> --open-pr

It refuses until the slug, agent name, both consents and a joint version both
partners confirmed exist. It writes `operator/customers/<slug>/customer.yaml`
from `operator/customers/_couples-template/`, validates it, branches from
`origin/main`, and opens a PR. It never merges. Review the PR: a merged
customer.yaml publishes to R2 within a minute and the seat reads it on its next
boot. `hermes_ref` is the pin every live seat agrees on; pass `--hermes-ref`
when they disagree.

Schedules are converted to UTC at today's offset. Hermes at the pinned ref
(`345cd2b`) has no per-job timezone (a cron job runs in its profile's
timezone, and translate authors none), so a timezone that observes daylight
saving time drifts by an hour after each change. The generator prints a
warning and notes it in the file header for such a timezone (America/Phoenix
does not observe DST); regenerate after each change to keep the local time.

Each partner's preferred channel (email by default) is authored as
`people[].channel`. The seat delivers that partner's routines and two-person
notices on it: Telegram only when the partner chose Telegram AND is bound,
otherwise email. A shared routine goes to the group only when every partner is
reached on Telegram; otherwise it is one email to both.

Provision with `reprovision.sh <slug>` (Captain). A seat that forwards unknown
Telegram DMs to the binding hook refuses to boot if that hook is not loaded
(overlay activation gate), so a broken plugin is a visible boot failure, never
pairing codes to strangers.

Go live from the instance page (Activate) after the boot smoke test passes: the
go-live email, with the channel details you author, goes to both partners at
their authored addresses.

## 5. Telegram, optional and any time later (manual steps)

1. BotFather: `/newbot` for this couple, then `/setprivacy` -> Disable, so the
   bot sees the shared group. Stage the token as the Fly secret
   `TELEGRAM_BOT_TOKEN` on `hermes-<slug>`. Never put it in the repo.
2. Record the bot username on the instance page, then regenerate (step 4).
   With a bot recorded the seat's Telegram block is `enabled: true` with only
   the SMD sentinel id (`777000`, Telegram's service account, admits no one) on
   `allow_from`. With no bot and nobody bound it is `enabled: false`. Record the
   bot BEFORE staging the token, so the seat never runs a bot without an
   authored allowlist.
3. Each partner, from their setup page: Connect Telegram. The portal mints a
   single-use link (`t.me/<bot>?start=<token>`, 30 minutes, bound to that
   partner). The seat's bot receives `/start <token>`, the seat asks the console
   to redeem it (`POST /api/internal/telegram-bind`, the seat's Machine
   credential), and the binding is recorded. The partner is NOT yet admitted.
4. Regenerate (step 4). The bound id lands on `people[]`, `allow_from` and
   `group_allow_from` through the reviewed PR; after merge, restart the seat.
   The sentinel stays on `allow_from` until both partners are bound.
5. Shared group (optional): a partner creates a Telegram group with both
   partners and adds the bot. Read the group's chat id (negative; in Telegram
   Web it is the number after `#` in the address bar), record it on the
   instance page, regenerate, merge, restart.

Why the group id is recorded by hand: until it is authored, the seat
materializes an empty `group_allow_from`, which drops every group message at
the Telegram adapter before any plugin runs, so the binding hook never sees a
group.
