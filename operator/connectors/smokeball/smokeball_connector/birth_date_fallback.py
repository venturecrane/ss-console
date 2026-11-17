"""A bill whose printed name is not the firm's record still finds its matter,
on the date of birth and the date of loss, never on a name.

THE DEFECT (a client firm's post, 2026-11-17). Three of thirteen scanned claim
forms came back ``none``: each printed the patient's surname differently from
the firm's contact (a misspelling, or another surname altogether), so the name
search generated no candidate and the date of birth the form printed never got
a chance. The firm's contact directory, read that day, matched all three on the
same first name, date of birth and date of loss. Smokeball's contact search
refuses a birth-date term (HTTP 400), so candidates cannot come from the birth
date directly; they come from the printed FIRST name, filtered here on the
recorded birth date.

WHEN IT RUNS. Only after the ordinary resolution answered ``none``, only for the
post's own calls (``fallback_ok``: the tool passes it with the bundle's pages),
only with no matter number, a date of birth and ONE clean ISO date of loss, and
only when no contact the full name matched could be the person: the name found
nobody, or everybody it found records a different birth date. A name-matched
contact with NO recorded birth date (most of a firm's contacts) blocks it, so a
bill that resolves today resolves exactly as it did.

WHAT COUNTS. A contact whose recorded first given name equals the printed one
(and whose second given name, when both sides carry one, agrees) and whose
recorded birth date equals the printed one. Its matters are the candidates, and
the date of loss must then be read back from a matter's layout or text:
``unique`` here requires ``date_of_loss`` in ``matched_on``, so a birth date
alone, or a birth date with a claim number, is ``none``. A row with the same
first given name and the same recorded SURNAME as a hit but no recorded birth
date is a twin or a duplicate record: its matters join the survivors and the
answer becomes ``ambiguous`` rather than a guess. A ``none`` from here names no
candidate, so nobody is asked "is it X?" on a birth date alone.

The verdict says so: a ``unique`` from here carries ``name_on_record_differs``
and ``NAME_DIFFERS`` as its ``reason``, which the outcome
ledger keeps and the daily list turns into a line for a person to check.
"""

from __future__ import annotations

import unicodedata
from typing import Any

#: The reason a ``unique`` verdict from here carries; ``post_outcomes`` keys on it.
NAME_DIFFERS = "the printed name differs from the firm's record; matched on date of birth and date of loss"

#: Leading words that are not a given name.
_TITLES = frozenset({"mr", "mrs", "ms", "miss", "dr", "estate", "the"})
_MIN_GIVEN = 3
#: Rows per page and pages read before a first name is too common to search to
#: the end. Measured on a client tenant 2026-11-17: the five commonest first
#: names return 174 to 289 rows; the endpoint accepts Limit up to 500 and pages
#: by Offset. So 1,000 rows (3.5 times the worst) in one call for almost every
#: name, two at most.
PAGE_ROWS = 500
MAX_PAGES = 2


class TooCommon(Exception):
    """The first name filled every page this search may read."""


def fold(text: str) -> str:
    """Accents to their base letters, so "María" matches "Maria". Used on this
    path only: the ordinary name search keeps the printed form unchanged."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _given(mr: Any, name: str) -> list[str]:
    """The printed given names, lowercased: every token but the last."""
    tokens = [t.lower() for t in mr._name_tokens(fold(name))]
    return tokens[:-1] if len(tokens) >= 2 else []


def _record_given(mr: Any, contact: dict[str, Any]) -> list[str]:
    person = contact.get("person") if isinstance(contact.get("person"), dict) else {}
    tokens: list[str] = []
    for part in (person.get("firstName"), person.get("middleName")):
        tokens += [t.lower() for t in mr._name_tokens(fold(part) if isinstance(part, str) else part)]
    return tokens


def _surname(mr: Any, contact: dict[str, Any]) -> str:
    person = contact.get("person") if isinstance(contact.get("person"), dict) else {}
    last = person.get("lastName")
    return " ".join(t.lower() for t in mr._name_tokens(fold(last) if isinstance(last, str) else last))


def _same_given(mr: Any, printed: list[str], record: list[str]) -> bool:
    if not record or record[0] != printed[0]:
        return False
    if len(printed) > 1 and len(record) > 1:
        return mr._agrees(printed[1], record[1])
    return True


def _rows(mr: Any, client: Any, first: str) -> list[dict[str, Any]]:
    """Every contact the first name's search returns, paged to the end."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(MAX_PAGES):
        batch = mr._get(client, "/contacts", Search=[f"name:*{first}*"], Limit=PAGE_ROWS, Offset=page * PAGE_ROWS)
        ids = [c.get("id") for c in batch if isinstance(c.get("id"), str)]
        if ids and all(i in seen for i in ids):
            # The server ignored Offset. A repeated SHORT page was the whole list;
            # a repeated FULL one hides the rest, which is never "read to the end".
            if len(batch) >= PAGE_ROWS:
                raise TooCommon(first)
            return rows
        seen.update(ids)
        rows += batch
        if len(batch) < PAGE_ROWS:
            return rows
    raise TooCommon(first)


def contacts(mr: Any, client: Any, name: str, birth: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(contacts that match first given name and birth date, possible twins)."""
    printed = _given(mr, name)
    if not printed or len(printed[0]) < _MIN_GIVEN or printed[0] in _TITLES:
        return [], []
    rows = [c for c in _rows(mr, client, printed[0]) if isinstance(c.get("id"), str) and c["id"]]
    named = [c for c in rows if isinstance(c.get("person"), dict) and _same_given(mr, printed, _record_given(mr, c))]
    hits = [c for c in named if mr._birth_date(c) == birth]
    surnames = {_surname(mr, c) for c in hits}
    hit_ids = {c["id"] for c in hits}
    twins = [c for c in named if c["id"] not in hit_ids and not mr._birth_date(c) and _surname(mr, c) in surnames]
    return hits, twins


def eligible(result: dict[str, Any], values: dict[str, str], parties: dict[str, list[dict[str, Any]]], mr: Any) -> bool:
    """The ordinary answer was ``none`` and nobody the name found can be this person."""
    if result.get("verdict") != mr.VERDICT_NONE or values[mr.FACT_NUMBER] or not values[mr.FACT_BIRTH]:
        return False
    if "date_of_loss_read_as" in result or not mr._ISO_DATE.match(values[mr.FACT_LOSS] or ""):
        return False  # one clean date of loss, never one read out of a garbled print
    found = [c for group in parties.values() for c in group]
    return all(mr._birth_date(c) and mr._birth_date(c) != values[mr.FACT_BIRTH] for c in found)


def resolve(mr: Any, client: Any, base: dict[str, Any], supplied: list[str], values: dict[str, str]) -> dict[str, Any]:
    """The fallback's own verdict. Raises ``mr.SearchFailed`` on a failed read."""
    flagged = dict(base)
    try:
        hits, twins = contacts(mr, client, values[mr.FACT_NAME], values[mr.FACT_BIRTH])
    except TooCommon:
        return {**flagged, "verdict": mr.VERDICT_NONE, "reason": "the first name is too common to search to the end"}
    if not hits:
        return {**flagged, "verdict": mr.VERDICT_NONE, "reason": "no contact has that first name and date of birth"}
    records: dict[str, dict[str, Any]] = {}
    parties: dict[str, list[dict[str, Any]]] = {}
    born = _matters(mr, client, hits, records, parties)
    alike = _matters(mr, client, twins, records, parties)
    survivors = born | alike
    narrowing = [f for f in (mr.FACT_CLAIM, mr.FACT_LOSS, mr.FACT_CASE) if values[f]]
    layouts: dict[str, dict[str, list[str]]] = {}
    if 0 < len(survivors) <= mr.MAX_LAYOUT_SURVIVORS:
        layouts = {mid: mr._layout_facts(client, mid) for mid in sorted(survivors)}
    survivors = mr._narrow(survivors, narrowing, records, parties, values, layouts)
    facts = [f for f in supplied if f != mr.FACT_NAME]
    generated = {mr.FACT_BIRTH: born}
    if len(survivors) == 1:
        # Decided BEFORE _verdict, which mints the write's token for a unique
        # answer: a token minted and then disowned would still open the write.
        # BOTH facts, read back for THIS matter: a twin's matter carries no
        # birth date, so a loss date and a claim number alone never open it.
        (only,) = survivors
        matched = mr._matched_on(only, facts, generated, records, parties, values, layouts)
        if mr.FACT_LOSS not in matched or mr.FACT_BIRTH not in matched:
            return {
                **flagged,
                "verdict": mr.VERDICT_NONE,
                "reason": "not matched on both date of birth and date of loss",
            }
    out = mr._verdict(sorted(survivors), flagged, facts, generated, records, parties, values, layouts)
    if out.get("verdict") == mr.VERDICT_UNIQUE:
        out["reason"], out["name_on_record_differs"] = NAME_DIFFERS, True
    if out.get("verdict") == mr.VERDICT_NONE:
        out.pop("candidates", None)
    return out


def _matters(
    mr: Any,
    client: Any,
    people: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    parties: dict[str, list[dict[str, Any]]],
) -> set[str]:
    found: set[str] = set()
    for contact in people:
        for matter in mr._get(client, "/matters", ContactId=contact["id"], Limit=mr._SEARCH_LIMIT):
            mid = mr._matter_id(matter)
            if mid:
                records.setdefault(mid, matter)
                found.add(mid)
                parties.setdefault(mid, []).append(contact)
    return found
