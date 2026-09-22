"""msgraph-read-app-cannot-read-staff-probe.py: every verdict arm, and no secret out.

The other half of ADR 0089 amendment 5a. The approve buttons are signed links,
and the broker keeps no Sent Items copy of the email carrying them, so the only
mailbox holding a link is the approver's. This probe asks Microsoft whether the
agent's read app can open that mailbox; if it can, the Operator can lift its own
approve link and press its own button. Driven against a fake /proc root, a fake
mint and a fake Graph.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

PROBE = Path(__file__).resolve().parents[2] / "templates" / "msgraph-read-app-cannot-read-staff-probe.py"


def _load():
    spec = importlib.util.spec_from_file_location("msgraph_read_app_cannot_read_staff_probe", PROBE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


probe = _load()
SECRET = "read-app-secret-value-0123456789"
UID = os.getuid()
STAFF = "paralegal@firm.example"

WITH_STAFF = f"scope:\n  staff_send_as:\n    - address: {STAFF}\n      name: Pat\n"
WITHOUT_STAFF = "scope:\n  admins:\n    - chris@firm.example\n"


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


def _user() -> str:
    import pwd

    return pwd.getpwuid(UID).pw_name


def _run(tmp_path: Path, yaml_text: str, status: int, *, env: bool = True):
    yaml_path, proc = _seat(tmp_path, yaml_text, env=env)
    asked: list[str] = []

    def ask(_token: str, mailbox: str) -> int:
        asked.append(mailbox)
        return status

    rc = probe.run(yaml_path, _user(), proc, mint=lambda *_: "token", ask=ask)
    return rc, asked


def test_a_scoped_read_app_passes(tmp_path: Path, capsys) -> None:
    rc, asked = _run(tmp_path, WITH_STAFF, 403)
    out = capsys.readouterr().out
    assert (rc, asked) == (0, [STAFF])
    assert "refused" in out and "403" in out


def test_a_missing_mailbox_also_passes(tmp_path: Path, capsys) -> None:
    # 404 is what Graph answers for a mailbox the policy hides. Same verdict.
    rc, _asked = _run(tmp_path, WITH_STAFF, 404)
    assert rc == 0 and "refused" in capsys.readouterr().out


def test_an_unscoped_read_app_fails_and_names_the_fix(tmp_path: Path, capsys) -> None:
    rc, _asked = _run(tmp_path, WITH_STAFF, 200)
    out = capsys.readouterr().out
    assert rc == 1
    assert "can read" in out and "ApplicationAccessPolicy" in out


def test_a_seat_without_staff_send_as_passes_vacuously(tmp_path: Path, capsys) -> None:
    rc, asked = _run(tmp_path, WITHOUT_STAFF, 200, env=False)
    assert (rc, asked) == (0, [])
    assert "vacuous" in capsys.readouterr().out


def test_no_credential_to_ask_with_fails_closed(tmp_path: Path, capsys) -> None:
    rc, asked = _run(tmp_path, WITH_STAFF, 403, env=False)
    assert (rc, asked) == (1, [])
    assert "cannot ask" in capsys.readouterr().out


def test_a_failed_mint_fails_closed(tmp_path: Path, capsys) -> None:
    yaml_path, proc = _seat(tmp_path, WITH_STAFF)

    def boom(*_args):
        raise OSError("tenant unreachable")

    rc = probe.run(yaml_path, _user(), proc, mint=boom, ask=lambda *_: 403)
    assert rc == 1 and "could not mint" in capsys.readouterr().out


def test_the_secret_never_reaches_the_output(tmp_path: Path, capsys) -> None:
    for status in (200, 403):
        seat = tmp_path / f"s{status}"
        seat.mkdir()
        _run(seat, WITH_STAFF, status)
        assert SECRET not in capsys.readouterr().out
