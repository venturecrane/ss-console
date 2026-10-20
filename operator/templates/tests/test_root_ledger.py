"""runners/supervisor/root_ledger.py: root's ledgers on the agent's volume.

2026-10-20: the gateway-liveness kill ledger sat directly under the agent-owned
/opt/data, so the agent could rename its directory aside and plant one whose
`kills` was a symlink; root's shell `>>` and boot `chmod` then wrote to, or
re-moded, whatever it named. These hold the helper that replaced them: it never
follows a link, never reads a FIFO or device, refuses a directory that is not
its own, and reads a bounded tail.
"""

from __future__ import annotations

import importlib.util
import os
import threading
import time
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "runners" / "supervisor" / "root_ledger.py"
_spec = importlib.util.spec_from_file_location("root_ledger", _PATH)
assert _spec and _spec.loader
rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rl)


@pytest.fixture
def ledger(tmp_path):
    d = tmp_path / "gateway-liveness"
    d.mkdir(mode=0o755)
    return d


def test_append_then_count_the_window(ledger):
    now = int(time.time())
    rl.append(str(ledger), "kills", f"{now - 10} iso loop-wedge")
    rl.append(str(ledger), "kills", f"{now - 7200} iso loop-wedge")
    rl.append(str(ledger), "kills", "garbage")
    assert rl.count(str(ledger), "kills", now - 3600) == 1
    assert rl.count(str(ledger), "absent", now) == 0


def test_a_planted_file_link_is_never_written_or_re_moded(ledger, tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("secret\n")
    os.chmod(victim, 0o600)
    (ledger / "kills").symlink_to(victim)
    with pytest.raises(OSError):
        rl.append(str(ledger), "kills", "1 x y")
    assert victim.read_text() == "secret\n"
    assert victim.stat().st_mode & 0o777 == 0o600


def test_a_directory_link_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "gateway-liveness"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        rl.append(str(link), "kills", "1 x y")
    assert list(real.iterdir()) == []


def test_a_parent_link_is_refused(tmp_path):
    """.smd-root itself sits in the agent's /opt/data and can be swapped for a
    link; the parent is opened without following one too."""
    real = tmp_path / "real-root"
    (real / "gateway-liveness").mkdir(parents=True)
    (tmp_path / ".smd-root").symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        rl.append(str(tmp_path / ".smd-root" / "gateway-liveness"), "kills", "1 x y")
    assert list((real / "gateway-liveness").iterdir()) == []


def test_a_directory_others_can_write_is_refused(ledger):
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- the test's own tmp dir, made group-writable to prove the helper REFUSES it
    os.chmod(ledger, 0o775)
    with pytest.raises(PermissionError):
        rl.append(str(ledger), "kills", "1 x y")


def test_a_fifo_or_device_never_blocks_or_floods_the_count(ledger):
    os.mkfifo(ledger / "kills")
    done: list = []
    t = threading.Thread(target=lambda: done.append(_count_or_error(ledger)), daemon=True)
    t.start()
    t.join(timeout=3)
    assert not t.is_alive(), "count blocked on a FIFO"
    assert done == ["refused"]
    os.unlink(ledger / "kills")
    (ledger / "kills").symlink_to("/dev/zero")
    assert _count_or_error(ledger) == "refused"


def _count_or_error(ledger: Path):
    try:
        return rl.count(str(ledger), "kills", 0)
    except OSError:
        return "refused"


def test_the_file_is_readable_by_the_gate_under_umask_027(ledger):
    old = os.umask(0o027)
    try:
        rl.append(str(ledger), "kills", "1 x y")
    finally:
        os.umask(old)
    assert (ledger / "kills").stat().st_mode & 0o777 == 0o644


def test_the_cli_refuses_with_exit_2_and_never_raises(ledger, tmp_path):
    (ledger / "kills").symlink_to(tmp_path / "victim")
    assert rl.main(["append", str(ledger), "kills", "1 x y"]) == 2
    assert rl.main(["count", str(ledger), "kills", "0"]) == 2
    assert rl.main(["bogus"]) == 64
