"""ga4: conformance, the stdio round-trip, and the four reads against a fake
Data API client (no network, no credential)."""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from ga4_connector import server
from mcp import Client, StdioServerParameters
from operator_connector_sdk import conformance
from operator_connector_sdk.manifest import AuthModel, ConnectorManifest

ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = shutil.which("ga4-mcp")


def _manifest() -> ConnectorManifest:
    return ConnectorManifest.from_toml(ROOT / "manifest.toml")


def test_manifest() -> None:
    m = _manifest()
    assert m.name == "ga4" and m.capability == "Analytics"
    assert m.auth_model is AuthModel.CLIENT_CREDENTIALS
    assert {s.runtime_env for s in m.required_secrets} == {"GA4_SERVICE_ACCOUNT_JSON", "GA4_PROPERTY_ID"}


def test_conformance_all_reads() -> None:
    runtime_map = conformance.run_all(server.server, _manifest())
    assert runtime_map == {
        "mcp_ga4_run_report": "read",
        "mcp_ga4_top_pages": "read",
        "mcp_ga4_traffic_sources": "read",
        "mcp_ga4_month_over_month": "read",
    }


def test_no_tool_takes_a_property() -> None:
    for tool in server.server.tool_surface():
        assert not any("property" in p for p in tool.input_schema["properties"]), tool.name


def _val(v: str) -> SimpleNamespace:
    return SimpleNamespace(value=v)


class FakeClient:
    def __init__(self, rows: list[tuple[list[str], list[str]]], dims: list[str], mets: list[str]) -> None:
        self.rows = rows
        self.dims = dims
        self.mets = mets
        self.requests: list[Any] = []

    def run_report(self, request: Any) -> SimpleNamespace:
        self.requests.append(request)
        return SimpleNamespace(
            dimension_headers=[SimpleNamespace(name=d) for d in self.dims],
            metric_headers=[SimpleNamespace(name=m) for m in self.mets],
            rows=[
                SimpleNamespace(dimension_values=[_val(x) for x in d], metric_values=[_val(x) for x in m])
                for d, m in self.rows
            ],
            row_count=len(self.rows),
        )


@pytest.fixture(autouse=True)
def _property(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GA4_PROPERTY_ID", "123456789")


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeClient) -> FakeClient:
    monkeypatch.setattr(server, "_client", fake)
    return fake


def test_run_report_shapes_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(
        monkeypatch,
        FakeClient(
            [(["/blog/a", "A\x00title\nwith breaks"], ["120", "0.5"])],
            ["pagePath", "pageTitle"],
            ["screenPageViews", "engagementRate"],
        ),
    )
    out = server.run_report(
        metrics=["screenPageViews", "engagementRate"],
        dimensions=["pagePath", "pageTitle"],
        start_date="30daysAgo",
        end_date="today",
    )
    assert out["property"] == "properties/123456789"
    assert out["rows"] == [
        {"pagePath": "/blog/a", "pageTitle": "A title with breaks", "screenPageViews": 120, "engagementRate": 0.5}
    ]
    req = fake.requests[0]
    assert req.property == "properties/123456789"
    assert [m.name for m in req.metrics] == ["screenPageViews", "engagementRate"]
    assert req.date_ranges[0].start_date == "30daysAgo"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"metrics": [], "start_date": "today", "end_date": "today"},
        {"metrics": ["bad name"], "start_date": "today", "end_date": "today"},
        {"metrics": ["sessions"], "start_date": "2026-13-01", "end_date": "today"},
        {"metrics": ["sessions"], "start_date": "last month", "end_date": "today"},
    ],
)
def test_run_report_validates_before_calling(monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any]) -> None:
    fake = _install(monkeypatch, FakeClient([], [], []))
    with pytest.raises(ValueError):
        server.run_report(**kwargs)
    assert fake.requests == []


def test_long_strings_are_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeClient([(["x" * 5000], ["1"])], ["pageTitle"], ["activeUsers"]))
    out = server.run_report(metrics=["activeUsers"], dimensions=["pageTitle"], start_date="today", end_date="today")
    assert len(out["rows"][0]["pageTitle"]) == 200


def test_top_pages_orders_by_views(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(
        monkeypatch,
        FakeClient(
            [(["/", "Home"], ["900", "400", "1000"])],
            ["pagePath", "pageTitle"],
            ["screenPageViews", "activeUsers", "userEngagementDuration"],
        ),
    )
    out = server.top_pages(start_date="2026-09-01", end_date="2026-09-30", limit=5)
    assert out["rows"][0]["screenPageViews"] == 900
    req = fake.requests[0]
    assert req.limit == 5
    assert req.order_bys[0].metric.metric_name == "screenPageViews" and req.order_bys[0].desc


def test_traffic_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        FakeClient(
            [(["Organic Search"], ["300", "250", "200"])],
            ["sessionDefaultChannelGroup"],
            ["sessions", "activeUsers", "newUsers"],
        ),
    )
    out = server.traffic_sources(start_date="2026-09-01", end_date="2026-09-30")
    assert out["rows"][0]["sessionDefaultChannelGroup"] == "Organic Search"


def test_month_over_month(monkeypatch: pytest.MonkeyPatch) -> None:
    mets = server._MOM_METRICS
    fake = _install(
        monkeypatch,
        FakeClient(
            [
                (["current"], ["110", "50", "200", "500", "0.61"]),
                (["previous"], ["100", "0", "160", "400", "0.5"]),
            ],
            ["dateRange"],
            mets,
        ),
    )
    out = server.month_over_month("2026-01")
    assert out["month"] == "2026-01" and out["previous_month"] == "2025-12"
    assert out["metrics"]["activeUsers"] == {"current": 110, "previous": 100, "change_pct": 10.0}
    assert out["metrics"]["newUsers"]["change_pct"] is None
    ranges = fake.requests[0].date_ranges
    assert (ranges[0].start_date, ranges[0].end_date) == ("2026-01-01", "2026-01-31")
    assert (ranges[1].start_date, ranges[1].end_date) == ("2025-12-01", "2025-12-31")
    with pytest.raises(ValueError):
        server.month_over_month("2026-13")


def test_property_id_must_be_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GA4_PROPERTY_ID", "G-ABC123")
    with pytest.raises(server.Ga4ConfigError):
        server.property_name()


def test_key_accepts_raw_or_base64_and_refuses_other_shapes() -> None:
    key = {"type": "service_account", "client_email": "a@b.iam.gserviceaccount.com"}
    raw = json.dumps(key)
    assert server._load_key(raw) == key
    assert server._load_key(base64.b64encode(raw.encode()).decode()) == key
    for bad in ("not a key", json.dumps({"type": "authorized_user"}), base64.b64encode(b"[]").decode()):
        with pytest.raises(server.Ga4ConfigError):
            server._load_key(bad)


def test_missing_key_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_client", None)
    monkeypatch.delenv("GA4_SERVICE_ACCOUNT_JSON", raising=False)
    with pytest.raises(server.Ga4ConfigError):
        server.run_report(metrics=["sessions"], start_date="today", end_date="today")


@pytest.mark.skipif(_SCRIPT is None, reason="ga4-mcp console-script not on PATH")
def test_stdio_serves_over_console_script() -> None:
    async def _roundtrip() -> None:
        async with Client(StdioServerParameters(command=_SCRIPT)) as client:
            listed = await client.list_tools()
            assert {t.name for t in listed.tools} == {"run_report", "top_pages", "traffic_sources", "month_over_month"}

    anyio.run(_roundtrip)
