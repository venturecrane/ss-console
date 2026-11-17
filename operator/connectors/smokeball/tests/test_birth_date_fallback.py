"""A post bill whose printed name is not the firm's record (birth_date_fallback.py).

THE DEFECT (a client firm's post, 2026-11-17). Three claim forms printed the
patient's surname differently from the firm's contact (a misspelling, or another
surname) and came back ``none``. The firm's record matched each on first name,
date of birth and date of loss. These tests hold both halves: the fallback finds
those, and everything weaker (a birth date alone, a wrong date, a possible twin,
a name that resolves today) answers exactly as before or more cautiously. The
names below are invented.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from smokeball_connector import birth_date_fallback as bdf
from smokeball_connector import matter_resolution as mr
from smokeball_connector import resolution_token as rt
from smokeball_connector.client import SmokeballClient

LOSS_KEY = "Matter/CaseDetails/AccidentDetails/AccidentDate"
CLAIM_KEY = "Matter/Plaintiffs/InsurancePolicy/Claims/Number"
BIRTH = "1984-07-09"
LOSS = "2026-05-14"
M1, M2, M3 = (f"a0000000-0000-4000-8000-00000000000{i}" for i in (1, 2, 3))
C1, C2, C3 = (f"c0000000-0000-4000-8000-00000000000{i}" for i in (1, 2, 3))


def _person(cid: str, first: str, last: str, birth: str = "", middle: str = "") -> dict[str, Any]:
    person: dict[str, Any] = {"firstName": first, "lastName": last}
    if middle:
        person["middleName"] = middle
    if birth:
        person["birthDate"] = f"{birth}T00:00:00"
    return {"id": cid, "person": person}


class Tenant:
    """Person records searched as Smokeball does (a substring of first, middle
    and last name), paged by ``Offset``, matters by ``ContactId``, and a loss
    date in each matter's layout."""

    def __init__(
        self,
        people: list[dict[str, Any]],
        contact_matters: dict[str, list[str]],
        losses: dict[str, str],
        *,
        ignore_offset: bool = False,
        claims: dict[str, str] | None = None,
    ) -> None:
        self.claims = claims or {}
        self.people = people
        self.contact_matters = contact_matters
        self.losses = losses
        self.ignore_offset = ignore_offset
        self.searches: list[str] = []

    def client(self) -> SmokeballClient:
        c = SmokeballClient(region="us", environment="staging", client_id="c", client_secret="s", api_key="k")
        c._http = httpx.Client(transport=httpx.MockTransport(self.handle))
        return c

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if path == "/contacts":
            want = params.get("Search", "").removeprefix("name:*").removesuffix("*").casefold()
            self.searches.append(want)
            rows = [
                c
                for c in self.people
                if want
                and want
                in " ".join(str(c["person"].get(k) or "") for k in ("firstName", "middleName", "lastName")).casefold()
            ]
            limit, offset = int(params.get("Limit", 50)), 0 if self.ignore_offset else int(params.get("Offset", 0))
            return httpx.Response(200, json={"value": rows[offset : offset + limit]})
        if path == "/matters":
            ids = self.contact_matters.get(params.get("ContactId", ""), [])
            return httpx.Response(200, json={"value": [{"id": m, "number": f"20{m[-1]}", "title": "PI"} for m in ids]})
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[2] == "layouts":
            has = parts[1] in self.losses or parts[1] in self.claims
            return httpx.Response(200, json={"value": [{"id": "i1"}] if has else []})
        if len(parts) == 4 and parts[2] == "layouts":
            values = [{"key": LOSS_KEY, "value": self.losses[parts[1]]}] if parts[1] in self.losses else []
            if parts[1] in self.claims:
                values.append({"key": CLAIM_KEY, "value": self.claims[parts[1]]})
            return httpx.Response(200, json={"values": values})
        return httpx.Response(404, json={"error": f"unscripted {path}"})


@pytest.fixture(autouse=True)
def _empty_store() -> None:
    rt._reset_for_tests()


def _resolve(tenant: Tenant, name: str, *, birth: str = BIRTH, loss: Any = LOSS, ok: bool = True, **more: Any):
    return mr.resolve_matter(
        tenant.client(), client_name=name, date_of_birth=birth, date_of_loss=loss, fallback_ok=ok, **more
    )


def _misspelled() -> Tenant:
    """The firm records "Leocadia Brennaman"; the bill prints "Brenamann"."""
    return Tenant([_person(C1, "Leocadia", "Brennaman", BIRTH)], {C1: [M1]}, {M1: LOSS})


# ---- what the fallback finds ---------------------------------------------------


def test_a_misspelled_surname_resolves_on_birth_date_and_date_of_loss() -> None:
    out = _resolve(_misspelled(), "Leocadia Brenamann")
    assert out["verdict"] == "unique" and out["matter_id"] == M1
    assert out["matched_on"] == ["date_of_loss", "date_of_birth"]
    assert out["name_on_record_differs"] is True and out["reason"] == bdf.NAME_DIFFERS
    assert out["matter_resolution"]


def test_another_surname_altogether_resolves_the_same_way() -> None:
    tenant = Tenant([_person(C1, "Tamsin", "Okafor", BIRTH)], {C1: [M1]}, {M1: LOSS})
    out = _resolve(tenant, "Tamsin Delacroix")
    assert out["verdict"] == "unique" and out["matter_id"] == M1 and out["name_on_record_differs"]


def test_an_accented_first_name_finds_the_unaccented_record() -> None:
    tenant = Tenant([_person(C1, "Maria", "Lopez", BIRTH)], {C1: [M1]}, {M1: LOSS})
    out = _resolve(tenant, "María Lopes")
    assert out["verdict"] == "unique" and out["matter_id"] == M1


def test_one_contact_on_two_matters_is_decided_by_the_date_of_loss() -> None:
    tenant = Tenant([_person(C1, "Tamsin", "Okafor", BIRTH)], {C1: [M1, M2]}, {M1: LOSS, M2: "2025-09-30"})
    assert _resolve(tenant, "Tamsin Delacroix")["matter_id"] == M1
    rt._reset_for_tests()
    # A loss date neither matter records is inert, so both of hers remain and a
    # person is asked which (her matters, never a stranger's).
    both = _resolve(tenant, "Tamsin Delacroix", loss="2026-01-01")
    assert both["verdict"] == "ambiguous" and len(both["candidates"]) == 2 and not rt._LIVE


# ---- what it must not do -------------------------------------------------------


def test_a_name_that_resolves_today_never_reaches_the_fallback() -> None:
    """The regression the critique found: the full name matches a contact with
    NO recorded birth date (most of a firm's contacts). Today that resolves on
    name and date of loss, and it still must, with no first-name search."""
    tenant = Tenant(
        [_person(C1, "Jordan", "Vale"), _person(C2, "Jordan", "Smith", BIRTH)], {C1: [M1], C2: [M2]}, {M1: LOSS}
    )
    out = _resolve(tenant, "Jordan Vale")
    assert out["verdict"] == "unique" and out["matter_id"] == M1
    assert out["matched_on"] == ["client_name", "date_of_loss"] and "name_on_record_differs" not in out
    assert tenant.searches == ["jordan"], "one name search, no first-name fallback"


def test_a_possible_twin_with_no_recorded_birth_date_makes_it_ambiguous() -> None:
    tenant = Tenant(
        [_person(C1, "Jose", "Ramos", BIRTH), _person(C2, "Jose", "Ramos")],
        {C1: [M1], C2: [M2]},
        {M1: LOSS, M2: LOSS},
    )
    out = _resolve(tenant, "Jose Romos")
    assert out["verdict"] == "ambiguous" and len(out["candidates"]) == 2
    assert not rt._LIVE


def test_a_twin_alone_never_resolves() -> None:
    """The birth-date contact's matter fails the loss date; the twin's carries
    it. The twin has no recorded birth date, so its matter is one fact: none."""
    tenant = Tenant(
        [_person(C1, "Jose", "Ramos", BIRTH), _person(C2, "Jose", "Ramos")],
        {C1: [M1], C2: [M2]},
        {M1: "2020-01-01", M2: LOSS},
    )
    out = _resolve(tenant, "Jose Romos")
    assert out["verdict"] == "none" and "candidates" not in out and not rt._LIVE


def test_a_twins_matter_never_resolves_on_a_loss_date_and_a_claim_number() -> None:
    """The real match's matter records another loss date; the twin's records the
    printed one AND the printed claim number. Two facts, but neither is a birth
    date read for this matter. FALSIFIER: drop the FACT_BIRTH half of the
    pre-verdict check and this mints a token for the twin's matter."""
    tenant = Tenant(
        [_person(C1, "Jose", "Ramos", BIRTH), _person(C2, "Jose", "Ramos")],
        {C1: [M1], C2: [M2]},
        {M1: "2020-01-01", M2: LOSS},
        claims={M2: "CLM-99-12345"},
    )
    out = _resolve(tenant, "Jose Romos", claim_number="CLM-99-12345")
    assert out["verdict"] == "none" and not rt._LIVE


def test_a_second_given_name_that_disagrees_is_not_the_person() -> None:
    tenant = Tenant([_person(C1, "Maria", "Lopez", BIRTH, middle="Isabel")], {C1: [M1]}, {M1: LOSS})
    assert _resolve(tenant, "Maria Elena Lopes")["verdict"] == "none"


@pytest.mark.parametrize(
    ("birth", "loss"),
    [("1985-07-09", LOSS), (BIRTH, "2020-01-01"), (BIRTH, None), (BIRTH, "08/11/2026")],
    ids=["wrong birth date", "wrong date of loss", "no date of loss", "date of loss not ISO"],
)
def test_anything_weaker_is_none_and_names_no_candidate(birth: str, loss: Any) -> None:
    out = _resolve(_misspelled(), "Leocadia Brenamann", birth=birth, loss=loss)
    assert out["verdict"] == "none" and "candidates" not in out and not rt._LIVE
    assert "name_on_record_differs" not in out, "the flag is a unique answer's, never a none's"


def test_a_claim_number_never_stands_in_for_the_date_of_loss() -> None:
    out = _resolve(_misspelled(), "Leocadia Brenamann", loss=None, claim_number="CLM-99-12345")
    assert out["verdict"] == "none" and not rt._LIVE


def test_a_carried_claim_number_with_an_uncarried_date_of_loss_is_still_none() -> None:
    """Birth date plus a claim number is two facts to ``_verdict``. On this path
    the date of loss is the required second fact. FALSIFIER: drop the explicit
    loss rule in ``birth_date_fallback.resolve`` and this resolves ``unique``."""
    tenant = Tenant([_person(C1, "Leocadia", "Brennaman", BIRTH)], {C1: [M1]}, {}, claims={M1: "CLM-99-12345"})
    out = _resolve(tenant, "Leocadia Brenamann", loss="2020-01-01", claim_number="CLM-99-12345")
    assert out["verdict"] == "none" and not rt._LIVE


def test_a_garbled_date_read_into_candidates_does_not_qualify() -> None:
    out = _resolve(_misspelled(), "Leocadia Brenamann", loss=None, date_of_loss_as_printed="08/l1/2026")
    assert out["verdict"] == "none" and not rt._LIVE


def test_a_surname_printed_first_without_a_comma_finds_nothing() -> None:
    out = _resolve(_misspelled(), "BRENAMANN LEOCADIA")
    assert out["verdict"] == "none" and not rt._LIVE


def test_a_call_outside_the_post_keeps_the_old_rule() -> None:
    tenant = _misspelled()
    out = _resolve(tenant, "Leocadia Brenamann", ok=False)
    assert out["verdict"] == "none" and "name_on_record_differs" not in out
    assert tenant.searches == ["brenamann", "leocadia"], "the name search only, no paged fallback"


def test_a_matter_number_keeps_the_old_rule() -> None:
    out = _resolve(_misspelled(), "Leocadia Brenamann", matter_number="2026-PI-404")
    assert "name_on_record_differs" not in out


# ---- the bound ---------------------------------------------------------------


def test_a_first_name_too_common_to_read_to_the_end_is_none_not_a_failed_search(monkeypatch) -> None:
    monkeypatch.setattr(bdf, "MAX_PAGES", 2)
    people = [_person(f"c-{i}", "Maria", f"Z{i}") for i in range(bdf.PAGE_ROWS * 2)]
    out = _resolve(Tenant(people, {}, {}), "Maria Brenamann")
    assert out["verdict"] == "none" and "too common" in out["reason"]


def test_a_server_that_ignores_offset_does_not_loop(monkeypatch) -> None:
    monkeypatch.setattr(bdf, "MAX_PAGES", 5)
    people = [_person(f"c-{i}", "Maria", f"Z{i}") for i in range(bdf.PAGE_ROWS)]
    tenant = Tenant(people, {}, {}, ignore_offset=True)
    out = _resolve(tenant, "Maria Brenamann")
    # A repeated FULL page is never the end of the list: too common to search.
    assert out["verdict"] == "none" and "too common" in out["reason"]
    assert tenant.searches.count("maria") == 3
