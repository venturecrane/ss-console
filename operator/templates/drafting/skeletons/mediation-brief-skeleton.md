<!-- SMD default skeleton (rehearsal + demonstration). Replaced by the firm's own skeleton at onboarding; structure authored 2026-07-28, proven in the drafting prove-out. -->

# SKELETON: Mediation Brief (California, Plaintiff PI)

Firm template. Fixed structure and boilerplate; case-specific content is filled from the matter record.

## How to use this skeleton

Fill every `{{FILL: what goes here | source}}` marker from the matter record named in the marker. The `GUIDANCE` comments in each section describe what a good fill draws from. They are instructions to the drafter and do not appear in the finished brief.

**Marker legend (applies to every section):**

| Marker                                                        | Meaning                                                 | Rule                                                                                 |
| ------------------------------------------------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `{{FILL: what goes here \| source}}`                          | Drafting content built from the named record source.    | Fill from that source. If the source is silent, convert to a `NOT IN RECORD` marker. |
| `{{NOT IN RECORD: what was sought, where it was looked for}}` | The record does not establish this fact.                | Leave the marker in the draft. Never supply a plausible substitute.                  |
| `{{ATTORNEY: decision reserved}}`                             | Legal judgment, case strategy, or settlement authority. | Do not resolve. Lay out the record bearing on the decision and stop.                 |

**Record sources referenced throughout.** TCR (traffic collision report), med chron (medical chronology), the treating and diagnostic records themselves, deposition transcripts, written discovery from both sides, defense medical examination reports, expert designations and reports, the pleadings, and the settlement correspondence file.

**Citation convention.** Every factual assertion carries a record cite in parentheses: deposition by witness surname, volume where applicable, and page and line (`[Witness] Dep. [page]:[lines]`); documents by exhibit or bates (`Ex. [no.]`, `[prefix]-[number]`); records by provider and date (`[Provider], [date]`). A sentence in the fact statement without a cite is a defect, not a style choice. The bracketed forms above are format illustrations only and are never carried into a draft as content.

**Invention rule.** Every date, figure, diagnosis, quotation, and characterization of testimony must trace to a document in the record. Do not paraphrase testimony from memory of what such testimony usually says. Do not round or smooth a number. A visible gap is always better than a smooth invention.

**Voice.** The boilerplate here is deliberately generic. Register, argument temperature, and sentence rhythm come from the firm voice layer, not from this file.

---

## SKELETON BODY

### Caption block

<!-- GUIDANCE: Pull the caption verbatim from the operative pleading, not from the matter name in the practice management system, which is frequently abbreviated or informal. Confirm the case number, the court and branch, the assigned department, and the current trial date against the most recent court notice rather than the complaint, since departments and trial dates move. -->

`{{FILL: attorney name, bar number, firm block, address, telephone, email | firm record}}`
`{{FILL: attorneys for designation, for example "Attorneys for Plaintiff [name]" | operative pleading}}`

**`{{FILL: court and branch | operative pleading caption, most recent court notice}}`**

|                            |                                            |
| -------------------------- | ------------------------------------------ |
| `{{FILL: plaintiff name(s) | operative pleading caption}}`, Plaintiff,  | Case No. `{{FILL: case number | operative pleading}}`       |
| v.                         | Assigned to Dept. `{{FILL: department      | most recent court notice}}`   |
| `{{FILL: defendant name(s) | operative pleading caption}}`, Defendants. | Trial Date: `{{FILL: date     | most recent court notice}}` |

**PLAINTIFF'S CONFIDENTIAL MEDIATION BRIEF**

Mediator: `{{FILL: mediator name and provider | mediation scheduling correspondence}}`
Mediation Date: `{{FILL: date | mediation scheduling correspondence}}`

> **CONFIDENTIAL. Prepared for mediation and subject to Evidence Code sections 1115 through 1128. Not admissible, subject to discovery, or usable for any purpose in this or any other proceeding, and not to be disclosed except as those sections permit.**

<!-- GUIDANCE: Confirm whether this brief is served on opposing counsel or submitted to the mediator alone, because that determines what belongs in sections VI and VII. A brief exchanged with the defense is written to be read by the adjuster on the other side of the table. A mediator-only brief can carry candid valuation and authority discussion. Never carry mediator-only content into an exchanged brief. If the record does not show which it is, that is an ATTORNEY question, not a drafting assumption. -->

`{{ATTORNEY: confirm exchanged brief or mediator-only submission before drafting sections VI and VII}}`

### I. Introduction

<!-- GUIDANCE: One to three paragraphs. State who the plaintiff is, what happened, why liability is not seriously in dispute (or where the real dispute lies if it is), the injury in one line, the specials figure, and what it will take to resolve the case. The mediator reads this section first and often forms the working frame of the case from it. It should be capable of standing alone as the whole brief in miniature. Draw from the operative pleading for claims, from the TCR for the incident, from the med chron for the injury line, and from the specials table in section V for the figure. Do not argue in the introduction; assert and cite, then argue in sections III and VI. -->

`{{FILL: who plaintiff is, in human terms: age, occupation, household role, as documented | client intake, deposition testimony, employment records}}`

`{{FILL: the incident in two or three sentences | TCR, operative pleading}}`

`{{FILL: the liability posture in one or two sentences, and where the genuine dispute lies if there is one | TCR findings, defense answer, defense discovery responses}}`

`{{FILL: the injury and the current status in one or two sentences | med chron, most recent treating records}}`

`{{FILL: the economic damages figure and the demand posture | section V table, settlement correspondence}}`

### II. Statement of Facts

<!-- GUIDANCE: The fact statement is built from the TCR first for the mechanism and the scene, then from deposition transcripts for what the parties and witnesses actually said under oath, then from the documentary record. Testimony is the strongest material here because it is what the defense is stuck with at trial, so quote it rather than summarizing it where the words matter. Where the TCR and the sworn testimony conflict, do not silently pick the favorable one; present both and address the conflict in section III. Facts about the plaintiff's life before the incident belong here, not in the damages section, because a mediator who meets the plaintiff as a person in the fact statement reads the damages section differently. Keep argument out of this section entirely. -->

#### A. The parties

`{{FILL: plaintiff background: age at the time of the incident, occupation, family and household role, physical baseline and activities | deposition testimony, intake, employment records}}`

`{{FILL: defendant identity and the capacity relevant to liability, for example employment status if respondeat superior is pleaded | operative pleading, defense discovery responses, deposition testimony}}`

#### B. The incident

`{{FILL: the incident narrative in chronological order: conditions, vehicle or party positions, movements, point of impact | TCR narrative and diagram, party deposition testimony}}`

`{{FILL: the investigating agency's findings, including the primary collision factor and the party designated at fault | TCR face page and factual summary}}`

`{{FILL: independent witness accounts, by witness and source | TCR witness statements, witness deposition transcripts}}`

#### C. Post-incident sequence

`{{FILL: what happened immediately after: transport, first complaints, first medical contact | TCR injury section, EMS run sheet, emergency department records}}`

#### D. Procedural posture

`{{FILL: filing date, service, operative pleading, cross-complaints, discovery completed, expert designation status, trial date | docket, case file}}`

### III. Liability Analysis

<!-- GUIDANCE: This is argument, and it is built from the sworn record rather than from the pleadings. The strongest liability sections in this practice are the ones that quote the defendant's own deposition admissions and then show that the defense theory cannot survive them. Move in this order: the duty and its source (statute where one applies, common law where not), the breach as established by the record with cites, causation, and then the comparative fault picture. Where a Vehicle Code violation is cited in the TCR, the negligence per se presumption under Evidence Code section 669 is the natural structure. Address the defense liability theory here rather than waiting for section VI if the theory goes to liability itself; section VI is for defense positions across the whole case. Do not overstate: a mediator who catches one overstatement discounts the rest. -->

#### A. Duty and standard of care

`{{FILL: the duty and its source, with the statutory citation where the record supports one | TCR primary collision factor, applicable Vehicle Code section, or common law duty}}`

#### B. Breach

`{{FILL: the breach as established, built on deposition admissions where they exist, each with a transcript cite | deposition transcripts, TCR, defense discovery responses}}`

#### C. Causation

`{{FILL: the causal chain from the breach to the injuries, including any medical causation opinion in the record with its source | treating provider opinions, expert reports, med chron}}`

#### D. Comparative fault

`{{FILL: the comparative fault the defense has pleaded or developed, and the record answer to it | defense answer, defense discovery responses, deposition transcripts, TCR}}`

<!-- GUIDANCE: If the defense has pleaded comparative fault but developed no evidence for it in discovery, that is itself the argument and it is worth stating with the discovery cite that shows the absence. Check the defense responses to contention interrogatories before concluding the theory is unsupported. -->

### IV. Medical Treatment and Injuries

<!-- GUIDANCE: Built from the med chron, verified against the underlying records for anything load-bearing. Move chronologically. Quote radiology impressions rather than characterizing them. Attribute every diagnosis to the provider and the date. Distinguish objective findings (imaging, measured range of motion, surgical findings) from subjective complaints, because the defense brief and the DME report will make that distinction whether or not this brief does, and a brief that has already made it honestly is more credible than one that gets corrected. Address gaps in treatment directly where the chronology shows them: an unexplained gap is the single most common defense theme in this practice, and leaving it for section VI concedes the framing. Prior injuries and prior treatment to the same body part must be addressed here, drawn from the plaintiff's own records and deposition testimony, not left for the defense to raise. -->

#### A. Emergency and initial care

`{{FILL: facility, date, presenting complaints, examination findings, imaging and impressions, discharge diagnoses | emergency department records, radiology reports}}`

#### B. Course of treatment

`{{FILL: chronological treatment by provider and date range, with modality, documented response, and referrals | med chron}}`

#### C. Objective findings

`{{FILL: imaging and diagnostic findings quoted from the impressions, each with study type, date, and reading provider; surgical findings where applicable | radiology reports, operative reports}}`

#### D. Treatment gaps and prior conditions

`{{FILL: any gap in treatment shown by the chronology with the documented explanation if the record contains one, and any prior injury or treatment to the same body part with its source | med chron, prior records, deposition testimony}}`

<!-- GUIDANCE: If a gap exists and the record contains no explanation for it, that is a NOT IN RECORD marker and an ATTORNEY flag, not a sentence to be smoothed over. -->

#### E. Current condition and prognosis

`{{FILL: current symptoms, functional limitations, work restrictions, and prognosis as written by a treating provider, with provider and date | most recent treating records}}`

#### F. Future care

`{{FILL: future care recommended in writing, with the recommending provider and date, and cost where a written estimate or life care plan exists | treating records, life care plan, written estimates}}`

### V. Damages

<!-- GUIDANCE: Economic damages come from the billing file and the employment records, never from the chronology. Whether the demand figure rests on billed or on paid amounts depends on how this plaintiff treated: under Howell v. Hamilton Meats & Provisions, Inc. (2011) 52 Cal.4th 541, a plaintiff treated through insurance recovers the amount paid and accepted; under Pebley v. Santa Clara Organics, LLC (2018) 22 Cal.App.5th 1266, a plaintiff treating on a lien outside insurance is not held to negotiated rates. Determine which applies from the file and stay consistent. Do not blend the two into one total. Non-economic damages are argued from documented human consequence, never from a multiplier of specials, and the material for them is in the deposition testimony and the treating records rather than in the bills. -->

#### A. Economic damages

| Category                                                      | Amount                      | Source                                                     |
| ------------------------------------------------------------- | --------------------------- | ---------------------------------------------------------- |
| Past medical (`{{FILL: billed or paid, per the guidance above | billing file}}`)            | `{{FILL: $                                                 | billing file}}` | `{{FILL: cite | file}}` |
| Future medical                                                | `{{FILL: $ or NOT IN RECORD | life care plan, written estimates}}`                       | `{{FILL: cite   | file}}`       |
| Past wage loss                                                | `{{FILL: $                  | employment verification, pay records, work status notes}}` | `{{FILL: cite   | file}}`       |
| Loss of earning capacity                                      | `{{FILL: $ or NOT IN RECORD | vocational or medical opinion}}`                           | `{{FILL: cite   | file}}`       |
| Property damage and out of pocket                             | `{{FILL: $                  | repair estimates, receipts}}`                              | `{{FILL: cite   | file}}`       |
| **Total economic**                                            | `{{FILL: $                  | sum of the above}}`                                        |                 |

`{{FILL: provider-by-provider medical specials breakdown, or a reference to the attached itemization | billing file}}`

**Liens and reimbursement interests.** `{{FILL: each lienholder, the nature of the interest, and the asserted amount as of a stated date | lien correspondence, reimbursement notices}}`

<!-- GUIDANCE: Lien exposure belongs in the brief because it shapes what a settlement number actually delivers to the plaintiff, and a mediator working toward a number needs it. Confirm current lien figures rather than carrying forward a stale number from an earlier demand. -->

#### B. Non-economic damages

`{{FILL: pre-incident baseline: work, activities, family role, physical condition | deposition testimony, intake, treating history}}`

`{{FILL: documented daily impact since the incident, attributed to source | deposition testimony, treating records, family or coworker accounts in the record}}`

`{{FILL: duration of symptoms to date and the documented trajectory | med chron}}`

`{{ATTORNEY: valuation range and the general damages figure argued}}`

### VI. Defense Positions and Responses

<!-- GUIDANCE: Take the defense positions from the record rather than from anticipation: the answer and its affirmative defenses, the defense responses to contention interrogatories, the DME report, the defense expert designation and reports, and the positions taken in settlement correspondence and at the deposition. For each position, state it fairly in the defense's own terms, then answer it with a record cite. Fair statement matters: a mediator who sees the defense theory strawmanned stops trusting the rest of the brief, and the defense will state it accurately in their own brief anyway. Where a defense position is genuinely strong, the honest treatment is to say what limits it rather than to pretend it away, and that is an ATTORNEY call on how far to go. The recurring positions in this practice are low property damage relative to claimed injury, gaps or delays in treatment, prior injury to the same body part, degenerative imaging findings characterized as pre-existing, treatment on liens characterized as attorney-directed, and comparative fault. Only address the ones this record actually raises. -->

`{{FILL: for each defense position in the record: the position as the defense states it, its source, and the record response with cites | defense answer, defense discovery responses, DME report, expert designations, settlement correspondence, deposition transcripts}}`

<!-- GUIDANCE: Structure each one as its own subsection with the position as the heading, so the mediator can carry a single issue across the table without hunting for it. -->

`{{FILL: the DME examiner's findings and conclusions, and the record response, including any conflict with the treating records or with the examiner's own measured findings | DME report, treating records}}`

### VII. Settlement Posture and Demand History

<!-- GUIDANCE: Build the history from the settlement correspondence file, in date order, with the exact figures and dates, including any Code of Civil Procedure section 998 offers on either side and their status. The 998 history matters at mediation because it sets the cost-shifting exposure both sides are actually negotiating against, and getting a date or a figure wrong here is a credibility loss with the one reader whose trust the brief exists to earn. Available coverage belongs here where it is known, with the disclosure source. Current authority, the number the plaintiff will take, and any bracket to be proposed are ATTORNEY decisions and are never drafted. If this brief is exchanged with the defense rather than mediator-only, candid authority discussion does not belong in it at all. -->

`{{FILL: chronological demand and offer history with dates, figures, and the source document for each | settlement correspondence file}}`

`{{FILL: any section 998 offers by either party, with date, amount, and current status | case file}}`

`{{FILL: known available coverage and the disclosure source and date, or NOT IN RECORD | coverage disclosure, defense correspondence}}`

`{{ATTORNEY: current authority, target, and any bracket; omit entirely from an exchanged brief}}`

### VIII. Conclusion

<!-- GUIDANCE: Short. Restate the liability posture in a sentence, the damages picture in a sentence, and what plaintiff is prepared to do at the mediation. No new facts, no new argument, no figures that have not already appeared with a cite above. -->

`{{FILL: two to four sentences closing on liability, damages, and the posture into the mediation | sections above}}`

Dated: `{{FILL: date | drafting date}}`

`{{FILL: firm signature block | firm record}}`
`{{FILL: attorneys for designation | operative pleading}}`

### Attachments

<!-- GUIDANCE: Attach only what the brief cites and the mediator will actually open. A medical specials itemization, key imaging impressions, the TCR, and short excerpts of the deposition testimony the brief quotes are the usual set. Confirm the mediator's page or format instructions from the scheduling correspondence before assembling. -->

`{{FILL: itemized attachment list, each keyed to the section that cites it | the documents actually attached}}`

---

## Pre-submission review points

Not part of the brief. Confirm before it is submitted.

1. Every sentence in section II carries a record cite, and every cite resolves to a document in the file.
2. Every quotation from a transcript matches the transcript at the cited page and line.
3. Billed and paid medical figures are not mixed within one total, and the choice between them matches how this plaintiff treated.
4. Treatment gaps and prior injuries to the same body part are addressed in section IV rather than left for the defense.
5. Every defense position stated in section VI is stated in terms the defense would recognize as its own.
6. The demand and offer history in section VII matches the settlement correspondence file exactly, dates and figures both.
7. If the brief is exchanged rather than mediator-only, no authority, valuation range, or bracket appears anywhere in it.
8. Every `NOT IN RECORD` marker has been resolved by locating the document or accepted as a visible gap by the attorney. None reach the mediator.
9. Every `ATTORNEY` marker has been resolved by the attorney.
10. The confidentiality legend is present and the caption matches the most recent court notice.
