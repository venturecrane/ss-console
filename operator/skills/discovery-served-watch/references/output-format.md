# Discovery Served Watch - Output Format

Derived from the pack's capture/surface discipline
(`ca-served-discovery-capture-spec.md` §3–4) and the shared assembler principle that
**every value is traceable to a read** (`_shared-assembler-output-format.md`). This
skill surfaces a **captured input**; it never authors content and never emits a final
deadline. The situation determines the shape.

Every capture is keyed to `(matter, served-document, type)`.

## Shape A - Captured & surfaced for attorney confirm (rog / RFP / RFA)

```markdown
# Served Discovery - <type> - <matter descriptor> - matter <id> - YYYY-MM-DD

**Matter:** matched to a single unique existing matter by case name + number - <case name>, <number> (caption is the untrusted search key, not a matter assertion)
**Type:** <interrogatories (Form / Special) | requests for production | requests for admission>, <set number as stated on the document>
**Service (read off the proof of service):** <date> by <single method> - POS located at <page/section>, quoted below
**Response verification required?** <Yes, unless objections-only (§2030.250 / §2031.250 / §2033.240)>
**Deadline:** <the rules engine's date is to be read and confirmed> OR
<proposed, confirm - base 30 days (§2030.260 / §2031.260 / §2033.250) + <method extension, §1013 / §1010.6>; NOT final>

## Proof of service (as read)

> <the POS text located and read - the source of the date + method; nothing inferred>

## Surfaced to <responsible attorney> (confirm task - the EMAIL-SAFE block)

When the surface goes out by email (create_draft), the draft carries ONLY this
block, written citation-free (see SKILL.md "Delivery channels + refusal
fallback"): the governing rule in plain words, never a section number. The full
capture record above (header + POS quote, with citations) is the matter-internal
artifact, never the email body.

> Served <type> captured on <case>. Service <date> by <method> (proof of service located).
> Proposed response deadline <date>: <plain words, e.g. "30 days from service by mail plus five calendar days for mail service">. Not final until you confirm.
> Confirm the type, service date, and method so the deadline is set. <RFA: flag in plain words that an unanswered set risks the requests being deemed admitted.>

## Internal log (create_memo body)

> Captured served <type> on matter <id>: service <date> by <method>, read off the POS.
> Surfaced to <attorney> to confirm. Deadline owned by the rules engine / confirmed by hand; not computed here.
```

## Shape B - Bare deposition notice captured (calendar + prep, not a response clock)

Use this **only** when the notice carries **no** document-production demand. If it
carries a document rider, use Shape C.

```markdown
# Served Discovery - Deposition Notice - <matter descriptor> - matter <id> - YYYY-MM-DD

**Type:** deposition notice (no embedded document demand) - **no party response-verification**; drives calendar + prep
**Read off the notice:** deponent <name/role>, date/time <...>, place/remote <...>
**Decision:** surfaced for scheduling and prep; this is not a response-verification and starts no response clock.

## Internal log (create_memo body)

> Captured a bare deposition notice on matter <id> (deponent <...>, <date/place>), no document demand. fileId <...> recorded. Surfaced for calendar + prep.
```

## Shape C - Deposition notice WITH an embedded document demand (compound - both facets)

A deposition notice carrying a document-production rider (records deposition,
§2025.220(a)(4)) is **not** a bare "no response clock" notice: the production items
carry their own objection window. Surface **both** facets. If either facet cannot be
read cleanly, fall back to Shape D rather than dropping the production obligation.

```markdown
# Served Discovery - Deposition Notice + Document Demand - <matter descriptor> - matter <id> - YYYY-MM-DD

**Type:** deposition notice with an embedded document demand (§2025.220(a)(4))
**Facet 1 - calendar + prep:** deponent <name/role>, date/time <...>, place/remote <...>
**Facet 2 - document-production objection window:** objections to the production items are due **at least 3 calendar days before the deposition** (§2025.410) - proposed, confirm the statute and the count at connect; surfaced for attorney confirm, never calendared here.
**Service (read off the proof of service):** <date> by <single method> - POS located at <page/section>, quoted below

## Proof of service (as read)

> <the POS text located and read>

## Internal log (create_memo body)

> Captured a records deposition notice on matter <id> (deponent <...>, <date/place>) carrying a document demand.
> Surfaced BOTH the calendar/prep facet and the §2025.410 document-objection window. fileId <...> recorded. Not calendared here.
```

## Shape D - Surface & ask (fail-closed: cannot read / classify / match / one method)

```markdown
# ⚠ Served Discovery - needs a human - matter <id> - YYYY-MM-DD

**Situation:** <proof of service missing / illegible / blank / ambiguous date or method
| POS states more than one service method | discovery type unclear | deposition notice
carries a document demand and a facet cannot be read | inbound document matches no
matter / more than one matter / ambiguous match>
**What was readable:** <only the facts actually read - never a filled-in guess>
**Decision:** surfaced for a person. Nothing captured as fact, nothing calendared. The
service date/method/type is not guessed.
```

## Rules

1. **Shapes A/B/C carry only captured facts** - the type descriptor (Form vs Special,
   set number) and the service date + method, each read off the document (the POS for
   date/method). No value is inferred from a postmark, an email header, or the document
   body's own claims.
2. **The deadline is never final here.** Shape A shows either "engine's date to be
   read and confirmed" or a "proposed, confirm" base window with the statute cited -
   never a silently calendared or asserted-final date. The Shape C objection window and
   any plaintiff-early-service extension are likewise surfaced "proposed, confirm."
3. **An unreadable POS, a multi-method POS, an unclear type, an unreadable compound
   facet, or a matter that is unmatched or matches more than once is Shape D** -
   surface and ask; never a guessed or silently-resolved date, method, type, or matter.
4. **No response is drafted and no request is characterized** anywhere.
5. Base-lane statutes cited come **only** from the capture spec's verified set
   (§2030.250 / §2031.250 / §2033.240; §2030.260 / §2031.260 / §2033.250; §1013 /
   §1010.6; §2033.280 for RFA). The compound deposition-notice statutes
   (§2025.220(a)(4), §2025.410) and the early-service caveat (exact governing
   subsections confirm-at-connect per the capture spec) are cited **only as surfaced
   flags for attorney confirm**, until they are added to the capture spec's verified
   grid. Never invent or assert a section number for these.
6. **A caption is a search key, not a matter identity.** A capture attaches to a matter
   only on a single unique existing Smokeball match; the surface names the caption as
   the untrusted source of the search.
7. **Dedup on `(matter, fileId)`.** A scan does not re-surface a document whose `fileId`
   already appears in a prior capture memo; every capture memo records its `fileId`.
8. **Writes are surfaced as done only after a confirming read** (`get_memos_on_matter`
   after `create_memo`; `list_tasks` / `get_task` after `create_task`); otherwise the
   write failure is surfaced.
9. The captured input and its source are always stated, so the capture is auditable.
