# Medical Records Chaser — Voice

Derived from `operator/verticals/law-firm/addons/pi/references/_shared-chase-voice.md`
(the pack's canonical chase voice). Fix the shared voice there first; this file adds
only what is specific to a records chase. One outbound draft, one recipient type:
the provider or records vendor. Whether it sends or is held for review follows the
firm's authored ceiling (see SKILL.md "The send seam").

## Floor-clean by construction (#1878)

The chase body follows the shared substitution table (`_shared-chase-voice.md`,
"Floor-clean by construction"): a graduated vendor chase is re-scanned by the
content-sensitivity floor (ADR 0031) before it delivers, and the floor's `contract`
category matches `signed`. So the chase offers to resend "the authorization form",
never "the signed authorization" — the recipient knows which form it is, and the
body clears the floor. Internal memos and tasks keep the precise words.

## The records chase (to the provider / records vendor)

Professional, businesslike, brief — a vendor/provider tone, not a client warmth
tone. It moves one outstanding record request forward.

The chase MAY: name the patient and the request (provider, date of request); state
that the records are still outstanding; ask for a status or an expected date; offer
to resend the authorization form if that helps; note when timing matters in plain
practical terms.

The chase MAY NOT: say anything about what the records contain, the diagnosis, or the
course of treatment; characterize whether a production is "complete" or "sufficient";
state or invent a legal consequence; advise; pressure or guilt; assert that records
were received, filed, or reviewed unless that is an observed fact.

## Salutation and signature

Per the shared "Salutation and signature" section (`_shared-chase-voice.md`):
salutation resolves down the ladder (named records contact from the request
record, then "records team at <vendor name from the request record>", then no
salutation), and the body closes with "Thank you." plus the authored firm name
(`customer_name`, or the persona `signature:` override). Authored data only;
never a guessed name, never a title line.

## Hard rules

- No em dashes.
- No "just circling back," "just following up," "touching base."
- No legalese ("per our correspondence," "heretofore," "execute").
- Short and crisp; one clear next step (status or expected date).
- Never assert receipt of records that are not observed in the matter.
- Never reference or characterize treatment or diagnosis.

## Examples

**Good — chase to the records vendor / provider:**

> Hi <name>, following up on the records request for <patient> (requested <date>). We
> show the <provider> records still outstanding on our end. Could you share a status
> or an expected date? Happy to resend the authorization form if that would help.
>
> Thank you.
> <firm name from customer_name>

**Bad — characterizes treatment / reads the records (crosses the line):**

> We need the rest of the physical therapy notes since the ones you sent only cover
> the first two weeks after the surgery and we can see the treatment is ongoing.

(Characterizes treatment and reads clinical content; judges completeness.)

**Bad — asserts receipt not observed / invents a consequence:**

> Got everything, thanks, we have all the records now and the case is ready to file,
> so we are all set on our end.

(Asserts receipt and a status that is not an observed fact.)
