# Data Processing Addendum - Operator Service (Template)

**Status:** Template, v0.1 (2026-07-04, issue #1680). Fulfills the obligation named in ADR 0057 §"login layer" (SMD becomes a processor of firm employee-identity data). Reviewed by Captain before any execution; have counsel review before the first client signature. Per-engagement values (notification windows, retention figures, governing law) are completed in the signing flow, never assumed.

---

This Data Processing Addendum ("DPA") forms part of the services agreement (the "Agreement") between **SMDurgan, LLC d/b/a SMD Services** ("SMD") and the client identified in the Agreement ("Client"), and governs SMD's processing of Client Data in connection with the Operator service.

## 1. Roles and scope

1.1. Client is the controller of Client Data. SMD acts as processor, processing Client Data only to provide the Operator service and only on Client's documented instructions, which consist of the Agreement, the authored Operator configuration, and instructions given through the client portal or the Operator's authorized channels.

1.2. Client's business systems (practice management, email, calendars, documents) remain the systems of record. SMD does not warehouse copies of the data held in those systems; the Operator accesses them transiently through Client-authorized connections to perform requested work.

## 2. Data processed

| Category                 | Examples                                                                             | Where it lives                                          |
| ------------------------ | ------------------------------------------------------------------------------------ | ------------------------------------------------------- |
| Employee identity data   | Names, work email addresses, roles of Client staff granted Operator or portal access | SMD control plane (identity provider and access grants) |
| Configuration data       | Authored skills, permissions, voice samples, rosters                                 | Client's dedicated machine and SMD control plane        |
| Operational memory       | Working memory the Operator builds performing Client's work                          | Client's dedicated machine                              |
| Governance records       | Append-only audit log of Operator actions; access-grant audit                        | Client's dedicated machine and SMD control plane        |
| Transient task content   | Business data read through Client's systems while performing a task                  | In memory during the task; not retained by SMD          |
| Account and billing data | Contacts, invoices, subscription records                                             | SMD control plane and billing sub-processor             |

Data subjects are Client's personnel and the individuals whose information appears in Client's business records in the ordinary course of Client's work.

## 3. Security measures

SMD implements the measures described in its Security Overview (published at smd.services/security and available in long form on request), including: a dedicated, isolated compute instance and encrypted volume per client; connection credentials stored only on Client's machine with process-level custody separation; a fail-closed authority model in which unconfigured action classes are refused; structural isolation of untrusted inbound content; an append-only audit log the agent cannot modify; access grants evaluated live per request with immediate revocation effect; and a controlled software delivery pipeline with automated security gates.

## 4. Sub-processors

4.1. Client authorizes the sub-processors listed in Exhibit A. SMD will notify Client at least **30** days before adding or replacing a sub-processor that will process Client Data, and Client may object on reasonable grounds relating to data protection. (Standard term per ADR 0065.)

4.2. SMD remains responsible for its sub-processors' performance of this DPA's obligations.

## 5. Confidentiality

SMD limits access to Client Data to personnel and processes that need it to deliver the service, binds personnel to confidentiality obligations, and does not sell Client Data or use it for any purpose other than providing the service. Content processed through the language model provider is not used to train models, per that provider's commercial terms.

## 6. Incident notification

SMD will notify Client without undue delay, and in any case within **24** hours, after becoming aware of a security incident affecting Client Data, will provide information reasonably required for Client's own notification obligations as it becomes available, and will cooperate in the investigation and remediation. (Standard term per ADR 0064, matching SMD's standing partner-review commitment.)

## 7. Return and deletion

7.1. During the term, Client may export its governance records from the client portal and may request an evidence packet at any time.

7.2. On termination of the Agreement, SMD will: (a) deliver Client's audit record and the Operator's operational memory in exportable form, within 14 days of the termination effective date; (b) revoke all access grants and connection credentials; (c) destroy Client's dedicated machine and volume; and (d) delete residual Client Data from the control plane, excepting records SMD must retain for legal, tax, or accounting purposes. Return and destruction are completed within **30** days of termination, and SMD will confirm destruction in writing on request. (Standard terms per ADR 0065.)

## 8. Assistance and audits

8.1. SMD will reasonably assist Client with data subject requests and regulatory inquiries that concern the Operator's processing.

8.2. Once per year, or following a security incident affecting Client Data, Client may request a review of SMD's controls in the form of a documented walkthrough with SMD's security contact, together with supporting evidence. SMD does not hold its own SOC 2 or ISO 27001 certification and says so plainly; sub-processor attestations are listed in Exhibit A.

## 9. Term and precedence

This DPA applies for as long as SMD processes Client Data and survives termination until return and deletion complete. If this DPA conflicts with the Agreement, this DPA controls with respect to data protection.

---

## Exhibit A - Sub-processors

| Sub-processor | Role                                                          | Client Data touched                                                                                                                                                                                                                                                                                           | Attestation                              |
| ------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| Fly.io        | Hosts Client's dedicated machine and encrypted volume         | Transient task content in memory; credentials, configuration, memory, and audit records at rest (encrypted)                                                                                                                                                                                                   | SOC 2 Type II (trust.fly.io)             |
| Anthropic     | Language model inference                                      | Task content, transiently; not used for model training per commercial terms                                                                                                                                                                                                                                   | SOC 2 Type II (trust.anthropic.com)      |
| Cloudflare    | Control plane, client portal, governance record store         | Employee identity data, configuration projections, governance summaries                                                                                                                                                                                                                                       | SOC 2 / ISO 27001                        |
| AgentMail     | The Operator's own mailbox, where the engagement includes one | Email sent to and from the Operator's address                                                                                                                                                                                                                                                                 | Vendor security documentation on request |
| Clerk         | Portal and access sign-in                                     | Employee identity data                                                                                                                                                                                                                                                                                        | SOC 2 Type II                            |
| Stripe        | Billing                                                       | Billing contact and payment records                                                                                                                                                                                                                                                                           | PCI DSS Level 1, SOC 2                   |
| SignWell      | Agreement signing                                             | Signatory names and executed documents                                                                                                                                                                                                                                                                        | SOC 2                                    |
| Resend        | Transactional email, including request notices to SMD         | Recipient names and addresses; request notices to SMD's operations mailbox carry the requester, the subject line, the words of the request and of the Operator's reply (each up to about 4,000 characters), the matter or record reference, and the names of the tools it ran, never documents or attachments | SOC 2 Type II                            |
| Infisical     | SMD-side secrets management                                   | No Client Data                                                                                                                                                                                                                                                                                                | Encrypted store, named-principal access  |

Google Workspace appears as a sub-processor only for engagements where Client connects its own Google tenancy; in that case processing occurs within Client's tenancy under Client's own Google terms.

---

**Completion checklist (per engagement, at signing):** the standard terms above (30 days §4.1, 24 hours §6, 30 days §7.2) are the ADR 0064/0065 defaults; override per engagement only with a recorded reason. Governing law is inherited from the Agreement.
