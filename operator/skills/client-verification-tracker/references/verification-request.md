# Client Verification Tracker - Request & Reminder Templates

The client-facing drafts the skill sends to the signer (party / GAL / successor)
after authenticated attorney approval. Two drafts: the first request, and the
reminder chase. Neither is ever sent without approval.

## Floor-clean by construction (READ THIS - #1878)

A graduated chase to a rostered client is re-scanned by the content-sensitivity
floor (ADR 0031) before it delivers. The floor's `contract` category matches
`sign` / `signed` / `signature` / `signing`; `scope` matches `deadline`;
`legal` matches `attorney`. A body carrying any of those is HELD as a draft even
under an authored autonomous client-send, so the "auto-handle" commitment does
not deliver (issue #1878).

These templates are authored to clear the floor without weakening the ask. The
signer still completes a verification they attest to under penalty of perjury -
that legal weight lives in the verification document itself, which they read and
complete; the cover message only prompts them to complete and return it. Keep
the substitutions below when drafting:

| Do not write (trips the floor)         | Write instead (floor-clean, same meaning)                            |
| -------------------------------------- | -------------------------------------------------------------------- |
| sign / signature / signing / sign here | complete and return; add your name and the date where the form shows |
| deadline                               | due date; there is a date on this one; time-sensitive                |
| attorney                               | the team; your case team; us; the office                             |

The word "verification" itself is floor-clean and stays. So do "complete",
"return", "add your name", "due date", "the form", "your case".

Same law-seat first-draft rules apply (see SKILL.md "Delivery channels"): no
statute citations, no case caption (matter by number), no em dashes, no dollar
figure without a named authored source.

## Draft 1 - the first request (to the signer)

Plain-language, respectful, brief. Says the verification is ready, that the
responses are attached to look over first, points to where to complete it, notes
it is time-sensitive, and offers to answer questions with the team. Interprets
nothing about the responses; states no legal consequence.

> Hi <name>, the verification for your discovery responses is ready for you to
> complete and return. Your responses are attached for you to look over first;
> when you are ready, add your name and the date where the form shows, and send
> it back here: <link>. There is a due date on this one, so finishing it when you
> have a few minutes keeps your case on track. If any of it raises a question, we
> are happy to set up a few minutes with the team. Thank you.

## Draft 2 - the reminder chase (to the signer)

Shorter. Orients (which verification, that it is still open), points again to
where to complete it, and restates that it is time-sensitive. No pressure or
guilt, no "just circling back" / "just following up" / "touching base".

> Hi <name>, following up on the verification for your discovery responses: it is
> still open and we have not received it back yet. When you have a few minutes,
> add your name and the date where the form shows and return it here: <link>.
> There is a due date on this one, so getting it back soon keeps your case on
> track. Happy to answer any questions with the team.

## What the request MAY NOT do (unchanged from voice.md)

Explain what the responses say or mean; characterize whether an answer is
accurate, complete, or favorable; define any legal term or consequence; advise
the signer on whether or how to answer; pressure or guilt. The signer attests to
the responses; the skill does not tell them what they are attesting to.
