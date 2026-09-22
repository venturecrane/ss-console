# Triage Output Format

Daily note path: `~/.hermes/customer_notes/smd/triage-YYYY-MM-DD.md`

Structure is fixed. The agent must produce exactly these sections in exactly this order. Captain scans this file daily; predictability matters more than cleverness.

## Header block

```markdown
# Inbox Triage - YYYY-MM-DD

**Run started:** ISO-8601 timestamp
**Window:** what was searched (e.g., "is:unread newer_than:1d")
**Messages scanned:** N
**Decisions waiting on you:** M
**Drafts ready to ship:** K
**Low-confidence flags:** L
```

## Themes section

Optional. Only include if the agent identified cross-message patterns. Skip if there's nothing real to say.

```markdown
## Themes

- **Theme name (e.g., "Vendor X follow-ups").** 2-line summary of what's happening across N messages and what Captain should know.
- ...
```

Three theme bullets max. The agent does not pad themes to look thorough.

## Action queue

The substance of the note. Grouped by priority, within priority grouped by action class.

```markdown
## P0 - Today

### REPLY · HIGH · From: name <email>

**Subject:** subject line
**Thread:** 3 messages, last reply 4 hours ago

**Why this:** 1-line reason this is P0 and a reply.

**Draft:**

> Plain-text draft body. No greeting like "Hi Jane" unless the previous
> messages used greetings. Match thread register. Sign "Scott".

---

### ACT · MED · From: name <email>

**Subject:** subject line

**Why this:** 1-line reason.

**Suggested action:** Specific next step. Where it would happen.
What command or click. (Agent does NOT execute.)

**Recommended action I did not take:** If the agent thinks it could
just do this thing if the ceiling were raised, name the command. Empty
if not applicable.

---

(More P0 entries...)

## P1 - This week

(Same format...)

## P2 - Later

(Same format. Often these are just one-liners - "Saved to read later.")

## ARCHIVE candidates

For each: subject, from, one-line reason it's archive-worthy. The agent
does NOT archive. Captain decides.
```

## Junk section

Newsletters, promotions, obvious junk. One line each, no draft, no priority. Captain bulk-acts.

```markdown
## Junk (suggested archive)

- Subject - from - reason (e.g., "Newsletter, weekly cadence")
- ...
```

## Footer

```markdown
---

**Run completed:** ISO-8601 timestamp
**Model:** claude-opus-4-7 (or whichever)
**Token usage:** N input / M output
**Notes for the human:** Anything weird the agent noticed that
doesn't fit elsewhere. Empty if nothing to report.
```

## Rules

1. **No prose anywhere outside the named sections.** The agent does not write paragraphs of analysis or self-justification. The note is scannable.
2. **`P0` shows first, always.** If `P0` is empty, the section header still appears with the literal text `_(nothing P0 today)_` underneath.
3. **`LOW` confidence items get a `🔴` glyph in front of the action class.** That is the only emoji the agent uses in the note. It is a scan signal, not decoration.
4. **No more than 25 entries per run.** If the window has more, the agent triages 25 and notes in the footer how many were skipped.
5. **Drafts use blockquote (`>`) prefix.** Suggested actions use plain paragraphs. Captain scans for `>` to find shippable text.
