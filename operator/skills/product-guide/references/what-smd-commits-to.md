# What SMD Commits To

This document summarises, in plain words, the standard commitments SMD Services makes when it provides the Operator. They come from three standard documents: the service agreement, the data processing addendum, and a confidentiality addendum for clients who handle privileged or professionally protected information.

## How to read this document

- These are the standard terms. Each client's signed agreement governs, and an engagement can differ from the standard where both sides agree in writing.
- Where a notice period or a window is given, it is what the standard agreement states. It is not a promise about how fast any piece of work will be delivered.
- Pricing is set per engagement and is not covered here. Fees, payment terms, liability cap amounts and insurance limits are set in each signed agreement and are not part of this library.
- This is a summary, not legal advice. A prospect's own counsel should read the agreement itself.

## What the service is

- SMD sets up and runs a dedicated Operator for one client, configured to that client's written instructions: which skills are on, which systems it may reach, how the business writes, and how far it may go for each kind of action.
- The service includes setting up the client's machine, hosting, monitoring, maintenance, support, configuration changes and, at the end, offboarding.
- The Operator does work and prepares work for the client's people to review: replies, summaries, calendar entries, document drafts and similar. The skills turned on for a client are listed in that client's statement of work.
- SMD warrants that the service is performed in a professional and workmanlike way and conforms in all material respects to its documentation. Beyond the warranties the agreement states, the service is provided without other warranties.

## What the client controls

- **The limits.** For every kind of action the Operator can take (reading, writing inside the business, sending, making commitments, destructive actions), the client writes the limit. A kind of action with nothing written for it is refused.
- **The Operator cannot raise its own limits.** The limits are enforced in code, every action is recorded against them, and a change to them is made by the client's authorised person through SMD, never by the Operator.
- **Sending identity.** For each kind of outside send, the client chooses whether the Operator sends on its own, prepares a draft for a person to send under their own name, or is refused. Every outside action is traceable in the audit record to a named person at the client.
- **Floors for some professions.** For a regulated profession, the standard terms provide that outside sends can be fixed at "drafts for a person" as a floor the client's own settings cannot raise. In the product that floor is set through each seat's own written limits, and the gate can add an industry-wide floor on top of them (see the permission model, "Rules that hold everywhere").
- **Commitments and destructive actions** keep a requirement for a person's yes at the time, even where the limit is acts on its own.
- **Changes on request.** The client can ask for new skills, a different voice, or changed limits at any time during the term.
- **Stopping it.** The client can pause the Operator in the Client portal, or cut its access in the client's own systems.

## Who owns the data

- The client owns its data and the Operator's memory built for it: rules, voice and format specifications, the people it knows, drafts and the audit record.
- The client is the controller of the data. SMD is the processor and acts only on the client's documented instructions: the agreement, the client's authored configuration, and instructions given through the Client portal or the Operator's authorised channels.
- The client's business systems stay the systems of record. SMD does not warehouse copies of the client's files held in them; the Operator reads what a task needs through connections the client has granted.
- The client can ask for an export of the Operator's memory and audit record during the term, and receives one at the end.

## How client data is used, and what it is never used for

- SMD processes client data only to provide the service: doing the work, keeping the memory and the audit record, answering support requests and incidents, meeting legal obligations, and producing the export and deletion at the end.
- **No training.** SMD does not use client data to train or fine-tune any model, and neither do its sub-processors. Task content sent to the model provider is processed for the task and is not used to train models, under that provider's commercial terms.
- **No cross-client learning.** One client's data does not flow to any other client's Operator, to SMD's shared skills, or to any process that combines content across clients. Improvements to the product are written by SMD, not learned from a client's runtime data.
- **No other use.** SMD does not sell client data and does not use it for its own product development or marketing unless the client agrees in writing.
- **Isolation.** Each client's Operator runs on its own dedicated machine with its own storage and credentials. No other client shares them.

## Sub-processors

- The standard addendum lists the outside providers that process client data on SMD's behalf: hosting for the client's machine, the model provider, the providers behind SMD's console and the Client portal, the Operator's mailbox where the engagement includes one, sign-in, billing and agreement signing. Each signed agreement carries its own list.
- Connections to the client's own systems use the client's own access grants, and the providers of those systems follow from the connections the client authorises.
- SMD binds each sub-processor to written data protection terms and remains responsible to the client for their performance.
- The standard agreement states at least 30 days' notice before SMD adds or replaces a sub-processor that will process client data. The client may object on reasonable data protection grounds.

## Security measures

The standard terms describe these measures:

- A dedicated, isolated machine and encrypted storage for each client.
- Connection credentials kept on the client's machine, with the credentials that can send held apart from the part that does the work.
- Unconfigured kinds of action refused by default.
- Untrusted inbound content fenced so that it cannot drive actions.
- An append-only audit record of the Operator's actions that the Operator cannot change.
- Access grants checked live on every request, so a revoked grant takes effect at once.
- SMD's own access to a client's machine limited to authorised people and logged.
- Encryption in transit, and a controlled release process with automated security checks.

SMD may update these measures over time, but not in a way that materially reduces the protection of client data.

## Security incident notice

- The standard agreement states that SMD notifies the client without undue delay, and in any case within 24 hours, after becoming aware of a security incident affecting client data.
- SMD shares what it knows as it becomes available (what happened, what data and people are affected, the likely consequences and what is being done), investigates, gives regular updates until it is resolved, and cooperates with the client's own notification duties.
- Notice of an incident is not an admission of fault.

## Confidentiality

- Each side protects the other's confidential information with at least reasonable care, uses it only to perform the agreement, and does not disclose it except as the agreement allows. Client data is the client's confidential information.
- The usual exclusions apply, such as information that is already public or was independently developed.
- SMD limits access to client data to the people and processes that need it to deliver the service, and binds its people to confidentiality.

## Confidentiality for privileged and professionally protected information

For clients who hold information protected by professional rules (for example, a law firm's privileged communications and work product), a confidentiality addendum adds to the standard terms. It plays the role for that kind of information that a business associate agreement plays for health information. Where a client handles regulated health information, a separate business associate agreement may be needed in addition. For an accounting firm, the Operator works inside the firm's tax-return disclosure consents under section 7216 of the tax code: the firm holds the consents, and the seat's limits enforce them.

- **Narrow use.** SMD uses protected information only to provide the service, never for its own development or marketing, never to train a model, never for another client's benefit, and never to build insight about the client's own clients, matters or adversaries.
- **Sends stay with a person.** For a regulated profession, outside communications about a matter are prepared for a person at the client to send under their own name.
- **Sources that forbid AI use.** Where a connected source's vendor prohibits feeding its content to an outside AI, the addendum states that content from that source is read by the client's people only and is not given to the Operator.
- **Conflicts.** SMD's design prevents one client's data from informing another's Operator. If SMD learns that it serves clients who may be adverse in a known matter, it tells the client in general terms, without disclosing the other client's information. SMD does not promise that its client base is free of conflicts; conflict checks remain the client's responsibility.
- **Privilege is the client's call.** The arrangement is meant to support the client's privilege and work-product positions, but whether privilege holds in a given situation is for the client's counsel and the relevant tribunal, and SMD does not warrant it.
- **Disclosure of AI use.** Whether and how the client tells its own clients, courts or regulators about the Operator is governed by the client's professional rules. SMD supports the client's choice.
- **Breach.** In an incident affecting protected information, SMD names the categories affected and cooperates with any notice the client owes under its professional rules.

## Legal process and compelled disclosure

- If SMD receives a subpoena, court order or similar demand for a client's protected information, it tells the client in writing (unless the law forbids it), shares a copy, and does not produce anything until the client has had a reasonable chance to object or seek protection.
- If production is finally required, SMD produces only the minimum the law requires and asks for confidential treatment.
- SMD does not volunteer a client's protected information to anyone, including law enforcement, without compulsory process or the client's written consent, except where the law requires it.

## AI output and the client's review

- AI output can be wrong. The client's people review it before relying on it, and the product is built so that review is the normal path.
- Where the client chooses "drafts for a person" for a kind of send, nothing in that kind goes out under the Operator's name: a person reviews it and sends it.
- Responsibility follows the limits. Under the standard terms, the client is responsible for actions its people take through the service and for actions the Operator takes within limits the client authored. That responsibility does not extend to actions outside the authored limits, to a failure by SMD to enforce those limits, or to SMD's gross negligence or wilful misconduct.

## Responsibility and liability

- The agreement caps each side's liability and excludes indirect and consequential losses, with exceptions that include indemnity obligations, breach of confidentiality, and gross negligence or wilful misconduct. The cap is set in each signed agreement and is not covered here.
- SMD defends the client against third-party claims that the service, used as agreed, infringes a patent, copyright or trademark.
- SMD carries professional liability, technology and cyber liability, and general liability insurance during the term, with limits set in the signed agreement, and provides a certificate on request.

## Service levels and support

- SMD's service commitment is about detection, response and communication, ranked by severity, rather than an uptime percentage.
- SMD watches every seat continuously: each seat reports its health, work is checked through the day, and SMD is paged when a seat goes quiet or a request goes unanswered. How often each check runs is how SMD watches, not a response-time commitment.
- Releases are rolled out seat by seat, with a point to roll back to.

## Audits and evidence

- On request, SMD provides what the client needs to see that the data terms are met: a description of its security measures, an evidence packet, and information about its sub-processors.
- The client can export its governance records from the Client portal during the term.
- The standard terms allow the client a periodic review of SMD's controls, in the form of a documented walkthrough with supporting evidence, and a further review after a security incident affecting client data.
- SMD assists the client with requests from individuals about their data and with regulators' questions about the Operator's processing. SMD does not answer individuals directly; it passes such requests to the client.

## Ending the service, return and deletion

- The client can cancel. The standard terms let a client cancel in the Client portal or by letter, effective at the end of the period already paid for. SMD's own termination carries notice to the client, which the standard terms set at 30 days. Either side may also end the agreement for an uncured material breach.
- On exit, the standard agreement states that SMD delivers the client's audit record and the Operator's memory in exportable form within 14 days of the termination date.
- SMD revokes every access grant and connection credential, closes the managed mailbox, destroys the client's dedicated machine and storage, and deletes remaining client data from SMD's console, except records it must keep for legal, tax or accounting reasons.
- The standard agreement states that return and destruction are complete within 30 days of termination, and SMD confirms destruction in writing.
- Where a client's professional rules require return rather than destruction, the export serves as the return.
- The persona name the client chose may stay with the client. The Operator's own email identity and SMD-provided assets are closed with the machine.

## What SMD does not promise

- No uptime percentage.
- No service credits. The remedy for a failure is that SMD fixes it and communicates honestly.
- No SOC 2 or ISO 27001 certification of SMD's own. SMD says so plainly; its sub-processors' attestations are listed in the agreement.
- No promise that AI output is correct. The client's review is part of the design.
- No warranty that privilege is preserved in any given situation, and no promise that SMD's client base is free of conflicts.
- No published delivery or onboarding timeframes. Timing is agreed per engagement.
