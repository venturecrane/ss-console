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

#: Two open matters for one client: the shape of the live defect. These records
#: carry the claim number and date of loss in the description, which is where
#: MATTER_TEXT_FIELDS looks; the layout tests below carry them where Smokeball
#: keeps them, in the matter's layout (``LAYOUT_KEYS``).
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
        layouts: dict[str, list[dict[str, Any]]] | None = None,
        matters: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.contact_matters = contact_matters if contact_matters is not None else {CONTACT: [M101, M107]}
        self.contacts = contacts if contacts is not None else {CONTACT: {"id": CONTACT, "name": NAME}}
        self.fail = fail
        #: matter id -> its layout items, each ``{"id": ..., "values": {key: value}}``.
        #: A matter not listed has no layout items, as a bare matter has.
        self.layouts = layouts or {}
        self.matters = matters if matters is not None else MATTERS
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
                    200, json={"value": [self.matters[m] for m in self.contact_matters.get(contact_id, [])]}
                )
            keyword = (request.url.params.get("Search") or "").casefold()
            rows = [m for m in self.matters.values() if keyword and keyword in str(m.get("number", "")).casefold()]
            return httpx.Response(200, json={"value": rows})
        parts = path.strip("/").split("/")
        if len(parts) in (3, 4) and parts[0] == "matters" and parts[2] == "layouts":
            items = self.layouts.get(parts[1], [])
            if len(parts) == 3:
                return httpx.Response(200, json={"value": [{"id": i["id"]} for i in items]})
            for item in items:
                if item["id"] == parts[3]:
                    values = [{"key": k, "value": v} for k, v in item["values"].items()]
                    return httpx.Response(200, json={"values": values})
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


def test_the_tenants_own_birth_date_shape_corroborates() -> None:
    """The live tenant keeps a birth date at person.birthDate with a time on
    it (A&P, 2026-10-01). A bill printing the client's name and birth date
    must resolve on the two, which it never did when only top-level fields
    were read."""
    tenant = Tenant(
        contact_matters={CONTACT: [M101], OTHER_CONTACT: [M107]},
        contacts={
            CONTACT: {"id": CONTACT, "name": NAME, "person": {"birthDate": "1979-04-02T00:00:00"}},
            OTHER_CONTACT: {"id": OTHER_CONTACT, "name": f"{NAME} Jr", "person": {"birthDate": "1980-01-01T00:00:00"}},
        },
    )
    out = _resolve(tenant, client_name=NAME, date_of_birth="1979-04-02")
    assert out["verdict"] == mr.VERDICT_UNIQUE and out["matter_id"] == M101
    assert out["matched_on"] == [mr.FACT_NAME, mr.FACT_BIRTH]
    assert mr._birth_date({"person": {"birthDate": "not a date"}}) == ""


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
    # EVERY query, not the first: a name that matches nothing now sends its other
    # tokens as well (the full-or-empty fallback), and each of them is built from
    # the same untrusted text, so each must be as clean as the first.
    queries = tenant.queries("/contacts")
    assert queries, "the search must have been attempted"
    for query in queries:
        term = query["Search"]
        assert term.startswith("name:*") and term.endswith("*")
        inside = term[len("name:*") : -1]
        assert not set(inside) & set(':"*'), f"search syntax reached the query: {term!r}"


def test_the_number_equality_is_rechecked_on_what_came_back() -> None:
    """``/matters?Search=`` is a plain keyword, so a partial hit is not a match."""
    out = _resolve(Tenant(), matter_number="2026-PI-10", client_name=NAME)
    assert out["verdict"] == mr.VERDICT_NONE and _minted() == 0


# ---- The vendor's own search under-reports duplicates (2026-09-23) -----------
#
# Found on the pilot seat by a combined-post run, not by this suite, and the
# reason this suite missed it is worth keeping: ``Tenant.handle`` models the
# contact search as a clean substring match, which is MORE CORRECT than the
# vendor. A fixture kinder than reality cannot disagree with the code, so the
# defect lived behind a passing suite.
#
# Measured, pilot tenant, two distinct contact records both recorded as
# firstName "Maria" / lastName "Alvarez", one on each of two open matters:
#
#     name:*Maria Alvarez*  -> 1 row    (one of the two, silently)
#     name:*Alvarez*        -> 2 rows   (both)
#
# The consequence was not a wrong FILE (the fail-closed design held and the
# letter was held) but a wrong SENTENCE: "the only matter I found for that
# client is <the other one>", which invites a one-word yes that misfiles.


class DuplicateNameTenant(Tenant):
    """A tenant whose contact search behaves the way the vendor's actually does.

    Two contacts share a name. A term carrying MORE THAN ONE token returns only
    the first; a single-token term returns both. Records use the ``person``
    shape the live tenant uses, where top-level ``name`` is absent.
    """

    C1 = "c0000000-0000-4000-8000-00000000dup1"
    C2 = "c0000000-0000-4000-8000-00000000dup2"

    def __init__(self) -> None:
        super().__init__(
            contact_matters={self.C1: [M101], self.C2: [M107]},
            contacts={
                self.C1: {"id": self.C1, "person": {"firstName": "Maria", "lastName": "Alvarez"}},
                self.C2: {"id": self.C2, "person": {"firstName": "Maria", "lastName": "Alvarez"}},
            },
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/contacts":
            self.requests.append(request)
            term = request.url.params.get("Search", "")
            inner = term.removeprefix("name:*").removesuffix("*").strip()
            tokens = inner.casefold().split()
            rows = [
                c
                for c in self.contacts.values()
                if tokens
                and set(tokens)
                <= {
                    str((c.get("person") or {}).get("firstName", "")).casefold(),
                    str((c.get("person") or {}).get("lastName", "")).casefold(),
                }
            ]
            # THE VENDOR'S BEHAVIOUR: a multi-token term returns only the first.
            if len(tokens) > 1:
                rows = rows[:1]
            return httpx.Response(200, json={"value": rows})
        return super().handle(request)


def test_two_contacts_sharing_a_name_are_ambiguous_not_a_lone_candidate() -> None:
    """The live defect. Both matters must surface, so the reply asks which.

    Without the single-token search this returns VERDICT_NONE carrying ONE
    candidate, and the Operator says "the only matter I found for that client
    is 2026-PI-101" about a letter belonging to 2026-PI-107.
    """
    out = _resolve(DuplicateNameTenant(), client_name="Maria Alvarez")
    assert out["verdict"] == mr.VERDICT_AMBIGUOUS, out.get("reason")
    assert out["candidate_count"] == 2
    assert sorted(c["matter_number"] for c in out["candidates"]) == ["2026-PI-101", "2026-PI-107"]
    assert _minted() == 0


def test_the_contact_search_asks_on_one_token() -> None:
    """The mechanism the test above depends on, asserted directly: a multi-token
    query is the thing the vendor mishandles, so we must not send one."""
    tenant = DuplicateNameTenant()
    _resolve(tenant, client_name="Maria Alvarez")
    (query,) = tenant.queries("/contacts")
    inner = query["Search"].removeprefix("name:*").removesuffix("*")
    assert len(inner.split()) == 1, f"searched on {inner!r}; a multi-token term loses duplicates"
    assert inner.casefold() == "alvarez", "the longest token is the most selective one"


def test_a_one_token_search_still_requires_the_WHOLE_name_to_match() -> None:
    """The falsifier for the fix: searching a surname must not widen the net.

    A different Alvarez shares the probe token and must be filtered out here, or
    the fix has traded a missed duplicate for a false one.
    """
    tenant = DuplicateNameTenant()
    tenant.contacts[OTHER_CONTACT] = {
        "id": OTHER_CONTACT,
        "person": {"firstName": "Ruben", "lastName": "Alvarez"},
    }
    tenant.contact_matters[OTHER_CONTACT] = [M101]
    kept = mr._contacts_by_name(tenant.client(), "Maria Alvarez")
    assert [c["id"] for c in kept] == [DuplicateNameTenant.C1, DuplicateNameTenant.C2]


def test_a_single_token_name_sends_the_same_query_it_always_did() -> None:
    """A company or a mononym has one token; the fix must not change its query."""
    tenant = Tenant(
        contact_matters={CONTACT: [M101]},
        contacts={CONTACT: {"id": CONTACT, "name": "Acme"}},
    )
    _resolve(tenant, client_name="Acme")
    (query,) = tenant.queries("/contacts")
    assert query["Search"] == "name:*Acme*"


# ---- A common first name fills the page (2026-09-23, the same day) -----------
#
# The fix above searched on the LONGEST token. "Daniel Porter" ties at six
# letters and max() returns the first, so it searched "Daniel". On a client tenant
# that filled the 50-row page with other Daniels, the client was not on it,
# and the resolver answered `none` with no candidate for a client who has an
# open matter. Not a misfile -- a false "not found", which the
# reply turns into "I could not find a matter for this client".


class CommonFirstNameTenant(Tenant):
    """Sixty other Daniels, then the one we want, and a search that honours Limit.

    The vendor returns rows in its own order and stops at ``Limit``; the target
    is placed LAST so a first-name search cannot reach it.
    """

    TARGET = "c0000000-0000-4000-8000-0000000porter"

    def __init__(self) -> None:
        contacts: dict[str, dict[str, Any]] = {}
        for i in range(60):
            cid = f"c0000000-0000-4000-8000-{i:012d}"
            contacts[cid] = {"id": cid, "person": {"firstName": "Daniel", "lastName": f"Other{i}"}}
        contacts[self.TARGET] = {"id": self.TARGET, "person": {"firstName": "Daniel", "lastName": "Porter"}}
        super().__init__(contact_matters={self.TARGET: [M101]}, contacts=contacts)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/contacts":
            self.requests.append(request)
            term = request.url.params.get("Search", "")
            want = term.removeprefix("name:*").removesuffix("*").strip().casefold()
            limit = int(request.url.params.get("Limit", "50"))
            rows = [
                c
                for c in self.contacts.values()
                if want
                and want
                in {
                    str((c.get("person") or {}).get("firstName", "")).casefold(),
                    str((c.get("person") or {}).get("lastName", "")).casefold(),
                }
            ]
            return httpx.Response(200, json={"value": rows[:limit]})
        return super().handle(request)


def test_a_tie_in_length_searches_the_later_token_first() -> None:
    """In western order the later token is the surname, the selective one."""
    tenant = CommonFirstNameTenant()
    mr._contacts_by_name(tenant.client(), "Daniel Porter")
    first = tenant.queries("/contacts")[0]
    assert first["Search"] == "name:*Porter*"


def test_a_client_behind_a_full_page_of_common_first_names_is_still_found() -> None:
    """The live defect, asserted on the outcome rather than the query order.

    Even if the ordering regressed to the first token, a full page must trigger
    the other tokens, so the client is found either way.
    """
    out = _resolve(CommonFirstNameTenant(), client_name="Daniel Porter")
    assert [c["matter_number"] for c in out.get("candidates") or []] == ["2026-PI-101"]


def test_a_full_page_on_the_first_probe_searches_the_other_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fallback on its own, with the ordering deliberately defeated.

    Forcing the first-name probe reproduces the exact live shape; the client can
    only be reached through the full-page fallback.
    """
    monkeypatch.setattr(mr, "_probe_order", lambda tokens: list(tokens))
    tenant = CommonFirstNameTenant()
    kept = mr._contacts_by_name(tenant.client(), "Daniel Porter")
    queries = [q["Search"] for q in tenant.queries("/contacts")]
    assert queries[0] == "name:*Daniel*"
    assert "name:*Porter*" in queries, "a full page must send the other tokens"
    assert [c["id"] for c in kept] == [CommonFirstNameTenant.TARGET]


def test_a_page_that_is_neither_full_nor_empty_costs_one_query() -> None:
    """The fallback must not fire on the ordinary case; it spends rate limit."""
    tenant = DuplicateNameTenant()
    mr._contacts_by_name(tenant.client(), "Maria Alvarez")
    assert len(tenant.queries("/contacts")) == 1


# ---- Names with a middle, a hyphen, or a suffix (2026-09-25) ------------------
#
# A read-only replay of the post lane on a client seat returned verdict `none`
# with ZERO candidates for four letters whose client the firm has: a hyphenated
# first name, and three names carrying a middle initial or a middle name the
# firm did not record. The old match required every caller token on the record
# and kept `.` and `-` inside tokens, so "R." and "Rosa-Linda" could never be
# found. The names below are invented.


class PersonTenant(Tenant):
    """Person-shaped records; the search returns every contact whose recorded
    name (first, middle, last) contains the probe, case-insensitively."""

    def __init__(self, people: dict[str, tuple[str, str, str]], contact_matters: dict[str, list[str]]) -> None:
        contacts: dict[str, dict[str, Any]] = {}
        for cid, (first, middle, last) in people.items():
            person = {"firstName": first, "lastName": last}
            if middle:
                person["middleName"] = middle
            contacts[cid] = {"id": cid, "person": person}
        super().__init__(contact_matters=contact_matters, contacts=contacts)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/contacts":
            self.requests.append(request)
            want = request.url.params.get("Search", "").removeprefix("name:*").removesuffix("*").casefold()
            rows = []
            for c in self.contacts.values():
                p = c.get("person") or {}
                full = " ".join(str(p.get(k) or "") for k in ("firstName", "middleName", "lastName")).casefold()
                if want and want in full:
                    rows.append(c)
            return httpx.Response(200, json={"value": rows})
        return super().handle(request)


def _one(first: str, middle: str, last: str) -> PersonTenant:
    return PersonTenant({CONTACT: (first, middle, last)}, {CONTACT: [M101]})


def _kept(tenant: PersonTenant, name: str) -> list[str]:
    return [c["id"] for c in mr._contacts_by_name(tenant.client(), name)]


def test_a_hyphenated_first_name_matches_a_two_word_record() -> None:
    assert _kept(_one("Rosa Linda", "", "Quill"), "Rosa-Linda Quill") == [CONTACT]


@pytest.mark.parametrize("middle", ["", "Rupert", "R"])
def test_a_middle_initial_matches_a_record_with_no_middle_or_an_agreeing_one(middle: str) -> None:
    assert _kept(_one("Tobias", middle, "Wren"), "Tobias R. Wren") == [CONTACT]


def test_a_contradicting_middle_initial_excludes_the_record() -> None:
    assert _kept(_one("Tobias", "T", "Wren"), "Tobias R. Wren") == []


def test_a_lone_record_with_a_contradicting_middle_is_none_with_no_candidate() -> None:
    """Not a lone candidate: offering "Maria B." for a letter to "Maria A."
    invites a one-word yes that misfiles."""
    out = _resolve(_one("Maria", "B", "Lopez"), client_name="Maria A. Lopez")
    assert out["verdict"] == mr.VERDICT_NONE
    assert not out.get("candidates")
    assert _minted() == 0


def test_a_middle_name_and_initial_match_a_record_with_neither() -> None:
    assert _kept(_one("Lucia", "", "Fenn"), "Lucia Beatriz A. Fenn") == [CONTACT]


def test_two_contacts_matching_a_name_with_an_initial_are_ambiguous() -> None:
    tenant = PersonTenant(
        {CONTACT: ("Oren", "", "Halvard"), OTHER_CONTACT: ("Oren", "James", "Halvard")},
        {CONTACT: [M101], OTHER_CONTACT: [M107]},
    )
    out = _resolve(tenant, client_name="Oren J. Halvard")
    assert out["verdict"] == mr.VERDICT_AMBIGUOUS, out.get("reason")
    assert out["candidate_count"] == 2
    assert _minted() == 0


def test_two_identical_names_are_still_ambiguous() -> None:
    tenant = PersonTenant(
        {CONTACT: ("Maria", "", "Alvarez"), OTHER_CONTACT: ("Maria", "", "Alvarez")},
        {CONTACT: [M101], OTHER_CONTACT: [M107]},
    )
    assert _resolve(tenant, client_name="Maria Alvarez")["verdict"] == mr.VERDICT_AMBIGUOUS


def test_a_generational_suffix_is_not_required() -> None:
    assert _kept(_one("John", "", "Smith"), "John Smith Jr.") == [CONTACT]


def test_the_comma_form_reads_as_first_and_last() -> None:
    assert _kept(_one("Tobias", "Rupert", "Wren"), "Wren, Tobias R.") == [CONTACT]
    assert _kept(_one("Tobias", "T", "Wren"), "Wren, Tobias R.") == []


def test_a_company_name_still_needs_every_token() -> None:
    record = {"id": CONTACT, "name": "Smith Holdings Trust", "company": {"name": "Smith Holdings Trust"}}
    tenant = Tenant(contact_matters={CONTACT: [M101]}, contacts={CONTACT: record})
    assert [c["id"] for c in mr._contacts_by_name(tenant.client(), "Smith Family Trust")] == []
    assert [c["id"] for c in mr._contacts_by_name(tenant.client(), "Smith Holdings Trust")] == [CONTACT]


def test_the_probe_is_a_normalized_token() -> None:
    """The search must not send "Rosa-Linda" or "R." as a probe: the record
    holds neither spelling."""
    tenant = _one("Rosa Linda", "", "Quill")
    mr._contacts_by_name(tenant.client(), "Rosa-Linda Quill")
    assert tenant.queries("/contacts")[0]["Search"] == "name:*Quill*"


# ---- Facts kept in the matter's layout (2026-10-30) ----------------------------
#
# A client seat's post: a carrier letter printing a claim number for a client
# with two matters, and a court notice printing its case number, were held as
# questions because the firm keeps both in the matter's layout and the resolver
# read only the matter's text. These records carry no claim, date or case in
# their text, so every corroboration below comes from a layout.

BARE: dict[str, dict[str, Any]] = {
    M101: {"id": M101, "number": "2026-PI-101", "title": "Motor Vehicle Accident", "description": ""},
    M107: {"id": M107, "number": "2026-PI-107", "title": "Property Damage", "description": ""},
}
P_CLAIM, D_CLAIM = mr.LAYOUT_KEYS[mr.FACT_CLAIM]
(LOSS_KEY,) = mr.LAYOUT_KEYS[mr.FACT_LOSS]
(CASE_KEY,) = mr.LAYOUT_KEYS[mr.FACT_CASE]
TWO_CLIENTS = {
    CONTACT: {"id": CONTACT, "name": NAME},
    OTHER_CONTACT: {"id": OTHER_CONTACT, "name": "Casey Vale"},
}
ONE_EACH = {CONTACT: [M101], OTHER_CONTACT: [M107]}


def _item(item_id: str, **values: Any) -> dict[str, Any]:
    return {"id": item_id, "values": values}


def _laid(layouts: dict[str, list[dict[str, Any]]], **kw: Any) -> Tenant:
    return Tenant(matters=BARE, layouts=layouts, **kw)


def _layout_requests(tenant: Tenant) -> list[str]:
    return [r.url.path for r in tenant.requests if "/layouts" in r.url.path]


def test_a_claim_in_the_layout_separates_one_clients_two_matters() -> None:
    tenant = _laid(
        {
            M101: [_item("p1", **{P_CLAIM: "CLM-123456"})],
            M107: [_item("p1", **{P_CLAIM: "CLM-999999"})],
        }
    )
    out = _resolve(tenant, client_name=NAME, claim_number="CLM 123456")
    assert out["verdict"] == mr.VERDICT_UNIQUE and out["matter_id"] == M101
    assert out["matched_on"] == [mr.FACT_NAME, mr.FACT_CLAIM]
    assert rt.verify(out["matter_resolution"], M101).matched_on == (mr.FACT_NAME, mr.FACT_CLAIM)


def test_the_other_sides_claim_number_corroborates_too() -> None:
    tenant = _laid(
        {
            M101: [_item("d1", **{D_CLAIM: "0545-22-918"})],
            M107: [_item("d1", **{D_CLAIM: "0545-22-100"})],
        }
    )
    out = _resolve(tenant, client_name=NAME, claim_number="054522918")
    assert out["verdict"] == mr.VERDICT_UNIQUE and out["matter_id"] == M101


def test_a_claim_recorded_on_only_one_of_two_matters_stays_ambiguous() -> None:
    """An injury matter and a property-damage matter from one accident, the
    claim recorded on only the first. The empty field on the second is not
    evidence the letter belongs to the first: filing there is the 2026-09-18
    defect in another form."""
    tenant = _laid({M101: [_item("p1", **{P_CLAIM: "CLM-123456"})]})
    out = _resolve(tenant, client_name=NAME, claim_number="CLM-123456")
    assert out["verdict"] == mr.VERDICT_AMBIGUOUS and out["candidate_count"] == 2
    assert _minted() == 0


def test_a_second_claimants_claim_number_is_seen_not_hidden_behind_the_first() -> None:
    tenant = _laid(
        {
            M101: [_item("p1", **{P_CLAIM: "CLM-111111"}), _item("p2", **{P_CLAIM: "CLM-222222"})],
            M107: [_item("p1", **{P_CLAIM: "CLM-333333"})],
        }
    )
    out = _resolve(tenant, client_name=NAME, claim_number="CLM-222222")
    assert out["verdict"] == mr.VERDICT_UNIQUE and out["matter_id"] == M101


def test_two_clients_sharing_the_other_sides_claim_number_are_kept_apart_by_name() -> None:
    """One accident, two of the firm's clients, one at-fault carrier's claim."""
    shared = {M101: [_item("d1", **{D_CLAIM: "DEF-777777"})], M107: [_item("d1", **{D_CLAIM: "DEF-777777"})]}
    out = _resolve(
        _laid(shared, contacts=TWO_CLIENTS, contact_matters=ONE_EACH), client_name=NAME, claim_number="DEF-777777"
    )
    assert out["verdict"] == mr.VERDICT_UNIQUE and out["matter_id"] == M101
    rt._reset_for_tests()
    surname = _resolve(
        _laid(shared, contacts=TWO_CLIENTS, contact_matters=ONE_EACH), client_name="Vale", claim_number="DEF-777777"
    )
    assert surname["verdict"] == mr.VERDICT_AMBIGUOUS and _minted() == 0


def test_a_date_of_loss_in_the_layout_separates_them() -> None:
    tenant = _laid(
        {
            M101: [_item("c1", **{LOSS_KEY: "2026-03-04T00:00:00"})],
            M107: [_item("c1", **{LOSS_KEY: "2025-01-01T00:00:00"})],
        }
    )
    out = _resolve(tenant, client_name=NAME, date_of_loss="2026-03-04")
    assert out["verdict"] == mr.VERDICT_UNIQUE and out["matter_id"] == M101
    assert out["matched_on"] == [mr.FACT_NAME, mr.FACT_LOSS]


def test_a_court_notices_case_number_corroborates_the_clients_one_matter() -> None:
    """The held court notice: one matter for the name, its case number in the
    layout. A name alone is a question; the case number is the second fact."""
    tenant = _laid({M101: [_item("c1", **{CASE_KEY: "STK-CV-2026-0042"})]}, contact_matters={CONTACT: [M101]})
    out = _resolve(tenant, client_name=NAME, case_number="STK-CV-2026-0042")
    assert out["verdict"] == mr.VERDICT_UNIQUE and out["matter_id"] == M101
    assert out["matched_on"] == [mr.FACT_NAME, mr.FACT_CASE]


def test_a_consolidated_docket_is_settled_by_the_name_not_the_number() -> None:
    docket = {
        M101: [_item("c1", **{CASE_KEY: "STK-CV-2026-0042"})],
        M107: [_item("c1", **{CASE_KEY: "STK-CV-2026-0042"})],
    }
    out = _resolve(
        _laid(docket, contacts=TWO_CLIENTS, contact_matters=ONE_EACH), client_name=NAME, case_number="STK-CV-2026-0042"
    )
    assert out["verdict"] == mr.VERDICT_UNIQUE and out["matter_id"] == M101
    rt._reset_for_tests()
    surname = _resolve(
        _laid(docket, contacts=TWO_CLIENTS, contact_matters=ONE_EACH),
        client_name="Vale",
        case_number="STK-CV-2026-0042",
    )
    assert surname["verdict"] == mr.VERDICT_AMBIGUOUS and _minted() == 0


def test_a_case_number_is_never_read_from_a_description() -> None:
    """A description may cite a related or prior case."""
    matters = {M101: {**BARE[M101], "description": "Related: STK-CV-2026-0042"}}
    tenant = Tenant(matters=matters, contact_matters={CONTACT: [M101]})
    out = _resolve(tenant, client_name=NAME, case_number="STK-CV-2026-0042")
    assert out["verdict"] == mr.VERDICT_NONE and out["matched_on"] == [mr.FACT_NAME]


def test_a_layout_that_cannot_be_read_is_a_failed_search_not_an_answer() -> None:
    tenant = _laid({M101: [_item("p1", **{P_CLAIM: "CLM-123456"})]}, fail="/layouts/")
    out = _resolve(tenant, client_name=NAME, claim_number="CLM-123456")
    assert out["verdict"] == mr.VERDICT_SEARCH_FAILED and _minted() == 0


def test_layouts_are_read_once_per_survivor_and_not_beyond_the_cap() -> None:
    tenant = _laid(
        {
            M101: [_item("p1", **{P_CLAIM: "CLM-123456"}), _item("c1", **{LOSS_KEY: "2026-03-04"})],
            M107: [_item("p1", **{P_CLAIM: "CLM-999999"})],
        }
    )
    _resolve(tenant, client_name=NAME, claim_number="CLM-123456", date_of_loss="2026-03-04")
    # one listing per matter and one read per item (2 + 3), however many facts use them
    assert len(_layout_requests(tenant)) == 5

    many = {f"m-{i}": {"id": f"m-{i}", "number": f"2026-PI-20{i}", "title": "", "description": ""} for i in range(5)}
    crowd = Tenant(matters=many, contact_matters={CONTACT: list(many)})
    out = _resolve(crowd, client_name=NAME, claim_number="CLM-123456")
    assert out["verdict"] == mr.VERDICT_AMBIGUOUS
    assert _layout_requests(crowd) == []


def test_no_layout_fact_supplied_reads_no_layout() -> None:
    tenant = _laid({M101: [_item("p1", **{P_CLAIM: "CLM-123456"})]})
    _resolve(tenant, matter_number="2026-PI-101", client_name=NAME)
    assert _layout_requests(tenant) == []


@pytest.mark.parametrize("weak", ["2024", "ABCDEFG", "1-2"])
def test_a_weak_claim_number_is_reported_and_not_used(weak: str) -> None:
    tenant = _laid({M101: [_item("p1", **{P_CLAIM: weak})]})
    out = _resolve(tenant, client_name=NAME, claim_number=weak)
    assert out["ignored"] == [mr.FACT_CLAIM]
    assert out["verdict"] == mr.VERDICT_AMBIGUOUS and _minted() == 0
    assert _layout_requests(tenant) == []


def test_the_tool_takes_the_case_number() -> None:
    import inspect

    from smokeball_connector import vendor_invoice_tools

    assert "case_number" in inspect.signature(vendor_invoice_tools.resolve_invoice_matter).parameters
