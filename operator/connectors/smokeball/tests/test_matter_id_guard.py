"""The client refuses a /matters/{id} path whose id is not a UUID - before any
HTTP leaves the process.

Why (2026-09-01, the A&P hard stop): the agent passed matter NUMBERS (10006,
202248) to id-shaped paths, the vendor 404'd three times in a burst, Hermes'
derivative "MCP server unreachable" circuit opened (three business errors
manufacture it - see the overlay's test_connector_signatures.py), every
subsequent Smokeball call failed instantly, and the sticky-stop ladder ran
5 -> 8 consecutive failures and HARD_STOPped a production seat. The guard
turns mistake #1 into one instructive refusal the agent can act on.
"""

from __future__ import annotations

import pytest

from smokeball_connector.client import SmokeballClient


def _client() -> SmokeballClient:
    return SmokeballClient(
        region="us",
        environment="staging",
        client_id="cid",
        client_secret="sec",
        api_key="key",
    )


@pytest.mark.parametrize(
    "path",
    [
        "/matters/10006/documents/files",
        "/matters/202248/documents/files",
        "/matters/10006",
        "/matters/2026-PI-102/memos",
    ],
)
def test_a_matter_number_in_an_id_path_is_refused_without_http(path: str) -> None:
    with pytest.raises(ValueError, match="matter number"):
        _client().get(path)


def test_a_uuid_path_passes_the_guard() -> None:
    # The guard lets a UUID through; the request then fails on auth (no real
    # tenant here), which proves the refusal above happened BEFORE the wire.
    client = _client()
    with pytest.raises(Exception) as exc:
        client.get("/matters/a5899808-89ac-4390-bdc3-17641c4c968c")
    assert "matter number" not in str(exc.value)


def test_the_guard_is_deliberately_narrow_alpha_ids_pass() -> None:
    # #2673 doctrine: refuse only what CANNOT be right. Synthetic ids like
    # "m-9" (the test suites') and unknown alpha formats stay allowed - a
    # wrong-but-plausible id earns the vendor's own 404, not a local refusal.
    client = _client()
    with pytest.raises(Exception) as exc:
        client.get("/matters/m-9/documents/files")
    assert "matter number" not in str(exc.value)


def test_bare_matters_listing_is_untouched() -> None:
    client = _client()
    with pytest.raises(Exception) as exc:
        client.get("/matters", Limit=1)
    assert "matter number" not in str(exc.value)
