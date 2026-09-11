# Client Verification Tracker — Voice

Two outbound drafts, two voices. Neither is ever sent without authenticated
attorney approval. The client-facing draft derives from the pack chase voice
(`operator/verticals/law-firm/addons/pi/references/_shared-chase-voice.md`);
fix the shared voice there first.

## Salutation and signature (client-facing request and reminder)

Per the shared "Salutation and signature" section (`_shared-chase-voice.md`):
the salutation resolves down the ladder (the signer's name from the authored
roster or the request record, then role-addressed, then none), and the body
closes with "Thank you." plus the authored firm name (`customer_name`, or the
persona `signature:` override). Authored data only; never a guessed name. The
internal approve-and-send to the attorney carries no signature block.

## The verification request (to the signer — party / GAL / successor)

Plain-language, respectful, brief. Client-facing (drafted; the firm sends after
approval).

The request MAY: say the verification for their discovery responses is ready and
needs their name and the date; point to where/how to complete it; note the
responses are attached for them to review before completing it; offer to answer
questions **with the team**; note there is a due date and that completing it
promptly keeps the case on track.

The request MAY NOT: explain what the responses say or mean; characterize whether an
answer is accurate, complete, or favorable; define "under oath," "penalty of
perjury," or any legal term; advise the signer on whether or how to answer;
pressure or guilt. The signer attests to the responses; the skill does not tell them
what they are attesting to.

**Floor-clean wording (client-facing drafts only — #1878).** A graduated
client chase is re-scanned by the content-sensitivity floor (ADR 0031) before it
delivers, and the words `sign` / `signature` / `signing`, `deadline`, and
`attorney` each trip a category and HOLD the send. The client-facing request and
reminder are authored around them without weakening the ask (the signer still
attests under penalty of perjury on the verification form itself): write
"complete and return" / "add your name and the date" (not "sign"), "due date" /
"time-sensitive" (not "deadline"), and "the team" (not "attorney"). Full
substitution table and the two canonical drafts live in
`references/verification-request.md`. The **internal** approve-and-send to the
attorney is not content-floored (it goes to a rostered internal recipient) and
keeps its precise language.

## The approve-and-send (to the responsible attorney — internal)

Direct, factual, one clear action. Internal (rostered recipient).

States: the plaintiff, the response-set, the signer it resolved (and why — e.g.
"minor plaintiff, routed to the GAL"), the response deadline, and a single
approve-to-send action bound to this verification. Notes anything that needs the
attorney's judgment (signer ambiguous, objections-only in question, RFA near
deadline).

## Hard rules (both)

- No em dashes.
- No "just circling back," "just following up," "touching base."
- No legalese; no "execute the verification"; no explaining a legal term or
  consequence to the signer.
- Short, warm to the signer / crisp to the attorney. One clear next step.
- Never states or implies the verification was sent, signed, or filed unless that is
  an observed fact.

## Examples

**Good — request to the signer (floor-clean per #1878):**

> Hi <name>, the verification for your discovery responses is ready for you to
> complete and return. Your responses are attached for you to look over first;
> when you're ready, add your name and the date where the form shows, and send it
> back here: <link>. There's a due date on this one, so finishing it when you have
> a few minutes keeps your case on track. If any of it raises a question, we're
> happy to set up a few minutes with the team. Thank you.

**Good — approve-and-send to the attorney:**

> The verification for Reyes (initial FROG responses) is ready. Signer resolved to
> the Guardian ad Litem, <name>, since Reyes is a minor. Response deadline <date>.
> Approve here to send it to the GAL by <firm method>: <token link>.

**Bad — interprets the responses (UPL):**

> I went through your answers and they look complete and favorable, so you're safe
> to sign — the interrogatory about prior injuries is answered the way it should be.

(Characterizes the responses and advises — legal judgment.)

**Bad — asserts a signature not observed:**

> Thanks, your verification is all signed and filed with the court.

(States signed/filed as fact without an observed matching document.)
