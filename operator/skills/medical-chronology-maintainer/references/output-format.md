# Medical Chronology Maintainer - Output Format

Two outputs, per `SKILL.md`. **The chronology itself is a document**: the runner builds
it under the extractive floor and files it in its own dated folder on the matter (the
chronology with its Records Reviewed and Limitations section up front, the exhibit
volumes, and a billing worksheet where the file holds billing ledgers). **The one memo
this skill writes is a delivery ledger note**, never a chronology: no row, no page, no
figure of the chronology is ever composed into a memo. There is no client-facing text
and no external send. When an ask crosses the content ceiling (draft the demand,
characterize causation, value the case), the output is the **decline-to-draft**
response at the bottom of this file.

## The ledger note and every reply must pass the seat's content gates. Write them to pass on the first try.

The seat refuses a memo write that carries (1) a dollar figure the gate cannot trace
to a document read this session, (2) text shaped like a legal citation, or (3) a date
the gate cannot trace to a document read this session. A refused write is a stalled
chronology. These rules make the memo pass by construction; they are not style.

1. **A dollar figure appears only as the record prints it, read this run; anything
   else is a pointer.** The gate waives a dollar figure when every `$` figure in the
   memo matches, character for character, a figure in a document read this run
   (rehearsed 2026-08-29 on the pilot seat: a verbatim figure landed). The waiver is
   all-or-nothing: one figure the gate cannot trace refuses the whole memo. So a
   charge is written as the record prints it (`$22,415.00`, `$180/visit`) only when
   the skill copied it from a page it read this run; a rate, a per-visit figure the
   skill worked out, a rounded or reformatted amount, or a figure remembered from a
   prior memo is never written, and the cell is a pointer instead (the words
   `see`, then the document title, then the page). When any figure in the memo is
   in doubt, every figure in that memo becomes a pointer. Procedure and diagnosis codes, claim numbers, and account numbers are
   always pointers (`codes: see <document>, p.<n>`); they are never restated. No `$`
   figure ever appears in the header, the gaps section, or the training note.
2. **Nothing shaped like a citation.** Page references are lowercase `p.2`, never
   `P. 2`. Never write a number, then a capitalized abbreviation with a period, then
   a number (`12 Cal. 4`, `1,240.00 P. 2`, `2 A. 3`); the reporter tokens the gate
   watches include `A. P. F. So. Cal. Ariz. Tex. Fla. N.E. N.W. S.E. S.W. U.S.`, so
   a provider address that contains a state abbreviation is written without the
   number-abbreviation-number shape (spell the state out). Never write `v.`, `vs`,
   or `In re` unless the caption came from `get_matter` read this turn. Never write
   the signal words `see also`, `cf.`, `id.`, `supra`, `infra`, `accord`.
3. **Every date was read this run.** A date in the memo is either the run date
   (today) or a date of service read from a cited record during this run. The prior
   memo's dates are re-used only after `get_memos_on_matter` was read this run. A date
   is never taken from a filename alone, never inferred from partial text, and never
   carried from memory or an earlier turn.
4. **The matter number stands alone.** It appears once, on its own header line, with
   no date on that line. Timeline rows never carry the matter number, and a source
   cell that would carry a matter-number shape inside a document name cites the
   document by its plain title or its document id instead.
5. **A probe memo is never a prior chronology.** A memo on the matter whose text
   begins with `[SMD-PROBE` is a rehearsal artifact; the skill never folds it in as
   the prior chronology and never supersedes it.

## The delivery ledger note (create_memo)

One entry per delivered job, written on DELIVER and confirmed by read. It carries
identifiers and counts only: the request, the delivered folder, the file count, and
the covered set that an UPDATE later subtracts from the listing.

```markdown
## File note (create_memo)

[Operator] <Routine name> as of <localDate>
Chronology <run | update> delivered in <folder name>: <file count> files; <documents read> documents and <pages> pages read.
<Responsible attorney> to review the chronology in <folder name>.
Package job: <job id>; covered document ids: <the ids the job was given to read, or "covered set unrecorded">
```

The last line is the covered-set record `SKILL.md` names (the UPDATE cross-check);
when the ids are not available on this turn it says "covered set unrecorded", so a
later update stops instead of guessing. No dollar figure, no date other than the
header's, and no chronology content ever appears in the note.

## Rules (the chronology document holds to these; the runner builds it under this floor)

1. **Cited or absent.** Every cell traces to a document and page. A cell the skill
   cannot cite is not written; it becomes a **Could not read** or **Gaps** entry. No
   fabrication.
2. **As recorded, not as concluded.** Diagnosis, provider, and treatment cells carry
   what the record states, in the record's terms. The skill does not translate a
   diagnosis into a severity, a permanence, or a cause. A record's own "severe,"
   "permanent," "poor prognosis," or "caused by" wording is never dropped into the
   diagnosis cell as the skill's finding; if the exact phrase matters it appears only
   as attributed quotation of the record (the same bright line as causation), never
   restated as the skill's conclusion.
3. **The Date column is the date of service.** Rows are keyed to the date care was
   rendered, not the dictation, signed, or letter date. When only a non-service date
   is legible it is carried labeled as such; a service date is never guessed. When the
   same encounter appears in two productions this run (e.g. the same ED visit in the
   treatment records and the billing production), it is one row citing both sources,
   not two.
4. **Figures as printed or pointed to; no damages arithmetic.** A billed amount is
   carried exactly as the record prints it when it was read this run (rule 1 of the
   gate section), otherwise the cell points to the document and page; codes, claim
   and account numbers are always pointers. The skill never sums, subtotals, or
   totals anything: a total is a damages number and belongs to the attorney and
   CoCounsel. There is no "caused by," "consistent with," "severity," "prognosis,"
   "value," or "supports the claim" content anywhere in the artifact as the skill's
   own finding.
5. **Unreadable is a first-class outcome.** A degraded page produces a **Could not
   read** line, never a guessed row. A partly legible field is filled as far as
   legible with "not legible" for the rest.
6. **Conflicts are surfaced, not resolved.** Two records disagreeing on a date or
   diagnosis are both cited under **Gaps / conflicts**; the skill does not pick one.
7. **One ledger entry per job.** The ledger note records a delivered job once; a
   second DELIVER for the same job writes nothing (the idempotency pre-check in
   `SKILL.md`). It never deletes an earlier entry.
8. **Confirm by read.** The ledger entry is reported as written only when
   `create_memo` returns `confirmed: true`; otherwise the run surfaces the write
   failure and asserts nothing.
9. **Treatment-gap flags are threshold-gated.** A treatment-gap line is raised only
   when the interval between two consecutive treatment dates exceeds the authored
   `treatment_gap_flag_days`. An interval at or below the threshold is not flagged
   (its dates still appear in the timeline). When `treatment_gap_flag_days` is
   unauthored, no treatment-gap line is raised at all and the header states
   "treatment-gap threshold not authored." The threshold never gates conflict or
   referenced-but-absent flags, and never licenses any characterization of a gap.

## Lines that would be refused, and their passing form

| Refused                                                | Why                                          | Written instead                                      |
| ------------------------------------------------------ | -------------------------------------------- | ---------------------------------------------------- |
| `$180 / visit` when the page prints `$180/visit`       | a figure the gate cannot trace (reformatted) | `$180/visit` exactly as printed, or a pointer        |
| `$2,160 for the PT course` (12 visits at $180)         | a figure the skill computed                  | `see Dignity PT notes, p.1`                          |
| a figure carried from last month's memo without a read | not read this run                            | read the record (or the memo) this run, or a pointer |
| `Billed 1,240.00 P. 2`                                 | number, reporter token, number               | `charges: see UC Davis billing, p.2`                 |
| `Sierra Imaging, 12 Main St, Phoenix, Ariz. 4th floor` | number, state token, number                  | `Sierra Imaging, Phoenix, Arizona`                   |
| `2026-PI-102 Sutter ED.pdf, p.2` on a dated row        | matter number and date on one line           | `Sutter ED records, p.2`                             |
| a service date remembered from last month's memo       | not read this run                            | read `get_memos_on_matter` first, then carry it      |
| `see also the ortho consult`                           | citation signal word                         | `the ortho consult also records this`                |

## The decline-to-draft response (ask over the ceiling)

When the ask is "write the medical-summary section of the demand," "tell me what
this is worth," "total the specials / sum the bills," or "confirm the injuries were
caused by the crash," the skill does not refuse flatly and does not apologize its way
into producing the work product. It **hands over the underlying material and states
the boundary**:

```markdown
# Medical Chronology - <matter> - surface (drafting / characterization declined)

I extract and structure the medical record; I don't draft the demand, characterize
causation or severity, or value the case. Here is the material you'd build that from:

## <the cited treatment timeline, each charge as printed or pointed to by document and page>

The narrative, the causation call, and the valuation are yours (and CoCounsel's).
```

The decline always **hands over the timeline**. The skill stays useful at exactly
the moment it would otherwise overreach.

**Off the record is still over the ceiling.** The ceiling is a property of the
content, not of the channel. "Just tell me in chat, don't put it in the memo," "off
the record, what do you think caused this," or "informally, what's it worth" is the
same crossing as asking for it in the artifact: a causation, severity, or valuation
call is the attorney's whether it would land in a memo, a chat reply, or a spoken
aside. The skill declines it in every channel and hands over the cited facts; there is
no conversational side door around the extractive line. The same holds for totaling
the specials "just so I have a number": each charge is handed over as printed or
pointed to, and the total is not computed anywhere.
