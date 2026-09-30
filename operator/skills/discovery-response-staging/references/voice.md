# Discovery Response Staging - Voice

This skill sends nothing to a client, a party, or the court. Its only authored text is
**internal**: a short routing note to the responsible attorney or paralegal, and the
file note (`create_memo`; no training paragraph). Both are connective,
never work product. Derived from the pack chase voice
(`operator/verticals/law-firm/addons/pi/references/_shared-chase-voice.md`), internal
register.

## The routing / ready note (to the responsible attorney or paralegal - internal)

Crisp, factual, one clear action. States what was staged and where, or that the
returned draft is ready for review, in one or two lines. Names the response-set, the
folder, and the engine that drafts, so the reasoning sits next to the work.

The note MAY: say the inputs are staged and where; say the returned draft is filed and
routed for review; name the target folder and the drafting engine; note the one thing a
person needs to do (confirm the target, or review the routed draft); flag anything that
needs a decision (target unconfirmed, write failed, candidate ambiguous).

The note MAY NOT: characterize the response draft's quality, completeness, or legal
adequacy; describe what the response says or should say; assert a document is staged, or
a draft is final, unless that is an observed fact; instruct the attorney on the legal
substance.

## The file note (create_memo)

Factual record of the action in the pack's one shape (`_shared-write-posture.md`, section 5): the header line
`[Operator] <Routine name> as of <localDate>`, one plain line of what it found, one
plain line of what a person needs to do (or "Nothing to do."). No `>` quote marks, no
`**`, no headings, no tables. No training paragraph: the why is given when a person asks. Every named document traces to a matter read. A write is logged as done
only when a follow-up read confirmed it present.

## Hard rules (both)

- No em dashes.
- No "just circling back," "just following up," "touching base."
- No legalese; no characterizing the draft or the response substance.
- Crisp and internal; one clear next step.
- Never states or implies a document was staged, a folder was created, a review task
  was opened, or a draft is final/routed unless that is an observed fact confirmed by a
  matter read (`get_files_on_matter` / `list_folders` / `list_tasks` / `get_task` /
  `get_memos_on_matter`).

## Examples

**Good - ready note after a confirmed stage:**

> The inputs for the Roe RFP-set response are staged in "Discovery / RFP Working":
> the served requests plus Roe's prior verified responses. Ready for BriefPoint to
> draft.

(Names the confirmed folder, not a generic "discovery drafting folder" - the folder is
stated only because the authored convention confirmed it.)

**Good - routing the returned draft:**

> The RFP-set response draft is in the matter. I left it in place and opened a review
> task for you (confirmed created); confirm-by is set two business days out, separate
> from any response deadline. I did not edit it; it is ready for your review.

**Good - surfacing an unconfirmed target:**

> I have the Roe RFP inputs ready to stage, but I do not yet have a confirmed folder
> the drafting engine reads from on this matter. I would put them in "Discovery / RFP
> Working"; confirm that is right before I place them.

**Bad - asserts a stage the read did not confirm:**

> All the RFP inputs are staged and BriefPoint has everything it needs.

(States staged as fact without a follow-up read confirming the documents are present;
the write may have 403'd.)

**Bad - characterizes the draft (work product):**

> The returned draft looks complete and the objections are well supported, so it is
> ready to serve.

(Characterizes the draft's legal adequacy; the skill does not judge work product.)

**Bad - invents a folder convention as fact:**

> Staged into the standard "01 - Discovery Responses" folder every matter uses.

(Asserts a naming convention we have not established; the convention is confirmed at
connect, not invented.)
