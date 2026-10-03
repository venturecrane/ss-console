#!/usr/bin/env python3
"""Seed the Clio developer sandbox with the demo law firm's book of business.

This is ONE-TIME setup for the tangible law demo (the email-in Operator that
runs `new-matter-intake` against a real Clio dev tenant). It is NOT the demo
Operator and NOT part of the demo loop -- the Clio MCP has no `create_contact`
tool, so the seed runs against Clio's REST API directly with an OAuth access
token.

It plants ONE conflict: **Greg Whitfield** as an EXISTING CLIENT of the firm
(his own open estate matter). When a prospect later emails an intake naming Greg
as the party they want to act against, `new-matter-intake`'s read-only conflict
check (`search_contacts(Greg)` + matter cross-check) HITS an existing client and
produces a CONFLICT-HOLD -- the demo's money shot, on real (sandbox) Clio data.

Idempotent: every record is matched by name first and skipped if present, so the
script is safe to re-run. Clio assigns its own contact/matter IDs on creation;
the demo never depends on specific IDs (the conflict check matches by NAME).

Auth: reads CLIO_ACCESS_TOKEN from the environment -- it is NEVER printed. Get a
token via the refresh flow or a one-time consent; see operator/bin/clio-token.py
or the demo runbook. Region base is CLIO_API_BASE (default US app.clio.com).

Usage:
    CLIO_ACCESS_TOKEN=<token> python operator/bin/seed-clio-demo.py
    CLIO_ACCESS_TOKEN=<token> python operator/bin/seed-clio-demo.py --dry-run
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = os.environ.get("CLIO_API_BASE", "https://app.clio.com/api/v4").rstrip("/")
DRY_RUN = "--dry-run" in sys.argv[1:]


# --- The demo firm: a small estate + immigration practice ------------------
# Each contact is a Person who is an existing CLIENT of the firm, with one open
# matter. Greg Whitfield is the PLANTED CONFLICT.
SEED = [
    {
        "first_name": "Greg",
        "last_name": "Whitfield",
        "email": "greg.whitfield@example.com",
        "matter": "Estate Plan - Whitfield Family Trust",
        "_note": "PLANTED CONFLICT: existing client; a prospect naming Greg as an adverse party must trigger CONFLICT-HOLD",
    },
    {
        "first_name": "Maria",
        "last_name": "Delgado",
        "email": "maria.delgado@example.com",
        "matter": "Naturalization - Delgado",
    },
    {
        "first_name": "Robert",
        "last_name": "Kline",
        "email": "robert.kline@example.com",
        "matter": "Estate Plan - Kline",
    },
]


class ClioError(RuntimeError):
    pass


def _token() -> str:
    tok = os.environ.get("CLIO_ACCESS_TOKEN", "").strip()
    if not tok:
        raise ClioError(
            "CLIO_ACCESS_TOKEN is not set. Source it from the secret tooling; "
            "do not paste a token on the command line in a shared transcript."
        )
    return tok


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {_token()}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        if exc.code in (401, 403):
            raise ClioError(
                f"{exc.code} from Clio -- the access token is missing, expired, "
                f"or lacks scope. Refresh it and retry. ({detail})"
            ) from exc
        raise ClioError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ClioError(f"{method} {path} -> network error: {exc.reason}") from exc


def find_contact(full_name: str) -> int | None:
    q = urllib.parse.urlencode({"query": full_name, "fields": "id,name"})
    res = _request("GET", f"/contacts.json?{q}")
    for row in res.get("data", []):
        if (row.get("name") or "").strip().lower() == full_name.strip().lower():
            return int(row["id"])
    return None


def ensure_contact(first: str, last: str, email: str) -> tuple[int, bool]:
    full = f"{first} {last}"
    existing = find_contact(full)
    if existing is not None:
        return existing, False
    if DRY_RUN:
        return -1, True
    body = {
        "data": {
            "type": "Person",
            "first_name": first,
            "last_name": last,
            "email_addresses": [
                {"name": "Work", "address": email, "default_email": True}
            ],
        }
    }
    res = _request("POST", "/contacts.json?fields=id,name", body)
    return int(res["data"]["id"]), True


def find_matter(client_id: int, description: str) -> int | None:
    q = urllib.parse.urlencode(
        {"client_id": client_id, "fields": "id,description,status"}
    )
    res = _request("GET", f"/matters.json?{q}")
    for row in res.get("data", []):
        if (row.get("description") or "").strip().lower() == description.strip().lower():
            return int(row["id"])
    return None


def ensure_matter(client_id: int, description: str) -> tuple[int, bool]:
    if client_id != -1:
        existing = find_matter(client_id, description)
        if existing is not None:
            return existing, False
    if DRY_RUN:
        return -1, True
    body = {
        "data": {
            "client": {"id": client_id},
            "description": description,
            "status": "Open",
        }
    }
    res = _request("POST", "/matters.json?fields=id,description,status", body)
    return int(res["data"]["id"]), True


def main() -> int:
    mode = "DRY-RUN (no writes)" if DRY_RUN else "LIVE"
    print(f"Seeding Clio dev tenant at {API_BASE}  [{mode}]\n")
    # Fail fast on auth before any writes.
    try:
        _token()
        whoami = _request("GET", "/users/who_am_i.json?fields=id,name")
        who = whoami.get("data", {})
        print(f"Authenticated as: {who.get('name', '?')} (user {who.get('id', '?')})\n")
    except ClioError as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return 2

    created = 0
    for rec in SEED:
        full = f"{rec['first_name']} {rec['last_name']}"
        try:
            cid, c_new = ensure_contact(rec["first_name"], rec["last_name"], rec["email"])
            mid, m_new = ensure_matter(cid, rec["matter"])
        except ClioError as exc:
            print(f"  ! {full}: {exc}", file=sys.stderr)
            return 1
        tag = "PLANTED CONFLICT" if "_note" in rec else "neutral"
        c_state = "created" if c_new else "exists"
        m_state = "created" if m_new else "exists"
        cid_s = "(dry)" if cid == -1 else f"#{cid}"
        mid_s = "(dry)" if mid == -1 else f"#{mid}"
        print(f"  - {full:20s} contact {cid_s:>8} [{c_state}]  matter {mid_s:>8} [{m_state}]  <{tag}>")
        created += int(c_new) + int(m_new)

    print(f"\nDone. {created} record(s) {'would be ' if DRY_RUN else ''}created; rest already present.")
    if not DRY_RUN:
        print("Verify the conflict will fire: search_contacts('Greg Whitfield') should return an existing client.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ClioError as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        raise SystemExit(2)
