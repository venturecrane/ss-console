"""agentmail-webhook-secret-probe.py: the compare can fail, and never prints a value.

The probe is the boot-smoke half of the scott 2026-09-15 fix
(vfy_01M2HXT17Q32RX6TCV5NVZA9D6): the seat held the global AgentMail webhook
secret, not its own webhook's, and every inbound was rejected 401 while smoke
passed. These tests drive the probe against a fake /proc root and a fake vendor
so each verdict arm is exercised: match, mismatch, no vendor webhook for the
host, two webhooks for the host, vacuous (no secret in any agent process), and
the secret-but-no-org-key defect. The secret value must never appear in output.

Since 2026-09-17 the vendor lookup presents the ORG-scoped webhook_read key from
PID 1's environ, never the agent's inbox-scoped AgentMail key -- that key
returns 403, and even re-minted with webhook_read it lists zero webhooks, so the
old shape could never pass on a correctly-scoped seat. `_fetch` below asserts the
key the probe presents is PID 1's, which is what keeps that regression from
returning quietly.
"""

from __future__ import annotations

import importlib.util
import os
import pwd
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[2] / "templates" / "agentmail-webhook-secret-probe.py"


def _load():
    spec = importlib.util.spec_from_file_location("agentmail_webhook_secret_probe", PROBE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


probe = _load()

SEAT_SECRET = "whsec_seatsecretvalue_0123456789abcdef"
VENDOR_SECRET = "whsec_vendorsecretvalue_fedcba9876543210"
HOST = "hermes-scott.fly.dev"
ORG_KEY = "am_us_orgwebhookreadkey"
AGENT_KEY = "am_us_inbox_agentkey"


def _init_env(tmp_path: Path, value: str | None = ORG_KEY) -> None:
    """PID 1's environ, where Fly keeps the org key; None means the seat lacks it."""
    pid1 = tmp_path / "1"
    pid1.mkdir(exist_ok=True)
    env = {} if value is None else {probe._KEY_VAR: value}
    pid1.joinpath("environ").write_bytes(
        b"".join(f"{k}={v}".encode() + b"\x00" for k, v in env.items())
    )


def _fake_proc(tmp_path: Path, env: dict[str, str] | None) -> str:
    """A /proc root with one process owned by the CURRENT uid (the test runs as it)."""
    pid_dir = tmp_path / "4242"
    pid_dir.mkdir()
    if env is not None:
        (pid_dir / "environ").write_bytes(b"".join(f"{k}={v}".encode() + b"\x00" for k, v in env.items()))
    return str(tmp_path)


def _me() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def _fetch(webhooks: list[dict], secrets: dict[str, str]):
    def fetch(path: str, key: str) -> dict:
        assert key == ORG_KEY, "the probe must present the ORG-scoped webhook_read key, not the agent's"
        if path == "/webhooks":
            return {"webhooks": webhooks}
        wid = path.rsplit("/", 1)[-1]
        return {"webhook_id": wid, "secret": secrets.get(wid, "")}

    return fetch


def test_match_passes(tmp_path, capsys):
    root = _fake_proc(tmp_path, {"AGENTMAIL_API_KEY": AGENT_KEY, "WEBHOOK_SECRET_AGENTMAIL": SEAT_SECRET})
    _init_env(tmp_path)
    fetch = _fetch([{"webhook_id": "wh1", "url": f"https://{HOST}/webhooks/agentmail"}], {"wh1": SEAT_SECRET})
    assert probe.run(HOST, _me(), proc_root=root, fetch=fetch) == 0
    out = capsys.readouterr().out
    assert out.startswith("PASS:")
    assert SEAT_SECRET not in out


def test_mismatch_fails_and_never_prints_the_value(tmp_path, capsys):
    root = _fake_proc(tmp_path, {"AGENTMAIL_API_KEY": AGENT_KEY, "WEBHOOK_SECRET_AGENTMAIL": SEAT_SECRET})
    _init_env(tmp_path)
    fetch = _fetch([{"webhook_id": "wh1", "url": f"https://{HOST}/webhooks/agentmail"}], {"wh1": VENDOR_SECRET})
    assert probe.run(HOST, _me(), proc_root=root, fetch=fetch) == 1
    out = capsys.readouterr().out
    assert "does NOT match" in out
    assert SEAT_SECRET not in out and VENDOR_SECRET not in out
    assert "sha8=" in out


def test_no_vendor_webhook_for_host_fails(tmp_path, capsys):
    root = _fake_proc(tmp_path, {"AGENTMAIL_API_KEY": AGENT_KEY, "WEBHOOK_SECRET_AGENTMAIL": SEAT_SECRET})
    _init_env(tmp_path)
    fetch = _fetch([{"webhook_id": "wh9", "url": "https://hermes-other.fly.dev/webhooks/agentmail"}], {})
    assert probe.run(HOST, _me(), proc_root=root, fetch=fetch) == 1
    assert "no vendor webhook names host" in capsys.readouterr().out


def test_two_vendor_webhooks_for_host_refuses_to_guess(tmp_path, capsys):
    root = _fake_proc(tmp_path, {"AGENTMAIL_API_KEY": AGENT_KEY, "WEBHOOK_SECRET_AGENTMAIL": SEAT_SECRET})
    _init_env(tmp_path)
    hooks = [
        {"webhook_id": "a", "url": f"https://{HOST}/webhooks/agentmail"},
        {"webhook_id": "b", "url": f"https://{HOST}/webhooks/agentmail"},
    ]
    fetch = _fetch(hooks, {"a": SEAT_SECRET, "b": SEAT_SECRET})
    assert probe.run(HOST, _me(), proc_root=root, fetch=fetch) == 1
    assert "refusing to guess" in capsys.readouterr().out


def test_vacuous_when_no_agent_process_carries_the_secret(tmp_path, capsys):
    root = _fake_proc(tmp_path, {"AGENTMAIL_API_KEY": AGENT_KEY})
    _init_env(tmp_path)

    def fetch(path: str, key: str) -> dict:  # pragma: no cover - must not be called
        raise AssertionError("vendor must not be consulted on a vacuous seat")

    assert probe.run(HOST, _me(), proc_root=root, fetch=fetch) == 0
    assert "vacuous" in capsys.readouterr().out


def test_secret_without_org_key_is_a_defect(tmp_path, capsys):
    """A seat carrying the secret while PID 1 carries no org key cannot be proven.

    This is the arm that makes 'cannot ask the vendor' distinct from 'the value is
    fine'. Treating them the same is what let the scott seat read green for two
    months, so the probe must exit 1 and say which credential was missing.
    """
    root = _fake_proc(tmp_path, {"WEBHOOK_SECRET_AGENTMAIL": SEAT_SECRET})
    _init_env(tmp_path, None)

    def fetch(path: str, key: str) -> dict:  # pragma: no cover - must not be called
        raise AssertionError("vendor must not be consulted without a key")

    assert probe.run(HOST, _me(), proc_root=root, fetch=fetch) == 1
    out = capsys.readouterr().out
    assert "PID 1 carries no AGENTMAIL_WEBHOOK_READ_API_KEY" in out
    assert SEAT_SECRET not in out


def test_agent_key_is_never_presented_to_the_vendor(tmp_path, capsys):
    """The regression guard for 2026-09-17: an inbox-scoped key cannot answer.

    If someone restores the old read-from-agent-environ shape, the probe would
    present AGENT_KEY and `_fetch`'s assertion fires - a failing test rather than
    a check that silently cannot pass on a correctly-scoped seat.
    """
    root = _fake_proc(tmp_path, {"AGENTMAIL_API_KEY": AGENT_KEY, "WEBHOOK_SECRET_AGENTMAIL": SEAT_SECRET})
    _init_env(tmp_path)
    fetch = _fetch([{"webhook_id": "wh1", "url": f"https://{HOST}/webhooks/agentmail"}], {"wh1": SEAT_SECRET})
    assert probe.run(HOST, _me(), proc_root=root, fetch=fetch) == 0


def test_vendor_failure_is_fail_closed(tmp_path, capsys):
    root = _fake_proc(tmp_path, {"AGENTMAIL_API_KEY": AGENT_KEY, "WEBHOOK_SECRET_AGENTMAIL": SEAT_SECRET})
    _init_env(tmp_path)

    def fetch(path: str, key: str) -> dict:
        raise TimeoutError("vendor down")

    assert probe.run(HOST, _me(), proc_root=root, fetch=fetch) == 1
    assert "vendor lookup failed: TimeoutError" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["", "x"])
def test_verdict_by_hash_prefix_only(value):
    ok, msg = probe.verdict(SEAT_SECRET, value or SEAT_SECRET)
    assert SEAT_SECRET not in msg
    assert ok is (value == "")
