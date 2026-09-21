<!-- SMD default skeleton (rehearsal + demonstration). Replaced by the firm's own skeleton at onboarding; structure authored 2026-07-28, proven in the drafting prove-out. -->

# SHELL: Responses to Written Discovery (California, Plaintiff PI)

Firm template for responses to inspection demands, interrogatories, and requests for admission under the Civil Discovery Act. Fixed structure and boilerplate; case-specific content is filled from the matter record.

## How to use this shell

Fill every `{{FILL: what goes here | source}}` marker from the matter record named in the marker. Objections are drafted as candidates and are resolved by the responding attorney before service.

**Marker legend (applies to every section):**

| Marker                                                                           | Meaning                                                          | Rule                                                                                                                                                                 |
| -------------------------------------------------------------------------------- | ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `{{FILL: what goes here \| source}}`                                             | Response content built from the named record source.             | Fill from that source. If the source is silent, convert to a `NOT IN RECORD` marker.                                                                                 |
| `{{NOT IN RECORD: what was sought, where it was looked for}}`                    | The record does not establish this.                              | Leave the marker in the draft. Never supply a plausible substitute. A verified response that contains an invented fact is a perjury exposure, not a drafting defect. |
| `{{CANDIDATE OBJECTION: ground, with the basis in this record}}`                 | A proposed objection, not a taken position.                      | Every objection in this shell is a candidate until the responding attorney adopts it. State the ground and the basis. Never present an objection as settled.         |
| `{{PRIVILEGE CANDIDATE: document or information, and why it may be privileged}}` | Material that may be attorney-client privileged or work product. | Hold it out of the compiled response and flag it. Do not produce it and do not certify that it is privileged. Only the attorney clears or asserts privilege.         |
| `{{ATTORNEY: decision reserved}}`                                                | Legal judgment or a position the client must verify.             | Do not resolve.                                                                                                                                                      |

**The two hard lines in this shell.**

1. **Objections are candidates.** The drafter proposes an objection with its ground and the basis for it in this record. The drafter never adopts one. An objection served without an attorney adopting it is an unmeritorious objection risk under Code of Civil Procedure section 2023.010(e), and boilerplate carries sanctions exposure in California specifically.
2. **Privilege is flagged, never certified.** Material that may be privileged or protected work product is held out of the compiled draft and flagged with its basis. The drafter does not decide that something is privileged, does not decide that something is not, and never states or implies that a privilege screen has been performed. Uncertainty resolves toward holding the material out and flagging it.

## Response deadline computation

<!-- GUIDANCE: Confirm the deadline against the proof of service on the propounded set, not against the date the set was received or opened. The base period is 30 days after service. Service by mail within California extends it under Code of Civil Procedure section 1013; electronic service extends it by two court days under section 1010.6(a)(3)(B). Compute from the served set and record the computation in the matter file. -->

|                                           |                                                            |
| ----------------------------------------- | ---------------------------------------------------------- |
| Set served                                | `{{FILL: date                                              | proof of service on the propounded set}}` |
| Method of service                         | `{{FILL: method                                            | proof of service}}`                       |
| Base period                               | 30 days (Code Civ. Proc., §§ 2030.260, 2031.260, 2033.250) |
| Extension applied                         | `{{FILL: extension and its statutory basis                 | proof of service method}}`                |
| Response due                              | `{{FILL: computed date                                     | the above}}`                              |
| Extension agreed with propounding counsel | `{{FILL: date and the writing memorializing it, or none    | discovery correspondence}}`               |

<!-- GUIDANCE: Untimely service waives objections, including privilege and work product, under sections 2030.290(a), 2031.300(a), and 2033.280(a), with relief available only on motion and a showing that includes substantial compliance. Deadline slippage is therefore not a scheduling problem in this practice; it is an objection-waiver problem. Flag any computed deadline inside seven days for attorney attention rather than proceeding quietly. -->

---

## SHELL BODY

### Caption block

`{{FILL: attorney name, bar number, firm block, address, telephone, email | firm record}}`
`{{FILL: attorneys for designation | operative pleading}}`

**`{{FILL: court and branch | operative pleading caption, most recent court notice}}`**

|                            |                                            |
| -------------------------- | ------------------------------------------ |
| `{{FILL: plaintiff name(s) | operative pleading caption}}`, Plaintiff,  | Case No. `{{FILL: case number | operative pleading}}` |
| v.                         |                                            |
| `{{FILL: defendant name(s) | operative pleading caption}}`, Defendants. |                               |

**`{{FILL: title, selected from the block below | propounded set caption}}`**

<!-- GUIDANCE: Title the response to match the propounded set exactly, including the set number and the propounding and responding party designations. A mismatch between the title on the set and the title on the response is the first thing a meet and confer letter picks at. Standard forms:
  PLAINTIFF [NAME]'S RESPONSES TO DEFENDANT [NAME]'S REQUESTS FOR PRODUCTION OF DOCUMENTS, SET [ONE]
  PLAINTIFF [NAME]'S RESPONSES TO DEFENDANT [NAME]'S FORM INTERROGATORIES, SET [ONE]
  PLAINTIFF [NAME]'S RESPONSES TO DEFENDANT [NAME]'S SPECIAL INTERROGATORIES, SET [ONE]
  PLAINTIFF [NAME]'S RESPONSES TO DEFENDANT [NAME]'S REQUESTS FOR ADMISSION, SET [ONE]
-->

|                   |                                |
| ----------------- | ------------------------------ |
| PROPOUNDING PARTY | `{{FILL: party and designation | propounded set}}` |
| RESPONDING PARTY  | `{{FILL: party and designation | propounded set}}` |
| SET NUMBER        | `{{FILL: set number            | propounded set}}` |

### Preliminary statement

<!-- GUIDANCE: Keep this short and factual. Its legitimate work is to state that discovery and investigation are continuing and that responses reflect information reasonably available as of the response date. It is not a place to reserve rights wholesale or to disclaim the responses that follow. California authority treats a preliminary statement that functions as a blanket qualification on every response as an evasive response under Code of Civil Procedure section 2023.010(f), and a qualification that swallows the answers is a motion-to-compel invitation rather than a protection. If the draft preliminary statement could be pasted unchanged into any case in the office, it is boilerplate and should be cut back to what this record supports. -->

Responding party has not completed investigation of the facts relating to this case, has not completed discovery, and has not completed preparation for trial. The responses that follow are based on information reasonably available to responding party as of the date of service. Responding party reserves the right to supplement or amend these responses as discovery proceeds, to the extent permitted by the Civil Discovery Act.

`{{FILL: any case-specific qualification actually supported by this record, for example records requested from a named provider and not yet received; omit this paragraph entirely if the record supports no such qualification | matter file, records request log}}`

### General objections

> **CANDIDATE SECTION. Nothing below is a taken position. Each entry is a proposed objection with its ground and its basis in this record, presented for the responding attorney to adopt, narrow, or strike before service.**

<!-- GUIDANCE: California practice does not favor a standing block of general objections the way federal practice historically tolerated it. Objections here must be stated with particularity as to each request under Code of Civil Procedure sections 2030.240 and 2031.240, and an objection asserted without substantial justification is a misuse of the discovery process under section 2023.010(e). The practical consequence is that a general objections block does not preserve anything by itself: the objection must also appear, specifically, in the response to the individual request it applies to. Draft this section only where a ground genuinely applies across the set, and carry each adopted ground into the individual responses rather than relying on incorporation. Where an objection rests on privilege or work product, the particular privilege must be identified under sections 2030.240(b) and 2031.240(b), and for document demands the response must supply enough factual information for the propounding party to evaluate the claim, including a privilege log where necessary, under section 2031.240(c). -->

`{{CANDIDATE OBJECTION: ground | the specific feature of this set that raises it | the requests it would apply to}}`

<!-- GUIDANCE: Grounds that recur in this practice, each of which still needs a basis in the actual set before it belongs here: attorney-client privilege (Evid. Code, § 954); attorney work product (Code Civ. Proc., § 2018.030); the physician-patient and psychotherapist-patient privileges and the constitutional privacy interest, as narrowed by the patient-litigant exception for the conditions actually placed in controversy; requests exceeding the scope of permissible discovery under section 2017.010; requests that are not full and complete in themselves or that contain subparts, compound, conjunctive, or disjunctive questions where the propounding party has not complied with section 2030.060(d); requests that call for a legal conclusion; and requests that are unintelligible as phrased. Do not list a ground that this set does not raise. -->

`{{ATTORNEY: adopt, narrow, or strike each candidate above; every adopted ground must also appear in the individual responses below}}`

---

### Response blocks

Repeat the applicable block once per request, in the order the requests appear in the propounded set. Reproduce each request verbatim from the served set.

#### Block A: Request for production of documents

**REQUEST FOR PRODUCTION NO. `{{FILL: number | propounded set}}`:**

`{{FILL: the request, reproduced verbatim | propounded set}}`

**RESPONSE TO REQUEST FOR PRODUCTION NO. `{{FILL: number | propounded set}}`:**

`{{CANDIDATE OBJECTION: ground, stated with particularity as to this request, with the extent of the objection and its basis in this record; identify the particular privilege where privilege is the ground | this request as phrased, matter record}}`

Then one of the following three dispositions, which are exhaustive under Code of Civil Procedure section 2031.210(a):

<!-- GUIDANCE: Choose the disposition from what the file actually shows, not from what is convenient. A statement of compliance means the documents will be produced, so it must not be drafted before someone has confirmed they exist and can be produced. A representation of inability to comply carries specific statutory content and is not a place for a general disclaimer. Documents produced must be identified with the specific request number to which they respond under section 2031.280(a), so the production index and these response numbers have to match. -->

**Disposition 1, statement of compliance (Code Civ. Proc., § 2031.220).** Responding party will comply with this request and will produce all documents in responding party's possession, custody, or control to which no objection is being made. `{{FILL: identification of the category of documents that will be produced, keyed to the request number for the production index | matter record, document collection log}}`

**Disposition 2, representation of inability to comply (Code Civ. Proc., § 2031.230).** A diligent search and a reasonable inquiry have been made in an effort to comply with this request. Responding party is unable to comply because `{{FILL: select and complete the applicable clause: the item has never existed; the item has been destroyed; the item has been lost, misplaced, or stolen; or the item has never been, or is no longer, in responding party's possession, custody, or control | matter record, document collection log, client}}`. `{{FILL: the name and address of any natural person or organization known or believed to have possession, custody, or control of the item, or a statement that none is known | matter record, client}}`

**Disposition 3, partial compliance with objection (Code Civ. Proc., § 2031.240).** Responding party objects to this request in part. `{{FILL: identify with particularity each document or category to which the objection is directed | matter record}}`. `{{CANDIDATE OBJECTION: the extent of and the specific ground for the objection | this request, matter record}}`. Subject to and without waiving the foregoing, responding party will comply with the remainder of the request and will produce `{{FILL: the documents that will be produced | document collection log}}`.

`{{PRIVILEGE CANDIDATE: any document responsive to this request that may be privileged or protected work product, with the reason it may be, held out of the production and listed for attorney clearance}}`

#### Block B: Interrogatory (form or special)

**INTERROGATORY NO. `{{FILL: number | propounded set}}`:**

`{{FILL: the interrogatory, reproduced verbatim; for form interrogatories, the Judicial Council number and text | propounded set}}`

**RESPONSE TO INTERROGATORY NO. `{{FILL: number | propounded set}}`:**

`{{CANDIDATE OBJECTION: ground, stated with particularity as to this interrogatory, with the extent of the objection; identify the particular privilege where privilege is the ground | this interrogatory as phrased, matter record}}`

Then one of the following, per Code of Civil Procedure section 2030.210(a):

<!-- GUIDANCE: Section 2030.220 requires that each answer be as complete and straightforward as the information reasonably available permits. Where responding party lacks personal knowledge sufficient to respond fully, the response must say so and must state that a reasonable and good faith effort to obtain the information was made, except where the information is equally available to the propounding party. That statutory sentence is not a formality; a response that simply omits what is not known, without the required statement, is incomplete on its face. The option to produce writings under section 2030.230 is available only where the answer would require compiling or summarizing documents and the burden is substantially the same for both sides, and it requires specifying the writings in sufficient detail for the propounding party to locate and identify them. Do not use the option as a way around a question that can be answered. -->

**Disposition 1, answer.** `{{FILL: the answer, complete and straightforward, built from the matter record and the client's own knowledge; every date, figure, provider, and name traced to a document or to the client | matter record, client, med chron, billing file, employment records}}`

**Disposition 2, answer with a statement of limited knowledge (Code Civ. Proc., § 2030.220(c)).** `{{FILL: what is known and is being answered | matter record}}`. Responding party lacks personal knowledge sufficient to respond further to this interrogatory and has made a reasonable and good faith effort to obtain the information by inquiry to other natural persons or organizations, except where the information is equally available to propounding party. `{{FILL: the effort actually made, if the attorney wishes to state it | matter file}}`

**Disposition 3, option to produce writings (Code Civ. Proc., § 2030.230).** Responding party exercises the option to produce writings. `{{FILL: specification of the writings from which the answer may be derived or ascertained, in sufficient detail to permit propounding party to locate and identify them as readily as responding party could | document collection log}}`

`{{PRIVILEGE CANDIDATE: any information responsive to this interrogatory that may be privileged or protected work product, with the reason, held out of the answer and listed for attorney clearance}}`

#### Block C: Request for admission

<!-- GUIDANCE: Included because incoming sets in this practice routinely pair requests for admission with Form Interrogatory 17.1, which requires, for each response that is not an unqualified admission, the facts, the witnesses, and the documents supporting the response. Draft the 17.1 answers alongside the admission responses rather than afterward, since a 17.1 answer that does not track its admission response is the most common deficiency in this class. Under section 2033.220, each response must admit so much of the matter as is true, deny what is untrue, or state that responding party lacks sufficient information or knowledge to admit or deny, and that last response requires a statement that a reasonable inquiry concerning the matter has been made. -->

**REQUEST FOR ADMISSION NO. `{{FILL: number | propounded set}}`:**

`{{FILL: the request, reproduced verbatim | propounded set}}`

**RESPONSE TO REQUEST FOR ADMISSION NO. `{{FILL: number | propounded set}}`:**

`{{CANDIDATE OBJECTION: ground, stated with particularity as to this request | this request as phrased, matter record}}`

`{{FILL: admit, deny, admit in part and deny in part with the parts specified, or state that responding party lacks sufficient information or knowledge to admit or deny together with the statement that a reasonable inquiry has been made | matter record, client}}`

`{{ATTORNEY: every admission and every denial is an attorney decision; the drafter proposes from the record and does not resolve}}`

---

### Privilege log

<!-- GUIDANCE: Required where an objection to a document demand rests on privilege or work product, under Code of Civil Procedure section 2031.240(c), which codifies the privilege log concept from California case law and requires sufficient factual information for the propounding party to evaluate the merits of the claim. The log describes the withheld document without disclosing its privileged content, which is a line the drafter should treat as narrow: a description that recites the substance of the communication defeats the purpose of withholding it. Entries here are drafted from the collection log as candidates. The attorney decides what is actually withheld and on what ground. -->

| No.        | Date              | Author        | Recipients  | Type            | Subject matter (non-privileged description) | Ground asserted     | Request nos. |
| ---------- | ----------------- | ------------- | ----------- | --------------- | ------------------------------------------- | ------------------- | ------------ |
| `{{FILL: n | collection log}}` | `{{FILL: date | document}}` | `{{FILL: author | document}}`                                 | `{{FILL: recipients | document}}`  | `{{FILL: type | document}}` | `{{FILL: description sufficient to evaluate the claim without disclosing privileged content | document}}` | `{{CANDIDATE OBJECTION: attorney-client privilege, work product, or both | the document}}` | `{{FILL: nos. | propounded set}}` |

`{{ATTORNEY: confirm each entry is properly withheld and on the stated ground before the log is served}}`

---

### Signature block

<!-- GUIDANCE: Under Code of Civil Procedure sections 2030.250 and 2031.250, the party verifies the responses and the attorney signs any responses that contain objections. Both signatures are therefore required on a response set that contains objections, and the attorney signature is not a substitute for the party verification. -->

Dated: `{{FILL: date | service date}}`

`{{FILL: firm signature block | firm record}}`
`{{FILL: attorney name | firm record}}`
`{{FILL: attorneys for designation | operative pleading}}`

---

### Verification

<!-- GUIDANCE: Verification is signed by the party, not by the drafter and not by the attorney except in the narrow statutory circumstances. Responses consisting solely of objections do not require a party verification. The declaration language below tracks Code of Civil Procedure section 2015.5, which requires the place of execution as well as the date. A verification page served without the place of execution filled in is a routine deficiency letter target. Never fill the execution date or the place: the client fills those at signing, and pre-filling them misstates when and where the client actually signed. -->

**`{{FILL: court and branch | caption above}}`**

|                            |                               |
| -------------------------- | ----------------------------- |
| `{{FILL: plaintiff name(s) | caption above}}`, Plaintiff,  | Case No. `{{FILL: case number | caption above}}` |
| v.                         |                               |
| `{{FILL: defendant name(s) | caption above}}`, Defendants. |                               |

**VERIFICATION**

I am the `{{FILL: party designation, for example "plaintiff" | operative pleading}}` in the above-entitled action. I have read the foregoing `{{FILL: exact title of the response document | title block above}}` and know its contents. The matters stated in it are true of my own knowledge, except as to those matters stated on information and belief, and as to those matters I believe them to be true.

I declare under penalty of perjury under the laws of the State of California that the foregoing is true and correct.

Executed on `{{FILL: date of execution, left blank | the client, at signing}}`, at `{{FILL: city of execution, left blank | the client, at signing}}`, California.

---

`{{FILL: party name, typed beneath the signature line | operative pleading}}`

---

### Proof of service

`{{FILL: the firm's standard proof of service, completed with the service method, date, and served parties | firm record, service list}}`

---

## Pre-service review points

Not part of the response set. Confirm before service.

1. The response deadline was computed from the proof of service on the propounded set, and the service date meets it. If it does not, objections are waived by statute and that is an immediate attorney escalation, not a note.
2. Every request in the served set has a numbered response, and the numbering matches the set exactly with no gaps.
3. Every request is reproduced verbatim, including any typographical error in the original.
4. Every `CANDIDATE OBJECTION` has been adopted, narrowed, or struck by the responding attorney. No candidate reaches the propounding party as drafted.
5. Every adopted objection is stated with particularity as to the individual request, not only in the general objections block.
6. Every privilege-grounded objection identifies the particular privilege, and every withheld document appears on the privilege log.
7. Every `PRIVILEGE CANDIDATE` has been cleared or asserted by the attorney. Nothing flagged as a privilege candidate was produced without that clearance, and nothing was withheld on the drafter's judgment alone.
8. Every statement of compliance is backed by documents that have been located and can actually be produced.
9. Every representation of inability to comply contains the diligent search and reasonable inquiry language, the applicable statutory clause, and the person believed to have the item where one is known.
10. Every `NOT IN RECORD` marker has been resolved or accepted by the attorney. None reach the propounding party, and none survive into a verified response.
11. The verification page is present where the set contains substantive responses, and the execution date and place are left blank for the client.
12. The production index numbers documents to the specific requests they respond to.
