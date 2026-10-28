"""entrypoint.sh waits long enough for the workspace broker socket, and still fails closed without one.

The old wait was five one-second polls. The boot harness measured a CPU-throttled
shared-cpu-1x Machine binding the broker socket in about six seconds (2026-10-28),
so a healthy broker could end the boot with "Workspace broker socket was not
created". The wait is now up to SMD_BROKER_SOCKET_WAIT_S (default 60 s), polled
once a second; what happens on timeout is unchanged.

These run the entrypoint's own wait lines, extracted from the file, against a
real AF_UNIX socket that appears after a delay.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

_ENTRYPOINT = Path(__file__).resolve().parents[1] / "entrypoint.sh"
_TEXT = _ENTRYPOINT.read_text()


@pytest.fixture
def sock() -> Iterator[Path]:
    # AF_UNIX paths cap at 104 bytes on macOS, which pytest's tmp_path exceeds.
    d = Path(tempfile.mkdtemp(prefix="smdbs-"))
    yield d / "broker.sock"
    shutil.rmtree(d, ignore_errors=True)


def _wait_lines() -> str:
    """The entrypoint's own wait: from the `_broker_waited=0` line to the timeout branch, exclusive."""
    m = re.search(
        r"^_broker_waited=0; until .*?\n(?=\[ -S \"\$\{BROKER_SOCKET\}\" \] \|\| \{)", _TEXT, re.MULTILINE | re.DOTALL
    )
    assert m, "the broker socket wait is not in entrypoint.sh"
    return m.group(0)


def _run(sock: Path, limit: str | None, bind_after: float | None) -> tuple[subprocess.CompletedProcess, float]:
    if bind_after is not None:

        def bind() -> None:
            time.sleep(bind_after)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.bind(str(sock))
            s.listen(1)

        threading.Thread(target=bind, daemon=True).start()
    env_line = f"SMD_BROKER_SOCKET_WAIT_S={limit}\n" if limit is not None else ""
    script = (
        "set -euo pipefail\n"
        'log() { echo "LOG $*"; }\n'
        f'BROKER_SOCKET="{sock}"\n{env_line}'
        f"{_wait_lines()}"
        'if [ -S "$BROKER_SOCKET" ]; then echo "RC=0"; else echo "RC=1"; fi\n'
    )
    start = time.monotonic()
    done = subprocess.run(  # noqa: S603 - fixed argv; the script is the entrypoint's own function
        ["bash", "-c", script], capture_output=True, text=True, timeout=120, check=False
    )
    return done, time.monotonic() - start


def test_a_socket_that_binds_after_six_seconds_is_waited_for(sock: Path) -> None:
    # FALSIFIER: the old five-poll loop gave up at about 5 s, before this bind.
    done, took = _run(sock, None, 6.0)
    assert "RC=0" in done.stdout, done.stdout + done.stderr
    m = re.search(r"LOG Workspace broker socket up after (\d+)s", done.stdout)
    assert m and 6 <= int(m.group(1)) <= 9, done.stdout
    assert took < 15


def test_an_existing_socket_returns_at_once(sock: Path) -> None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(sock))
    done, took = _run(sock, None, None)
    assert "RC=0" in done.stdout and "up after 0s" in done.stdout
    assert took < 3
    s.close()


def test_no_socket_still_fails_after_the_budget(sock: Path) -> None:
    done, took = _run(sock, "2", None)
    assert "RC=1" in done.stdout and "up after" not in done.stdout
    assert 2 <= took < 10


def test_the_timeout_path_is_unchanged() -> None:
    # On timeout: the same FATAL line, the supervisor killed, exit 1.
    assert re.search(
        r'\[ -S "\$\{BROKER_SOCKET\}" \] \|\| \{\n\s*log "FATAL: Workspace broker socket was not created"\n'
        r'\s*kill -KILL "\$\{SUPERVISOR_PID\}" 2>/dev/null \|\| true\n\s*exit 1\n\}',
        _TEXT,
    )
    assert "for _ in 1 2 3 4 5; do" not in _TEXT
    assert "SMD_BROKER_SOCKET_WAIT_S:-60" in _wait_lines()
