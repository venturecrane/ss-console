"""The two connector-venv reads statute-watch makes, one subprocess each.

``smokeball_connector`` is importable only from the connector's own venv, so
each read runs a module-constant snippet under that interpreter and answers
with ONE JSON line on its stdout, which this module parses and never prints.
The snippets run SINGLE-THREADED on purpose: the connector's token refresh is
shared state, and one ordered pass is what the 2026-09-30 timing (about twelve
minutes for every open matter's layout) was measured on.

1. ``pull_matters``: the Open and Pending matter lists (leads excluded), paged
   with Offset, then each matter's layouts, reduced IN THE VENV to the four
   facts the selection needs (statute date, whether a Filed date and a Case
   number are present, and the read failing). A matter-list failure answers
   ``listError`` and nothing else, so no partial list can pass for a whole one.
2. ``pull_details``: for the listed cases only, the staff roster (last names by
   staff id), each case's first client contact reduced to a last name, and a
   count of the case's documents whose file names read as court papers.

The pulled values ride stdin and stdout, never argv. A subprocess that fails,
times out or answers something unparseable yields ``None`` here; the callers
turn that into a matter-list failure (no report) or an explicit "not
available" on each line (details).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_CONNECTOR_PYTHON_DEFAULT = "/opt/connectors/smokeball/.venv/bin/python"

#: The scheduler kills a pre_run at one hour. The layout pass was measured at
#: about twelve minutes on 2026-09-30; the budgets leave room for both reads.
_MATTERS_TIMEOUT_SECONDS = 2700
_DETAILS_TIMEOUT_SECONDS = 600

#: File names that read as court papers (the column the first report carried).
COURT_DOC_PATTERN = (
    r"complaint|summons|conformed|proof of serv|\bPOS\b|answer|case management|\bCMC\b"
    r"|civil case cover|notice of (related|appearance)|court"
)

_SHARED = """\
import json
import sys

from smokeball_connector.client import build_client_from_env


def listing(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "value", "results", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return None


def paged(client, path, **params):
    rows, offset = [], 0
    while True:
        page = listing(client.get(path, Limit=500, Offset=offset, **params))
        if page is None:
            raise RuntimeError("unrecognized listing")
        rows.extend(page)
        if len(page) < 500:
            return rows
        offset += 500
        if offset > 50000:
            raise RuntimeError("listing did not end")


client = build_client_from_env()
"""

MATTERS_SNIPPET = (
    _SHARED
    + """\
SOL = "Matter/CaseDetails/StandardCaseDetails/StatuteOfLimitationDate"
CASE_NUMBER = "Matter/CaseDetails/StandardCaseDetails/CaseNumber"
FILED = "Matter/CaseDetails/PleadingsDetails/FiledDate"
out = {"matters": []}
try:
    rows = []
    for status in ("Open", "Pending"):
        rows.extend(paged(client, "/matters", Status=status, IsLead=False))
except Exception as exc:
    print(json.dumps({"listError": type(exc).__name__}))
    raise SystemExit(0)
for row in rows:
    if not isinstance(row, dict) or not row.get("id") or row.get("isLead") is True:
        continue
    rec = {
        "id": str(row["id"]),
        "number": row.get("number"),
        "clientIds": [str(c) for c in (row.get("clientIds") or []) if c],
        "staffId": row.get("personResponsibleStaffId"),
    }
    try:
        designs = listing(client.get("/matters/" + rec["id"] + "/layouts"))
        if designs is None:
            raise RuntimeError("unrecognized layouts")
        values = {}
        for design in designs:
            for item in (design.get("values") or []) if isinstance(design, dict) else []:
                key, value = item.get("key"), item.get("value")
                if key in (SOL, CASE_NUMBER, FILED) and value not in (None, "") and key not in values:
                    values[key] = value
        rec["statute"] = values.get(SOL)
        rec["caseNumber"] = CASE_NUMBER in values
        rec["filed"] = FILED in values
    except Exception:
        rec["layoutError"] = True
    out["matters"].append(rec)
print(json.dumps(out, default=str))
"""
)

DETAILS_SNIPPET = (
    _SHARED
    + """\
import re

COURT = re.compile(@@PATTERN@@, re.IGNORECASE)
wanted = json.loads(sys.stdin.read() or "[]")
out = {"staff": {}, "matters": {}}
try:
    for person in paged(client, "/staff"):
        if isinstance(person, dict) and person.get("id"):
            out["staff"][str(person["id"])] = person.get("lastName")
except Exception:
    out["staffError"] = True
for case in wanted:
    facts = {}
    ids = case.get("clientIds") or []
    if ids:
        try:
            contact = client.get("/contacts/" + str(ids[0])) or {}
            person = contact.get("person") if isinstance(contact.get("person"), dict) else None
            company = contact.get("company") if isinstance(contact.get("company"), dict) else None
            if person is not None:
                facts["clientLastName"] = person.get("lastName")
            elif company is not None:
                facts["clientLastName"] = company.get("name")
            else:
                facts["clientLastName"] = contact.get("lastName")
        except Exception:
            facts["clientError"] = True
    try:
        files = paged(client, "/matters/" + str(case["id"]) + "/documents/files")
        names = [str(f.get("name") or f.get("fileName") or "") for f in files if isinstance(f, dict)]
        facts["courtDocuments"] = sum(1 for n in names if COURT.search(n))
    except Exception:
        facts["documentsError"] = True
    out["matters"][str(case["id"])] = facts
print(json.dumps(out, default=str))
""".replace("@@PATTERN@@", repr(COURT_DOC_PATTERN))
)


def _run(snippet: str, stdin_text: str, timeout: int) -> dict | None:
    """One connector-venv read; the parsed last stdout line, or None."""
    python = os.environ.get("SMD_CONNECTOR_VENV_PYTHON", _CONNECTOR_PYTHON_DEFAULT)
    try:
        result = subprocess.run(  # noqa: S603 - connector-venv interpreter and a module-constant snippet; pulled values ride stdin and stdout, never argv, and there is no shell
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args — argv[0] is the module-constant connector-venv interpreter, overridable only via SMD_CONNECTOR_VENV_PYTHON from the Machine's own boot env (the test seam every pre_run pull carries). argv[2] is a module constant. Matter ids ride stdin; there is no shell.
            [python, "-c", snippet],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - a read that cannot run is reported by type and handled by the caller
        sys.stderr.write("[pre_run] statute-watch read failed: " + type(exc).__name__ + "\n")
        return None
    if result.returncode != 0:
        sys.stderr.write("[pre_run] statute-watch read exit " + str(result.returncode) + "\n")
        return None
    try:
        out = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        sys.stderr.write("[pre_run] statute-watch read answered no JSON line\n")
        return None
    return out if isinstance(out, dict) else None


def pull_matters() -> dict:
    """Every open and pending matter's four statute facts, or ``listError``."""
    out = _run(MATTERS_SNIPPET, "", _MATTERS_TIMEOUT_SECONDS)
    return out if out is not None else {"listError": "read_failed"}


def pull_details(cases) -> dict:
    """Staff last names, client last names and court-named document counts for
    the listed cases. ``{}`` when the read failed: every line then says what is
    not available rather than guessing."""
    wanted = [{"id": c.matter_id, "clientIds": list(c.client_ids)} for c in cases]
    out = _run(DETAILS_SNIPPET, json.dumps(wanted), _DETAILS_TIMEOUT_SECONDS)
    return out if out is not None else {}
