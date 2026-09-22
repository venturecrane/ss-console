"""msgraph-read-app-cannot-send-probe.py: every verdict arm, and no secret in output.

The boot-smoke half of ADR 0089's rogue-path guard: on a seat whose Operator
mailbox holds Send As on staff, the agent's own Graph credential must not carry
Mail.Send, or code the agent runs could send as a person with no approval.
Driven against a fake /proc root and a fake token mint.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path

PROBE = Path(__file__).resolve().parents[2] / "templates" / "msgraph-read-app-cannot-send-probe.py"


def _load():
    spec = importlib.util.spec_from_file_location("msgraph_read_app_cannot_send_probe", PROBE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


probe = _load()
SECRET = "read-app-secret-value-0123456789"
UID = os.getuid()

WITH_STAFF = "scope:\n  staff_send_as:\n    - address: paralegal@firm.example\n      name: Pat\n"
WITHOUT_STAFF = "scope:\n  admins:\n    - chris@firm.example\n"


def _token(roles: list[str]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"roles": roles}).encode()).decode().rstrip("=")
    return f"hdr.{payload}.sig"


def _seat(tmp_path: Path, yaml_text: str, *, env: bool = True) -> tuple[str, str]:
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(yaml_text)
    proc = tmp_path / "proc"
    (proc / "123").mkdir(parents=True)
    body = b"PATH=/bin\x00"
    if env:
        body += (
            b"MSGRAPH_TENANT_ID=tid\x00MSGRAPH_CLIENT_ID=cid-read\x00MSGRAPH_CLIENT_SECRET=" + SECRET.encode() + b"\x00"
        )
    (proc / "123" / "environ").write_bytes(body)
    return str(yaml_path), str(proc)


def _user(monkeypatch) -> str:
    import pwd

    name = pwd.getpwuid(UID).pw_name
    return name


def test_a_read_app_without_mail_send_passes(tmp_path: Path, monkeypatch, capsys) -> None:
    yaml_path, proc = _seat(tmp_path, WITH_STAFF)
    rc = probe.run(yaml_path, _user(monkeypatch), proc, mint=lambda *_: _token(["Mail.ReadWrite"]))
    out = capsys.readouterr().out
    assert rc == 0 and "PASS" in out and "Mail.ReadWrite" in out


def test_a_read_app_holding_mail_send_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    yaml_path, proc = _seat(tmp_path, WITH_STAFF)
    rc = probe.run(yaml_path, _user(monkeypatch), proc, mint=lambda *_: _token(["Mail.ReadWrite", "Mail.Send"]))
    out = capsys.readouterr().out
    assert rc == 1 and "Mail.Send" in out


def test_a_seat_without_staff_send_as_passes_vacuously(tmp_path: Path, monkeypatch, capsys) -> None:
    yaml_path, proc = _seat(tmp_path, WITHOUT_STAFF, env=False)
    rc = probe.run(yaml_path, _user(monkeypatch), proc, mint=lambda *_: _token(["Mail.Send"]))
    assert rc == 0 and "vacuous" in capsys.readouterr().out


def test_no_credential_to_ask_with_fails_closed(tmp_path: Path, monkeypatch, capsys) -> None:
    yaml_path, proc = _seat(tmp_path, WITH_STAFF, env=False)
    rc = probe.run(yaml_path, _user(monkeypatch), proc, mint=lambda *_: _token([]))
    assert rc == 1 and "cannot ask" in capsys.readouterr().out


def test_a_failed_mint_fails_closed(tmp_path: Path, monkeypatch, capsys) -> None:
    yaml_path, proc = _seat(tmp_path, WITH_STAFF)

    def boom(*_):
        raise OSError("network down")

    assert probe.run(yaml_path, _user(monkeypatch), proc, mint=boom) == 1


def test_the_secret_never_reaches_output(tmp_path: Path, monkeypatch, capsys) -> None:
    yaml_path, proc = _seat(tmp_path, WITH_STAFF)
    for roles in (["Mail.ReadWrite"], ["Mail.Send"]):
        probe.run(yaml_path, _user(monkeypatch), proc, mint=lambda *_, r=roles: _token(r))
    assert SECRET not in capsys.readouterr().out
