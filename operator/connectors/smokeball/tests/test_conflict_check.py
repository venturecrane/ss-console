"""The conflict check's rule table, against a mock tenant (no live calls).

hit iff the sender or a named party is a contact in an adverse role (other
side, opposing counsel, their carrier) on an OPEN matter that is not the one
the router resolved; clear otherwise; search_failed on any tenant fault. On a
hit the tool writes the one clearance task in the rubric's fixed shape, and a
second call for the same message returns that task, never a second one.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from smokeball_connector import conflict_check as cc
from smokeball_connector.client import SmokeballClient

M_OPEN = "11111111-1111-4111-8111-111111111111"
M_OTHER = "22222222-2222-4222-8222-222222222222"
M_CLOSED = "33333333-3333-4333-8333-333333333333"
STAFF = "99999999-9999-4999-8999-999999999999"

C_DRIVER = "c0000000-0000-4000-8000-000000000001"  # the other side on M_OPEN
C_COUNSEL = "c0000000-0000-4000-8000-000000000002"  # opposing counsel on M_OPEN
C_ADJUSTER = "c0000000-0000-4000-8000-000000000003"  # their carrier on M_OTHER
C_CLIENT = "c0000000-0000-4000-8000-000000000004"  # the firm's client on M_OPEN
C_WITNESS = "c0000000-0000-4000-8000-000000000005"  # a witness relationship


def _person(cid: str, first: str, last: str) -> dict[str, Any]:
    return {"id": cid, "person": {"firstName": first, "lastName": last}}


class Tenant:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.contacts = [
            _person(C_DRIVER, "Dana", "Quill"),
            _person(C_COUNSEL, "Victor", "Hale"),
            _person(C_ADJUSTER, "Rhea", "Stoll"),
            _person(C_CLIENT, "Lena", "Marsh"),
            _person(C_WITNESS, "Owen", "Pike"),
        ]
        self.matters = {
            M_OPEN: {
                "id": M_OPEN,
                "number": "2026-PI-101",
                "status": "Open",
                "clientIds": [C_CLIENT],
                "otherSideIds": [C_DRIVER],
                "personResponsibleStaffId": STAFF,
            },
            M_OTHER: {
                "id": M_OTHER,
                "number": "2026-PI-107",
                "status": "Open",
                "clientIds": [],
                "otherSideIds": ["c-elsewhere"],
                "personResponsibleStaffId": STAFF,
            },
            M_CLOSED: {"id": M_CLOSED, "number": "2019-PI-004", "status": "Closed", "otherSideIds": [C_WITNESS]},
        }
        self.roles = {
            M_OPEN: [
                {"id": "r1", "isClient": True, "contactId": C_CLIENT, "relationships": []},
                {
                    "id": "r2",
                    "isOtherSide": True,
                    "contactId": C_DRIVER,
                    "relationships": [
                        {"id": "x1", "name": "Attorney", "contactId": C_COUNSEL},
                        {"id": "x2", "name": "Witness", "contactId": C_WITNESS},
                    ],
                },
            ],
            M_OTHER: [
                {
                    "id": "r3",
                    "isOtherSide": True,
                    "contactId": "c-elsewhere",
                    "relationships": [{"id": "x3", "name": "Insurance Adjuster", "contactId": C_ADJUSTER}],
                }
            ],
        }
        self.membership = {
            C_DRIVER: [M_OPEN],
            C_COUNSEL: [M_OPEN],
            C_ADJUSTER: [M_OTHER],
            C_CLIENT: [M_OPEN],
            C_WITNESS: [M_OPEN, M_CLOSED],
        }
        self.fail_on = fail_on
        self.posted: list[dict[str, Any]] = []

    def client(self) -> SmokeballClient:
        c = SmokeballClient(region="us", environment="staging", client_id="c", client_secret="s", api_key="k")
        c._http = httpx.Client(transport=httpx.MockTransport(self.handle))
        return c

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if self.fail_on and self.fail_on in path:
            return httpx.Response(503, text="down")
        q = parse_qs(request.url.query.decode())
        parts = path.strip("/").split("/")
        if request.method == "POST" and parts == ["tasks"]:
            self.posted.append(json.loads(request.content))
            return httpx.Response(200, json={"id": f"task-{len(self.posted)}"})
        if request.method == "GET" and parts == ["tasks"]:
            rows = [{"id": f"task-{i + 1}", **p} for i, p in enumerate(self.posted)]
            want = (q.get("MatterId") or [""])[0]
            return httpx.Response(200, json={"value": [r for r in rows if r.get("matterId") == want]})
        if parts[0] == "tasks" and len(parts) == 2:
            index = int(parts[1].split("-")[1]) - 1
            body = self.posted[index]
            return httpx.Response(200, json={"id": parts[1], **body, "matter": {"id": body.get("matterId")}})
        if parts == ["contacts"]:
            term = (q.get("Search") or [""])[0].removeprefix("name:*").removesuffix("*").lower()
            rows = [c for c in self.contacts if term in json.dumps(c["person"]).lower()]
            return httpx.Response(200, json={"value": rows})
        if parts == ["matters"]:
            cid = (q.get("ContactId") or [""])[0]
            rows = [self.matters[m] for m in self.membership.get(cid, [])]
            if (q.get("Status") or [""])[0] == "Open":
                rows = [m for m in rows if m["status"] == "Open"]
            return httpx.Response(200, json={"value": rows})
        if parts[0] == "matters" and len(parts) == 3 and parts[2] == "roles":
            return httpx.Response(200, json={"value": self.roles.get(parts[1], [])})
        if parts[0] == "matters" and len(parts) == 2 and parts[1] in self.matters:
            return httpx.Response(200, json=self.matters[parts[1]])
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    cc._reset_for_tests()
    monkeypatch.setattr(cc, "TODAY", lambda: date(2026, 11, 6))  # a Friday


def _check(tenant: Tenant, **kwargs: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "sender_name": "Lena Marsh",
        "sender_email": "lena@example.com",
        "subject_line": "Question about my case",
        "named_parties": [],
        "resolved_matter_id": None,
    }
    args.update(kwargs)
    return cc.conflict_check(tenant.client(), **args)


# ---- the rule table --------------------------------------------------------


def test_the_firm_s_own_client_writing_in_is_clear() -> None:
    tenant = Tenant()
    out = _check(tenant)
    assert out["verdict"] == "clear" and out["matters"] == [] and out["clearance_task_id"] is None
    assert tenant.posted == []


@pytest.mark.parametrize(
    ("name", "role", "matter"),
    [
        ("Dana Quill", "other_side", M_OPEN),
        ("Victor Hale", "opposing_counsel", M_OPEN),
        ("Rhea Stoll", "their_carrier", M_OTHER),
    ],
)
def test_each_adverse_role_on_an_open_matter_is_a_hit(name: str, role: str, matter: str) -> None:
    out = _check(Tenant(), sender_name=name)
    assert out["verdict"] == "hit"
    assert [(m["matter_id"], m["role"], m["matched"]) for m in out["matters"]] == [(matter, role, "sender")]


def test_a_named_party_on_the_other_side_is_a_hit_for_the_named_party() -> None:
    out = _check(Tenant(), named_parties=["Dana Quill"])
    assert out["verdict"] == "hit"
    assert out["matters"] == [
        {"matter_id": M_OPEN, "matter_number": "2026-PI-101", "role": "other_side", "matched": "named_party"}
    ]


def test_a_witness_and_a_closed_matter_are_not_adverse() -> None:
    assert _check(Tenant(), sender_name="Owen Pike")["verdict"] == "clear"


def test_already_resolved_to_that_matter_is_clear() -> None:
    """The router resolved the message to the matter the sender is adverse on:
    an existing relationship, not a new one."""
    tenant = Tenant()
    out = _check(tenant, sender_name="Victor Hale", resolved_matter_id=M_OPEN)
    assert out["verdict"] == "clear" and tenant.posted == []


def test_resolved_to_a_different_matter_is_still_a_hit() -> None:
    out = _check(Tenant(), sender_name="Victor Hale", resolved_matter_id=M_OTHER)
    assert out["verdict"] == "hit" and out["matters"][0]["matter_id"] == M_OPEN


@pytest.mark.parametrize("fault", ["/contacts", "/roles", "/matters"])
def test_a_tenant_fault_is_search_failed_never_clear(fault: str) -> None:
    tenant = Tenant(fail_on=fault)
    out = _check(tenant, sender_name="Victor Hale")
    assert out["verdict"] == "search_failed" and out["reason"] and tenant.posted == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sender_name": ""},
        {"named_parties": "Dana Quill"},
        {"named_parties": [f"Party {i}" for i in range(cc.MAX_PARTIES + 1)]},
    ],
)
def test_a_request_that_cannot_be_searched_completely_is_search_failed(kwargs: dict[str, Any]) -> None:
    assert _check(Tenant(), **kwargs)["verdict"] == "search_failed"


# ---- the clearance task ---------------------------------------------------


def test_a_hit_writes_one_clearance_task_in_the_rubric_s_fixed_shape() -> None:
    tenant = Tenant()
    out = _check(
        tenant,
        sender_name="Victor Hale",
        sender_email="vhale@example.com",
        subject_line="Re: settlement " + chr(0x2014) + " Quill",
        named_parties=["Some body text the sender wrote"],
    )
    assert out["clearance_task_id"] == "task-1"
    [task] = tenant.posted
    assert task["subject"] == (
        "[Operator] Conflict clearance: inbound from Victor Hale <vhale@example.com> "
        "re Re: settlement - Quill (held, no reply sent)"
    )
    assert task["dueDateOnly"] == "2026-11-09"  # Friday -> the next business day is Monday
    assert task["staffId"] == STAFF and "matterId" not in task  # firm-general: nothing was resolved
    assert "Matter 2026-PI-101: Victor Hale is opposing counsel." in task["note"]
    assert "Some body text" not in json.dumps(task)
    assert all(ord(ch) < 128 for ch in task["subject"] + task["note"])


def test_the_task_goes_on_the_resolved_matter_when_one_was_resolved() -> None:
    tenant = Tenant()
    _check(tenant, sender_name="Victor Hale", resolved_matter_id=M_OTHER)
    assert tenant.posted[0]["matterId"] == M_OTHER


def test_a_second_call_for_the_same_message_returns_the_same_task() -> None:
    tenant = Tenant()
    first = _check(tenant, sender_name="Victor Hale")
    second = _check(tenant, sender_name="Victor Hale")
    assert first["clearance_task_id"] == second["clearance_task_id"] == "task-1"
    assert len(tenant.posted) == 1


def test_after_a_restart_the_task_on_the_matter_is_found_not_duplicated() -> None:
    tenant = Tenant()
    _check(tenant, sender_name="Victor Hale", resolved_matter_id=M_OTHER)
    cc._reset_for_tests()
    again = _check(tenant, sender_name="Victor Hale", resolved_matter_id=M_OTHER)
    assert again["clearance_task_id"] == "task-1" and len(tenant.posted) == 1


def test_a_task_that_cannot_be_written_is_reported_and_the_verdict_stays_hit() -> None:
    tenant = Tenant(fail_on="/tasks")
    out = _check(tenant, sender_name="Victor Hale")
    assert out["verdict"] == "hit" and out["clearance_task_id"] is None and out["clearance_task_error"]


def test_next_business_day_skips_the_weekend() -> None:
    assert cc.next_business_day(date(2026, 11, 4)) == date(2026, 11, 5)
    assert cc.next_business_day(date(2026, 11, 7)) == date(2026, 11, 9)
