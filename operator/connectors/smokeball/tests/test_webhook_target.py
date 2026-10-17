"""``create_webhook_subscription`` only ever registers the seat's own gate
(webhook_target.py, 2026-10-17).

The tool took ``notification_url`` from the model and sent it straight to
Smokeball as ``eventNotificationUrl``, signed with the gate's own
``WEBHOOK_SECRET_SMOKEBALL``. One injected email turn could have registered a
persistent feed of the firm's matter events to any host. The callback is now the
``webhook_url`` authored on the seat's enabled ``mcp:smokeball`` connector;
anything else, and the absence of an authored URL, is refused before any vendor
call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from smokeball_connector import server
from smokeball_connector.webhook_target import seat_webhook_url

GATE = "https://hermes-pilot-smokeball.fly.dev/webhooks/smokeball"


class _Recorder:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def request(self, method: str, path: str, *, json: dict | None = None) -> Any:
        self.posts.append({"method": method, "path": path, "json": dict(json or {})})
        return {"id": "wh-1"}


def _author(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> Path:
    cfg = tmp_path / "customer.yaml"
    cfg.write_text(text)
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg))
    return cfg


def _seat(url: str = GATE, backend: str = "mcp:smokeball", enabled: str = "true") -> str:
    return (
        "connectors:\n"
        "  PracticeManagement:\n"
        f"    backend: {backend}\n"
        f"    enabled: {enabled}\n"
        f"    webhook_url: '{url}'\n"
        "  Email:\n"
        "    backend: mcp:agentmail\n"
        "    enabled: true\n"
        "    webhook_url: 'https://hermes-pilot-smokeball.fly.dev/webhooks/agentmail'\n"
    )


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    r = _Recorder()
    monkeypatch.setattr(server, "_get_client", lambda: r)
    monkeypatch.setattr(server, "prove_webhook", lambda _c, _b, result: result)
    monkeypatch.setenv("WEBHOOK_SECRET_SMOKEBALL", "gate-secret")
    return r


def test_a_foreign_notification_url_is_refused_before_any_vendor_call(
    rec: _Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _author(tmp_path, monkeypatch, _seat())
    with pytest.raises(ValueError, match="not this seat's webhook gate"):
        server.create_webhook_subscription("x", ["matter.updated"], "https://attacker.example/collect")
    with pytest.raises(ValueError, match="not this seat's webhook gate"):
        server.create_webhook_subscription("x", ["matter.updated"], GATE + "?then=https://attacker.example")
    assert rec.posts == []


def test_the_subscription_always_targets_the_authored_gate(
    rec: _Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _author(tmp_path, monkeypatch, _seat())
    server.create_webhook_subscription("op", ["matter.updated"])
    server.create_webhook_subscription("op", ["matter.updated"], GATE)
    assert [p["json"]["eventNotificationUrl"] for p in rec.posts] == [GATE, GATE]
    assert rec.posts[0]["json"]["key"] == "gate-secret"


@pytest.mark.parametrize(
    "text",
    [
        "",  # nothing authored
        "connectors: {}\n",
        _seat(enabled="false"),  # the smokeball connector is off
        _seat(backend="mcp:clio"),  # no smokeball connector at all
        _seat(url="http://hermes-pilot-smokeball.fly.dev/webhooks/smokeball"),  # not https
        _seat(url="https://hermes-pilot-smokeball.fly.dev/webhooks/agentmail"),  # not the smokeball gate
    ],
)
def test_no_authored_gate_refuses_even_with_a_caller_url(
    rec: _Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    _author(tmp_path, monkeypatch, text)
    with pytest.raises(ValueError, match="no authored Smokeball webhook gate URL"):
        server.create_webhook_subscription("x", ["matter.updated"], "https://attacker.example/collect")
    assert rec.posts == []


def test_an_unreadable_customer_yaml_is_no_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(tmp_path / "absent.yaml"))
    assert seat_webhook_url() is None


def test_two_different_smokeball_gates_are_ambiguous_and_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _seat() + (
        "  Second:\n"
        "    backend: mcp:smokeball\n"
        "    enabled: true\n"
        "    webhook_url: 'https://elsewhere.example/webhooks/smokeball'\n"
    )
    _author(tmp_path, monkeypatch, text)
    assert seat_webhook_url() is None


def test_every_authored_seat_resolves_to_its_own_gate() -> None:
    """The authored seats that declare a Smokeball webhook keep working."""
    root = Path(__file__).resolve().parents[3] / "customers"
    found = 0
    for cfg in sorted(root.glob("*/customer.yaml")):
        text = cfg.read_text()
        if "/webhooks/smokeball" not in text or cfg.parent.name.startswith("_"):
            continue
        url = seat_webhook_url(str(cfg))
        assert url == f"https://hermes-{cfg.parent.name}.fly.dev/webhooks/smokeball", cfg
        found += 1
    assert found >= 1
