# Microsoft Graph Azure AD app registration

> **Two runbooks live in this file.** The section immediately below covers the
> **client-custody, app-only** path an Operator seat uses for the client's own
> mailbox ([ADR 0078](../../adr/0078-client-custody-email-channel.md)) — this is
> what a client's IT administrator does, in the client's own tenant, before
> stand-up day. Everything after it covers the older **multi-tenant delegated**
> app registered once in the SMD tenant for the admin console's OAuth flow. They
> are different apps for different purposes; do not conflate them.

## Client-custody app-only registrations — TWO apps, not one (ADR 0078)

**What the client's IT administrator must have ready before stand-up day: two app
registrations in the firm's own Microsoft Entra tenant, and one Exchange access
policy for each.** Provisioning refuses a seat that has only one — this is a hard
requirement, not a recommendation (Captain decision 2026-08-13).

### Why two

A Microsoft Graph **app-only** token is always issued for `/.default`: every
application permission the registration already holds, with no per-request
scope-down and no narrower variant. One registration is therefore one permission
set. If the app can read the mailbox and also holds `Mail.Send`, then anything
holding that app's credential can send — including a path inside the agent that
was never meant to transmit.

The Operator's mail credentials live in two different places on the seat. The
agent process holds the read credential (its delta poller and its `msgraph-mail`
tool surface both genuinely need it); the send credential is materialized to a
file only the workspace broker can read and is stripped from the agent's
environment before the agent starts. That split is only worth anything if the two
credentials belong to **different registrations with different grants** — which is
the only way this channel gets the vendor-enforced fence that a rogue in-agent
path cannot talk its way around.

Proven live on the `smdopslab` sandbox seat, 2026-08-13
(`vfy_01KZXX523V6JNWEETG4PSZDQY3`): the read app is refused `sendMail` with
`403 ErrorAccessDenied` while the broker's send app returns `202`, and the read
app still reads the pinned mailbox and is refused a tenant user list.

### App 1 — the READ app (the agent holds this)

1. **Entra → App registrations → + New registration.** Name it for what it is,
   e.g. `Operator Mail (read)`. **Single tenant** — this app is the firm's own,
   not a multi-tenant SMD app.
2. **API permissions → Microsoft Graph → Application permissions:**
   - `Mail.ReadWrite`
   - `MailboxSettings.Read` _(optional; only if the seat needs mailbox settings)_
   - **Do NOT add `Mail.Send`.** Adding it defeats the entire arrangement — this
     app's credential is the one the agent process holds.
3. **Grant admin consent** for the firm's tenant.
4. **Certificates & secrets → + New client secret.** Record the **Value** once.
5. Record the **Application (client) ID**. It is authored into the seat's
   `customer.yaml` as `connectors.Email.msgraph_auth.client_id`.

### App 2 — the SEND app (only the broker ever holds this)

1. A second registration, e.g. `Operator Mail (send)`. Single tenant, same tenant.
2. **API permissions → Microsoft Graph → Application permissions:** `Mail.Send`,
   and nothing else.
3. **Grant admin consent.**
4. Create a client secret and record the **Value** once.
5. This client ID and secret never appear in `customer.yaml`. They are staged as
   operator-env secrets (below) and reach the broker alone.

### Pin BOTH apps to the one mailbox (ApplicationAccessPolicy)

Without this, an application permission is tenant-wide — the app could reach every
mailbox in the firm. Run once per app, in Exchange Online PowerShell, as a tenant
admin:

```powershell
# Repeat for BOTH app ids. $Mailbox is the Operator's own mailbox.
New-ApplicationAccessPolicy `
  -AppId "<client-id>" `
  -PolicyScopeGroupId "<operator-mailbox-or-mail-enabled-security-group>" `
  -AccessRight RestrictAccess `
  -Description "Operator seat — pinned to the Operator mailbox only"

# Prove it, per app: Accessible = True for the Operator mailbox…
Test-ApplicationAccessPolicy -Identity "operator@firm.com"  -AppId "<client-id>"
# …and Accessible = False for any other mailbox in the tenant.
Test-ApplicationAccessPolicy -Identity "someone.else@firm.com" -AppId "<client-id>"
```

Policy changes can take up to ~30 minutes to take effect across Exchange.

### What SMD stages (per seat)

`msgraph_auth` in the seat's `customer.yaml` carries the non-secret read-side
fields (`tenant_id`, `client_id` — **the READ app**, `mailbox`, `secret_ref`). The
secrets are staged in the operator env (Infisical `/ss`, prod) under per-customer
names, where `<CID>` is the customer id upper-cased with `-` → `_`:

| Variable                            | Which app | Notes                                                                                                   |
| ----------------------------------- | --------- | ------------------------------------------------------------------------------------------------------- |
| `MSGRAPH_CLIENT_SECRET__<CID>`      | READ      | Matches `msgraph_auth.client_id`                                                                        |
| `MSGRAPH_SEND_CLIENT_ID__<CID>`     | SEND      | Must differ from the read client id                                                                     |
| `MSGRAPH_SEND_CLIENT_SECRET__<CID>` | SEND      | Must differ from the read secret                                                                        |
| `MSGRAPH_SEND_TENANT_ID__<CID>`     | SEND      | Optional — defaults to the read tenant, which is correct when both apps live in the client's one tenant |

`operator/bin/provision-customer.sh` refuses to provision an msgraph seat when the
send client id or secret is unstaged, equal to the read app's, or half-staged, and
its refusal names the exact variable to set. The refusal arms are driven in
`tests/msgraph-two-app-fence.test.ts`.

### Verify after stand-up

The two-app split is only proven by watching the read app be refused. From the
seat, acquire an app-only token with each credential and attempt
`POST /v1.0/users/<mailbox>/sendMail`: the read app must return
`403 ErrorAccessDenied` and the send app `202`. A read app that returns `202` has
`Mail.Send` on it and the seat is not fenced.

---

> **Status note (2026-05-25):** The in-tree Python adapter (`operator/connectors/ms_graph/`) was removed per the 2026-05-24 Hermes alignment. Mail and Calendar capabilities now bind to the hosted Microsoft 365 MCPs (`mcp:m365-mail`, `mcp:m365-calendar`) per ADR 0020 and need no in-house adapter. DocumentStorage (OneDrive/SharePoint) ships as a sub-plugin in `venturecrane/hermes-smd-overlay` (issue #1055). The Azure AD app registration described below is still required; the smoke-test invocation later in this runbook will be replaced when the overlay sub-plugin ships.

How to register the SMD Services Operator app in Microsoft Entra ID (Azure AD), obtain the client credentials used by the OAuth callback, and grant the Phase-1 scopes that the MS Graph adapter requires.

This runbook is performed **once** by Captain. The resulting `client_id` and `client_secret` are stored in Infisical at `/operator/shared/microsoft-graph/` and pushed to the ss-web Worker as `MICROSOFT_GRAPH_CLIENT_ID` / `MICROSOFT_GRAPH_CLIENT_SECRET`. Per-customer consent (and the resulting per-tenant tokens) is collected separately during customer provisioning — see "Customer onboarding" below.

## Prerequisites

- Azure subscription with rights to register applications in the SMD Services Entra ID tenant.
- `ADMIN_BASE_URL` set in production (`https://admin.smd.services`) and `PORTAL_BASE_URL` set (`https://portal.smd.services`).
- Access to Infisical for the SMD Services workspace.

## App registration steps

1. Sign in to <https://entra.microsoft.com> as a global admin on the SMD Services tenant.
2. Navigate to **Identity → Applications → App registrations** → **+ New registration**.
3. Configure:
   - **Name:** `SMD Services Operator`
   - **Supported account types:** _Accounts in any organizational directory (Any Microsoft Entra ID tenant — multitenant)_.
     - Multi-tenant is required because each customer law-firm tenant will consent independently.
   - **Redirect URI:** `Web` → `https://portal.smd.services/operator/oauth/microsoft-graph/callback`
     - This is the customer-facing portal subdomain per [oauth-lifecycle.md](../../specs/operator/oauth-lifecycle.md) "Re-consent callback URL". The admin-subdomain endpoint at `https://admin.smd.services/api/oauth/callback` from PR #936 was deleted 2026-09-10 (nothing initiated a flow that returned to it); do not register it as a second registered URI during the transition window.
4. Click **Register**. Record the **Application (client) ID** — this becomes `MICROSOFT_GRAPH_CLIENT_ID`.

## Configure API permissions (Phase 1)

Phase 1 ships read + draft only. `Mail.Send` is intentionally excluded — programmatic send is wave-2 stream #881.

1. **Manage → API permissions → + Add a permission → Microsoft Graph → Delegated permissions**.
2. Add exactly the following delegated scopes:
   - `User.Read` _(default; keep)_
   - `Mail.Read`
   - `Mail.ReadWrite`
   - `MailboxSettings.Read`
   - `Calendars.ReadWrite`
   - `Files.Read`
   - `Files.ReadWrite.AppFolder`
   - `offline_access` _(required to issue refresh tokens)_
3. Do **not** add `Mail.Send`, `Files.ReadWrite`, `Files.ReadWrite.All`, or any `.All` application permission. If one is added by mistake, remove it before saving — leaving it on the registration broadens the consent prompt the customer sees, even if the adapter does not exercise it.
4. Click **Grant admin consent for SMD Services** to pre-consent on the SMD Services tenant only. Customer tenants grant their own consent during onboarding (see below).

## Create a client secret

1. **Manage → Certificates & secrets → + New client secret**.
2. **Description:** `prod-<YYYYMM>` (e.g. `prod-202605`).
3. **Expires:** 12 months.
4. Click **Add** and immediately copy the secret **Value** (not the Secret ID). This value is shown only once.
5. Store the secret in Infisical:
   ```bash
   # Run from a machine with Infisical CLI already authenticated
   pbpaste | infisical secrets set MICROSOFT_GRAPH_CLIENT_SECRET --env=prod --path=/operator/shared/microsoft-graph --plain
   ```
6. Add the client ID to Infisical the same way under `MICROSOFT_GRAPH_CLIENT_ID`.
7. Push to Workers env:
   ```bash
   infisical export --env=prod --path=/operator/shared/microsoft-graph --format=dotenv \
     | npx wrangler secret bulk
   ```

Rotation: schedule the next rotation 30 days before the expiry date. Replace the secret value in Infisical, re-push to Workers, then delete the old secret in Entra after a 24-hour overlap window so in-flight customer consent flows are not interrupted.

## Customer onboarding (per-tenant consent)

When a new customer is provisioned, Captain runs `bin/reauth-connector.sh <customer-slug> microsoft-graph`, which:

1. Issues a signed OAuth state parameter bound to the customer slug and Captain's reviewer ID.
2. Generates an authorize URL of the form `https://login.microsoftonline.com/common/oauth2/v2.0/authorize?...&state=<signed>`.
3. Emails the URL to the customer's principal user listed in `customer.yaml` under `users[].role: principal`.

The customer signs in to their own Microsoft 365 tenant, reviews the requested scopes, and consents. Microsoft Entra redirects to the portal callback at `https://portal.smd.services/operator/oauth/microsoft-graph/callback?code=...&state=...`. The portal handler verifies the state, exchanges the code for tokens via the registered token endpoint, and proxies the resulting `{ access_token, refresh_token, expires_at, scopes }` payload to the per-customer Hermes Machine for atomic write to `/opt/data/oauth/microsoft.json` per [ADR 0010](../../adr/0010-per-customer-oauth-token-storage.md).

If the customer tenant blocks third-party multi-tenant apps, the customer's Microsoft 365 admin must first add the SMD Services Operator app from the Entra **Enterprise applications** blade. The error returned in that case is `AADSTS650056` — the portal callback surfaces it back to the customer as a short failure reason.

## Verification

After onboarding a customer:

```bash
# On the customer's Fly machine
fly ssh console -a hermes-<customer-slug>
ls -la /opt/data/oauth/
# Expect: microsoft.json, mode 0600, owned by uid 10000 (hermes)
```

Smoke-test invocation is pending — the in-tree `operator_ms_graph.smoke` module was removed in the 2026-05-24 burial. The overlay sub-plugin tracked in #1055 will ship its own smoke entrypoint; update this runbook with the new command when that lands.

Manual verification in the meantime: from the per-customer Machine, exchange a refresh token and call `GET https://graph.microsoft.com/v1.0/me` directly. A 401 indicates the token is missing or expired; a 403 indicates the customer tenant did not grant a required scope.

## Related references

- [oauth-lifecycle.md](../../specs/operator/oauth-lifecycle.md) — refresh policy, re-consent flow, failure modes
- [ADR 0010](../../adr/0010-per-customer-oauth-token-storage.md) — token storage decision
- [capability-contracts.md](../../specs/operator/capability-contracts.md) — Email / Calendar / DocumentStorage interfaces
- [customer-yaml-schema.md](../../specs/operator/customer-yaml-schema.md) — `connectors.Email/Calendar/DocumentStorage` bindings to the `microsoft-graph` adapter
