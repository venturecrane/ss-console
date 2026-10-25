"""The mcp:ga4 tool surface: four READ tools over the GA4 Data API.

``run_report`` is the general query; ``top_pages``, ``traffic_sources`` and
``month_over_month`` are the presets a monthly analytics note needs. The
property is fixed by the seat (``GA4_PROPERTY_ID``) and takes no argument, so a
turn cannot point the connector at another property.

String values (page titles, paths, source names) are cleaned and truncated
before they are returned: anyone who knows a site's measurement id can send a
hit carrying arbitrary page-title text, so these strings are not the firm's
words even though they come from the firm's own property.

The client is built lazily, so the tool surface introspects without
credentials.
"""

from __future__ import annotations

import base64
import binascii
import calendar
import json
import os
import re
from datetime import date
from typing import Any

from operator_connector_sdk.server import ConnectorServer

server = ConnectorServer("ga4")

SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
MAX_ROWS = 1000
MAX_METRICS = 10
MAX_DIMENSIONS = 9
_VALUE_CHARS = 200

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_:]{0,79}$")
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}|today|yesterday|\d{1,4}daysAgo)$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")

_client: Any = None


class Ga4ConfigError(ValueError):
    """The connector's env is missing or unusable (a seat wiring problem)."""


def _load_key(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text.startswith("{"):
        try:
            text = base64.b64decode(text, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise Ga4ConfigError("GA4_SERVICE_ACCOUNT_JSON is neither JSON nor base64-encoded JSON") from exc
    try:
        info = json.loads(text)
    except ValueError as exc:
        raise Ga4ConfigError("GA4_SERVICE_ACCOUNT_JSON is not valid JSON") from exc
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise Ga4ConfigError("GA4_SERVICE_ACCOUNT_JSON is not a service-account key")
    return info


def property_name() -> str:
    pid = os.environ.get("GA4_PROPERTY_ID", "").strip()
    if not pid.isdigit():
        raise Ga4ConfigError("GA4_PROPERTY_ID must be the numeric GA4 property id")
    return f"properties/{pid}"


def _get_client() -> Any:
    global _client
    if _client is None:
        raw = os.environ.get("GA4_SERVICE_ACCOUNT_JSON", "")
        if not raw.strip():
            raise Ga4ConfigError("ga4 connector is not configured: missing GA4_SERVICE_ACCOUNT_JSON")
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_info(_load_key(raw), scopes=[SCOPE])
        _client = BetaAnalyticsDataClient(credentials=creds)
    return _client


# ---- request building -----------------------------------------------------
def _names(values: list[str] | None, kind: str, cap: int) -> list[str]:
    out = [str(v).strip() for v in (values or []) if str(v).strip()]
    if len(out) > cap:
        raise ValueError(f"at most {cap} {kind}s per report")
    for v in out:
        if not _NAME_RE.fullmatch(v):
            raise ValueError(f"{kind} {v!r} is not a GA4 API name (for example activeUsers, pagePath)")
    return out


def _date(value: str, label: str) -> str:
    v = (value or "").strip()
    if not _DATE_RE.fullmatch(v):
        raise ValueError(f"{label} must be YYYY-MM-DD, today, yesterday or NdaysAgo")
    if "-" in v:
        date.fromisoformat(v)
    return v


def _clean(value: str) -> str:
    return _CONTROL_RE.sub(" ", value)[:_VALUE_CHARS]


def _run(
    *,
    metrics: list[str],
    dimensions: list[str],
    date_ranges: list[tuple[str, str, str | None]],
    limit: int,
    order_metric: str | None = None,
) -> dict[str, Any]:
    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, OrderBy, RunReportRequest

    request = RunReportRequest(
        property=property_name(),
        metrics=[Metric(name=m) for m in metrics],
        dimensions=[Dimension(name=d) for d in dimensions],
        date_ranges=[DateRange(start_date=s, end_date=e, name=n or "") for s, e, n in date_ranges],
        limit=max(1, min(int(limit), MAX_ROWS)),
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name=order_metric), desc=True)] if order_metric else [],
    )
    response = _get_client().run_report(request=request)
    dim_names = [h.name for h in response.dimension_headers]
    met_names = [h.name for h in response.metric_headers]
    rows: list[dict[str, Any]] = []
    for row in response.rows:
        rec: dict[str, Any] = {}
        for name, val in zip(dim_names, row.dimension_values, strict=False):
            rec[name] = _clean(val.value)
        for name, val in zip(met_names, row.metric_values, strict=False):
            rec[name] = _number(val.value)
        rows.append(rec)
    return {
        "property": property_name(),
        "dimensions": dim_names,
        "metrics": met_names,
        "rows": rows,
        "row_count": int(getattr(response, "row_count", len(rows)) or len(rows)),
    }


def _number(value: str) -> float | int | str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return _clean(str(value))
    return int(f) if f.is_integer() else round(f, 4)


# ---- tools ----------------------------------------------------------------
@server.tool()
def run_report(
    metrics: list[str],
    start_date: str,
    end_date: str,
    dimensions: list[str] | None = None,
    limit: int = 100,
) -> Any:
    """Run a GA4 report on the firm's property. ``metrics`` and ``dimensions``
    are GA4 API names (metrics such as activeUsers, sessions, screenPageViews,
    newUsers, engagementRate; dimensions such as pagePath, pageTitle,
    sessionDefaultChannelGroup, sessionSourceMedium, date, city). Dates are
    YYYY-MM-DD, today, yesterday or NdaysAgo. Returns ``{property, dimensions,
    metrics, rows: [{<dimension>: str, <metric>: number}], row_count}``."""
    m = _names(metrics, "metric", MAX_METRICS)
    if not m:
        raise ValueError("at least one metric is required")
    d = _names(dimensions, "dimension", MAX_DIMENSIONS)
    return _run(
        metrics=m,
        dimensions=d,
        date_ranges=[(_date(start_date, "start_date"), _date(end_date, "end_date"), None)],
        limit=limit,
    )


@server.tool()
def top_pages(start_date: str, end_date: str, limit: int = 10) -> Any:
    """The most viewed pages between two dates: path, title, views, users and
    average engagement time, most viewed first."""
    return _run(
        metrics=["screenPageViews", "activeUsers", "userEngagementDuration"],
        dimensions=["pagePath", "pageTitle"],
        date_ranges=[(_date(start_date, "start_date"), _date(end_date, "end_date"), None)],
        limit=min(int(limit), 100),
        order_metric="screenPageViews",
    )


@server.tool()
def traffic_sources(start_date: str, end_date: str) -> Any:
    """Where visitors came from between two dates: sessions and users by
    default channel group (Organic Search, Direct, Referral, Organic Social,
    and so on), most sessions first."""
    return _run(
        metrics=["sessions", "activeUsers", "newUsers"],
        dimensions=["sessionDefaultChannelGroup"],
        date_ranges=[(_date(start_date, "start_date"), _date(end_date, "end_date"), None)],
        limit=50,
        order_metric="sessions",
    )


_MOM_METRICS = ["activeUsers", "newUsers", "sessions", "screenPageViews", "engagementRate"]


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"


@server.tool()
def month_over_month(month: str) -> Any:
    """Compare one calendar month (``YYYY-MM``) with the month before it:
    active users, new users, sessions, page views and engagement rate, each
    with the previous value and the percent change (null when the previous
    month is zero)."""
    m = _MONTH_RE.fullmatch((month or "").strip())
    if not m:
        raise ValueError("month must be YYYY-MM")
    year, mon = int(m.group(1)), int(m.group(2))
    if not 1 <= mon <= 12:
        raise ValueError("month must be YYYY-MM")
    prev_year, prev_mon = (year, mon - 1) if mon > 1 else (year - 1, 12)
    cur_start, cur_end = _month_bounds(year, mon)
    prev_start, prev_end = _month_bounds(prev_year, prev_mon)
    report = _run(
        metrics=_MOM_METRICS,
        dimensions=[],
        date_ranges=[(cur_start, cur_end, "current"), (prev_start, prev_end, "previous")],
        limit=10,
    )
    # With two named date ranges GA4 adds a dateRange dimension to each row.
    by_range = {str(r.get("dateRange", "")): r for r in report["rows"]}
    cur = by_range.get("current", {})
    prev = by_range.get("previous", {})
    out: dict[str, Any] = {}
    for name in _MOM_METRICS:
        c, p = cur.get(name, 0), prev.get(name, 0)
        change = None
        if isinstance(c, (int, float)) and isinstance(p, (int, float)) and p:
            change = round((c - p) / p * 100, 1)
        out[name] = {"current": c, "previous": p, "change_pct": change}
    return {
        "property": report["property"],
        "month": f"{year:04d}-{mon:02d}",
        "previous_month": f"{prev_year:04d}-{prev_mon:02d}",
        "metrics": out,
    }
