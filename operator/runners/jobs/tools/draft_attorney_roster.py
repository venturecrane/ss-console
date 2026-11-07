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

A list entry with no staff member behind it is NOT drafted: it is named in the
review notes for SMD, who decides. The output names people because it runs where
names live; this file carries none.

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


def _entry(row: dict[str, Any], block: list[str] | None, demand: Any, letters: Any) -> dict[str, Any]:
    email = str(row.get("email") or "").strip().lower()
    entry: dict[str, Any] = {"name": full_name(row), "emails": [email] if "@" in email else []}
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


def draft(
    rows: list[dict[str, Any]], drafting: list[dict[str, Any]], demand: dict[str, Any], letters: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """The draft roster and the review notes."""
    by_email = {str(d.get("email") or "").strip().lower(): d.get("block") for d in drafting}
    by_demand = {roster_mod.fold(k): v for k, v in demand.items()}
    attorneys: list[dict[str, Any]] = []
    staff_signers: list[dict[str, Any]] = []
    used: set[str] = set()
    for row in sorted(rows, key=full_name):
        name, email = full_name(row), str(row.get("email") or "").strip().lower()
        key, sid = roster_mod.fold(name), roster_mod.fold(str(row.get("id") or ""))
        letter = letters.get(key) or (letters.get(sid) if sid else None)
        role = " ".join(str(v) for v in role_fields(row).values()).casefold()
        is_attorney = email in by_email or key in by_demand or "attorney" in role
        if not name or not (is_attorney or letter):
            continue
        used |= {email, key, sid}
        if is_attorney:
            attorneys.append(_entry(row, by_email.get(email), by_demand.get(key), letter))
        else:
            staff_signers.append({"name": name, "signs": {"letters": dict(letter or {})}})
    notes = [
        f"drafting attorneys[{i}]: no staff member has this email"
        for i, d in enumerate(drafting)
        if str(d.get("email") or "").strip().lower() not in used
    ]
    notes += [
        f"demand attorneys entry {i + 1}: no staff member has this name"
        for i, k in enumerate(demand)
        if roster_mod.fold(k) not in used
    ]
    notes += [
        f"form letter signers entry {i + 1}: no staff member has this name or id"
        for i, k in enumerate(letters)
        if k not in used
    ]
    notes += [
        f"staff signer {s['name']}: signs form letters and is not on an attorney list; confirm they are not an attorney"
        for s in staff_signers
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
