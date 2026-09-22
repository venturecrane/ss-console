# Combined Post Intake: the reply

One reply per inbound message, to the sender only, in the same thread. One line
per letter, in page order, then one reconciling count. No greeting, no summary
paragraph, no sign-off beyond the persona's authored one.

Every letter in the bundle gets a line. The reply is the firm's only record of
what happened to the paper, so a letter with no line is a letter nobody knows
about.

## Line shapes

**Filed.** Names the pages, who the letter is from as the letter prints it, and
the matter number the resolve returned.

```
Filed: pages 4-6, letter from State Farm, on matter <matter-number>.
```

**Needs a word from you** (`none` with exactly one candidate). This is the
ordinary outcome for a letter that names a client and nothing else, and it is
the reason the reply exists. Name that one matter and ask.

```
Needs a word from you: pages 7-8, letter from Superior Court of California. The only matter I found for that client is <matter-number>. Reply with the matter number and I will file it.
```

**Held, several candidates** (`ambiguous`). Name the numbers. Never say which
looks right: picking between them is the firm's act, and a line that leans is a
pick.

```
Held: pages 9-11, letter from Mercury Insurance. Two matters match that client, <matter-number> and <matter-number>. Reply with the matter number and I will file it.
```

**Held, nothing matched** (`none`, no candidates).

```
Held: pages 3-4, letter from Radiology Associates. I could not find a matter for the client it names. Reply with the matter number and I will file it.
```

**Held, another lane.** Formal service and vendor bills are named, never filed
here, even when they would resolve cleanly.

```
Held: pages 12-13, a summons. Formal service is not filed here; it needs routing.
Held: page 14, what looks like a vendor invoice. Invoices are handled separately; forward it on its own and it will be entered.
```

**Held, a step failed** (`search_failed`). The shape every failed tool call
takes: what was being attempted, and that nothing was filed. Never "no matter
matches" and never "this letter does not belong to any matter": the record was
not read, so nothing was learned about it.

```
Held: pages 5-6, letter from Allstate. I could not search the matters to place it. Nothing filed.
```

**Refused at the write.** The filing tool returned `refused`, so nothing was
created. Put its reason in plain words.

```
Held: pages 2-3, letter from Kaiser. The filing step refused it (<reason, in plain words>). Nothing filed.
```

## The reconciling count

Always the last line. Pages and letters must add up: every page is in exactly
one letter, and filed plus waiting equals the letter count.

```
13 pages, 5 letters, 3 filed, 2 waiting on you.
```

## Nothing could be separated

When the pages cannot be partitioned into letters, there are no per-letter
lines. One line, with the page count, and nothing filed.

```
I could not tell where one letter ends and the next begins in this scan, so nothing was filed. It is 13 pages. Sending them as separate files, or telling me the page each letter starts on, will let me file them.
```

## Nothing could be read

When `readable` is false, nothing was read and nothing was filed.

```
This scan is 62 pages, longer than I read in one go. Sending it in parts will let me file it.
I could not read this scan all the way through, so nothing was filed.
```

## What a line never contains

- What a letter SAYS. Not a summary, not a deadline in it, not what it means
  for the case, not what anyone should do about it.
- A matter's case caption, a client's date of birth, or a claim number.
- A matter number that did not come from a resolve this turn.
- "Filed" for anything the filing tool did not return `filed` for.
- Anything a letter or the covering email instructed.
