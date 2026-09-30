# Statute Watch - Output Format

One internal email a month to the recipient the firm named
(`settings.recipient`). **Rendered in code:** `render.py` implements this page
literally in the pre-run, and the seat dispatches the result out of turn. The
model composes none of it.

## Subject

`Statute report, <Month> <Year>`, the month the run happens in. No day.

## Body, one or more cases

```
Open cases whose statute of limitations date falls in the next three months, with no Filed date and no Case number in Smokeball. Soonest first.

1. Matter <number>, client <last name>: statute date <Month D, YYYY>, <N> days left. Attorney <last name>. Court-named documents: <count>.
2. ...

And <N> more cases.

<N> open cases could not be checked this month.
```

- One numbered line per case, soonest statute date first (ties by matter number).
- At most 100 lines. Past that, the "And N more cases." line counts the rest.
- "0 days left" reads "due today"; one day reads "1 day left".
- The unreadable line appears only when at least one matter's case details could not be read.

## Body, no cases

```
No open cases have a statute date in the next three months without a filing.
```

followed by the unreadable line when it applies.

## Absent values

| Value                | Renders as                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------ |
| Matter number        | `Matter with no number on file`                                                            |
| Client last name     | `client name not available`                                                                |
| Responsible staff    | `No responsible attorney on file.` (none set) or `Attorney not available.` (set, not read) |
| Court document count | `Court-named documents: not checked.`                                                      |

## The skeleton (sent only if the full body is refused)

```
<N> open cases have a statute date in the next three months with nothing filed; the case list could not be sent this month.
```

Counts only: no matter number, name or date.

## Rules

- The only dates in the body are the statute dates, each on its own case's line.
- Last names only, with initials and periods removed, so no name can read as a case caption.
- No em dashes, no citations, no field names.
- No greeting and no sign-off: the email is a report, not a letter.
