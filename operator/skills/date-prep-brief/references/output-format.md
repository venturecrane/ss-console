# Date Prep Brief: output format

The brief is rendered by `casework_brief` (overlay,
`plugins/hermes-smd-escalation/casework.py`). The turn supplies two things and code
renders everything else.

## What the turn supplies

```
casework_brief({
  "done": ["<a fact read this turn, 200 chars at most>", "..."],
  "decisions": [
    {"catalog_id": "<an id from date_prep.catalog>", "question": "<two or three short sentences, 300 chars at most>"}
  ]
})
```

- `done`: up to 4 lines, 200 characters each. What is READY, and nothing else.
  Each line is a fact the turn read this run ("The draft trial binder is in the
  matter.", "The exhibit list matches the documents on file."). The first line
  says when and where the date is and what follows it, when the event or trial
  order gives them ("The Final Status Conference is Oct 2 at 8:30 in Dept 47;
  trial starts Oct 13."). A gap is not done: what is missing belongs inside the
  question it bears on ("The exhibit list and deposition summaries are not in the
  matter yet. ..."), or nowhere. A `handles` step recorded with `casework_step_done` is
  listed by code, first, in its catalog entry's `done_line`
  (`done_since.STEP_PHRASES`); the turn does not repeat it.
- `decisions`: one or two. Never zero (no decision, no message: call nothing). Each
  is two or three short sentences: the fact, one yes-or-no question, and what the
  Operator will do on a yes, naming who it is for ("If yes, I'll finalize it for
  your paralegal to serve."). Never an either/or the attorney has to untangle.

## What code renders

```
Subject: <matter number>: <event subject>, <Mon D>, two questions for you

Done:
- <done_line of each step recorded this run>
- <done line>

Done since last time: <line>; <line>. [quiet authored, this matter's untold earlier work only]

Needs you:
1. <question>
2. <question>

Reply here and I'll take it from there.
```

## Example (illustrative only: invented names, never facts about any matter)

```
Subject: 2026-PI-999: Final Status Conference, Oct 2, two questions for you

Done:
- The Final Status Conference is Oct 2 at 8:30 in Dept 47; trial starts Oct 13.
- The draft trial binder is in the matter.

Needs you:
1. The witness list on file is the June 18 draft. Is the accident reconstruction expert still testifying? If yes, I'll finalize it for your paralegal to serve.
2. The treating doctor's records are still outstanding after two requests. Want me to chase them again before trial?

Reply here and I'll take it from there.
```

In a real brief every value comes from a read on the one matter. A value the turn
did not read is left out, never filled, and nothing in this example is ever a fact
about a matter.

## Wording rules

- Plain words. No codes, no "ACK", no magic reply words.
- The matter number, not a caption, in the subject (code builds it); a caption in a
  done line or question only when read from the matter this turn.
- No dashes as punctuation: no em dash, and no "--" or " - " standing in for one.
  Use a period or a comma. No greeting, no sign-off (code frames the message).
- No ids, hashes, or shortened ids (a task id like "223145b9" is our record, not
  the attorney's). Name a task by its subject.
- No capitals for emphasis ("DRAFT"); say "a draft".
- No legal judgment: ask, never advise ("Is she still testifying?", not "You should
  drop her.").
