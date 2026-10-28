"""The async handoff door, made real (ss-console #2616): translate materializes
a `handoff` route whenever the MCP bearer exists, the prompt carries the task,
the gate stops masking non-2xx forwards, and the medchron submit tool passes
the append selection through."""

from __future__ import annotations

import json

import pytest

from bootstrap import translate as _wh
from tests.conftest import FakePluginContext, load_plugin

# ---------------------------------------------------------------------------
# translate: the handoff route
# ---------------------------------------------------------------------------


def test_handoff_route_materialized_whenever_the_mcp_secret_exists(monkeypatch):
    # Deliberately NOT gated on mcp_connector.enabled: the console's
    # operator_handoff_task and the seat's runner daemon are wired at
    # provision, not authored per-connector.
    monkeypatch.setenv("WEBHOOK_SECRET_MCP", "shh-mcp")
    out = _wh._materialize_webhook_platform({"connectors": {}, "webhook_triggers": []})
    route = out["webhook"]["extra"]["routes"]["handoff"]
    assert route["secret"] == "shh-mcp"
    assert route["events"] == [] and route["skills"] == []
    assert "{task}" in route["prompt"] and "{handoff_id}" in route["prompt"]
    # The provenance claim and the initiation grant the deliver mode rests on.
    assert "DATA" in route["prompt"]
    assert "IS the initiation" in route["prompt"]


def test_handoff_route_fail_closed_without_the_secret(monkeypatch):
    monkeypatch.delenv("WEBHOOK_SECRET_MCP", raising=False)
    out = _wh._materialize_webhook_platform({"connectors": {}, "webhook_triggers": []})
    assert out == {}


def test_handoff_route_rides_beside_the_mcp_route(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET_MCP", "shh-mcp")
    cust = {"connectors": {}, "webhook_triggers": [], "mcp_connector": {"enabled": True}}
    routes = _wh._materialize_webhook_platform(cust)["webhook"]["extra"]["routes"]
    assert set(routes) == {"mcp", "handoff"}
    assert routes["handoff"]["prompt"] != routes["mcp"]["prompt"]


# ---------------------------------------------------------------------------
# the handoff prompt names the skill file (the 2026-10-28 pilot defect)
# ---------------------------------------------------------------------------
#
# A casefile DELIVER wake ran on a webhook turn with no skill_view and no skill
# index, under a prompt that said only "using your skills exactly as their files
# direct". The model guessed paths, read a skills root (refused), and wrote a
# digest memo instead of sending the reply. Each test below fails on that prompt.

_ENABLED = "case-file-manager"
_DISABLED = "demand-letter-drafter"
_PERSONA = {
    "slug": "operator",
    "name": "Operator",
    "status": "active",
    "skills": [
        {"name": _ENABLED, "enabled": True},
        {"name": _DISABLED, "enabled": False},
    ],
}
_HANDOFF_YAML = f"""
customer_id: example
vertical: law-firm
personas:
  - slug: operator
    skills:
      - name: {_ENABLED}
        enabled: true
      - name: {_DISABLED}
        enabled: false
"""


def _persona_handoff_prompt(monkeypatch) -> str:
    """Through _persona_config, as the seat's profile config is built."""
    monkeypatch.setenv("WEBHOOK_SECRET_MCP", "shh-mcp")
    config = _wh._persona_config(dict(_PERSONA), {"customer_id": "example"}, {})
    return config["platforms"]["webhook"]["extra"]["routes"]["handoff"]["prompt"]


def _skill_path(prompt: str, skill: str) -> str:
    import re

    found = re.search(r"read_file on (/\S+)/<name>/SKILL\.md", prompt)
    assert found, "the handoff prompt names no skill file to read"
    return f"{found.group(1)}/{skill}/SKILL.md"


def test_the_handoff_prompt_names_the_personas_own_skill_file(monkeypatch):
    prompt = _persona_handoff_prompt(monkeypatch)
    assert (
        _skill_path(prompt, _ENABLED) == f"/opt/data/profiles/operator/skills/{_ENABLED}/SKILL.md"
    )
    # The image copy goes stale on a skills-only release; it is never the one named.
    assert "/app/skills" not in prompt
    assert "FIRST tool call is read_file" in prompt
    assert "{task}" in prompt and "{handoff_id}" in prompt and "<<" not in prompt


def test_the_handoff_prompt_authors_the_failure_branch_and_fences_the_digest_memo(monkeypatch):
    prompt = _persona_handoff_prompt(monkeypatch)
    assert "If that file cannot be read, say so in one sentence and do nothing else" in prompt
    assert "no memo, no digest entry, no email" in prompt
    assert "Digest home in your instructions is for the daily needs-you digest only" in prompt
    assert "never stands in for a job's DELIVER" in prompt
    # "Report through your authored channels" was the phrase that read as license to memo.
    assert "authored channels" not in prompt


def test_the_digest_home_section_says_it_is_never_a_deliver():
    section = _wh._digest_home_soul_section({"digest": {"home_matter_id": "ops-1"}})
    assert "holds the daily digest only" in section
    assert "a job's DELIVER" in section


def test_the_named_skill_file_passes_both_read_fences_only_when_enabled(monkeypatch, tmp_path):
    """The path the prompt names must actually be readable for an enabled skill
    (the omitted-skill fence, and the record-store allowlist on a records seat,
    where HERMES_HOME is the profile home), and refused for a disabled one."""
    prompt = _persona_handoff_prompt(monkeypatch)
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(_HANDOFF_YAML)
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(yaml_path))
    monkeypatch.setenv("HERMES_HOME", "/opt/data/profiles/operator")
    skill_fence = load_plugin("hermes-smd-initiation").skill_fence
    path_fence = load_plugin("hermes-smd-record-store").path_fence

    enabled = {"path": _skill_path(prompt, _ENABLED)}
    assert skill_fence.on_pre_tool_call(tool_name="read_file", args=enabled, session_id="s") is None
    assert path_fence.check("read_file", enabled, [], {}) is None

    disabled = {"path": _skill_path(prompt, _DISABLED)}
    verdict = skill_fence.on_pre_tool_call(tool_name="read_file", args=disabled, session_id="s")
    assert verdict is not None and verdict["action"] == "block"


# ---------------------------------------------------------------------------
# gate: any non-2xx forward is a retryable failure
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def read(self) -> bytes:
        return b""


class _FakeConnection:
    status = 200
    last_request: dict = {}

    def __init__(self, *args, **kwargs) -> None:
        pass

    def request(self, method, path, body=None, headers=None) -> None:
        _FakeConnection.last_request = {
            "method": method,
            "path": path,
            "body": body,
            "headers": dict(headers or {}),
        }

    def getresponse(self) -> _FakeResponse:
        return _FakeResponse(_FakeConnection.status)

    def close(self) -> None:
        pass


class _FakeHandler:
    """Just enough of the BaseHTTPRequestHandler surface for _handle_handoff."""

    def __init__(self, body: dict, bearer: str) -> None:
        import io

        raw = json.dumps(body).encode()
        self.headers = {"Authorization": f"Bearer {bearer}", "Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.answers: list[tuple[int, dict]] = []

    def _json(self, status: int, payload: dict) -> None:
        self.answers.append((status, payload))


@pytest.fixture
def gate(monkeypatch):
    import webhook_gate

    monkeypatch.setenv("WEBHOOK_SECRET_MCP", "shh-mcp")
    monkeypatch.setattr(webhook_gate.http.client, "HTTPConnection", _FakeConnection)
    return webhook_gate


def _handoff(gate_mod, status: int) -> tuple[int, dict]:
    _FakeConnection.status = status
    h = _FakeHandler({"handoff_id": "medchron-01A", "task": "run deliver mode"}, "shh-mcp")
    gate_mod._Handler._handle_handoff(h)
    return h.answers[-1]


def test_a_2xx_forward_is_accepted(gate):
    code, payload = _handoff(gate, 200)
    assert code == 202 and payload["accepted"] is True


def test_the_forward_hop_is_signed_with_the_adapters_hmac_v2(gate):
    # The adapter at v0.21 warns that body-only X-Webhook-Signature "is vulnerable
    # to replay attacks"; the gate signs "<timestamp>.<body>" and sends the
    # timestamp, and sends nothing the adapter could fall back to.
    import hashlib
    import hmac
    import time

    _handoff(gate, 200)
    sent = _FakeConnection.last_request
    assert sent["path"] == "/webhooks/handoff"
    headers = sent["headers"]
    assert "X-Webhook-Signature" not in headers
    ts = headers["X-Webhook-Timestamp"]
    assert abs(int(ts) - int(time.time())) <= 5
    expected = hmac.new(b"shh-mcp", ts.encode() + b"." + sent["body"], hashlib.sha256).hexdigest()
    assert headers["X-Webhook-Signature-V2"] == expected
    assert headers["X-Request-ID"] == "medchron-01A"


def test_a_404_forward_is_a_retryable_failure_not_a_silent_202(gate):
    # The exact window between a config change and its reprovision: the route
    # is not materialized, the adapter 404s, and before #2616 the gate
    # swallowed that into a 202 — a lost wake indistinguishable from a
    # delivered one.
    code, payload = _handoff(gate, 404)
    assert code == 503 and payload["retry"] is True


def test_a_500_forward_stays_a_retryable_failure(gate):
    code, payload = _handoff(gate, 500)
    assert code == 503 and payload["retry"] is True


# ---------------------------------------------------------------------------
# medchron submit: the append selection passes through
# ---------------------------------------------------------------------------


def test_submit_passes_include_file_ids_through(monkeypatch):
    mod = load_plugin("hermes-smd-medchron")
    sent: dict = {}

    class _Client:
        def submit(self, envelope):
            sent.update(envelope)
            return {"accepted": True, "job_id": "01J", "state": "submitted"}

    monkeypatch.setattr(mod, "MedchronBrokerClient", _Client)
    ctx = FakePluginContext()
    mod.register(ctx)
    out = json.loads(
        ctx.tools["medchron_job_submit"]["handler"](
            {
                "matter_id": "m-1",
                "matter_number": "10006",
                "units": [{"client_name": "A B", "surname": "B", "dob": "01/02/1980"}],
                "incident_date": "2026-01-15",
                "incident_source": "administrator_request",
                "selection": {"include_file_ids": ["f-9", "f-10"]},
            }
        )
    )
    assert out["accepted"] is True
    assert sent["selection"] == {"include_file_ids": ["f-9", "f-10"]}
    schema = ctx.tools["medchron_job_submit"]["schema"]["parameters"]
    assert "include_file_ids" in schema["properties"]["selection"]["properties"]


def test_submit_omits_selection_when_not_given(monkeypatch):
    mod = load_plugin("hermes-smd-medchron")
    sent: dict = {}

    class _Client:
        def submit(self, envelope):
            sent.update(envelope)
            return {"accepted": True, "job_id": "01J", "state": "submitted"}

    monkeypatch.setattr(mod, "MedchronBrokerClient", _Client)
    ctx = FakePluginContext()
    mod.register(ctx)
    ctx.tools["medchron_job_submit"]["handler"](
        {
            "matter_id": "m-1",
            "matter_number": "10006",
            "units": [{"client_name": "A B", "surname": "B", "dob": "01/02/1980"}],
            "incident_date": "2026-01-15",
            "incident_source": "administrator_request",
        }
    )
    assert "selection" not in sent
