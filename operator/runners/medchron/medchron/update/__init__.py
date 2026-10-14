"""UPDATE INTO THE DELIVERED CHRONOLOGY (2026-10-13).

A Named Administrator asks for a chronology on a matter that already holds a
delivered one. The firm holds that document; it wants it complete, not a
second, partial package beside it. So the update produces ONE document:

* every delivered entry carried byte-identical, in its delivered order;
* the visits recorded by documents the delivery did not cover, added in date
  order before AND after the incident;
* the delivered Prior Medical History carried verbatim, with the prior
  history the added pre-incident entries hold in a dated paragraph under it,
  audited as new claims;
* the Treatment Timeline and the gaps recomputed from the whole entry set,
  and a Diagnostic Highlights row for each new code whose audited claim the
  audit kept;
* the new records as exhibits numbered after every exhibit the folder holds
  (the firm's own numbered files included) and titled the delivered way
  ("<Provider> - MM-DD-YYYY - MM-DD-YYYY (Medical Records)"); a new record
  identical to a firm-numbered file takes that file's number;
* one factual Records Reviewed paragraph about the update.

Only the NEW claims are audited (a delivered claim was audited when it was
delivered and is not in the audit document at all), and before anything is
filed the proofs (``proofs.py``) must all hold, including proof 10: no value
an added entry composed from a new record is lost on the way to the document.

What the delivery covered is read off the delivered document itself, never
the ledger alone: a new document byte-identical to a delivered exhibit is
skipped, and a page whose words are already in a delivered exhibit (80% of its
digit-stripped 8-word shingles) is not read again (``select.py``). A coverage
record written by an older pipeline may be stale or missing; the delivered
exhibits are not.

The modules here are pure (text in, text out); the stages that run them are
``stages/update_*.py`` and the order is ``dag.UPDATE_STAGES``. The behaviour
is ported from the laptop pipeline's selected-page re-read, where it was
proven on a real matter first; nothing of that matter is in this tree.
"""
