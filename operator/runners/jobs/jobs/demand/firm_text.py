"""Every sentence the demand lane's CODE writes into something the firm reads.

The letter's own prose is the drafter's; the gap audit's rows are the
auditor's. What is left is code's: the attorney-notes lines that say what was
taken out, flagged or repaired, the held-out list under the gap audit, the
records-vendor table's prose, the deadline corrections. They live here, in one
place, for one reason: the pre-delivery review (``review.py``, check 5) holds a
demand on the system's own vocabulary, and three times on 2026-11-06 and 07 it
held one on a sentence code had written itself ("Never read by a model", "not
looked up (over the cap)" a row at a time, and the gap audit's "digest").
``tests/test_demand_firm_text.py`` renders every entry of ``ALL`` and runs it
through the review's own deterministic checks, so a new code-authored sentence
that would hold every demand fails in CI instead of on a client's seat.

Rule for adding one: write it here, add it to ``ALL``, import it where it is
used. A template's fields are filled by ``str.format``; the test fills each
field with an invented, neutral value.
"""

from __future__ import annotations

# ---- the attorney notes: final pass (finalpass.settle) ------------------------------------
NOTE_REMOVED = "{head}: taken out, the file does not support it: {claim} ({why})"
NOTE_FLAGGED = "{head}: figure marked for the attorney to confirm: {claim} ({why})"
NOTE_REMOVED_WITH = "{head}: taken out with the statement before it, the file does not support it: {claim} ({why})"
NOTE_AUTHORITY = (
    "{head}: cites legal authority, which the file is not expected to contain; left as written for the attorney to "
    "confirm the law: {claim}"
)
NOTE_IN_NOTES = (
    "Attorney notes: a note to the attorney names something the file does not state; left as written, it is not "
    "in the letter: {claim}"
)
NOTE_KEPT = (
    "{head}: the file may not support this and it is still in the letter, for the attorney to decide: {claim} ({why})"
)
NOTE_CLEARED = "{head}: rechecked {claim}; the arithmetic is correct and it is left as written"

# ---- the attorney notes: the run's own lines (run._demand_checks, run._build_deliverables) --------
NOTE_PART_SUPPORT = "The file supports this only in part: {item}"
NOTE_SETTLED = "Taken out or flagged before filing: {item}"
NOTE_AUTHORITIES = "Legal authorities cited in the letter, for the attorney to confirm: {items}"
NOTE_UNCHECKED = "Not checked against the matter record: {item}"
NOTE_WALLED = "Held out, never read (privilege wall: {reason}): {name}"
NOTE_QUOTE_LETTER = "Quotation repaired before filing (the letter): {item}"
NOTE_QUOTE_GAP = "Quotation repaired before filing (the gap audit): {item}"
NOTE_QUOTE_WARNING = "Quotation check: {item}"
NOTES_TITLE = "# Attorney notes: {name}"
NOTES_FOR_REVIEW = "## For review"

# ---- the attorney notes: the letter's caption and open items (crosscheck, format_check) -------------
NOTE_NO_COURT = "court and case number: the matter record read for this letter carries neither; they come from the file"
NOTE_CAPTION_MARKER = "caption: left as a marker in the letter"
NOTE_CAPTION_SURNAME = "caption: {caption!r} does not carry the client's surname as the matter record writes it"
NOTE_OPEN_ITEMS = "{count} open item(s) for the attorney: {items}"

# ---- the attorney notes: the deadline (house, house_crabtree) ---------------------------------
NOTE_EXPIRY_WEEKEND = (
    "The demand's expiry fell on a {weekday} ({bare}); it is written as {now}, the next business day. "
    "Public holidays are not checked: move it again if that Monday is one."
)
NOTE_EXPIRY_WEEKDAY = "The expiry's weekday was corrected to {now} (the letter had {was})."
NOTE_DEADLINE_WEEKEND = (
    "The deadline, {days} days after the letter, fell on a weekend; it is written as {due}, "
    "the next business day. Public holidays are not checked: move it again if that day is one."
)
NOTE_DEADLINE_WRITTEN = "The letter's deadline was written as {said}; it is {due}."

# ---- the gap audit: code's sections after the gate (run._wall_section, vendor.section) -----------
QUOTES_REPAIRED_HEAD = "## Quotations repaired before filing"
WALL_HEAD = "## Held out behind the privilege wall: {count} document(s)"
WALL_NONE = "None."
WALL_TABLE_HEAD = "| Document | Why it was held out |"
WALL_LEAD = "Not read for this demand; clear any of them and ask again to include it."
VENDOR_HEAD = "## Records vendor locations for the missing providers"
VENDOR_NOT_CONNECTED = "The records vendor is not connected on this seat, so no locations were looked up."
VENDOR_NONE = "The item table names no provider to look up."
VENDOR_TABLE_HEAD = "| Provider | Vendor location | Location id |"
VENDOR_SEVERAL = "several matches ({count}): {options}"
VENDOR_CHOOSE = "choose one"
VENDOR_BEYOND = (
    "The firm's directory lookups for one demand stop at {looked}; {more} more {providers} in the item table "
    "{were} not looked up: {names}."
)

#: Every entry above, by name, for the vocabulary test. A sentence code writes
#: for the firm and leaves out of this list is a sentence nothing checks.
ALL: dict[str, str] = {
    name: value
    for name, value in dict(globals()).items()
    if name.isupper() and isinstance(value, str) and not name.startswith("_")
}
