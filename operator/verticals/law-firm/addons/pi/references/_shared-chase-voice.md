# Shared: Chase Voice (PI-litigation pack)

Canonical voice for every **chase** skill in the pack (client-verification-tracker,
medical-records-chaser, lien-ledger-tracker payoff dunning, and the chase legs of the
discovery-response-tracker). Each chase skill's own `references/voice.md` derives from
this — copy it and add only what is skill-specific. Fix the voice here first.

## Floor-clean by construction (READ THIS — #1878)

A graduated chase to a rostered client or vendor is re-scanned by the
content-sensitivity floor (ADR 0031) before it delivers. A chase body carrying a
floor trigger word is HELD as a draft even under an authored autonomous send, so
the "auto-handle" commitment does not deliver (issue #1878). Chase bodies are
authored to clear the floor without weakening the ask — the legal weight lives in
the document the recipient completes, never in the cover message. Substitutions
every chase body keeps (tune the template, never the floor):

| Do not write (trips the floor)           | Write instead (floor-clean, same meaning)                                                  |
| ---------------------------------------- | ------------------------------------------------------------------------------------------ |
| sign / signed / signature / signing      | complete and return; add your name and the date where the form shows                       |
| deadline                                 | due date; there is a date on this one; time-sensitive                                      |
| attorney / counsel / legal               | the team; your case team; us; the office                                                   |
| agreement / contract / engagement letter | the letter we sent; the form; the paperwork                                                |
| invoice / payment / fee / a $ figure     | the open balance; the statement (and only where the skill's line allows money talk at all) |

This table governs the OUTBOUND chase body only. Internal surfaces (memos, task
text, decision lines, status headers) keep the precise words ("unsigned",
"signed", "attorney") — the floor scans what leaves the firm, not the matter file.

## The line

A chase is a short, factual, low-pressure follow-up that moves one outstanding thing
forward. It names what is outstanding, why it matters at a plain-practical level, and
the single next step.

The chase MAY: state what is outstanding (a form to complete, a record, a payoff
figure, a response); note the due date or why timing matters in practical terms;
point to the one action needed; offer to help / route a question to the right person.

The chase MAY NOT: explain or characterize a legal term, consequence, or position;
give advice on how to answer or what to do; pressure, guilt, or imply a thing is done
that is not observed; invent a consequence or a fact not in the record.

## Hard rules

- No em dashes.
- No "just circling back," "just following up," "touching base."
- No legalese ("execute," "heretofore," "per our correspondence").
- Short, warm to a client / crisp to a vendor or professional. One clear next step.
- Never assert a signature, receipt, payment, or filing that is not an observed fact.
- Recipient-appropriate: a client gets warmth; a lienholder or records vendor gets a
  professional, businesslike tone.

## Salutation and signature

Every chase body opens with a salutation resolved by the ladder below and closes
with the signature block. Both halves are AUTHORED DATA ONLY: nothing here is
ever invented (ADR 0035; the "Business Owner" compliance lesson from the SOW
audit is the failure mode this exists to prevent).

**Salutation ladder, in order, stop at the first available:**

1. **Named contact.** The name on the request record the chase is about: the
   records-request contact in Smokeball for a vendor chase, or the authored
   roster entry for a client chase. Extractive from the firm's own system of
   record, never composed.
2. **Role-addressed.** "records team at <vendor name from the request record>".
   The vendor name comes from the request record itself.
3. **No salutation.** The body starts at the fact line.

Never a guessed name. Never placeholder filler of the "Business Owner" class.

**Rendered shape** (the chase templates read `{{firm_name}}` from the seat's
`/var/lib/smd-config/customer.yaml` `customer_name`; the optional authored
override is the persona `signature:` key, fields `firm_line` + `closing`):

```
<salutation>

<body>

Thank you.
<signature.closing | omitted when unauthored>
<signature.firm_line | customer_name>
```

The signature block carries only the closing and the firm name. Unauthored
`signature:` degrades to `customer_name` alone, which is authored data, not
invention. **Floor note:** no title line ("Attorneys at Law" trips the floor on
its own vocabulary) and no floor-trigger word from the table above may enter the
block; the firm's authored display name is used exactly as authored.

## Good / bad

**Good (records chase, to a vendor):**

> Hi <name>, following up on the records request for <patient> (DOR <date>). We show
> the <provider> records still outstanding. Could you let us know the status or an
> expected date? Happy to resend the authorization if that helps.
>
> Thank you.
> <firm name from customer_name>

**Bad (invents a consequence / pressures):**

> If we don't get these records this week the whole case falls apart and it will be
> your office's fault, so treat this as urgent.

(Invents a consequence; pressures; not factual.)
