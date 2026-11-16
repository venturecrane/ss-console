# Law Firms and Personal Injury

This document describes how a small or midsize law firm works, with plaintiff personal injury as the worked example, where its staff's time goes, what the Operator can take on for the firm, its staff and its clients, the line it never crosses, the systems it connects to, and questions a firm can explore. The industry facts come from the public sources listed at the end. Every item under "What the Operator takes on" and "Systems it connects to" names the part of the library or of SMD's site it comes from. Each state's own rules govern a firm's practice, and nothing here is legal advice. A firm's seat is shaped with the firm: what follows is a starting point to react to, not the only way it can run.

## How the business works

- **Intake and sign-up.** A prospective client contacts the firm, and the firm screens the matter before signing a fee agreement. Under the ABA model rules, a contingent fee agreement is in writing, signed by the client, and states how the fee is determined, including how expenses are handled.
- **Investigation and treatment.** The firm gathers reports, photos, statements and insurance information while the client treats.
- **Records and bills.** A case is only as strong as its medical records: visits, imaging, therapy and bills, often thousands of pages for one client, and they never stop arriving.
- **The chronology and the demand.** The records are read into a dated account of every visit, diagnosis and treatment, which the attorney uses to value the case and write the demand. Most tort cases never reach trial: a U.S. Justice Department study of state courts in large counties found about 3 percent of tort cases were decided by trial.
- **Liens and reimbursement claims.** Medicare, Medicaid, health plans and providers can have claims against a recovery, and firms resolve them before the money goes out. An attorney can be sued to recover a Medicare conditional payment that was not repaid.
- **Negotiation and litigation.** The attorney decides what the case is worth and recommends to the client, who decides whether to settle. Without a settlement, suit is filed before the limitations date, and court deadlines multiply.
- **Settlement and disbursement.** At the end of a contingent fee matter the lawyer gives the client a written statement of the outcome and how the payout was worked out. Settlement funds pass through the firm's trust account, which state rules govern closely, and the model rules address how third-party claims on those funds are handled.

## Where coordinator time goes

- **Reading records.** One client's records can run to thousands of pages, and someone has to read all of them and put them in order before the case can be valued. That reading is slow, costly to send out, and never stops arriving.
- **The billing picture.** Provider bills have to be pulled together and matched to the records, with the missing ones chased.
- **Drafting.** Attorneys have more drafting in front of them than hours: demands, case evaluation memos, mediation briefs.
- **Deadlines.** Limitations dates vary by state, notice deadlines for a claim against a government entity are much shorter, and court deadlines run once suit is filed.
- **Status questions.** A client in treatment sees little movement and asks where the case stands. The model rules require a lawyer to keep the client reasonably informed and to answer reasonable requests for information promptly, and poor communication is among the most common sources of bar complaints.
- **Settled cases.** Settled cases wait on provider balances before the money can go out.

## What the Operator takes on

For the firm:

- Carrying a case from records to settlement inside the firm's case management system. (From [industry-packs.md](industry-packs.md), "Law firm", and SMD's law firm page, "What It Does".)
- Noticing new records and bills as they land in the case, logging what came in, checking it against what was ordered, and flagging a provider whose records have not arrived. (From SMD's law firm page, "The Lifecycle", "The records arrive".)
- Medical chronologies on request: every visit, diagnosis and treatment in order, each entry cited to the page it came from, filed back into the case with the records it relied on, saying plainly what it could not read. (From SMD's law firm page, "The Lifecycle", "The chronology", and [jobs.md](jobs.md), "Kinds of job".)
- The billing picture: provider bills pulled into one worksheet, reconciled to the records, marked incomplete where a bill is missing. (From SMD's law firm page, "The Lifecycle", "The billing picture".)
- Reviewing a case file, watching a negotiation, and a litigation status report, each run as a queued job with cost limits, its output checked against its sources and independently reviewed where judgment is needed, and anything it files read back from the firm's own system. (From [jobs.md](jobs.md), "Kinds of job" and "From request to delivery".)
- A working list of settled cases and the provider balances still open on each, so nothing sits unnoticed between a settlement and a payout. (From SMD's law firm page, "The Lifecycle", "Settlement to disbursement".)

For its attorneys and staff:

- First drafts for the attorneys: a demand from the record and the chronology, and documents a case needs along the way, such as a mediation brief or a case evaluation memo. (From SMD's law firm page, "The Lifecycle", "The demand" and "Mediation and case review", and [jobs.md](jobs.md), "Kinds of job".)
- Deadlines: it reads the dates a case depends on, proposes a deadline where one has to be worked out, and asks the attorney to confirm, with the confirmation logged under the attorney's name. (From SMD's law firm page, "The Lifecycle", "The deadlines".)
- One short summary of what needs attention: a draft waiting, a deadline near, a balance still open. (From SMD's law firm page, "The Lifecycle", "What needs you".)
- The connective work between the firm's systems: it reads what lands in the case, puts its chronologies and drafts back into the case, and routes them to the right person, so nothing has to be emailed around or re-uploaded by hand. (From SMD's law firm page, "How It Connects".)

For the firm's clients:

- Status updates and the routine back-and-forth. For each kind of message, the firm chooses whether its draft goes to a person at the firm first, and it can send from a staff member's address on that person's approval. (From SMD's Operator page, "Where It Fits", the "Recurring client communication" lens, and [connections-and-channels.md](connections-and-channels.md), "Email channels".)

## The line it never crosses

- **The lawyer's work stays the lawyer's.** The judgment stays with the attorneys: what a case is worth, the strategy, the call on a matter, and what gets signed. Every piece of work product goes to an attorney to review and finish, and it never signs on its own.
- **The firm sets the lines.** The firm decides where it works on its own and where a person stays in the loop, such as anything bound for opposing counsel or the court, and it can move those lines. Nothing goes to opposing parties or a court without a person.
- **Trust money is read, never moved.** It reads trust balances and never moves money, disburses funds or writes to a trust account.
- **Accuracy before autonomy.** The parts that depend on reading the firm's records and matching its rules, such as a date read off a document or a bill matched to a visit, are validated on the firm's real cases first. Until accuracy is proven, it surfaces what it found and asks.
- **One case stays in its case.** Content tied to one case cannot be sent to another case's recipient.
- **The firm holds the controls.** The configuration, the logs and the off switch stay with the firm.

These points rest on [industry-packs.md](industry-packs.md), "Law firm" and "The rule in every industry", [what-it-is.md](what-it-is.md), "A seat at work: one example", and SMD's law firm page, "The Line".

## Systems it connects to

- The firm's case management system: reading and updating matters, contacts and documents, with money movement and trust-account writes refused. (From [connections-and-channels.md](connections-and-channels.md), "Connections by client system type".)
- A mailbox in the firm's own email tenant, with a read path that cannot send and a separate send path, and sending as a staff member only on that person's approval. (From [connections-and-channels.md](connections-and-channels.md), "Connections by client system type" and "Email channels".)
- What the firm's records vendors and drafting tools deliver: it does not replace them, it takes what they deliver and puts the result back where the team already looks. (From SMD's law firm page, "What It Does" and "How It Connects".)
- Any other system the firm runs, connected the same way: the vendor's official server, a vetted community server, or one SMD writes. (From [connections-and-channels.md](connections-and-channels.md), "How a connection is built".)

## Questions to explore

1. Where does a case wait longest between sign-up and settlement, and what is it waiting on?
2. How are records and bills tracked today, and how does the firm learn that a provider has not sent them?
3. Which drafts take the attorneys' time that a first draft from the record would shorten?
4. How are deadlines entered and confirmed today, and who checks them?
5. Which status questions do clients ask most, and who answers them now?
6. How does a settled case move to disbursement, and where does it go quiet?
7. Which communications should always be sent by a person at the firm?
8. What would the firm want proven on its own real cases before it relies on the work?

## Sources

- Contingent fee agreements and settlement statements, ABA Model Rule 1.5: https://www.americanbar.org/groups/professional_responsibility/publications/model_rules_of_professional_conduct/rule_1_5_fees/
- Tort cases disposed of by trial: https://bjs.ojp.gov/content/pub/pdf/ttvlc01.pdf
- Medicare recovery process: https://www.cms.gov/medicare/coordination-benefits-recovery/beneficiary-services/recovery-process
- Suits to recover unrepaid conditional payments: https://healthlawadvisor.com/medicare-secondary-payer-compliance-conditional-payments-part-ii
- Arizona provider liens: https://codes.findlaw.com/az/title-33-property/az-rev-st-sect-33-931/
- Arizona government claim notice: https://www.azleg.gov/ars/12/00821-01.htm
- California government claim presentation: https://california.public.law/codes/ca_govt_code_section_911.2
- Who decides whether to settle, ABA Model Rule 1.2: https://www.americanbar.org/groups/professional_responsibility/publications/model_rules_of_professional_conduct/rule_1_2_scope_of_representation_allocation_of_authority_between_client_lawyer/
- Client communication, ABA Model Rule 1.4: https://www.americanbar.org/groups/professional_responsibility/publications/model_rules_of_professional_conduct/rule_1_4_communications.html
- Communication among common complaint types: https://www.osbar.org/publications/bulletin/09febmar/barcounsel.html
- Safekeeping property, ABA Model Rule 1.15: https://www.americanbar.org/groups/professional_responsibility/publications/model_rules_of_professional_conduct/rule_1_15_safekeeping_property.html
- Arizona trust accounts: https://azbar.org/for-legal-professionals/lawyer-regulation/trust-accounts
