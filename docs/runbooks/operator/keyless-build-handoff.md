# Keyless build → inject-at-handoff (Operator provisioning discipline)

**Issue:** [#1783](https://github.com/venturecrane/ss-console/issues/1783) · **Guard:** `operator/bin/lib/secret_custody.py` · **Classification:** `operator/contracts/env-consumption.yaml` (`custody:`)

## Why

`provision-customer.sh` is driven by `reprovision.sh` = `infisical run --env=prod --path=/ss -- provision-customer.sh <slug>`, which injects the **entire `/ss` vault** as env vars and stages the customer-owned ones onto the Machine in one uninterrupted pass. We have repeatedly been burned by real secrets and fixture data entering the build too early. The discipline: **build the overlay against placeholders, inject real customer credentials only at handoff through a door we never see.**

## The custody boundary

Every provisioning secret is one of two classes (see `operator/contracts/env-consumption.yaml` `custody:` and the connector tables in `secret_custody.py`):

- **infra** — operator/SMD-owned (R2, Fly, Sentry, HMAC masters, bucket names, D1 bindings). Safe to be **real or emulated** during a build; we hold it regardless of which customer is provisioning.
- **customer** — carries or grants access to a specific customer's world (their model key, connector OAuth creds, inbox/webhook secrets, Google identity). Enters **only at handoff**. A build sees a placeholder; staging/keyless runs must not borrow the live value. `GOOGLE_*` is customer-by-**effect** (DWD impersonation) → staging **substitutes** an isolated credential rather than the live one.

Classify one name:

```bash
uv run --with pyyaml python3 operator/bin/lib/secret_custody.py classify SMOKEBALL_CLIENT_ID   # -> customer
```

**Fail-closed rule:** an unclassified secret raises. `operator/bin/tests/test_secret_custody.py::test_every_staged_secret_is_classified` asserts every secret `provision-customer.sh` stages is classified — so a newly added customer secret cannot slip past the placeholder allowlist. If you add a staged secret, classify it in the same PR (default customer-owned secrets to `customer`).

## Phase A — keyless build (no real customer secret in scope)

Validate + materialize `customer.yaml`, render `fly.toml`, create app/volume/bucket, deploy, boot smoke test, safety_substrate invariants, overlay activation gate — all with **placeholder** customer-owned values and real (or emulated) infra creds. The Machine comes up fully governed and **inert on every customer capability**; connectors bound to placeholders fail closed at the connector boundary (existing posture).

The live, exercised form of Phase A today is **staging**:

```bash
operator/bin/reprovision-staging.sh          # hermes-smd-staging, wired to nothing real
```

It computes the isolate set from the classification (`secret_custody.py isolate-names smd-staging`), **blanks every customer-owned secret**, and **substitutes** an isolated Google SA. This replaced a hand-maintained `CLIO_*`-only denylist that failed open (it leaked AgentMail / Smokeball / per-seat Anthropic / webhook secrets onto staging the moment a staging seat bound that connector).

Inspect the isolate set for any seat:

```bash
uv run --with pyyaml python3 operator/bin/lib/secret_custody.py isolate-names <slug>
```

## Phase B — real injection at handoff (the door we never see)

The customer (or Captain at go-live) injects each customer-owned secret through a relay whose plaintext the build never observes:

- **BYO Anthropic (Hosted Agent):** `src/lib/operator/infisical-secret-transport.ts` writes the key to per-customer `/ss/hosted/<slug>/ANTHROPIC_API_KEY`; the provisioner's per-seat path reads it back. Ships dark until `INFISICAL_UA_*` is wired (returns `not_enabled`).
- **Connector secrets:** `src/lib/operator/credential-secret-transport.ts` (ADR 0036 per-customer Fly-secret relay). Deliberately stubbed fail-closed (`not_enabled`) until wired.

The value flows in over TLS and only a non-secret `ref` comes back; it is never logged or returned.

## Follow-on (phase 2, not yet built — issue #1783)

- `operator/bin/build-keyless.sh` — prod keyless build against an **emulated vault**: every customer-owned slot filled with `secret_custody.placeholder_for(name)` and `SMD_BUILD_PHASE=keyless`, guarded by `secret_custody.assert_no_real_customer_secret(env)` (raises if any customer-owned var holds a non-placeholder value). _(This whole-env guard is NOT wired into `reprovision-staging.sh`: `/ss` legitimately holds other seats' per-seat keys, so staging isolates by the contract-derived blank list instead.)_
- `assert_sealed` wired into `provision-customer.sh` post-deploy — refuse go-live while any bound customer-owned secret is still a placeholder.
- Emulated S3 for the config-bucket upload during a keyless build.

## Do / don't

- **Do** classify every new staged secret in `env-consumption.yaml` (or the `secret_custody.py` connector tables) in the same PR that adds it.
- **Do** run `reprovision-staging.sh` before touching a real seat with any deploy/boot/env change.
- **Don't** re-introduce a hand-maintained blank list — derive from the classification.
- **Don't** echo any secret value; the guard and CLI operate on **names** only.
