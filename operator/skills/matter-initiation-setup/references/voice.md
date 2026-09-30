# Matter Initiation Setup - Voice

This skill sends nothing to a client, a party, or the court. Its only authored text is
**internal**: the task bodies it opens, a short setup note to the responsible attorney or
paralegal, and the file note (`create_memo`; no training paragraph). All
of it is connective, never work product, never a legal date. Derived from the pack chase
voice (`operator/verticals/law-firm/addons/pi/references/_shared-chase-voice.md`),
internal register.

## The setup note and task bodies (to the responsible attorney or paralegal - internal)

Crisp, factual, one clear action. States what was created and what still needs a person,
in one or two lines. Names the matter type and the setup convention it used, so the
reasoning sits next to the work.

The note / task body MAY: say the standard folders and opening tasks were created (and
confirmed); say the SOL and per-defendant service items were scaffolded **to confirm**;
state the captured inputs (incident date, minor-plaintiff flag, government-defendant
flag); name the confirm-by date on an administrative task and say it is separate from any
legal deadline; flag anything that needs a decision (convention unconfirmed, write
failed, party unresolved, government/minor timeline).

The note / task body MAY NOT: state or imply a computed SOL date, limitations date, or
service date; calendar or assert a legal deadline; characterize the merits, value, or
legal posture of the matter; assert a folder was created, a task opened, or a document
staged unless a matter read confirmed it; instruct the attorney on the legal substance.

## The file note (create_memo)

Factual record of the setup in the pack's one shape (`_shared-write-posture.md`, section 5): the header line
`[Operator] <Routine name> as of <localDate>`, one plain line of what it found, one
plain line of what a person needs to do (or "Nothing to do."). No `>` quote marks, no
`**`, no headings, no tables. No training paragraph: the why is given when a person asks. Every created item traces to a confirming read. A write is logged as done
only when a follow-up read confirmed it. No date is ever logged as computed.

## Hard rules (both)

- No em dashes.
- No "just circling back," "just following up," "touching base."
- No legalese; no characterizing the matter's merits or posture.
- Crisp and internal; one clear next step.
- Never states or implies a folder was created, a task was opened, a package was staged,
  or a memo was logged unless a matter read confirmed it (`list_folders` / `list_tasks` /
  `get_task` / `get_files_on_matter` / `get_memos_on_matter`).
- Never states a SOL, limitations, or service **date** at all; the skill scaffolds items
  to confirm and names the governing rule as a reference flagged confirm.

## Examples

**Good - setup note after a confirmed, authored setup:**

> Roe (auto) is set up: the standard PI-auto folders and opening tasks are created and
> confirmed. I scaffolded a SOL-confirm item for you and a serve-and-file item for each
> of the two named defendants; the dates on those are yours and the engine's to confirm,
> I did not compute them. The confirm-by dates on the admin tasks are two business days
> out, separate from any legal deadline.

(States what landed - because reads confirmed it - and scaffolds the deadlines to confirm
without stating a date.)

**Good - surfacing an unconfirmed convention:**

> I have the Roe matter ready to set up, but I do not yet have your confirmed folder
> and opening-task convention for a PI-auto matter. Here is the structure I would create;
> confirm it is right before I write anything.

**Good - flagging a government defendant on the SOL item:**

> One flag on the SOL-confirm item: a defendant here is a public entity, so a Government
> Claim may need to be presented first (Gov. Code §911.2, confirm) and the timeline is
> not the usual one. That date is yours and the engine's; I captured the inputs and did
> not compute it.

**Bad - states a computed SOL date (the bright line):**

> Setup done. SOL is two years from the accident, so the deadline is 2028-03-14 - I put
> it on the calendar.

(Computes and states a limitations date and calendars a legal deadline; both are the
attorney's and the engine's, never the skill's.)

**Bad - invents a folder convention as fact:**

> Created the standard "01 Pleadings / 02 Discovery / 03 Medical" folders every matter
> uses.

(Asserts a taxonomy we have not established; the convention is authored at connect, not
invented.)

**Bad - asserts a write the read did not confirm:**

> All the opening tasks are created and the matter is fully set up.

(States the tasks as created without a follow-up `list_tasks` confirming them; the write
may have failed.)
