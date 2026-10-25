"""The California advertising gate at the outbound seam (ADR 0098).

Drives the REAL outbound scans (``check_outbound_send``, ``check_outbound_draft``)
and the reply relay's hold helper over a seat materialized the way the image
sees it: a customer.yaml with ``seat.role``, a roles dir carrying that role's
``policy:``, the checker from this repo (``operator/templates/drafting``) and the
example registers. Pins four things:

* a seat whose role sets ``advertising_gate: ca-law-firm`` refuses a
  quick-cash claim (a guarantee is already refused fleet-wide by the
  fabrication gate, so it would prove nothing about this one);
* the SAME text on a seat without the policy passes (inert on every other seat);
* a FLAG writes the audit row (rule ids only) and the send proceeds;
* missing registers, or a checker that cannot load, refuse (fail closed).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from shared import advertising_gate, policy_profile, provenance, send_dispatch, spec_gate
from tests.conftest import load_plugin

_REPO_DRAFTING = Path(__file__).resolve().parents[2] / "templates" / "drafting"
CHECKER = _REPO_DRAFTING / "advertising_gate_check.py"
REGISTERS = _REPO_DRAFTING / "fixtures" / "marketing-registers.example.yaml"

STAFF = "christa@firm.example"
BANNED = "Get money fast after a crash with Rivera & Cole in Riverside."
CLEAN = "Here are this month's drafts for your review."


class _FakeD1:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def execute(self, sql: str, *params):
        self.calls.append((sql, params))
        return 1


def _seat(tmp_path: Path, monkeypatch, *, role: str | None, policy: dict | None) -> None:
    roles = tmp_path / "roles"
    roles.mkdir(exist_ok=True)
    seat: dict = {"kind": "customer", "product": "operator"}
    if role:
        seat["role"] = role
        manifest: dict = {"role": role, "persona": "persona.md"}
        if policy is not None:
            manifest["policy"] = policy
        (roles / role).mkdir(exist_ok=True)
        (roles / role / "role.yaml").write_text(yaml.safe_dump(manifest))
    cfg = tmp_path / "customer.yaml"
    cfg.write_text(yaml.safe_dump({"customer_id": "mkt", "vertical": "law-firm", "seat": seat}))
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg))
    monkeypatch.setenv("ROLES_DIR", str(roles))


MARKETING = {
    "advertising_gate": "ca-law-firm",
    "rationale": "drafts of public law-firm advertising",
    "adr": "docs/adr/0098-marketing-seat-advertising-gate.md",
}


@pytest.fixture
def trust(monkeypatch):
    plugin = load_plugin("hermes-smd-trust")
    ob = plugin.outbound
    ob._AUDIT_CLIENT = None
    ob._AUDIT_CUSTOMER_SLUG = None
    ob._AUDIT_WIRED = False
    monkeypatch.setenv("SMD_VERTICAL", "law-firm")
    monkeypatch.setenv(advertising_gate.GATE_PATH_ENV, str(CHECKER))
    monkeypatch.setenv(advertising_gate.REGISTERS_ENV, str(REGISTERS))
    provenance._reset_for_tests()
    enforce = plugin.enforce
    monkeypatch.setattr(enforce, "_resolve_roster", lambda: [STAFF])
    monkeypatch.setattr(enforce, "_resolve_typed_roster", lambda: [])
    sent: list[dict] = []
    monkeypatch.setattr(
        send_dispatch,
        "_SENDER",
        lambda **k: (
            sent.append(k) or send_dispatch.DispatchResult(sent=True, recipients=tuple(k["to"]))
        ),
    )
    return plugin


@pytest.fixture
def rows(monkeypatch):
    fake = _FakeD1()
    monkeypatch.setattr(spec_gate, "_AUDIT_CLIENT", fake)
    monkeypatch.setattr(spec_gate, "_AUDIT_CUSTOMER_SLUG", "mkt")
    monkeypatch.setattr(spec_gate, "_AUDIT_WIRED", True)
    return fake


def _ad_rows(fake: _FakeD1) -> list[dict]:
    out = []
    for _sql, params in fake.calls:
        if "SPEC_GATE_TRIGGERED" not in params:
            continue
        meta = json.loads(params[-1])
        if meta.get("reason") == "advertising_gate":
            out.append(meta)
    return out


def _send(trust, text: str, session: str = "s1") -> dict | None:
    args = {"to": [STAFF], "subject": "Drafts for review", "text": text}
    return trust.outbound.check_outbound_send(
        tool_name="smd_send_message", args=args, session_id=session, tool_call_id="c"
    )


# ---------------------------------------------------------------------------
# On a marketing seat
# ---------------------------------------------------------------------------


def test_an_opted_in_seat_refuses_a_quick_cash_claim(trust, rows, tmp_path, monkeypatch):
    _seat(tmp_path, monkeypatch, role="marketing", policy=MARKETING)
    result = _send(trust, BANNED)
    assert result is not None and result["action"] == "block"
    assert result["message"].startswith("Refused:")
    assert "R-02" in result["message"] and "'money fast'" in result["message"]
    (row,) = _ad_rows(rows)
    assert row["rules"] == "R-02:BLOCK"


def test_the_third_refusal_withholds(trust, rows, tmp_path, monkeypatch):
    _seat(tmp_path, monkeypatch, role="marketing", policy=MARKETING)
    results = [_send(trust, BANNED, "s-loop") for _ in range(3)]
    assert results[0]["message"].startswith("Refused:")
    assert results[2]["message"].startswith("Withheld:")
    assert "attorney-advertising" in results[2]["message"]


def test_a_flag_lands_in_the_audit_row_and_the_send_proceeds(trust, rows, tmp_path, monkeypatch):
    _seat(tmp_path, monkeypatch, role="marketing", policy=MARKETING)
    email = (
        f"{CLEAN}\n"
        "--- DRAFT social ---\n"
        "Our lawyers handle truck crash cases and are experts in them.\n"
        "Rivera & Cole Injury Lawyers, Riverside.\n"
        "--- END DRAFT ---\n"
    )
    assert _send(trust, email) is None
    (row,) = _ad_rows(rows)
    assert row["rules"] == "R-17:FLAG"


def test_a_draft_block_missing_its_disclosure_is_refused(trust, rows, tmp_path, monkeypatch):
    _seat(tmp_path, monkeypatch, role="marketing", policy=MARKETING)
    email = (
        f"{CLEAN}\n"
        "--- DRAFT social ---\n"
        "No fee unless we recover for you. Rivera & Cole Injury Lawyers, Riverside.\n"
        "--- END DRAFT ---\n"
    )
    result = _send(trust, email)
    assert result is not None and "R-12" in result["message"]


def test_missing_registers_refuse(trust, rows, tmp_path, monkeypatch):
    _seat(tmp_path, monkeypatch, role="marketing", policy=MARKETING)
    monkeypatch.setenv(advertising_gate.REGISTERS_ENV, str(tmp_path / "absent.yaml"))
    result = _send(trust, CLEAN)
    assert result is not None and "registers unavailable" in result["message"]


def test_a_checker_that_cannot_load_refuses(trust, rows, tmp_path, monkeypatch):
    _seat(tmp_path, monkeypatch, role="marketing", policy=MARKETING)
    monkeypatch.setenv(advertising_gate.GATE_PATH_ENV, str(tmp_path / "gone.py"))
    result = _send(trust, CLEAN)
    assert result is not None and "could not run" in result["message"]


def test_a_garbled_gate_value_still_runs_the_gate(trust, rows, tmp_path, monkeypatch):
    _seat(tmp_path, monkeypatch, role="marketing", policy={**MARKETING, "advertising_gate": "off"})
    assert _send(trust, BANNED) is not None


def test_a_role_policy_malformed_elsewhere_keeps_the_gate(trust, rows, tmp_path, monkeypatch):
    broken = {**MARKETING, "output_checklist": {"profile": "casual"}}
    _seat(tmp_path, monkeypatch, role="marketing", policy=broken)
    policy = policy_profile.current()
    assert policy.source.startswith("fallback:")
    assert policy.advertising_gate == policy_profile.ADVERTISING_CA_LAW_FIRM
    assert _send(trust, BANNED) is not None


def test_a_draft_tool_body_is_checked_on_an_opted_in_seat(trust, rows, tmp_path, monkeypatch):
    _seat(tmp_path, monkeypatch, role="marketing", policy=MARKETING)
    draft = sorted(t for t in trust.outbound.GATED_DRAFT_TOOLS if "create_draft" in t)[0]
    result = trust.outbound.check_outbound_draft(
        tool_name=draft,
        args={"to": [STAFF], "subject": "Drafts", "body_text": BANNED, "text": BANNED},
        session_id="s-draft",
        tool_call_id="c",
    )
    assert result is not None and "R-02" in result["message"]


def test_the_reply_relay_holds_on_an_opted_in_seat(trust, rows, tmp_path, monkeypatch):
    reply = load_plugin("hermes-smd-reply")
    _seat(tmp_path, monkeypatch, role="marketing", policy=MARKETING)
    held = reply._advertising_violations(BANNED, internal=True, tool_name="t", session_id="s")
    assert held is not None and held[0] == "R-02:BLOCK" and "R-02" in held[1]
    assert (
        reply._advertising_violations(CLEAN, internal=True, tool_name="t", session_id="s") is None
    )


def test_the_hold_notice_explains_the_reason():
    notice = load_plugin("hermes-smd-reply").notice
    text = notice.render(
        notice.HoldNotice(reason="advertising_gate", recipient=STAFF, message_id="m")
    )
    assert "attorney-advertising" in text


# ---------------------------------------------------------------------------
# Inert on every other seat
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,policy",
    [
        (None, None),
        ("couples", {"output_checklist": {"profile": "paralegal"}}),
        ("marketing", {"rationale": "no gate key", "adr": "docs/adr/0098-x.md"}),
    ],
)
def test_the_same_text_passes_on_a_seat_without_the_policy(
    trust, rows, tmp_path, monkeypatch, role, policy
):
    _seat(tmp_path, monkeypatch, role=role, policy=policy)
    # Even with no checker and no registers anywhere: an inert seat reads neither.
    monkeypatch.setenv(advertising_gate.GATE_PATH_ENV, str(tmp_path / "gone.py"))
    monkeypatch.setenv(advertising_gate.REGISTERS_ENV, str(tmp_path / "absent.yaml"))
    assert not advertising_gate.enabled()
    assert _send(trust, BANNED) is None
    assert _ad_rows(rows) == []
    reply = load_plugin("hermes-smd-reply")
    assert (
        reply._advertising_violations(BANNED, internal=True, tool_name="t", session_id="s") is None
    )


def test_the_platform_default_has_the_gate_off():
    assert policy_profile.PLATFORM_DEFAULT.advertising_gate is None
    assert not policy_profile.PLATFORM_DEFAULT.loosened


def test_the_image_ships_the_checker_at_the_path_the_gate_loads():
    """The default path resolves in the image: the Dockerfile copies the whole
    drafting tree to /app/operator/templates/drafting/ (root-owned there), and
    the checker lives in that tree in this repo."""
    repo = Path(__file__).resolve().parents[3]
    dockerfile = (repo / "operator" / "templates" / "Dockerfile").read_text()
    assert "COPY operator/templates/drafting/ /app/operator/templates/drafting/" in dockerfile
    rel = "operator/templates/drafting/advertising_gate_check.py"
    assert advertising_gate.DEFAULT_GATE_PATH == "/app/" + rel
    assert (repo / rel).is_file()
    assert advertising_gate.DEFAULT_REGISTERS == "/var/lib/smd-config/marketing/marketing-firm.yaml"
