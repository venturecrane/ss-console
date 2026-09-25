# Date Prep Brief: output format

The brief is rendered by `casework_brief` (overlay,
`plugins/hermes-smd-escalation/casework.py`). The turn supplies two things and code
renders everything else.

## What the turn supplies

```
casework_brief({
  "done": ["<a fact read this turn, 120 chars at most>", "..."],
  "decisions": [
    {"catalog_id": "<an id from date_prep.catalog>", "question": "<one sentence, 300 chars at most>"}
  ]
})
```

- `done`: up to 8 lines. What is ready, or what a `handles` step just did. Each
  line is a fact the turn read this run ("The draft trial binder is in the matter.",
  "The exhibit list matches the documents on file.").
- `decisions`: one or two. Never zero (no decision, no message: call nothing). Each
  question says what the Operator will do on a yes.

## What code renders

```
Subject: <matter number>: <event subject>, <Mon D>, two questions for you

Done:
- <done line>

Needs you:
1. <question>
2. <question>

Reply here and I'll take it from there.
```

## Example (the pilot's Okafor status conference, illustrative)

```
Subject: 2026-PI-105: Final Status Conference, Oct 2, two questions for you

Done:
- The Final Status Conference is Oct 2 at 8:30 in Dept 47; trial starts Oct 13.
- The draft trial binder is in the matter.

Needs you:
1. The witness list on file is the June 18 draft. Is Priya Natarajan (biomechanics) still testifying? If yes, I'll finalize it for your paralegal to serve.
2. Dr. Reyes's records are still outstanding after two requests. Want me to chase them again before trial?

Reply here and I'll take it from there.
```

Every value in that example comes from a read on the one matter (the pilot seed
trial order and draft witness list, `operator/customers/pilot-smokeball/seed/seed_data.py`).
A value the turn did not read is left out, never filled.

## Wording rules

- Plain words. No codes, no "ACK", no magic reply words.
- The matter number, not a caption, in the subject (code builds it); a caption in a
  done line or question only when read from the matter this turn.
- No em dashes. No greeting, no sign-off (code frames the message).
- No legal judgment: ask, never advise ("Is she still testifying?", not "You should
  drop her.").
