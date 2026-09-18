"""Matter resolution: the closed verdict, and the token only ``unique`` mints.

THE DEFECT (proven live 2026-09-18). An invoice naming only the client, on a
tenant carrying two open matters for that client, was staged on one of them. The
skill body already said to resolve from two facts and flag what it could not
place; the model resolved anyway. So the question left the model's head, and
these tests hold the two halves of that move:

  * the ARITHMETIC -- a name alone is not a match, a number alone is not a match,
    two matters is ``ambiguous`` and names them, and a failure to search is a
    fourth verdict rather than a confident "nothing matched";
  * the CONSEQUENCE -- a token exists only for ``unique``, so there is nothing to
    hand the write in any other case. Each non-unique test asserts the token
    store stayed EMPTY, not merely that the returned dict lacked a key: a verdict
    is the tool's claim about itself, the store is what the write consults.

No live calls: an httpx.MockTransport plays the tenant, and it records every
query so a test can assert what was asked as well as what came back.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from smokeball_connector import matter_resolution as mr
from smokeball_connector import resolution_token as rt
from smokeball_connector.client import SmokeballClient

M101 = "f220c8e4-eab5-4fd9-8f1d-0becf715b390"
M107 = "a1b2c3d4-0000-4000-8000-00000000d107"
CONTACT = "c0000000-0000-4000-8000-0000000000c1"
OTHER_CONTACT = "c0000000-0000-4000-8000-0000000000c2"
NAME = "Jordan Vale"

#: Two open matters for one client: the shape of the live defect. Neither record
#: carries a structured claim number or date of loss -- Smokeball has no field
#: for either -- so the firm writes them into the description, which is where
#: MATTER_TEXT_FIELDS looks.
MATTERS: dict[str, dict[str, Any]] = {
    M101: {
        "id": M101,
        "number": "2026-PI-101",
        "title": "Motor Vehicle Accident",
        "description": "[SEED] MVA rear-end collision; date of loss 2026-02-10; claim ABC-99-771.",
    },
    M107: {
        "id": M107,
        "number": "2026-PI-107",
        "title": "Personal Injury",
        "description": "[SEED] Premises liability; date of loss 2025-11-14.",
    },
}


class Tenant:
    """A scripted tenant. ``contacts`` maps a contact id to its record and to the
    matters that contact is a party to; ``fail`` makes a path raise."""

    def __init__(
        self,
        *,
        contact_matters: dict[str, list[str]] | None = None,
        contacts: dict[str, dict[str, Any]] | None = None,
        fail: str | None = None,
    ) -> None:
        self.contact_matters = contact_matters if contact_matters is not None else {CONTACT: [M101, M107]}
        self.contacts = contacts if contacts is not None else {CONTACT: {"id": CONTACT, "name": NAME}}
        self.fail = fail
        self.requests: list[httpx.Request] = []

    def client(self) -> SmokeballClient:
        c = SmokeballClient(region="us", environment="staging", client_id="c", client_secret="s", api_key="k")
        c._http = httpx.Client(transport=httpx.MockTransport(self.handle))
        return c

    def queries(self, path: str) -> list[dict[str, str]]:
        return [dict(r.url.params) for r in self.requests if r.url.path == path]

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        self.requests.append(request)
        if self.fail and self.fail in path:
            return httpx.Response(503, text="boom")
        if path == "/contacts":
            term = request.url.params.get("Search", "")
            wanted = term.removeprefix("name:*").removesuffix("*").casefold()
            rows = [c for c in self.contacts.values() if wanted and wanted in str(c.get("name", "")).casefold()]
            return httpx.Response(200, json={"value": rows})
        if path == "/matters":
            contact_id = request.url.params.get("ContactId")
            if contact_id:
                return httpx.Response(
                    200, json={"value": [MATTERS[m] for m in self.contact_matters.get(contact_id, [])]}
                )
            keyword = (request.url.params.get("Search") or "").casefold()
            rows = [m for m in MATTERS.values() if keyword and keyword in str(m.get("number", "")).casefold()]
            return httpx.Response(200, json={"value": rows})
        return httpx.Response(404, json={"error": f"unscripted {path}"})


@pytest.fixture(autouse=True)
def _empty_store() -> None:
    rt._reset_for_tests()


def _resolve(tenant: Tenant, **facts: Any) -> dict[str, Any]:
    return mr.resolve_matter(tenant.client(), **facts)


def _minted() -> int:
    return len(rt._LIVE)


# ---- The live defect ---------------------------------------------------------


def test_two_matters_for_one_client_name_is_ambiguous_and_mints_nothing() -> None:
    """The invoice that was staged on 2026-PI-101 for $240."""
    out = _resolve(Tenant(), client_name=NAME)
    assert out["verdict"] == mr.VERDICT_AMBIGUOUS
    assert out["candidate_count"] == 2
    assert sorted(c["matter_number"] for c in out["candidates"]) == ["2026-PI-101", "2026-PI-107"]
    assert "matter_resolution" not in out
    assert _minted() == 0


def test_a_name_with_one_match_is_still_not_unique() -> None:
    tenant = Tenant(contact_matters={CONTACT: [M101]})
    out = _resolve(tenant, client_name=NAME)
    assert out["verdict"] == mr.VERDICT_NONE
    assert "alone" in out["reason"] and out["matched_on"] == [mr.FACT_NAME]
    assert _minted() == 0


def test_a_number_alone_is_not_a_match_either() -> None:
    out = _resolve(Tenant(), matter_number="2026-PI-101")
    assert out["verdict"] == mr.VERDICT_NONE
    assert out["matched_on"] == [mr.FACT_NUMBER]
    assert _minted() == 0


def test_a_number_and_a_name_resolve_and_mint_one_token() -> None:
    out = _resolve(Tenant(), matter_number="2026-PI-101", client_name=NAME)
    assert out["verdict"] == mr.VERDICT_UNIQUE
    assert out["matter_id"] == M101 and out["matter_number"] == "2026-PI-101"
    assert out["matched_on"] == [mr.FACT_NUMBER, mr.FACT_NAME]
    # The token opens THAT matter and no other, and it is the real store.
    assert rt.verify(out["matter_resolution"], M101).matched_on == (mr.FACT_NUMBER, mr.FACT_NAME)
    with pytest.raises(rt.ResolutionRefused):
        rt.verify(out["matter_resolution"], M107)


# ---- What each narrowing fact does ------------------------------------------


def test_a_date_of_loss_separates_two_matters_for_one_name() -> None:
    out = _resolve(Tenant(), client_name=NAME, date_of_loss="2026-02-10")
    assert out["verdict"] == mr.VERDICT_UNIQUE
    assert out["matter_id"] == M101
    assert out["matched_on"] == [mr.FACT_NAME, mr.FACT_LOSS]


def test_a_claim_number_separates_two_matters_for_one_name() -> None:
    out = _resolve(Tenant(), client_name=NAME, claim_number="ABC 99 771")
    assert out["verdict"] == mr.VERDICT_UNIQUE and out["matter_id"] == M101
    assert out["matched_on"] == [mr.FACT_NAME, mr.FACT_CLAIM]


def test_a_date_of_birth_on_the_party_contact_separates_them() -> None:
    tenant = Tenant(
        contact_matters={CONTACT: [M101], OTHER_CONTACT: [M107]},
        contacts={
            CONTACT: {"id": CONTACT, "name": NAME, "dateOfBirth": "1979-04-02"},
            OTHER_CONTACT: {"id": OTHER_CONTACT, "name": f"{NAME} Jr"},
        },
    )
    out = _resolve(tenant, client_name=NAME, date_of_birth="1979-04-02")
    assert out["verdict"] == mr.VERDICT_UNIQUE and out["matter_id"] == M101
    assert out["matched_on"] == [mr.FACT_NAME, mr.FACT_BIRTH]


def test_a_fact_no_candidate_carries_is_inert_not_fatal() -> None:
    """A date of loss the firm never recorded must not wipe a resolution the
    number and the name already agree on."""
    out = _resolve(Tenant(), matter_number="2026-PI-101", client_name=NAME, date_of_loss="2024-01-01")
    assert out["verdict"] == mr.VERDICT_UNIQUE
    assert out["matched_on"] == [mr.FACT_NUMBER, mr.FACT_NAME]


def test_a_date_of_loss_no_candidate_carries_leaves_two_ambiguous() -> None:
    out = _resolve(Tenant(), client_name=NAME, date_of_loss="2024-01-01")
    assert out["verdict"] == mr.VERDICT_AMBIGUOUS and _minted() == 0


def test_a_date_that_is_not_an_iso_date_is_reported_not_used() -> None:
    out = _resolve(Tenant(), client_name=NAME, date_of_loss="02/10/2026")
    assert out["ignored"] == [mr.FACT_LOSS]
    assert out["verdict"] == mr.VERDICT_AMBIGUOUS


def test_a_padded_date_in_the_record_matches_and_a_longer_run_does_not() -> None:
    assert mr._text_has_date("date of loss 02/10/2026", "2026-02-10")
    assert mr._text_has_date("date of loss 2/10/2026", "2026-02-10")
    assert not mr._text_has_date("date of loss 12/10/2026", "2026-02-10")


# ---- Disagreement, absence, and failure --------------------------------------


def test_a_number_and_a_name_that_disagree_resolve_to_nothing() -> None:
    """The invoice's number points at one matter and its client is a party to
    another. That is a flag for a person, never a pick."""
    tenant = Tenant(contact_matters={CONTACT: [M107]})
    out = _resolve(tenant, matter_number="2026-PI-101", client_name=NAME)
    assert out["verdict"] == mr.VERDICT_NONE
    assert "every fact" in out["reason"] and _minted() == 0


def test_nothing_matching_is_none() -> None:
    out = _resolve(Tenant(contacts={}, contact_matters={}), client_name="Nobody Here")
    assert out["verdict"] == mr.VERDICT_NONE and _minted() == 0


def test_no_searchable_fact_at_all_is_none_and_asks_the_tenant_nothing() -> None:
    tenant = Tenant()
    out = _resolve(tenant, claim_number="ABC-99-771")
    assert out["verdict"] == mr.VERDICT_NONE
    assert "nothing to search on" in out["reason"]
    assert tenant.requests == []


@pytest.mark.parametrize("path", ["/contacts", "/matters"])
def test_a_tenant_that_cannot_be_searched_is_its_own_verdict(path: str) -> None:
    """Not ``none``: "nothing matched" and "I could not look" are different
    facts, and only one of them is a statement about the firm's record."""
    tenant = Tenant(fail=path)
    out = _resolve(tenant, matter_number="2026-PI-101", client_name=NAME)
    assert out["verdict"] == mr.VERDICT_SEARCH_FAILED
    assert out["verdict"] != mr.VERDICT_NONE and _minted() == 0


# ---- The invoice's text is untrusted -----------------------------------------


def test_a_name_carrying_search_syntax_cannot_reach_the_query_as_syntax() -> None:
    """A vendor-authored name is wrapped, never interpreted: the colon that would
    make it a structured term is stripped before it goes near the endpoint."""
    tenant = Tenant()
    _resolve(tenant, client_name='number:*2026-PI-107* OR name:"x"')
    (query,) = tenant.queries("/contacts")
    term = query["Search"]
    assert term.startswith("name:*") and term.endswith("*")
    inside = term[len("name:*") : -1]
    assert not set(inside) & set(':"*')


def test_the_number_equality_is_rechecked_on_what_came_back() -> None:
    """``/matters?Search=`` is a plain keyword, so a partial hit is not a match."""
    out = _resolve(Tenant(), matter_number="2026-PI-10", client_name=NAME)
    assert out["verdict"] == mr.VERDICT_NONE and _minted() == 0
