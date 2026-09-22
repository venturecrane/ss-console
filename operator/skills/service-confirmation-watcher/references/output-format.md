# Service Confirmation Watcher - Output Format

Derived from the pack's capture/surface discipline (`ca-served-discovery-capture-spec.md`
§3-4, the sibling capture skill `discovery-served-watch`) and the shared principle that
**every value is traceable to a read** (`_shared-assembler-output-format.md`). This
skill surfaces a **captured input** (a served date); it never authors content, never
files a responsive pleading, and never emits a final deadline. The situation determines
the shape.

Every capture is keyed to `(matter, defendant, service-confirmation)`.

## Shape A - Served date captured & responsive-pleading deadline surfaced for confirm

```markdown
# Service Confirmation - <defendant> - <matter descriptor> - matter <id> - YYYY-MM-DD

**Matter:** <case name>, <number>
**Defendant served:** <defendant name/role, resolved from the matter's otherSideIds / roles>
**Service (read off the proof of service of summons):** <served date> by <method: personal | substituted | mail w/ acknowledgment | electronic | publication> - POS located at <page/section>, quoted below
**Responsive-pleading window:** 30 days after service of summons (§412.20(a)(3); demurrer likewise, §430.40(a))
**Deadline:** <the rules engine's date is to be read and confirmed> OR
<proposed, confirm - 30 days from the effective served date; NOT final; final day rolls to the next court day if it lands on a weekend/holiday, §12 / §12a - attorney/engine confirms>
**Method flags (surface, do not resolve):** <effective date defers from delivery: substituted service deemed complete on the 10th day after mailing, §415.20; service by mail with acknowledgment complete on the date the acknowledgment is executed, §415.30; service by publication complete per publication, not delivery, §415.50 - confirm which governs> / <the §1013 mail extension does NOT extend the summons response window (settled, §413.20); whether the §1010.6 electronic extension reaches it is confirm-at-connect>

## Proof of service (as read)

> <the POS text located and read; the source of the served date + method; nothing inferred>

## Surfaced to <responsible attorney> (confirm task)

> Service confirmation came back on <case> for <defendant>. Served <date> by <method>
> (POS located). Confirm the defendant, served date, and method so the
> responsive-pleading deadline is set. I have not calendared it.

## Internal log (create_memo body)

> Captured the service confirmation on matter <id> for <defendant>: served <date> by
> <method>, read off the POS. fileId <...> recorded. Surfaced to <attorney> to confirm.
> Responsive-pleading deadline owned by the rules engine / confirmed by hand; not computed here.
```

## Shape B - Multiple defendants, different service dates (one capture per defendant)

When more than one defendant on the matter has a service confirmation, surface **one
Shape A block per defendant**, each keyed to its own `(matter, defendant,
service-confirmation)`. **Never** collapse them into one clock and **never** apply one
defendant's served date to another. If a confirmation cannot be tied to a specific
defendant, that one is Shape C, even if the others captured cleanly.

```markdown
# Service Confirmations - <matter descriptor> - matter <id> - YYYY-MM-DD

Two defendants served on different dates; each has its own responsive-pleading clock.

## Defendant 1 - <name> - served <date A> by <method>

<Shape A block>

## Defendant 2 - <name> - served <date B> by <method>

<Shape A block>

**Note:** these are distinct clocks. Defendant 1's served date does not set Defendant 2's
deadline. Each is surfaced for the attorney to confirm; neither is calendared here.
```

## Shape C - Surface & ask (fail-closed: cannot read / not a confirmation / defendant ambiguous)

```markdown
# ⚠ Service Confirmation - needs a human - matter <id> - YYYY-MM-DD

**Situation:** <proof of service missing / illegible / blank / ambiguous served date or method
| document is not clearly a proof of service of summons | served defendant cannot be resolved to
a single defendant on the matter | an effective date that defers from delivery is unclear (substituted, 10th day
after mailing, §415.20; mail with acknowledgment, §415.30; publication, §415.50)>
**What was readable:** <only the facts actually read - never a filled-in guess>
**Decision:** surfaced for a person. Nothing captured as fact, nothing calendared. The
served date / method / defendant is not guessed.
```

## Rules

1. **Shapes A/B carry only captured facts** - the served date and method, each read off
   the proof of service; the defendant, resolved from the matter's roles. No value is
   inferred from a filename, a postmark, an email header, or the document body's own
   claims about timing.
2. **The responsive-pleading deadline is never final here.** Shape A shows either
   "engine's date to be read and confirmed" or a "proposed, confirm" 30-day window with
   the statute cited - never a silently calendared or asserted-final date.
3. **The method flags are surfaced, never resolved.** An effective date that defers from
   delivery (substituted §415.20, acknowledgment §415.30, publication §415.50) and whether
   the §1010.6 electronic extension reaches the summons response window are surfaced
   "confirm," never silently applied; §1013's mail extension does not extend that window
   (§413.20) and is never stacked onto it.
4. **Multiple defendants are Shape B - one clock per defendant.** Never a single collapsed
   clock; never one defendant's served date applied to another.
5. **An unreadable POS, a document that is not a service confirmation, or a defendant that
   cannot be resolved to a single defendant is Shape C** - surface and ask; never a
   guessed or defaulted date, method, or defendant.
6. **No responsive pleading is filed, drafted, or characterized** anywhere.
7. Statutes cited come **only** from the verified set (§412.20(a)(3), §430.40(a),
   §415.20, §415.30, §415.50; §12 / §12a for the weekend/holiday final-day roll; §413.20
   for the settled point that §1013's mail extension does **not** extend the summons
   response window; and §1010.6 named **only** as a confirm-at-connect flag, never
   applied). Never invent or assert a section number.
8. **A document/filename is never the identity.** The confirmation is identified from its
   contents (a proof of service of summons), not from a filename; the defendant from the
   matter's roles, not from a name typed in the body.
9. **Dedup on `(matter, defendant, fileId)`.** A scan does not re-surface a confirmation
   whose `fileId` + resolved defendant already appears in a prior capture memo.
10. **Writes are surfaced as done only after confirmation** (`create_memo`'s own
    `confirmed` field is `true`; `list_tasks` / `get_task` after `create_task`); otherwise
    the write failure is surfaced.
11. The captured input and its source are always stated, so the capture is auditable.
