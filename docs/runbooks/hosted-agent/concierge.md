# Hosted Agent — concierge runbook (ADR 0067)

Per-sale, Captain-side. The storefront and webhook automate everything up to the work item; this runbook is the manual half. Every numbered step is a named automation seam (ADR 0067 §Concierge seams).

## Trigger

Email to team@smd.services ("Hosted Agent purchase") + a new row in the admin queue at `admin.smd.services/admin/hosted-agent`. The subscription row exists at `provisioning`; the buyer has portal access showing "Setup in progress."

## Steps

1. **Wait for intake.** The buyer completes the questionnaire in their portal (queue row flips to `intake_submitted`). Nudge by replying to their welcome-email thread if it stalls.
2. **Assign the slug** in the queue (pattern `^[a-z0-9][a-z0-9-]{0,31}$`). This names the Fly app (`hermes-<slug>`), the Infisical path (`/ss/hosted/<slug>`), and unlocks the customer's key form.
3. **Key staging.** Two paths:
   - Portal relay wired (`INFISICAL_UA_*` env present): the buyer pastes the key in their portal; queue shows `key received`.
   - Relay not wired: collect at go-live via `crane_secret_set` (clipboard path) into `/ss/hosted/<slug>` as `ANTHROPIC_API_KEY`. Never ask for the key over chat/email in plaintext.
4. **AgentMail inbox.** Create the per-customer inbox in the AgentMail dashboard; register the webhook to `https://hermes-<slug>.fly.dev/webhooks/agentmail` (the path every live seat's `connectors.Email.webhook_url` names); stage the Svix signing secret as `WEBHOOK_SECRET_AGENTMAIL__<SLUG>` in `/ss`.
5. **Telegram bot** (when the intake row has a handle): create via BotFather, stage `TELEGRAM_BOT_TOKEN` as a Fly secret, resolve the buyer's numeric Telegram id, set `telegram.enabled: true` and put it in `telegram.allow_from` (MANDATORY: empty allowlist fails open). `allowed_users` is a deprecated alias; do not author it.
6. **Author customer.yaml** from `operator/customers/_hosted-template/` using the queue row's fields. Validate: `npx tsx scripts/validate-customer-yaml.ts operator/customers/<slug>/customer.yaml`. Commit via PR (git is the config source of truth, ADR 0012).
7. **Provision:** `operator/bin/reprovision.sh <slug>` then `operator/bin/boot-smoke-test.sh <slug>`.
8. **Send-a-message check.** From an allowlisted sender, email the agent; confirm the digest cron is scheduled; confirm Telegram round-trip if wired.
9. **Activate** in the admin queue: author the channel-details paragraph (bot handle, agent email address, allowlisted senders) and submit. This flips the subscription `active`, closes the work item, and emails the buyer.

## Adding or removing senders on request

A rostered sender can ask the agent to add people ("add these agents, here are their emails"). The open-house skill's **Access request** passes the exact addresses to scott@smd.services as an operations request (subject `Operations request from <sender> [ops <tag>]`) and tells the sender it was passed on. To act on it:

1. In one PR, change **only** `scope.inbound_allow_from` in `operator/customers/<slug>/customer.yaml`. Any other non-live key in the same diff (`record_stores`, `outbound_roster`, `ops_reply_from`) holds the whole change back.
2. Merge it, and wait for CI to publish `vaults/<slug>/customer.yaml` to R2.
3. Restart the Machine (`fly machine restart <id> -a hermes-<slug>`). The agent sees the new sender within seconds of the publish, but the workspace broker replies from a boot-time copy, so a new sender gets no reply until the restart.
4. Reply "done" to the `[ops <tag>]` email, and check the reply is addressed to the seat's own mailbox. The seat tells the sender. A reply addressed to team@smd.services never reaches the seat: that address is a Google Group, which rewrites an AgentMail seat's From to itself, so a reply built from From (the Gmail API does this) goes to the Group. Cards are addressed to scott@smd.services for that reason. A request nobody answers lapses after seven days and the sender is told. If the sender was already answered some other way, close the card instead so no second letter goes ([ops-close-by-smd](../operator/ops-close-by-smd.md)).

On the open-house seat, a new agent needs only `inbound_allow_from`: `owner_field: agent` makes them the owner of their own records. `readers` is for the broker.

## Hard rules

- `external_send: confirm` (ADR 0071, supersedes ADR 0067's launch draft-only posture) and a non-empty `inbound_allow_from` are the tier's channel constraint (ADR 0032 deferred list). `confirm` means the agent prepares the send and completes it only after an explicit in-turn owner approval over channel; the taint-gate still blocks even a confirmed send on a tainted turn. Do not raise to `autonomous` per customer during authoring — promotion is a per-skill control-plane act the owner performs later (ADR 0071 decision 5). Tightening to `draft_for_review` (agent never sends) is allowed if a customer asks for it.
- The Machine's `ANTHROPIC_API_KEY` is the CUSTOMER'S key. Never stage an SMD org key on a hosted seat.
- Payment failure handling follows ADR 0065: alert-and-wait, Captain decides, never a webhook side effect.

## Decommission

Standard offboarding (ADR 0065): `operator/bin/decommission-customer.sh <slug>` after the Stripe subscription cancels; the webhook has already marked the local row `cancelled`.
