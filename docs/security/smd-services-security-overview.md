# SMD Services — Information Security & Controls Overview

**Organization:** SMDurgan, LLC (trading as SMD Services)
**Product:** Operator — a managed, AI-powered operations assistant that integrates with a law firm's practice-management system (Smokeball) on behalf of authorized firm staff
**Prepared for:** Smokeball Marketplace / Information Security Review
**Primary security contact:** Scott Durgan, Founder — scott@smd.services
**Operational / escalation contact:** team@smd.services
**Document date:** June 2026
**Version:** 1.0

---

## 1. Purpose and scope

This document describes the information-security policies and controls SMD Services applies to the Operator service and its integration with the Smokeball API. It accompanies the Cloud Services Questionnaire and Supplier & 3rd Party Evaluation Questionnaire previously submitted, and provides the underlying control detail referenced as "available on request" in those responses.

It is written honestly to the stage and shape of our organization. SMD Services is a small, founder-led firm delivering a single, purpose-built integration. We do not present ourselves as a large enterprise with a formal certified ISMS. Instead, this document describes the concrete, implemented controls that protect Smokeball data and access — most of which derive their strength from architecture rather than from process volume.

### Certification status (stated plainly)

SMD Services does **not** currently hold ISO 27001, SOC 2, PCI DSS, or Cyber Essentials certification. Our material sub-processors — Fly.io and Anthropic — are independently **SOC 2 Type II** assessed, and Cloudflare maintains SOC 2 / ISO 27001 attestations (see §11). Our own controls are described directly below and are open to inspection.

---

## 2. The control that matters most: SMD does not store Smokeball data

The single most important fact about this integration's security posture:

> **SMD Services does not store, cache, or persist copies of Smokeball matter data.**

The Operator retrieves data transiently via authorized Smokeball API calls made on behalf of a credentialed firm user, uses it to perform the task that user requested, and returns the output to the user or back into Smokeball. Smokeball remains the sole system of record and data custodian.

This is materially different from a traditional cloud storage or SaaS provider. Many standard questionnaire items (data destruction methods, storage-device disposal, customer-held encryption keys, backup of customer data) are **not applicable** to us because the data they concern never comes to rest in our systems. The correct mental model is that **the Operator is a credentialed user acting on the firm's behalf — analogous to an employee with API access — not a data repository.**

What SMD _does_ store, on isolated per-customer infrastructure, is limited to: the customer's encrypted OAuth tokens, per-customer configuration, an audit log of Operator actions, and the bounded transcription cache described in §5 (text from scanned documents a firm user asked the Operator to read). These are addressed in §4, §5 and §8.

---

## 3. Architecture and tenant isolation

- **Per-customer isolation.** Each customer runs as a dedicated, isolated compute instance (a per-customer Fly.io Machine) with its own persistent volume. There is no shared application tier across customers and no shared data mount. Cross-customer data leakage is prevented architecturally — a different customer's data lives on a different Machine and a different volume, not behind an application access check.
- **Backend-only API access.** All Smokeball API calls, including authentication (OAuth2 client-credentials), originate exclusively from the secure backend (the per-customer Operator instance). No Smokeball credentials or tokens ever exist in frontend code, browser contexts, or client-side storage.
- **Scope of access.** Operator API access is scoped to the authenticated user's existing Smokeball permissions. The Operator does not elevate privilege beyond what the credentialed user already holds.
- **Privilege separation on the host.** Within each Machine, the agent process runs as an unprivileged user. Sensitive credentials (e.g. service-account keys used for managed-mailbox access) are materialized to a separate, broker-only process the agent cannot read, enforced with restrictive file permissions, a hardened environment allowlist, and `--no-new-privileges`. The agent literally cannot read the most sensitive secrets even if compromised.
- **Out-of-band control plane.** Administrative/provisioning actions are recorded as intent through a separate control plane; no customer-facing endpoint shells out to infrastructure tooling. Administrative credentials never reside on the customer Machine.

---

## 4. Access control and credential management

- **Multi-factor authentication** is enforced for administrative access to all infrastructure (Fly.io, Cloudflare, secrets management).
- **Named-principal access.** Access to Operator infrastructure is restricted to named individuals only — not shared role accounts. As a small named team, our administrative attack surface is inherently narrow. Access is reviewed and revoked promptly on any personnel change.
- **Smokeball OAuth token storage.** Per-customer OAuth tokens are stored only on that customer's isolated Fly.io volume, at restrictive (`0600`) permissions owned solely by the per-customer agent process. Tokens are:
  - never stored in any shared or central secret store,
  - never written to logs or shared storage,
  - never accessible to other customers or to SMD personnel in plaintext,
  - encrypted at rest (Fly.io platform volume encryption; AES-256-GCM application-layer encryption for connector tokens).
- **Per-customer LLM credentials.** Each customer's Operator runs on its own LLM workspace key rather than a shared account-wide key, so spend limits, revocation, and cost attribution are per customer. Every component of that customer's Operator — including the path that reads scanned documents — uses that same per-customer credential; no component holds a broader one.
- **SMD-side secrets** (e.g. the LLM API key) are held in a dedicated secrets-management system (Infisical), accessible only to named principals, and pushed to each customer Machine's secret store at provisioning time. Encryption keys are managed there and are not shared with sub-contractors.

---

## 5. Data handling and residency

- **Residency.** SMD does not store Smokeball data, with the single bounded exception described in the next bullet. Transient processing occurs on Fly.io infrastructure in the United States (Ashburn, VA).
- **One bounded at-rest exception: transcribed scans.** A scanned document (a PDF with no text layer) can only be read by transcribing the image of the page. When a firm user asks the Operator to read one, the resulting text is cached on that customer's own isolated volume so the same pages are not re-processed on every later read. That cache is keyed by a hash of the file's contents, restricted to the customer's own agent process (`0700` directory, `0600` files), size-bounded, and expires after 30 days. It holds only text SMD already processed transiently at the firm's request, it never leaves that customer's volume, and it is the only Smokeball-derived content written to disk.
- **LLM inference.** When — and only when — a firm user explicitly requests an AI-assisted task (drafting, summarizing, analysis, or reading a scanned document), the specific content required for that task passes transiently through Anthropic's API. Pure retrieval-and-forward actions do not invoke LLM inference and transmit no data to Anthropic. No autonomous action transmits firm data to an LLM without the user's express request.
- **No onward transfer.** No Smokeball data is stored or transferred to any other region, party, or service beyond the transient flows described above.
- **Law-enforcement requests.** Because SMD is not the controller of Smokeball data, any law-enforcement request referencing Smokeball data is promptly referred to Smokeball as the data controller, and Smokeball is notified.

---

## 6. Secure development and change management

All changes to the Operator platform flow through a controlled software-delivery pipeline:

- **Pull-request review** — no change reaches production without review.
- **Automated security gates in CI** run on every pull request and on a daily schedule:
  - dependency vulnerability scanning (fails the build on high/critical advisories),
  - automated secret detection (gitleaks) across full history,
  - static application security analysis (Semgrep).
- **Policy and correctness gates** — additional CI gates enforce engineering and content-integrity policies (e.g. preventing un-reviewed scope deferrals and verifying acceptance criteria are met before issues close).
- **Tested, repeatable deployment** — production deploys go through the same pipeline; security patches are applied promptly, with critical patches expedited outside the normal release cadence.
- **Change notification** — material changes affecting the integration or its security posture are communicated to Smokeball prior to deployment where operationally feasible.

---

## 7. Operator action governance (defense in depth at the action layer)

Beyond infrastructure controls, the Operator enforces governance on what it is permitted to _do_ on the firm's behalf:

- **Fail-closed authority model.** Sensitive action classes (external send, commitments, destructive operations) are **refused by default** unless explicitly authorized for that customer. An unconfigured Operator can read but cannot take consequential action — the safe state is the default state.
- **Principal-identity send is hard-banned** at the code level: the Operator will not send communications impersonating the firm principal.
- **Inbound-content trust fence.** Untrusted inbound content (e.g. inbound email) is structurally isolated so that instructions embedded in that content cannot drive privileged Operator actions (prompt-injection defense), using an unconditional cryptographic boundary.
- **Output-integrity gate.** A content gate blocks fabricated or unsupported client-facing output before any send action.

These controls are reviewed against a maintained threat model and are verified against the running system, not just asserted in design.

---

## 8. Logging and audit

- The Operator maintains a **tamper-resistant, append-only, hash-chained audit log** of its actions. The log is owned by a privileged broker process, not by the agent itself — the agent **cannot rewrite or delete its own audit trail** — and every row cryptographically commits to the row before it, so a mutated, deleted, or inserted row breaks the chain at a verifiable point. Exports carry the chain and are verifiable offline.
- A full audit trail of Operator actions is **available to Smokeball on request** in the event of an incident investigation, and serves as the basis for digital-evidence collection.
- Time across infrastructure is synchronized via NTP (Fly.io infrastructure).

---

## 9. Vulnerability and patch management

- **Scheduled security reviews** are conducted on a recurring (weekly) cadence, covering: currency of the threat model, attack-surface analysis of recent changes, verification that CI security gates are enforcing, and triage of open remediation items.
- **Periodic in-depth audits.** We conduct deeper adversarial security audits of the Operator environment; the most recent (June 2026) was a structured multi-perspective review with live exploit verification and a tracked remediation program.
- **Automated scanning** (dependency advisories, secret detection, SAST) runs continuously in CI as described in §6.
- Infrastructure-layer vulnerability management for compute, network, and storage is handled by Fly.io and Cloudflare under their own published security programs.

---

## 10. Information-security incident management

- **Notification triggers.** Smokeball is notified of any security incident that affects, or has the potential to affect, Smokeball data or API access. Platform-level incidents without direct Smokeball data exposure are also communicated.
- **Notification timelines.** Within **24 hours** for incidents affecting or potentially affecting Smokeball data or API access; within **72 hours** for platform-level incidents with no direct Smokeball data exposure.
- **Roles.** SMD investigates incidents within the Operator platform; Smokeball investigates within its own systems; both parties cooperate in good faith on any cross-system incident.
- **Contact and tracking.** Incidents are reported to and tracked through the SMD primary security contact (§ top of document), with status updates at regular intervals until resolution.

---

## 11. Sub-processors and supply-chain risk

SMD engages a deliberately minimal set of sub-processors. We assess each for security posture before engagement, monitor for material changes to their certifications, and re-evaluate when significant changes occur.

| Sub-processor  | Role in the Smokeball integration                                                                     | Touches firm data?                                                                                                                           | Security posture                                |
| -------------- | ----------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| **Fly.io**     | Per-customer compute Machines + isolated persistent volumes (hosts the Operator and encrypted tokens) | Transiently, in-memory during task execution; tokens at rest (encrypted)                                                                     | SOC 2 Type II — trust.fly.io                    |
| **Anthropic**  | LLM inference, **only** when a user explicitly requests an AI-assisted task                           | Only the specific content the user requests to process (including the page images of a scan the user asks the Operator to read), transiently | SOC 2 Type II — trust.anthropic.com             |
| **Cloudflare** | Network routing and edge for the control-plane/marketing surface                                      | No Smokeball matter data                                                                                                                     | SOC 2 / ISO 27001 attested                      |
| **Infisical**  | Secrets management for SMD-side secrets (not customer OAuth tokens)                                   | No Smokeball data                                                                                                                            | Encrypted secrets store; named-principal access |

Documentation for each sub-processor's compliance program is available on request, and the SOC 2 trust centers above are directly accessible.

---

## 12. Summary

SMD Services secures the Smokeball integration primarily through **architecture**: no storage of Smokeball data beyond the one bounded, per-customer transcription cache described in § 5, hard per-customer isolation, backend-only credential handling with tokens isolated to a single customer's volume, a fail-closed action-authority model, and a tamper-resistant audit trail. These structural controls are reinforced by automated security gates in CI, scheduled security reviews, a maintained threat model with live verification, and a minimal, SOC 2-assessed sub-processor set.

We do not yet hold formal certifications, and we say so plainly. We are glad to walk Smokeball's security team through any control in this document, provide supporting evidence, or answer follow-up questions.

**Contact:** Scott Durgan, Founder — scott@smd.services
