# Couples seat setup runbook

How SMD sets up a seat for the couples hosted-agent role (ADR 0090, ADR 0091).
SMD-paid proof of concept: there is no checkout and no price.

There is no web setup. The couple never visits the portal. Everything after
the seat boots, settings and schedules included, happens in conversation with
the agent over its channels: email now, Telegram later.

## 1. Author the seat

1. Copy `operator/customers/_couples-template/customer.yaml` to
   `operator/customers/<slug>/customer.yaml` (the Fly app is `hermes-<slug>`).
   Copy the template, never another couple's seat.
2. Fill in every bracketed value. The two partners go in `people:` (name,
   lowercased email, `channel: email`); the same two addresses are the whole of
   `scope.inbound_allow_from`. Leave `telegram_id` empty and `telegram.enabled`
   false.
3. Validate:

       npx tsx scripts/validate-customer-yaml.ts operator/customers/<slug>/customer.yaml

4. Open a PR and merge it. A merged customer.yaml publishes to R2 within a
   minute and the seat reads it on its next boot.

## 2. Inbox and provisioning

1. Create the seat's AgentMail inbox and webhook, exactly as for any hosted
   seat (`docs/runbooks/hosted-agent/concierge.md` step 4).
2. Provision with `operator/bin/reprovision.sh <slug>` (Captain), then
   `operator/bin/boot-smoke-test.sh <slug>`.

## 3. First contact

The agent emails each partner from its own inbox. From then on the partners
set the check-in topics, schedules, quiet hours and the rest by replying to
it; joint changes still need both partners (two-person acts, ADR 0091).

## 4. Telegram, later

When a partner wants Telegram, SMD creates the couple's bot (BotFather,
`/setprivacy` -> Disable), stages `TELEGRAM_BOT_TOKEN` as a Fly secret on
`hermes-<slug>` (never in the repo), and records that partner's numeric
Telegram id in `people[].telegram_id`, `telegram.allow_from` and
`telegram.group_allow_from` through a PR, with `telegram.enabled: true`.
Reprovision after merge. A shared group's chat id (negative) goes in
`spaces.shared_telegram_chat` and `telegram.group_chats` the same way.
