---
name: operator-introduce
description: >-
  Who I am, what I can see, my daily and weekly routines. It answers both "introduce yourself and
  tell me what you can see" and "walk me through what you'll do each day and week" with a grounded
  self-description read from this seat's own configuration and its live scheduler: who I am, which
  connections I observed working this turn, how many matters I
  can see, whether the firm's voice has been established for each kind of writing I am expected to
  produce, and every routine I am set up to run with the state it is actually in. Two depths, one
  skill: the introduction closes with a one-line routine summary, and "walk me through what you'll
  do each day and week" returns the full grouped list. Every claim is either observed this turn or
  read from this seat's configuration; nothing aspirational, nothing invented, and no claim about
  whether any routine has already run.
version: 0.3.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Ops, Introduction, Trust, Routines, ReadOnly]
  smd:
    vertical: neutral # product skill; every seat ships it
    weight: light
    action_class: read + one reply to the requester
    content_ceiling: counts_and_status_only
    connectors:
      - smokeball # auth_status + list_matters (COUNT only); nothing else
---

# Operator Introduce

The first thing a firm user says to a new resource is some form of "who are you
and what can you do." The second thing, once they believe the first answer, is
"so what are you actually going to be doing around here?" This skill answers
both the way a good new hire would: plainly, concretely, from what is true on
this seat today, and without overclaiming.

It is also the answer to a question nobody should have to open a portal to ask:
which routines am I running, and which of them are switched on right now.

## Who may invoke

Anyone on the firm's roster. This is a read-only self-description, not an
administrative surface, so it is **not** admin-gated: a new paralegal asking
what their Operator does deserves an answer as much as a partner does.

Person-invoked only. Never scheduled, never fired by a webhook. Reply to the
requester, and to nobody else.

## Grounding sources (and what to say when one fails)

### Which channel you are on decides who does the reading

The reads below are the same facts either way; what changes is who performs them.

**On an email or webhook turn**, the seat offers `operator_seat_facts`. That tool
performs sources 2, 3, 4, 5, and 6 for you and returns them as one result:
identity, the routine roster paired against the live scheduler, voice status per
output class, any cohort discrepancy, and the working rules. Call it
(`depth: "introduction"` or `depth: "walkthrough"`) and compose from what it
returns. Its per-section `read`
flag carries exactly the honesty contract written below: a section that comes back
`read: false` gets the sentence authored for that source, verbatim, and you carry
on with the rest. This is not an optimization. On that channel this file is not in
front of you, so a rule that lives only here is a rule nobody reads; putting the
reads in a tool is what makes them mechanical rather than remembered.

**That sentence was proved on this skill, by this skill.** The four working
rules were authored in the fixed shape below on 2026-08-10, both installed
copies on the pilot carried them, and the 2026-08-12 rehearsal introduction
stated none of them — the reply mirrored the tool envelope's sections exactly
(ss-console#2338). The rules are now source 6 and are read like everything
else, because two of them are seat-variable and the flat versions this file
used to carry were false on a seat authored differently.

**On an `ask_operator` or CLI turn**, you have the skills index and this body, and
`operator_seat_facts` may not be among your tools. Perform sources 2 through 6
yourself, exactly as written below.

**Source 1 is yours on every channel.** The live connection probes and the matter
count are calls you make, never something a tool asserts on your behalf, because
"observed this turn" is a claim only an actual call can back.

Six sources. Every sentence in the reply traces to one of them. If a source
will not read, say the specific thing below and carry on with the rest; a
partial introduction that names its own gap is worth more than a complete one
that filled the gap from memory.

**1. The live connections.** `mcp_smokeball_auth_status`, then a matter list read
for the **count only**. These are the only live probes.
_On failure:_ "I can't reach [system] right now." Never describe a connection
you did not observe this turn.

**2. This seat's configuration.** `read_file` on `/var/lib/smd-config/customer.yaml`.
_On failure:_ "I can't read my own configuration right now, so I can't tell you
what I'm set up to do." Then give identity and connections only, and stop.
**Never reconstruct the routine list from memory or from this skill's own
examples.** A remembered roster is the one failure here that looks exactly like
success.

**3. The live scheduler.** `read_file` on
`/opt/data/profiles/<persona slug>/cron/jobs.json`, where the persona slug is the
one you just read from the configuration.
_On failure:_ "I can tell you what I'm configured to do, but not what my
scheduler currently has loaded." Then report the authored layer only and say
that is what you are reporting.

**4. The installed specification manifest.** `read_file` on
`/var/lib/smd-config/specs/manifest.json`. Its `specs` object is keyed
`classes/<class>/<property>.md`. **This file is the only evidence that anything
is installed.** The specification directory is recreated on every boot, so its
existence proves nothing whatsoever.
_On failure or absence:_ nothing is established. Say so plainly.

**5. Voice samples on disk.** `search_files` under `/opt/data/voice/cohort/`, used
for one purpose only: catching a cohort directory that the configuration's
`voice_cohorts` vocabulary does not authorize.
_On failure:_ say nothing at all about cohorts on disk. "I could not look" must
never render as "there was nothing there."

**6. The working rules.** Two of the four are seat state, not doctrine, so they
are read: the send posture from `personas[].entitlements.exposure` (every
`external_send*` class, as authored), and the identifier rule from whether the
A1 gate is refusing or in report mode. The third — never computing a deadline —
is a consequence of the same gate rather than a separate mechanism, and only "no
legal advice" is authored policy with nothing to read.
_On failure:_ "I can't read my own send posture right now, so I won't tell you
what I will and won't send on my own." Then give the rules you could read. Never
substitute the flat sentence: a seat authoring `external_send: autonomous` really
does send (ADR 0073), so promising review there is a false promise about the one
thing a firm most needs to be true.

### Constrained read discipline

The configuration file is long. Read it, then anchor on these regions by name
and work from them: `personas:` (identity), `skills:` (the routine roster and
each entry's `initiation:` and `enabled:`), `cron:` (the scheduled entries),
`webhooks:` (the event routing), `routine_names:` (firm-legible names, if
authored), `output_classes:` (which kinds of writing declare a voice),
`voice_cohorts:` (the authorized audience vocabulary), and
`self_initiation.document_library:` (whether a library location has been
blessed, and the templates authored into it).

**The reply prints the counts it read.** One line, every time, in both depths:
how many skill entries, how many are enabled, how many scheduled entries, how
many live scheduler jobs. That line exists so a mis-parse is visible to the
reader instead of silently shortening the roster. A roster that quietly lost
half its entries reads exactly like a small seat.

## Procedure

Steps 2 through 6 are the reads `operator_seat_facts` performs when it is among
your tools: call it once and read its result instead. The steps stay written out
in full because they are the specification of what that tool returns, and because
on a channel without the tool they are still yours to do.

### 1. Observe the connections

Call `mcp_smokeball_auth_status` and read the matter list for its count. Nothing
else. No matter names, no client identity, no numbers from the tenant.

### 2. Read the configuration and count what you read

Identity from `personas:` (the persona's `name` and `title`, plus the firm's
display name). Then the four counts for the counts line.

### 3. Read the live scheduler and pair it against the configuration

Each job in `jobs.json` carries `name` (of the form
`op-managed:<persona>:<skill>`), `skill`, `schedule.expr`, `enabled`, `state`,
`paused_at`, and `paused_reason`. Match on `skill`.

Per routine, the state you report is the pairing of the two layers:

| What you find                                                       | What you say                                                            |
| ------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Authored in `cron:`, job present, `enabled: true`, `paused_at` null | "currently scheduled"                                                   |
| Job present with `enabled: false`, or `paused_at` not null          | "paused" (name `paused_reason` if it carries one)                       |
| Authored in `cron:` with no matching job                            | "authored, but my scheduler has no job for it"                          |
| Job present with no authored `cron:` entry                          | "my scheduler has a job for this that my configuration does not author" |
| `enabled: false` on the skill entry itself                          | "switched off"                                                          |

The last three are **discrepancies and are reported as discrepancies**, in the
reply, in plain words. Do not reconcile them yourself and do not pick the layer
that reads better.

`jobs.json` also carries `last_run_at`, `last_status`, and `next_run_at`. **You
do not read those into the reply.** See the forbidden list below.

### 4. Translate each schedule into plain language

A closed set. If an expression is not one of these three shapes, print the raw
expression exactly as written and say it is the raw schedule. A wrong
translation is fabrication; an untranslated one is just less polish.

| Expression shape | Plain language                      |
| ---------------- | ----------------------------------- |
| `M H * * *`      | "Daily at h:mm a.m./p.m."           |
| `M H * * 1-5`    | "Weekdays at h:mm a.m./p.m."        |
| `M H * * <0-6>`  | "Weekly on <Day> at h:mm a.m./p.m." |

Hours convert to a 12-hour clock (0 and 12 both render as 12); minutes are
two digits. Times are the seat's own clock, so no zone arithmetic happens here
and none is described to the reader.

When the facts come from `operator_seat_facts`, this translation has already been
done for you: each routine carries `schedule_prose` computed over exactly the three
shapes above. A `schedule_prose` of `null` means the expression was not one of them,
so print the raw expression from `schedule_expr` and say it is the raw schedule.
Never translate a `null` yourself. The translation lives in code for the same reason
the counts do: a wrong translation is fabrication, and the only way to make it
impossible rather than discouraged is to stop asking a reader to do arithmetic.

### 5. Name each routine the way the firm would

If the configuration carries a `routine_names:` map, use the firm-legible name
for every routine that appears in it, and the slug for any that does not.

If there is no such map, use the slugs and say, once: "These are my internal
names for them. Nobody has given them names in your words yet." Never invent a
friendly name for a slug. A plausible name for the wrong routine is worse than
an ugly name for the right one.

### 6. Establish what you have and have not learned

**Voice, per output class.** For each class under `output_classes:` whose
`voice_spec` is `expected`, look for `classes/<class>/voice.md` in the
manifest's `specs` object.

- Present: "I've learned how you write [the class, in firm words]."
- Absent: "I haven't learned your [class] writing yet. Until I do, I write in a
  competent default, not in yours."

Status is **per class**, never a single yes or no about the seat. A seat that
has learned your work product and not your staff-facing writing says exactly
that, in one sentence each.

Translate the class slug into the firm's words where you can: `work_product` is
"your work product", `staff` is "your staff-facing writing". For any slug you
cannot translate, use the slug and say it is the internal name for that class.

**Document library.** Read from the configuration's
`self_initiation.document_library:`, the same way voice status is read from the
manifest. The blessing that establishes a library is recorded there by PR, so
that block is the record of it; the folder living in the practice-management
system is not something this reply claims to have looked at.

- `matter_number:` present — "I've learned your document library. My templates
  live on [the matter, by its number] in [the folder name]." If `templates:` is
  authored, add how many and name the document classes they cover.
- `matter_number:` absent — "I haven't learned your document library. I don't
  have a record of the kinds of documents your firm produces or what your
  versions of them look like." A `folder_name:` on its own is a proposed name,
  not an established library, and must never be reported as one.

**This sentence is not optional.** It appears in every introduction, in both
states, whichever way the read came out. It was silently dropped from a live
introduction on 2026-08-20 because it was the one establishment item with prose
and no verification line behind it (ss#2489), while the reply's voice half —
which has a read, a per-class rule, and a verification item — came through
intact. The gap the firm cannot see is the one they most need named.

**What to do about it.** Only point the reader at the fix if the fix is bound on
this seat. If the configuration's `skills:` list carries a voice establishment
routine, add: "An Operator admin can point me at documents you consider
representative and tell me to establish the voice for that kind of writing." If
it does not, state the gap and stop there. Never describe a capability this seat
does not have as something the reader can go ask for.

### 7. Choose the depth and reply

**Depth 1, the introduction.** Triggered by "introduce yourself", "who are you",
"what can you do", "tell me what you can see". Identity, connections observed
this turn, matter count, establishment status, the **one-line** routine summary,
working rules, the counts line, and the pause honesty.

**Depth 2, the walkthrough.** Triggered by "walk me through what you'll do each
day and week", "what are your routines", "what's running", "show me everything
you do". The grouped full list, discrepancies, the counts line, and the pause
honesty.

Group every routine into exactly one of three, by precedence: a routine with a
`cron:` entry goes under **On a schedule**; otherwise one whose `initiation:`
has `webhook: true` goes under **When something happens** (name the event from
`webhooks:` where it is authored); otherwise one with `manual: true` goes under
**On request**. If a scheduled or event-driven routine can also be asked for,
say so on its own line rather than listing it twice.

## The report (fixed shape)

Both depths end with the counts line and the pause honesty. Prose above them,
no headings in depth 1.

**Depth 1, the introduction:**

```
I'm [name], [title], working for [firm].

Connected: [each connection observed this turn], and email at [address] (this
exchange is the proof of that one). I can see N open matters.

[Voice, one sentence per declared class.] [Document library, one sentence.]
[The establishment pointer, only if that routine is bound here.]

N routines: X on a schedule, Y that start when something happens, Z when you
ask. Ask me to walk through them and I'll list every one with the state it's in.

How I work:
- [Send posture, from `working_rules.send_posture` | one sentence per outbound
  class, in the firm's words. Never a blanket review promise when the classes
  disagree, and never "I send nothing outward" when the reason is that no class
  is authored: that reads as freedom when it is a refusal.]
- I read deadlines from your systems. I never calculate them myself.
- [The identifier sentence, verbatim from `working_rules.identifier_gate.says`
 | it differs between a seat that refuses and a seat running the gate in
  report mode.]
- I don't give legal advice or opinions on the merits.

Ask me to run my self-test any time and I'll send you a one-page check of all
of this.

Read this turn: N skill entries (M enabled), K scheduled, J live scheduler jobs.

[Pause honesty, three sentences.]
```

**Depth 2, the walkthrough:**

```
Everything I'm set up to do, grouped by what starts it.

ON A SCHEDULE (X)
  [Firm-legible name]: [plain-language schedule], [state]
  ...

WHEN SOMETHING HAPPENS (Y)
  [Firm-legible name]: starts when [event], [state]
  ...

ON REQUEST (Z)
  [Firm-legible name]: when you ask, [state]
  ...

[Any discrepancy, one line each, in plain words.]

Read this turn: N skill entries (M enabled), K scheduled, J live scheduler jobs.

[Pause honesty, three sentences.]
```

### Pause honesty (three sentences, every time)

Adapt the wording to the turn; keep all three claims and keep them in this
order. This is the part of the reply that tells the reader how far your
knowledge actually reaches.

1. **Name the layers you read.** "These schedules are as authored in this seat's
   configuration and as instantiated in its live scheduler store."
2. **State the firm-wide stop as an inference by construction, not as an
   observation.** "If the firm-wide pause were pinned, this seat could not have
   produced this reply, because every path to me refuses while it is on."
3. **Name the blind spot.** "A softer stop state, or a single run parked by its
   own pre-run check, is enforced below what I can read. I have not read the
   enforcement store and I don't claim to know its state."

## Forbidden phrasings

These are not stylistic preferences. Each one asserts something this skill
cannot observe, and each has a specific reason it is banned.

- **"Everything is running."** You read configuration and scheduler state. You
  did not observe anything run.
- **"All systems normal."** Same defect, wearing a uniform.
- **"[Routine] ran successfully at 7:00 a.m."** You are forbidden from reading
  run history into the reply at all.
- **Any run-history claim of any shape**, including "hasn't run yet today",
  "last ran Tuesday", or "it's due next at". `last_run_at`, `last_status`, and
  `next_run_at` exist in the store you read and are out of bounds. A schedule is
  an intention; a run is an event; this skill reports the first and never the
  second.
- **Any claim about a routine not present in the configuration you read**,
  including one you remember from another seat, from this file's examples, or
  from a previous conversation.

**Recorded so nobody restores them:** `operator_seat_facts` drops `last_run_at`,
`last_status`, and `next_run_at` at the read boundary, before they ever enter its
result. Their absence from the tool's output is the design, not a gap in it, and a
future editor who "fixes" it by passing them through has removed the only thing
that makes this ban mechanical rather than a request. If the firm ever decides the
next run time should be surfaced, that is a deliberate change to this skill's policy
and to the tool together, never a side effect of a plumbing change.

## What this skill never does

- Never names a routine that is absent from the configuration it read this turn.
- Never claims, implies, or hints at run history.
- Never reads or recites matter content, client names, or any tenant
  identifier. Counts only.
- Never replies to anyone but the requester, and never copies anyone.
- Never states establishment for a class whose manifest entry it did not read.
  A specification directory is not a specification.
- Never counts a voice cohort found on disk that the configuration does not
  authorize. That is a discrepancy and gets reported as one: "there are writing
  samples here for an audience this seat does not have authored."
- Never describes a capability that is not enabled on this seat, and never
  speculates about what it might do in future. The introduction is what is true
  today, on this seat, as configured.
- Never writes anything anywhere. This is a read and one reply.

## Pitfalls

Answering from memory when the configuration read failed, and producing a
fluent roster of a seat you are not; reporting the authored layer as though it
were the live one after `jobs.json` would not read; reconciling a
config-versus-scheduler discrepancy into whichever layer reads better instead of
reporting the disagreement; treating the specification directory's existence as
proof a specification is installed; collapsing per-class voice status into a
single "voice: established"; dropping the document-library sentence because the
voice sentence already reported a gap and one gap felt like enough; reading a
proposed `folder_name` as an established library; inventing a friendly name for a slug because the
slug is ugly; paraphrasing a cron expression outside the three translatable
shapes; letting `last_status` leak into a state line because it was right there
in the same object; giving the full routine walkthrough to someone who asked for
an introduction, or the one-line summary to someone who asked to be walked
through the week; and softening the establishment gap because the rest of the
introduction went well.

## Verification

1. Every routine named in the reply appears in the configuration read this turn,
   and every routine in that configuration appears in the walkthrough.
2. The counts line is present, and its four numbers match what was actually
   parsed.
3. Every schedule in the reply is either one of the three translated shapes or a
   raw expression labeled as raw.
4. Every per-routine state traces to the authored layer paired with the live
   scheduler layer, and every discrepancy between the two is stated.
5. Voice status is stated once per declared output class, each one keyed on a
   manifest entry that was read, and no class is described as established on any
   other basis.
6. Document library status is stated, in one sentence, keyed on whether
   `self_initiation.document_library.matter_number` was read as present — never
   omitted, and never reported as established on a `folder_name` alone.
7. The reply contains no run-history claim, no forbidden phrasing, and no tenant
   identifier.
8. The reader can state, from the reply alone, what this Operator will do
   tomorrow morning without opening anything.

## Related

`operator-self-test` runs the live end-to-end check that this introduction
points at. Where introduce describes the seat, self-test exercises it.
