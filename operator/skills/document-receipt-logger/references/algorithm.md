# document-receipt-logger algorithm

Source of truth for filing inbound documents by receipt, without reading them.

## The metadata-only rule

The skill touches attachment **metadata** (filename, MIME type, size) and never the document body. It does not extract text, OCR, parse, or summarize the contents. This is both a privilege/scope guard and the line between filing hygiene (this skill) and legal review (a human). The receipt is about provenance - who sent what, when, for which matter - not about what the document says.

## Sender → matter resolution

```
candidates = list_matters( contactId from get_contacts(sender) )
+ any matter reference parsed from the subject line (number)
resolve:
  exactly one confident match  -> proceed
  multiple / weak / none       -> surface for human assignment (do NOT guess)
```

A document filed to the wrong matter is worse than an unfiled one, so the bar is a confident single match. Everything else is a human assignment surface.

## Filing proposal

```
location = matter.document_area
+ category if firm authors a taxonomy (correspondence | signed-docs | records | ...)
```

The category names _where it goes_, not _what it legally is_. "Signed-docs" is a folder, not a determination that the document is an executed, enforceable agreement.

## Receipt entry (the draft)

A Smokeball memo (`create_memo`) containing receipt facts only:

```
Received: <filename> (<type>) on <date> from <sender name / address>
Matter: <number or title>
Filed to: <proposed location>
```

No summary, no terms, no "this appears to be …". If the human wants the document read, that is their work or another skill's, not this one's.

## Fail-closed this phase

Resolution, proposal, and the receipt draft are autonomous. The **file move** (DocumentStorage write) and the **receipt memo commit** (Smokeball `create_memo` write) are gated behind human review in the current phase per `operator/verticals/law-firm/wedge.md` write posture. Nothing is filed or committed without a person confirming the matter and location.

## Never destructive

The skill only _adds_ a received document and a receipt note. It never overwrites, renames, moves, or deletes an existing filed document - a misrouted overwrite could destroy the only copy of a client record.
