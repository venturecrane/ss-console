"""The connector-venv read records-order-chase does: one subprocess, one JSON line back.

``smokeball_connector`` (and its records-vendor client) is importable only from
the connector's own venv, and the vendor's token is the connector's, staged on
the Machine per customer (``bin/lib/stage-smokeball.sh``). So the read runs
:data:`SNIPPET` under that interpreter, with the matter ids on stdin, and
answers ONE JSON line on stdout, which this module parses and never prints.

Per matter it reads:

* from Smokeball, the matter's number and its first client contact's name, as
  Smokeball holds them (``GET /matters/{id}``, ``GET /contacts/{id}``): the
  first and last name of a person, or a company's name. Nothing else of the
  contact leaves the venv process.
* from the vendor, ``orders_for_matter`` (``GET /api/v1/orders/external:{id}``):
  each location's id, request number, facility name and status, scrubbed by the
  connector's own ``scrub``.

A read that fails is answered as a failure for THAT matter (``vendorError`` with
the HTTP status, ``matterError``), never as an empty list, so the report can
say the matter could not be read. A vendor 404 is a failure here too: these are
matters the Operator placed an order on, so "the vendor holds nothing" is
something a person needs to see, not a quiet week. No token on the seat answers
``notConnected`` and nothing else.

The values ride stdin and stdout, never argv. A subprocess that fails, times
out or answers something unparseable yields ``None``; the caller sends nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_CONNECTOR_PYTHON_DEFAULT = "/opt/connectors/smokeball/.venv/bin/python"

#: The scheduler kills a pre_run at one hour (hermes cron/scheduler.py
#: _DEFAULT_SCRIPT_TIMEOUT = 3600). The vendor's directory search was measured
#: at about 23 s from a seat (records_vendor.py); a matter read is lighter, and
#: the caller caps how many matters one run reads.
_READ_TIMEOUT_SECONDS = 2400

SNIPPET = """\
import json
import sys

from smokeball_connector.client import build_client_from_env
from smokeball_connector.records_vendor import RecordsVendorApiError, client_from_env, scrub, vendor_name

request = json.loads(sys.stdin.read() or "{}")
yc = client_from_env()
if yc is None:
    print(json.dumps({"notConnected": True}))
    raise SystemExit(0)
out = {"vendorName": vendor_name(), "matters": {}}
try:
    client = build_client_from_env()
except Exception as exc:
    client = None
    out["smokeballError"] = type(exc).__name__


def client_name(contact):
    person = contact.get("person") if isinstance(contact.get("person"), dict) else None
    company = contact.get("company") if isinstance(contact.get("company"), dict) else None
    if person is not None:
        parts = [str(person.get(k) or "").strip() for k in ("firstName", "lastName")]
        return " ".join(p for p in parts if p) or None
    if company is not None:
        return str(company.get("name") or "").strip() or None
    return None


for matter_id in request.get("matters") or []:
    mid = str(matter_id)
    facts = {}
    if client is None:
        facts["matterError"] = True
    else:
        try:
            matter = client.get("/matters/" + mid)
            if not isinstance(matter, dict) or not matter:
                raise RuntimeError("unrecognized matter")
            facts["number"] = str(matter.get("number") or "").strip() or None
            ids = [c for c in (matter.get("clientIds") or []) if isinstance(c, str) and c.strip()]
            if ids:
                contact = client.get("/contacts/" + ids[0])
                facts["client"] = client_name(contact) if isinstance(contact, dict) else None
        except Exception:
            facts["matterError"] = True
    try:
        found = yc.orders_for_matter(mid)
        rows = found.get("locations") if isinstance(found, dict) else None
        if not isinstance(rows, list):
            facts["vendorError"] = "no_locations"
        else:
            facts["locations"] = [
                {k: scrub(r.get(k)) for k in ("id", "request_id", "name", "status")}
                for r in rows
                if isinstance(r, dict)
            ]
    except RecordsVendorApiError as exc:
        facts["vendorError"] = "http_" + str(exc.status)
    except Exception as exc:
        facts["vendorError"] = type(exc).__name__
    out["matters"][mid] = facts
print(json.dumps(out, default=str))
"""


def read_matters(matter_ids: list[str]) -> dict | None:
    """The per-matter read, or None when the step could not run or answered nothing."""
    python = os.environ.get("SMD_CONNECTOR_VENV_PYTHON", _CONNECTOR_PYTHON_DEFAULT)
    try:
        result = subprocess.run(  # noqa: S603 - connector-venv interpreter and a module-constant snippet; matter ids ride stdin, results ride stdout, never argv, and there is no shell
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args — argv[0] is the module-constant connector-venv interpreter, overridable only via SMD_CONNECTOR_VENV_PYTHON from the Machine's own boot env (the test seam every pre_run pull carries). argv[2] is a module constant. Matter ids ride stdin; there is no shell.
            [python, "-c", SNIPPET],
            input=json.dumps({"matters": list(matter_ids)}),
            capture_output=True,
            text=True,
            timeout=_READ_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - a step that cannot run is reported by type and handled by the caller
        sys.stderr.write("[pre_run] records-order-chase read failed: " + type(exc).__name__ + "\n")
        return None
    if result.returncode != 0:
        sys.stderr.write("[pre_run] records-order-chase read exit " + str(result.returncode) + "\n")
        return None
    try:
        out = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        sys.stderr.write("[pre_run] records-order-chase read answered no JSON line\n")
        return None
    return out if isinstance(out, dict) else None
