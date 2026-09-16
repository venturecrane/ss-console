"""agentmail-webhook-secret-probe.py: the compare can fail, and never prints a value.

The probe is the boot-smoke half of the scott 2026-09-15 fix
(vfy_01M2HXT17Q32RX6TCV5NVZA9D6): the seat held the global AgentMail webhook
secret, not its own webhook's, and every inbound was rejected 401 while smoke
passed. These tests drive the probe against a fake /proc root and a fake vendor
so each verdict arm is exercised: match, mismatch, no vendor webhook for the
host, two webhooks for the host, vacuous (no secret in any agent process), and
the secret-but-no-key defect. The secret value must never appear in output.
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
        assert key == "readkey", "the probe must present the agent's own read key"
        if path == "/webhooks":
            return {"webhooks": webhooks}
        wid = path.rsplit("/", 1)[-1]
        return {"webhook_id": wid, "secret": secrets.get(wid, "")}

    return fetch


def test_match_passes(tmp_path, capsys):
    root = _fake_proc(tmp_path, {"AGENTMAIL_API_KEY": "readkey", "WEBHOOK_SECRET_AGENTMAIL": SEAT_SECRET})
    fetch = _fetch([{"webhook_id": "wh1", "url": f"https://{HOST}/webhooks/agentmail"}], {"wh1": SEAT_SECRET})
    assert probe.run(HOST, _me(), proc_root=root, fetch=fetch) == 0
    out = capsys.readouterr().out
    assert out.startswith("PASS:")
    assert SEAT_SECRET not in out


def test_mismatch_fails_and_never_prints_the_value(tmp_path, capsys):
    root = _fake_proc(tmp_path, {"AGENTMAIL_API_KEY": "readkey", "WEBHOOK_SECRET_AGENTMAIL": SEAT_SECRET})
    fetch = _fetch([{"webhook_id": "wh1", "url": f"https://{HOST}/webhooks/agentmail"}], {"wh1": VENDOR_SECRET})
    assert probe.run(HOST, _me(), proc_root=root, fetch=fetch) == 1
    out = capsys.readouterr().out
    assert "does NOT match" in out
    assert SEAT_SECRET not in out and VENDOR_SECRET not in out
    assert "sha8=" in out


def test_no_vendor_webhook_for_host_fails(tmp_path, capsys):
    root = _fake_proc(tmp_path, {"AGENTMAIL_API_KEY": "readkey", "WEBHOOK_SECRET_AGENTMAIL": SEAT_SECRET})
    fetch = _fetch([{"webhook_id": "wh9", "url": "https://hermes-other.fly.dev/webhooks/agentmail"}], {})
    assert probe.run(HOST, _me(), proc_root=root, fetch=fetch) == 1
    assert "no vendor webhook names host" in capsys.readouterr().out


def test_two_vendor_webhooks_for_host_refuses_to_guess(tmp_path, capsys):
    root = _fake_proc(tmp_path, {"AGENTMAIL_API_KEY": "readkey", "WEBHOOK_SECRET_AGENTMAIL": SEAT_SECRET})
    hooks = [
        {"webhook_id": "a", "url": f"https://{HOST}/webhooks/agentmail"},
        {"webhook_id": "b", "url": f"https://{HOST}/webhooks/agentmail"},
    ]
    assert probe.run(HOST, _me(), proc_root=root, fetch=_fetch(hooks, {"a": SEAT_SECRET, "b": SEAT_SECRET})) == 1
    assert "refusing to guess" in capsys.readouterr().out


def test_vacuous_when_no_agent_process_carries_the_secret(tmp_path, capsys):
    root = _fake_proc(tmp_path, {"AGENTMAIL_API_KEY": "readkey"})

    def fetch(path: str, key: str) -> dict:  # pragma: no cover - must not be called
        raise AssertionError("vendor must not be consulted on a vacuous seat")

    assert probe.run(HOST, _me(), proc_root=root, fetch=fetch) == 0
    assert "vacuous" in capsys.readouterr().out


def test_secret_without_read_key_is_a_defect(tmp_path, capsys):
    root = _fake_proc(tmp_path, {"WEBHOOK_SECRET_AGENTMAIL": SEAT_SECRET})
    assert probe.run(HOST, _me(), proc_root=root, fetch=_fetch([], {})) == 1
    assert "no AGENTMAIL_API_KEY" in capsys.readouterr().out


def test_vendor_failure_is_fail_closed(tmp_path, capsys):
    root = _fake_proc(tmp_path, {"AGENTMAIL_API_KEY": "readkey", "WEBHOOK_SECRET_AGENTMAIL": SEAT_SECRET})

    def fetch(path: str, key: str) -> dict:
        raise TimeoutError("vendor down")

    assert probe.run(HOST, _me(), proc_root=root, fetch=fetch) == 1
    assert "vendor lookup failed: TimeoutError" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["", "x"])
def test_verdict_by_hash_prefix_only(value):
    ok, msg = probe.verdict(SEAT_SECRET, value or SEAT_SECRET)
    assert SEAT_SECRET not in msg
    assert ok is (value == "")
