"""The (matterNumber, email) join on the ROLES reads (ADR 0086 / ss#2167).

``get_roles_on_matter`` / ``get_relationships_on_matter`` are the reads that
answer "who is on this matter", and ADR 0086 names them the canonical seeding
sources for outbound matter membership. The vendor payload does not carry the
join: a role record names its contact by id, and the matter appears only in the
request path. So the address lives on the contact, the number lives on the
matter, and the record itself carries neither.

What is locked here is the same rule ``parties_complete`` is built on, applied to
this shape: **an unresolved party must never be indistinguishable from a genuine
non-party.** A record that cannot be resolved is left exactly as the vendor
returned it, so the membership register learns nothing from it and the gate
reaches *unresolved* — never "not a party".

No live Smokeball calls: an ``httpx.MockTransport`` serves scripted responses so
the real resolution logic runs.
"""

from __future__ import annotations

import httpx

from smokeball_connector import server as srv
from smokeball_connector.client import SmokeballClient

MATTER_ID = "54bc1371-1111-2222-3333-444444444444"
NUMBER = "PI-2026-0001"


def _mock_client(handler) -> SmokeballClient:
    client = SmokeballClient(region="us", environment="staging", client_id="cid", client_secret="sec", api_key="apikey")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _handler(contacts: dict[str, dict], matter: dict | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        if "/contacts/" in path:
            cid = path.rsplit("/", 1)[-1]
            if cid in contacts:
                return httpx.Response(200, json=contacts[cid])
            return httpx.Response(404, json={"error": "not found"})
        if "/matters/" in path:
            if matter is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json=matter)
        return httpx.Response(200, json={"ok": True})

    return handle


def _person(last: str, email: str | None = None) -> dict:
    person: dict = {"firstName": "Given", "lastName": last}
    if email is not None:
        person["email"] = email
    return {"person": person}


def _matter_record() -> dict:
    return {"id": MATTER_ID, "number": NUMBER}


def _roles_envelope() -> dict:
    """The vendor shape: a role names its contact by id and nothing else."""
    return {
        "value": [
            {"id": "role-1", "name": "Client", "contactId": "c1"},
            {"id": "role-2", "name": "Defendant", "contactId": "d1"},
        ]
    }


# ---- the join itself --------------------------------------------------------


def test_role_records_carry_matter_number_and_party_email() -> None:
    client = _mock_client(
        _handler(
            {"c1": _person("Alvarez", "alvarez@example.com"), "d1": _person("Draper", "d@x.io")},
            _matter_record(),
        )
    )
    resp = _roles_envelope()
    srv._attach_matter_party_join(client, MATTER_ID, resp)

    by_role = {r["id"]: r for r in resp["value"]}
    assert by_role["role-1"]["party_of_matter"] == MATTER_ID
    assert by_role["role-1"]["email"] == "alvarez@example.com"
    assert by_role["role-1"]["matterNumber"] == NUMBER
    assert by_role["role-2"]["email"] == "d@x.io"


def test_addresses_are_lowercased_for_recipient_comparison() -> None:
    client = _mock_client(_handler({"c1": _person("Alvarez", "Alvarez@Example.COM")}, _matter_record()))
    resp = {"value": [{"id": "role-1", "contactId": "c1"}]}
    srv._attach_matter_party_join(client, MATTER_ID, resp)
    assert resp["value"][0]["email"] == "alvarez@example.com"


def test_a_nested_contact_ref_resolves_too() -> None:
    # The relationships sub-resource nests its contact as an object rather than
    # a bare id; both spellings must land the same join.
    client = _mock_client(_handler({"c9": _person("Bell", "b@x.io")}, _matter_record()))
    resp = [{"id": "rel-1", "contact": {"id": "c9", "href": "/contacts/c9"}}]
    srv._attach_matter_party_join(client, MATTER_ID, resp)
    assert resp[0]["party_of_matter"] == MATTER_ID
    assert resp[0]["email"] == "b@x.io"


def test_a_bare_record_is_enriched_without_an_envelope() -> None:
    client = _mock_client(_handler({"c1": _person("Bell", "b@x.io")}, _matter_record()))
    resp = {"id": "role-1", "contactId": "c1"}
    srv._attach_matter_party_join(client, MATTER_ID, resp)
    assert resp["email"] == "b@x.io"


# ---- unresolved must never read as non-membership ---------------------------


def test_failed_contact_lookup_attaches_nothing_to_that_record() -> None:
    # d1 404s. The record must come back exactly as the vendor sent it, so the
    # membership register learns nothing and the gate stays *unresolved*.
    client = _mock_client(_handler({"c1": _person("Alvarez", "a@x.io")}, _matter_record()))
    resp = _roles_envelope()
    srv._attach_matter_party_join(client, MATTER_ID, resp)
    by_role = {r["id"]: r for r in resp["value"]}
    assert "party_of_matter" not in by_role["role-2"]
    assert "email" not in by_role["role-2"]
    # The resolvable sibling is still enriched — one miss does not void the read.
    assert by_role["role-1"]["email"] == "a@x.io"


def test_party_without_an_address_attaches_nothing() -> None:
    client = _mock_client(_handler({"c1": _person("Alvarez")}, _matter_record()))
    resp = {"value": [{"id": "role-1", "contactId": "c1"}]}
    srv._attach_matter_party_join(client, MATTER_ID, resp)
    assert "party_of_matter" not in resp["value"][0]


def test_an_unresolvable_matter_number_still_lands_the_id_join() -> None:
    # The number is a convenience for bodies that cite it; the id join is the
    # membership fact. A missing number must not withhold the fact.
    client = _mock_client(_handler({"c1": _person("Alvarez", "a@x.io")}, None))
    resp = {"value": [{"id": "role-1", "contactId": "c1"}]}
    srv._attach_matter_party_join(client, MATTER_ID, resp)
    record = resp["value"][0]
    assert record["party_of_matter"] == MATTER_ID
    assert "matterNumber" not in record


def test_a_role_id_is_never_resolved_as_a_contact() -> None:
    # A role's own `id` is a ROLE id. Resolving it as a contact could attach a
    # WRONG address to a matter, which is the one output this control exists to
    # prevent.
    client = _mock_client(_handler({"role-1": _person("Wrong", "wrong@x.io")}, _matter_record()))
    resp = {"value": [{"id": "role-1", "name": "Client"}]}
    srv._attach_matter_party_join(client, MATTER_ID, resp)
    assert "email" not in resp["value"][0]


def test_enrichment_never_breaks_the_read() -> None:
    class Boom:
        def get(self, *a, **k):
            raise RuntimeError("vendor down")

    resp = _roles_envelope()
    srv._attach_matter_party_join(Boom(), MATTER_ID, resp)  # must not raise
    assert "party_of_matter" not in resp["value"][0]


def test_no_completeness_flag_is_ever_attached() -> None:
    # Seeding from roles may only ADD proven parties. A completeness flag here
    # would let a paged or role-less party list close a matter and withhold a
    # correct send while naming its recipient an outsider.
    client = _mock_client(
        _handler(
            {"c1": _person("Alvarez", "a@x.io"), "d1": _person("Draper", "d@x.io")},
            _matter_record(),
        )
    )
    resp = _roles_envelope()
    srv._attach_matter_party_join(client, MATTER_ID, resp)
    serialized = repr(resp)
    assert "complete" not in serialized


# ---- the tools carry the enrichment ----------------------------------------


def test_get_roles_on_matter_returns_the_enriched_payload(monkeypatch) -> None:
    client = _mock_client(_handler({"c1": _person("Alvarez", "a@x.io")}, _matter_record()))

    def _roles_response(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        if path.endswith("/roles"):
            return httpx.Response(200, json=_roles_envelope())
        if "/contacts/" in path:
            return httpx.Response(200, json=_person("Alvarez", "a@x.io"))
        if "/matters/" in path:
            return httpx.Response(200, json=_matter_record())
        return httpx.Response(200, json={"ok": True})

    client._http = httpx.Client(transport=httpx.MockTransport(_roles_response))
    monkeypatch.setattr(srv, "_get_client", lambda: client)

    out = srv.get_roles_on_matter(MATTER_ID)
    assert out["value"][0]["party_of_matter"] == MATTER_ID
    assert out["value"][0]["matterNumber"] == NUMBER
