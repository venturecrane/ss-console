"""Unit coverage for the composed matter ``caption`` field (ss churn fix).

The overlay's tier-2 citation gate blocks agent memos containing a case-name
("Alvarez v. Draper") unless the caption was READ this session, but Smokeball
matter reads carry parties only as UUID refs — so the caption never reaches the
overlay harvester and the matter's OWN caption gets refused → redraft churn.
``server._attach_caption`` composes the caption from the matter's own party
contacts and returns it as a ``caption`` field so the harvester catches it.

No live Smokeball calls: an ``httpx.MockTransport`` is injected so the real
contact-resolution logic runs against scripted responses. A final test imports
the byte-identical ``citation_filter`` twin and asserts the emitted string is
BOTH catchable (the harvester will register it) AND exempting (a memo naming the
matter by that caption is then allowed) — locking the emission form to the
deployed allowlist contract.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx

from smokeball_connector import server as srv
from smokeball_connector.client import SmokeballClient


def _mock_client(handler, captured: list[httpx.Request] | None = None) -> SmokeballClient:
    client = SmokeballClient(region="us", environment="staging", client_id="cid", client_secret="sec", api_key="apikey")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _contacts_handler(contacts: dict[str, dict], captured: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        path = request.url.path
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        if "/contacts/" in path:
            cid = path.rsplit("/", 1)[-1]
            if cid in contacts:
                return httpx.Response(200, json=contacts[cid])
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json={"ok": True})

    return handler


def _person(last: str) -> dict:
    return {"person": {"firstName": "Given", "lastName": last}}


def _company(name: str) -> dict:
    return {"company": {"name": name}}


def _contact_gets(captured: list[httpx.Request]) -> list[httpx.Request]:
    return [r for r in captured if "/contacts/" in r.url.path]


# ---- get_matter composition ----------------------------------------------


def test_person_v_person_plaintiff_side() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(_contacts_handler({"c1": _person("Alvarez"), "d1": _person("Draper")}, captured), captured)
    matter = {"clientIds": ["c1"], "otherSideIds": ["d1"], "matterType": {"name": "Motor Vehicle Accident - Plaintiff"}}
    srv._attach_caption(client, matter)
    assert matter["caption"] == "Alvarez v. Draper"
    assert len(_contact_gets(captured)) == 2


def test_defense_side_flips_orientation() -> None:
    # Firm's client is the defendant; the caption is still Plaintiff v. Defendant.
    client = _mock_client(_contacts_handler({"cl": _person("Draper"), "op": _person("Alvarez")}))
    matter = {"clientIds": ["cl"], "otherSideIds": ["op"], "matterType": {"name": "Motor Vehicle Accident - Defendant"}}
    srv._attach_caption(client, matter)
    assert matter["caption"] == "Alvarez v. Draper"


def test_multidef_appends_et_al_and_resolves_only_first_defendant() -> None:
    captured: list[httpx.Request] = []
    contacts = {"c1": _person("Bell"), "d1": _company("Acme Corp"), "d2": _person("Roe"), "d3": _person("Doe")}
    client = _mock_client(_contacts_handler(contacts, captured), captured)
    matter = {
        "clientIds": ["c1"],
        "otherSideIds": ["d1", "d2", "d3"],
        "matterType": {"name": "Personal Injury - Plaintiff"},
    }
    srv._attach_caption(client, matter)
    assert matter["caption"] == "Bell v. Acme Corp et al."
    assert len(_contact_gets(captured)) == 2  # plaintiff + first defendant only


def test_lead_no_otherside_yields_no_caption() -> None:
    client = _mock_client(_contacts_handler({"c1": _person("Alvarez")}))
    matter = {
        "clientIds": ["c1"],
        "otherSideIds": [],
        "matterType": {"name": "Personal Injury - Plaintiff"},
        "isLead": True,
    }
    srv._attach_caption(client, matter)
    assert "caption" not in matter


def test_missing_contact_yields_no_caption_no_raise() -> None:
    client = _mock_client(_contacts_handler({"c1": _person("Alvarez")}))  # d1 -> 404
    matter = {"clientIds": ["c1"], "otherSideIds": ["d1"], "matterType": {"name": "X - Plaintiff"}}
    srv._attach_caption(client, matter)
    assert "caption" not in matter


def test_blank_lastname_yields_no_caption() -> None:
    client = _mock_client(_contacts_handler({"c1": _person("Alvarez"), "d1": _person("   ")}))
    matter = {"clientIds": ["c1"], "otherSideIds": ["d1"], "matterType": {"name": "X - Plaintiff"}}
    srv._attach_caption(client, matter)
    assert "caption" not in matter


def test_poisoned_label_with_cite_shape_is_rejected() -> None:
    # A party label that itself carries a reporter-cite-shaped number run is dropped
    # (fail-safe: no caption rather than a poisoned one). Independent of this, the
    # overlay never allowlists reporter-cite/statute/rule patterns.
    client = _mock_client(_contacts_handler({"c1": _person("Alvarez"), "d1": _person("Draper 410 US 113")}))
    matter = {"clientIds": ["c1"], "otherSideIds": ["d1"], "matterType": {"name": "X - Plaintiff"}}
    srv._attach_caption(client, matter)
    assert "caption" not in matter


def test_company_flat_shape_fallback() -> None:
    client = _mock_client(_contacts_handler({"c1": _person("Whitfield"), "d1": _company("Pacific Freight")}))
    matter = {"clientIds": ["c1"], "otherSideIds": ["d1"], "matterType": {"name": "Personal Injury - Plaintiff"}}
    srv._attach_caption(client, matter)
    assert matter["caption"] == "Whitfield v. Pacific Freight"


# ---- list_matters composition (bounded + deduped) -------------------------


def test_list_lookup_cap_bounds_contact_gets() -> None:
    captured: list[httpx.Request] = []
    contacts: dict[str, dict] = {}
    items = []
    for i in range(50):  # 50 matters x 2 distinct parties = 100 potential lookups
        suffix = chr(65 + i // 26) + chr(65 + i % 26)  # digit-free distinct surnames ("AA", "AB", ...)
        contacts[f"c{i}"] = _person(f"Plaintiff{suffix}")
        contacts[f"d{i}"] = _person(f"Defendant{suffix}")
        items.append({"clientIds": [f"c{i}"], "otherSideIds": [f"d{i}"], "matterType": {"name": "X - Plaintiff"}})
    client = _mock_client(_contacts_handler(contacts, captured), captured)
    resp = {"value": items}
    srv._attach_captions_to_list(client, resp)
    assert len(_contact_gets(captured)) == srv._CAPTION_MAX_LOOKUPS  # capped at 40
    assert sum(1 for m in items if "caption" in m) == srv._CAPTION_MAX_LOOKUPS // 2  # 20 fully resolved


def test_list_shared_contacts_dedupe() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(_contacts_handler({"c1": _person("Alvarez"), "d1": _person("Draper")}, captured), captured)
    items = [{"clientIds": ["c1"], "otherSideIds": ["d1"], "matterType": {"name": "X - Plaintiff"}} for _ in range(3)]
    resp = {"value": items}
    srv._attach_captions_to_list(client, resp)
    assert len(_contact_gets(captured)) == 2  # deduped across all 3 matters
    assert all(m["caption"] == "Alvarez v. Draper" for m in items)


# ---- canonicalization proof vs the deployed citation-filter twin ----------


def _load_citation_filter():
    path = Path(__file__).resolve().parents[3] / "safety-substrate" / "citation_filter.py"
    spec = importlib.util.spec_from_file_location("ss_citation_filter", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass field resolution needs the module registered
    spec.loader.exec_module(mod)
    return mod


def test_emitted_caption_is_catchable_and_exempting() -> None:
    cf = _load_citation_filter()
    caption = "Alvarez v. Draper"
    # 1) The caption, as it appears embedded in a stringified read result, IS a
    #    case-name hit — so the overlay harvester will register it.
    assert cf.contains_citation(f"...'caption': '{caption}'...") is True
    # 2) A memo naming the matter by that caption is blocked WITHOUT the allowlist,
    #    and EXEMPT once the caption is registered — the whole point of the fix.
    memo = "Discovery capture on Alvarez v. Draper: SROG set 1 served 2026-06-26."
    assert cf.contains_citation(memo) is True
    assert cf.contains_citation(memo, allowed_case_names=[cf.canonical_caption(caption)]) is False
    # 3) Safety unchanged: a reporter cite is NEVER exempt, even allowlisted.
    fabricated = "As held in Alvarez v. Draper, 410 U.S. 113 (1973)."
    assert cf.contains_citation(fabricated, allowed_case_names=[cf.canonical_caption(caption)]) is True
