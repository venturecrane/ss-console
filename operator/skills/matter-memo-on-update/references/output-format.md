# Matter Memo on Update - Output Format (v0.2.0)

One output: a single `create_memo` body, or nothing. There is no client-facing text - the memo is an internal Smokeball record a supervising attorney reads. A duplicate delivery produces **no** output; that silence is correct.

## The memo body

Two lines. A factual one-line record (who / when / how), then the hidden change-key tag on its own final line. Plain ASCII, terse, no prose, no interpretation.

```
Matter updated by <actor> on <YYYY-MM-DD> (<source>).
op-mmou:<matterId>:<timestamp>
```

- **`<actor>`** = the resolved staff name, or **"an unidentified user"** when `userId` is absent or unresolvable. Never a guessed name.
- **`<source>`** = `in-app` (`source: Smokeball`) or `via an integration` (`source: API`).
- **`<YYYY-MM-DD>`** = the event `timestamp` (.NET ticks) converted to a calendar date. Date only - do not invent a precise clock time you are unsure of.
- **`op-mmou:<matterId>:<timestamp>`** = the idempotency tag. `<timestamp>` is the raw `.NET ticks` value, verbatim. It lets the next delivery detect that this exact change is already logged. Keep it on its own final line.

Worked example:

```
Matter updated by Jane Smith on 2026-06-14 (in-app).
op-mmou:6f6a1c2d-...:638609288928990639
```

`userId`-absent / unresolvable example:

```
Matter updated by an unidentified user on 2026-06-14 (via an integration).
op-mmou:6f6a1c2d-...:638609300000000000
```

## Rules

1. **No em-dashes.** The character `-` (U+2014) is a banned marker on authored content; a memo body containing it is refused before it is written. Write plainly - a period, or "by / on / via" phrasing. Hyphens (`-`) and right-arrows are fine; the em-dash is not.
2. **Facts only.** No "why," no judgment, no legal characterization, no next-step suggestion. The memo states who touched the matter, when, and how; the attorney interprets.
3. **No fabrication.** The actor is the resolved name or "an unidentified user." The date comes from the event timestamp. Do not invent a field-level diff, a reason, or a clock time. This version does **not** report which fields changed - see `algorithm.md`, "Deferred: field-level diff."
4. **Resolve the actor to a name when you can.** A supervision memo a human reads should say "Chris Price," not a raw UUID. If `get_staff` fails or `userId` is absent, write "an unidentified user" - never the raw id, never a guess, and never a retry.
5. **The tag is mandatory and last.** Without the `op-mmou:<matterId>:<timestamp>` line, the next delivery cannot dedupe and may double-log.
