"""Draft a firm's attorney roster from its case system's staff list: READ ONLY.

Run it where the case system's client can be built (the firm's ops workspace,
in the jobs runner's venv). It reads, and writes nothing anywhere but stdout
(or ``--out``):

1. every staff member of the firm's case system (``GET /staff``, paged): name,
   email, and every field of the record whose name says role, title, type,
   status, former or active;
2. the three attorney lists the seat reads today: the drafting inputs'
   ``attorneys`` (caption blocks, by email), the demand inputs' ``attorneys``
   (signatures, by name) and customer.yaml's ``form_letters.signers`` (form
   letter signatures, by name or staff id);
3. a DRAFT ``attorneys.json`` (``operator_connector_sdk/library/roster.py``),
   checked against the roster's own validator, for SMD to review before it is
   committed to the private engagements repo at
   ``operator/customers/<slug>/attorneys/attorneys.json``.

HOW THE DRAFT IS BUILT, in code, nothing guessed by a model:

* only ACTIVE staff are drafted: a former or disabled record is left out, and a
  former record that shares an active one's name is a REVIEW line by its
  position on the staff page; active rows that share one name are one entry
  with every email (a person is never on the roster twice);
* an ATTORNEY is a staff member the drafting or the demand list names (both are
  attorney-only lists), or whose record's role or title field contains the word
  "attorney"; a staff member only the form letters' signers name is a STAFF
  SIGNER, listed for review, because that list holds whoever signs letters;
* the entry's name is the staff record's first and last name, the identity the
  matter's responsible-attorney field resolves to; its emails, the record's;
* ``caption_block`` is the drafting list's block for that email, and
  ``bar_number`` the one "SBN <digits>" the block prints, when it prints one;
* ``signs.demand`` is the demand list's row (signer, title, initials) and
  ``signs.letters`` the form letters' row, each copied as authored.

A list entry with no active staff member behind it is NOT drafted: it is a
review note for SMD, who decides. Review notes give positions, never names; the
staff report and the draft itself name people because they run where names
live and stay there. This file carries none.

USAGE (from the repo root, in the jobs runner's venv, on the ops workspace):

    python operator/runners/jobs/tools/draft_attorney_roster.py \\
        [--drafting <drafting inputs dir>] [--demand <demand inputs dir>] \\
        [--customer-yaml <customer.yaml>] [--out <draft attorneys.json>]

Defaults are the seat's paths (``/var/lib/smd-config/...``). The report goes to
stderr, the draft to stdout. Exit 0 when the draft was built, 2 when the staff
list could not be read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from operator_connector_sdk.library import roster as roster_mod

PAGE = 500
_ROLEISH = re.compile(r"(?i)role|title|type|status|former|active|enabled")
_SBN = re.compile(r"\bSBN\s*(\d{3,})\b")


class ReadOnly:
    """The case system's client with every write refused."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, *args: Any, **kwargs: Any) -> Any:
        return self._client.get(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        raise PermissionError(f"the roster draft reads only; {name} is refused")


def staff(client: Any) -> list[dict[str, Any]]:
    """Every staff record, paged until a short page."""
    out: list[dict[str, Any]] = []
    while True:
        resp = client.get("/staff", Limit=PAGE, Offset=len(out))
        rows = resp.get("value") if isinstance(resp, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("the staff listing did not answer a list")
        out += [r for r in rows if isinstance(r, dict)]
        if len(rows) < PAGE:
            return out


def full_name(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(k) or "").strip() for k in ("firstName", "lastName")).strip()


def role_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if _ROLEISH.search(k) and isinstance(v, (str, int, float, bool))}


def legacy(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], list[str]]:
    """(drafting attorneys, demand attorneys, form letter signers, what could not load)."""
    from jobs.demand import firm as demand_firm
    from jobs.drafting import firm as drafting_firm
    from smokeball_connector.form_letter_facts import authored_signers

    notes: list[str] = []
    drafting: list[dict[str, Any]] = []
    demand: dict[str, Any] = {}
    try:
        drafting = list(drafting_firm.load(args.drafting).attorneys)
    except Exception as exc:  # noqa: BLE001 - a list that does not load is reported, and the draft goes on without it
        notes.append(f"drafting inputs not read ({type(exc).__name__})")
    try:
        demand = dict(demand_firm.load(args.demand).data["attorneys"])
    except Exception as exc:  # noqa: BLE001 - demand inputs that do not load are reported, and the draft goes on without them
        notes.append(f"demand inputs not read ({type(exc).__name__})")
    return drafting, demand, authored_signers(args.customer_yaml), notes


def is_active(row: dict[str, Any]) -> bool:
    """False for a former or disabled staff record: any field whose name says
    former that is true, or an enabled or active field that is false."""
    for k, v in row.items():
        key = k.casefold()
        if "former" in key and v is True:
            return False
        if key.removeprefix("is") in ("enabled", "active") and v is False:
            return False
    return True


def people(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """The ACTIVE staff, one person per name (same-name active rows folded into
    one: every email and id kept), and review notes by staff-page position
    (1-based), never by name: a former record that shares an active name, and
    active rows folded together."""
    by_name: dict[str, dict[str, Any]] = {}
    rows_of: dict[str, list[int]] = {}
    for pos, row in enumerate(rows, 1):
        name = full_name(row)
        if not name or not is_active(row):
            continue
        key = roster_mod.fold(name)
        rows_of.setdefault(key, []).append(pos)
        person = by_name.setdefault(key, {"name": name, "emails": [], "ids": [], "role": ""})
        email = str(row.get("email") or "").strip().lower()
        if "@" in email and email not in person["emails"]:
            person["emails"].append(email)
        person["ids"].append(roster_mod.fold(str(row.get("id") or "")))
        person["role"] += " " + " ".join(str(v) for v in role_fields(row).values()).casefold()
    notes = [
        f"staff rows {', '.join(map(str, at))} share one name and are drafted as one entry"
        for at in rows_of.values()
        if len(at) > 1
    ]
    notes += [
        f"staff row {pos} is a former record that shares its name with an active one; the draft uses the active one"
        for pos, row in enumerate(rows, 1)
        if full_name(row) and not is_active(row) and roster_mod.fold(full_name(row)) in by_name
    ]
    return sorted(by_name.values(), key=lambda p: p["name"]), notes


def _entry(person: dict[str, Any], block: list[str] | None, demand: Any, letters: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": person["name"], "emails": list(person["emails"])}
    bars = sorted(set(_SBN.findall(" ".join(block or []))))
    if len(bars) == 1:
        entry["bar_number"] = bars[0]
    if block:
        entry["caption_block"] = list(block)
    signs: dict[str, Any] = {}
    if isinstance(demand, dict):
        signs["demand"] = {
            k: str(demand.get(k) if demand.get(k) is not None else "") for k in ("signer", "title", "initials")
        }
    if isinstance(letters, dict) and letters:
        signs["letters"] = dict(letters)
    if signs:
        entry["signs"] = signs
    return entry


def _unmatched(
    drafting: list[dict[str, Any]], demand: dict[str, Any], letters: dict[str, Any], used: set[str]
) -> list[str]:
    notes = [
        f"drafting attorneys[{i}]: no active staff member has this email"
        for i, d in enumerate(drafting)
        if str(d.get("email") or "").strip().lower() not in used
    ]
    notes += [
        f"demand attorneys entry {i + 1}: no active staff member has this name"
        for i, k in enumerate(demand)
        if roster_mod.fold(k) not in used
    ]
    return notes + [
        f"form letter signers entry {i + 1}: no active staff member has this name or id"
        for i, k in enumerate(letters)
        if k not in used
    ]


def draft(
    rows: list[dict[str, Any]], drafting: list[dict[str, Any]], demand: dict[str, Any], letters: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """The draft roster and the review notes. Only ACTIVE staff are drafted."""
    by_email = {str(d.get("email") or "").strip().lower(): d.get("block") for d in drafting}
    by_demand = {roster_mod.fold(k): v for k, v in demand.items()}
    attorneys: list[dict[str, Any]] = []
    staff_signers: list[dict[str, Any]] = []
    used: set[str] = set()
    active, notes = people(rows)
    for person in active:
        key, ids = roster_mod.fold(person["name"]), [i for i in person["ids"] if i]
        letter = letters.get(key) or next((letters[i] for i in ids if i in letters), None)
        block = next((by_email[e] for e in person["emails"] if e in by_email), None)
        is_attorney = any(e in by_email for e in person["emails"]) or key in by_demand or "attorney" in person["role"]
        if not (is_attorney or letter):
            continue
        used |= {*person["emails"], key, *ids}
        if is_attorney:
            attorneys.append(_entry(person, block, by_demand.get(key), letter))
        else:
            staff_signers.append({"name": person["name"], "signs": {"letters": dict(letter or {})}})
    notes += _unmatched(drafting, demand, letters, used)
    notes += [
        f"staff_signers[{i}]: signs form letters and is not on an attorney list; confirm they are not an attorney"
        for i in range(len(staff_signers))
    ]
    doc: dict[str, Any] = {"schema_version": roster_mod.SCHEMA_VERSION, "attorneys": attorneys}
    if staff_signers:
        doc["staff_signers"] = staff_signers
    return doc, notes


def report(rows: list[dict[str, Any]], out: Any) -> None:
    print(f"case system staff: {len(rows)}", file=out)
    for row in sorted(rows, key=full_name):
        print(
            f"  {full_name(row) or '(no name)'} | {row.get('email') or '(no email)'} | {json.dumps(role_fields(row), sort_keys=True)}",
            file=out,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Draft a firm's attorney roster from its case system's staff list (read only)."
    )
    parser.add_argument("--drafting", default=None)
    parser.add_argument("--demand", default=None)
    parser.add_argument("--customer-yaml", dest="customer_yaml", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    from smokeball_connector.client import build_client_from_env

    try:
        rows = staff(ReadOnly(build_client_from_env()))
    except Exception as exc:  # noqa: BLE001 - a staff list we could not read is "could not look", never "nobody"
        print(f"the case system's staff list could not be read ({type(exc).__name__})", file=sys.stderr)
        return 2
    drafting, demand, letters, notes = legacy(args)
    report(rows, sys.stderr)
    print(
        f"drafting attorneys: {len(drafting)}, demand attorneys: {len(demand)}, form letter signers: {len(letters)}",
        file=sys.stderr,
    )
    doc, review = draft(rows, drafting, demand, letters)
    problems = roster_mod.problems(doc)
    for line in notes + review:
        print(f"REVIEW: {line}", file=sys.stderr)
    print(f"the draft {'validates' if not problems else 'does NOT validate: ' + '; '.join(problems)}", file=sys.stderr)
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
