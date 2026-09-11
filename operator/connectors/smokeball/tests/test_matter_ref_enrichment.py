"""Unit coverage for ``matterNumber`` / ``matterCaption`` enrichment on tasks and events.

A Smokeball task or event carries its matter as ``matter: {href, id, rel}`` — a
GUID and nothing else. The human-readable number lives on the matter record, so
rendering "2026-PI-101" beside a task requires a matter.id -> matter.number JOIN.
Nothing performed that join in code, so the model performed it in context on
every run and re-derived it differently on different days (2026-07-31 provenance
audit: one file GUID carried two different matter numbers across two days; a
third matter's discovery was attributed to a lookalike matter holding none of it).

``server._attach_matter_ref`` performs the join once, in code, and returns the
number as a field the skill can cite instead of compose.

The fail-safe direction is load-bearing and pinned below: an unresolved ref
attaches NOTHING. A task with no ``matterNumber`` gives the model nothing to
copy — enrichment can fail to supply a number, never supply a wrong one.

No live Smokeball calls: an ``httpx.MockTransport`` is injected so the real
resolution logic runs against scripted responses.
"""

from __future__ import annotations

import httpx

from smokeball_connector import server as srv
from smokeball_connector.client import SmokeballClient

M1 = "f220c8e4-eab5-4fd9-8f1d-0becf715b390"
M2 = "062d73bd-4d91-41a7-8160-34bea8f7f81b"


def _mock_client(handler) -> SmokeballClient:
    client = SmokeballClient(region="us", environment="staging", client_id="cid", client_secret="sec", api_key="apikey")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _handler(matters: dict[str, dict], captured: list[httpx.Request] | None = None):
    """Serve /matters/<id> from ``matters``; 404 anything absent. Party contacts
    are not scripted, so caption composition no-ops and the tests isolate number
    resolution."""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        path = request.url.path
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        if "/matters/" in path:
            mid = path.rsplit("/", 1)[-1]
            if mid in matters:
                return httpx.Response(200, json=matters[mid])
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json={"ok": True})

    return handler


def _task(matter_id: str, subject: str = "Response to RFP Set One") -> dict:
    return {"id": "t-1", "subject": subject, "matter": {"id": matter_id, "rel": "matter"}}


def _matter_gets(captured: list[httpx.Request]) -> list[httpx.Request]:
    return [r for r in captured if "/matters/" in r.url.path]


# ---- The join happens in code ---------------------------------------------


def test_attaches_number_from_matter_id() -> None:
    client = _mock_client(_handler({M1: {"id": M1, "number": "2026-PI-101"}}))
    task = _task(M1)
    srv._attach_matter_ref(client, task)
    assert task["matterNumber"] == "2026-PI-101"


def test_the_guid_decides_the_number_not_the_surrounding_text() -> None:
    """The audit's exact failure: a task on 062d73bd was reported as 2026-PI-107.
    Enrichment resolves the GUID, so the number cannot drift from the binding."""
    client = _mock_client(_handler({M2: {"id": M2, "number": "2026-PI-106"}}))
    task = _task(M2, subject="RFP Set One - Halverson to Bell")
    srv._attach_matter_ref(client, task)
    assert task["matterNumber"] == "2026-PI-106"


def test_attaches_caption_when_composable() -> None:
    matter = {"id": M1, "number": "2026-PI-101", "caption": "Alvarez v. Draper"}
    client = _mock_client(_handler({M1: matter}))
    task = _task(M1)
    srv._attach_matter_ref(client, task)
    assert task["matterCaption"] == "Alvarez v. Draper"


# ---- Fail-safe: attach nothing rather than something wrong -----------------


def test_unresolvable_matter_attaches_nothing() -> None:
    client = _mock_client(_handler({}))  # every /matters/<id> 404s
    task = _task(M1)
    srv._attach_matter_ref(client, task)
    assert "matterNumber" not in task
    assert "matterCaption" not in task


def test_matter_without_a_number_attaches_nothing() -> None:
    client = _mock_client(_handler({M1: {"id": M1}}))
    task = _task(M1)
    srv._attach_matter_ref(client, task)
    assert "matterNumber" not in task


def test_task_with_no_matter_ref_is_untouched() -> None:
    client = _mock_client(_handler({M1: {"id": M1, "number": "2026-PI-101"}}))
    task = {"id": "t-1", "subject": "Firm admin"}
    srv._attach_matter_ref(client, task)
    assert task == {"id": "t-1", "subject": "Firm admin"}


def test_enrichment_never_raises_on_a_broken_read() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        return httpx.Response(500, json={"error": "upstream exploded"})

    client = _mock_client(boom)
    task = _task(M1)
    srv._attach_matter_ref(client, task)  # must not raise
    assert "matterNumber" not in task


def test_non_dict_item_is_tolerated() -> None:
    client = _mock_client(_handler({}))
    srv._attach_matter_ref(client, "not a dict")  # must not raise


# ---- List path: shared cache and bounded lookups --------------------------


def test_list_shares_one_lookup_across_rows_on_the_same_matter() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(_handler({M1: {"id": M1, "number": "2026-PI-101"}}, captured))
    resp = {"value": [_task(M1), _task(M1), _task(M1)]}
    srv._attach_matter_refs_to_list(client, resp)
    assert [t["matterNumber"] for t in resp["value"]] == ["2026-PI-101"] * 3
    assert len(_matter_gets(captured)) == 1


def test_list_resolves_each_distinct_matter() -> None:
    client = _mock_client(_handler({M1: {"id": M1, "number": "2026-PI-101"}, M2: {"id": M2, "number": "2026-PI-106"}}))
    resp = {"value": [_task(M1), _task(M2)]}
    srv._attach_matter_refs_to_list(client, resp)
    assert [t["matterNumber"] for t in resp["value"]] == ["2026-PI-101", "2026-PI-106"]


def test_list_lookup_budget_is_bounded() -> None:
    captured: list[httpx.Request] = []
    matters = {f"m-{i}": {"id": f"m-{i}", "number": f"2026-PI-{i:03d}"} for i in range(60)}
    client = _mock_client(_handler(matters, captured))
    resp = {"value": [_task(f"m-{i}") for i in range(60)]}
    srv._attach_matter_refs_to_list(client, resp)
    assert len(_matter_gets(captured)) <= srv._MATTER_REF_MAX_LOOKUPS
    # Rows past the budget carry no number — they must not carry a guessed one.
    assert all("matterNumber" not in t or t["matterNumber"].startswith("2026-PI-") for t in resp["value"])


def test_list_tolerates_a_non_envelope_response() -> None:
    client = _mock_client(_handler({}))
    srv._attach_matter_refs_to_list(client, ["not", "an", "envelope"])  # must not raise
    srv._attach_matter_refs_to_list(client, {"value": "not a list"})  # must not raise
