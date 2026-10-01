# Statute Watch - Output Format

One internal email a month to the recipient the firm named
(`settings.recipient`), with a spreadsheet attached. **Rendered in code:**
`render.py` implements this page literally in the pre-run (`workbook.py` builds
the spreadsheet), and the seat dispatches the result out of turn. The model
composes none of it.

## Subject

`Statute watch, <Month> <YYYY>: <N> cases, <K> due this week`, the month the run happens in. No day.
One case reads `1 case`. With no cases: `Statute watch, <Month> <YYYY>: no cases`.

`<K>` counts the cases due within 7 days (0 through 7 days left).

## Body

Markdown the overlay renders to html: `##` headings, `- ` items, `**bold**`,
and an indented line under an item as that item's detail line.

```
<N> open cases have a statute date in the next three months with no Filed date and no Case number in Smokeball, <K> due within 7 days; since last month, <new> new and <P> passed with no filing recorded.

## Due this week

- **File <number>, <last name>**
   Statute date <Month D, YYYY>, <N> days left. Attorney <last name>. Court-named documents: <count>.

## Due this month

- ...

## Later

- ...

And <N> more cases.

<N> open cases could not be checked this month.

## Since last month

- **File <number>, <last name>**: <what the record shows>

The full list is attached as a spreadsheet.
```

- The headline is one sentence. The part from `; since last month` on appears only when there is a last run to compare with. One case reads `1 open case has`.
- With no cases the headline is `No open cases have a statute date in the next three months without a filing.` (with the since-last-month part before the period when it applies), and no case sections follow.
- Sections: `## Due this week` (0 through 7 days left), `## Due this month` (8 through 30), `## Later` (31 and over). A section with no cases is left out. Soonest statute date first (ties by file number).
- "0 days left" reads "due today"; one day reads "1 day left".
- At most 100 cases across the sections. Past that, `And <N> more cases.` counts the rest; the spreadsheet carries every case.
- The unreadable line appears only when at least one matter's case details could not be read.
- Client names come from the matter title (`NNNNNN - Last, First - ...`), last names only; co-clients read `A and B` (three or more: `A, B and C`). Initials are dropped.

## Since last month

Compares this month's list with last month's (the state file the pre-run keeps).
Each item is `- **File <number>, <last name>**: ` followed by one of:

| Change               | Sentence                                                                                     |
| -------------------- | -------------------------------------------------------------------------------------------- |
| New                  | `New on the list. Statute date <Month D, YYYY>.`                                             |
| Statute date passed  | `Statute date <Month D, YYYY> has passed. Smokeball shows no Filed date and no Case number.` |
| Statute date changed | `Statute date changed from <old> to <new>.`                                                  |
| Statute date removed | `Statute date removed in Smokeball.`                                                         |
| Not checked          | `Could not be checked this month.`                                                           |
| Filed per Smokeball  | `Filed per Smokeball (Filed date or Case number now recorded).`                              |
| Closed               | `Closed in Smokeball.`                                                                       |
| No longer open       | `No longer Open or Pending in Smokeball.`                                                    |

Items appear in that order, then by statute date. Each case that left the list
is read again this month and worded only as its record shows: nothing is
inferred, and a case whose record explains nothing reads `Could not be checked this month.`
At most 50 items; past that, `And <N> more changes.` (the spreadsheet has all).

With no last run the section reads `Changes since last month start with next month's report.`
With a last run and nothing changed: `No changes since last month.`

## The attachment line

`The full list is attached as a spreadsheet.` ends the body. When the workbook
could not be built, or the seat sends the body without it, the line reads
`The workbook could not be attached this month.`

## The spreadsheet

`Statute watch <Month> <YYYY>.xlsx`, three sheets:

- **Statute watch**: every listed case, columns `Days left`, `Statute date`
  (a date, mm/dd/yyyy), `File number`, `Client` (Last, First), `Attorney`
  (full name), `Status`, `Court-named documents`. Bold frozen header, filter
  on every column, rows due within 7 days shaded red and within 30 days
  shaded yellow.
- **Since last month**: `Change`, `File number`, `Client`, `Statute date`,
  `Detail` (the same sentence as the body).
- **About**: what is counted, in the words of the first report.

The only dates in the spreadsheet are statute dates, each on its own case's row.

## Absent values

| Value                | Renders as                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------ |
| File number          | `No file number` (body), `No number on file` (spreadsheet)                                 |
| Client name          | `client name not available` (body), `Client name not available` (spreadsheet)              |
| Responsible staff    | `No responsible attorney on file.` (none set) or `Attorney not available.` (set, not read) |
| Court document count | `Court-named documents: not checked.`                                                      |

## The skeleton (sent only if the full body is refused)

```
<N> open cases have a statute date in the next three months with nothing filed; the case list could not be sent this month.
```

Counts only: no file number, name or date, and no attachment.

## Rules

- The only dates in the body and the spreadsheet are statute dates; the date of last month's run is never printed.
- Last names only in the body, with initials and periods removed, so no name can read as a case caption.
- No em dashes, no citations, no field names.
- No greeting and no sign-off: the email is a report, not a letter.
