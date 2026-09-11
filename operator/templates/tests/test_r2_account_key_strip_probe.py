"""Unit tests for the OP-P2-1 runtime probe (r2-account-key-strip-probe.py).

The probe proves on the LIVE Machine that no agent-uid process carries the
account-wide R2 key. These tests drive its ``scan`` over a FAKE /proc tree so the
uid filter, the offender detection, the value-never-echoed property, and the
"no agent process is not a pass" rule are all covered without being root.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PROBE_PATH = Path(__file__).resolve().parents[1] / "r2-account-key-strip-probe.py"
_spec = importlib.util.spec_from_file_location("r2_strip_probe", _PROBE_PATH)
assert _spec and _spec.loader
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

_AGENT_UID = 1000
_ROOT_UID = 0
_KEYS = ["R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]
# A realistic-looking but entirely fake credential value — asserts the probe
# never echoes the value, only the var name.
_FAKE_VALUE = "deadbeefdeadbeefdeadbeefdeadbeef"


def _mkproc(proc_root: Path, pid: int, env: dict[str, str], comm: str = "python3") -> None:
    d = proc_root / str(pid)
    d.mkdir(parents=True)
    blob = b"".join(f"{k}={v}".encode() + b"\x00" for k, v in env.items())
    (d / "environ").write_bytes(blob)
    (d / "comm").write_text(comm + "\n")


def _uid_map(mapping: dict[int, int]):
    def uid_of(_proc_root: str, pid: str) -> int | None:
        return mapping.get(int(pid))

    return uid_of


def test_clean_agent_process_passes(tmp_path):
    _mkproc(tmp_path, 100, {"PATH": "/usr/bin", "R2_ENDPOINT_URL": "https://x"})
    count, offenders = probe.scan(str(tmp_path), _AGENT_UID, _KEYS, uid_of=_uid_map({100: _AGENT_UID}))
    assert count == 1
    assert offenders == []


def test_agent_process_holding_account_key_is_flagged(tmp_path):
    _mkproc(tmp_path, 100, {"R2_ACCESS_KEY_ID": _FAKE_VALUE}, comm="hermes")
    count, offenders = probe.scan(str(tmp_path), _AGENT_UID, _KEYS, uid_of=_uid_map({100: _AGENT_UID}))
    assert count == 1
    assert len(offenders) == 1
    assert "100" in offenders[0]
    assert "R2_ACCESS_KEY_ID" in offenders[0]


def test_offender_string_never_contains_the_value(tmp_path):
    _mkproc(tmp_path, 100, {"R2_ACCESS_KEY_ID": _FAKE_VALUE, "R2_SECRET_ACCESS_KEY": _FAKE_VALUE})
    _, offenders = probe.scan(str(tmp_path), _AGENT_UID, _KEYS, uid_of=_uid_map({100: _AGENT_UID}))
    assert offenders
    for line in offenders:
        assert _FAKE_VALUE not in line


def test_root_process_with_the_key_is_excluded(tmp_path):
    # PID 1 (root) legitimately keeps the key (entrypoint + config applier).
    _mkproc(tmp_path, 1, {"R2_ACCESS_KEY_ID": _FAKE_VALUE}, comm="entrypoint")
    _mkproc(tmp_path, 100, {"PATH": "/usr/bin"}, comm="hermes")
    count, offenders = probe.scan(str(tmp_path), _AGENT_UID, _KEYS, uid_of=_uid_map({1: _ROOT_UID, 100: _AGENT_UID}))
    assert count == 1  # only the agent process is scanned
    assert offenders == []


def test_sibling_child_leak_is_caught(tmp_path):
    # The gateway is clean but a same-uid child (e.g. a webhook-gate subshell
    # forked before the strip) still holds the key — the exact /proc/environ
    # sibling-leak bootstrap.sh closes with `env -u`.
    _mkproc(tmp_path, 100, {"PATH": "/usr/bin"}, comm="hermes")
    _mkproc(tmp_path, 101, {"R2_ACCESS_KEY_ID": _FAKE_VALUE}, comm="bash")
    count, offenders = probe.scan(str(tmp_path), _AGENT_UID, _KEYS, uid_of=_uid_map({100: _AGENT_UID, 101: _AGENT_UID}))
    assert count == 2
    assert len(offenders) == 1
    assert "101" in offenders[0]


def test_no_agent_process_is_not_a_silent_pass(tmp_path):
    # Only root processes exist -> scan finds zero agent procs. main() must turn
    # that into a loud failure (exit 3), never a vacuous 0.
    _mkproc(tmp_path, 1, {"R2_ACCESS_KEY_ID": _FAKE_VALUE}, comm="entrypoint")
    count, offenders = probe.scan(str(tmp_path), _AGENT_UID, _KEYS, uid_of=_uid_map({1: _ROOT_UID}))
    assert count == 0
    assert offenders == []


def test_unreadable_proc_root_raises_for_main_to_fail_closed(tmp_path):
    # An unreadable/absent process table must surface as an OSError so main()
    # fails closed (exit 4) rather than crashing or vacuously passing.
    missing = tmp_path / "no-such-proc"
    with pytest.raises(OSError):
        probe.scan(str(missing), _AGENT_UID, _KEYS, uid_of=_uid_map({}))


# ---- the cold-boot wait (ss#2420) -----------------------------------------
#
# wait_scan retries ONLY the "no agent process yet" verdict. The three cases
# that matter: a real offender ends the wait instantly (the retry must not
# swallow the signal), a gateway that comes up mid-wait turns a false FATAL
# into a pass, and a gateway that never comes up still fails loud at the
# deadline. Time is injected; no test sleeps.


class _FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def test_wait_never_swallows_a_real_offender(tmp_path):
    # Offender present from the first scan: exit path must be immediate — zero
    # sleeps — even with a generous wait budget.
    _mkproc(tmp_path, 100, {"R2_ACCESS_KEY_ID": _FAKE_VALUE}, comm="hermes")
    clock = _FakeClock()
    count, offenders = probe.wait_scan(
        str(tmp_path),
        _AGENT_UID,
        _KEYS,
        wait_s=120.0,
        uid_of=_uid_map({100: _AGENT_UID}),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert count == 1
    assert len(offenders) == 1
    assert clock.sleeps == []


def test_gateway_arriving_mid_wait_turns_into_a_pass(tmp_path):
    # No agent process for the first two scans, then the gateway appears clean.
    _mkproc(tmp_path, 100, {"PATH": "/usr/bin"}, comm="hermes")
    clock = _FakeClock()
    calls = {"n": 0}

    def uid_of(_proc_root: str, pid: str):
        # The process exists on disk the whole time; ownership resolves to the
        # agent uid only from the third scan on — the cold-boot shape.
        calls["n"] += 1
        return _AGENT_UID if calls["n"] >= 3 else None

    count, offenders = probe.wait_scan(
        str(tmp_path),
        _AGENT_UID,
        _KEYS,
        wait_s=120.0,
        uid_of=uid_of,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert count == 1
    assert offenders == []
    assert len(clock.sleeps) == 2


def test_gateway_never_arriving_still_fails_loud_at_the_deadline(tmp_path):
    # Nothing but root processes, forever: the wait must END, returning the
    # zero-count verdict main() turns into exit 3 — never spin, never pass.
    _mkproc(tmp_path, 1, {"R2_ACCESS_KEY_ID": _FAKE_VALUE}, comm="entrypoint")
    clock = _FakeClock()
    count, offenders = probe.wait_scan(
        str(tmp_path),
        _AGENT_UID,
        _KEYS,
        wait_s=30.0,
        uid_of=_uid_map({1: _ROOT_UID}),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert count == 0
    assert offenders == []
    assert clock.now >= 30.0


def test_zero_wait_keeps_the_original_single_scan_behavior(tmp_path):
    # Direct calls without the flag are unchanged: one scan, immediate verdict.
    clock = _FakeClock()
    count, offenders = probe.wait_scan(
        str(tmp_path),
        _AGENT_UID,
        _KEYS,
        wait_s=0.0,
        uid_of=_uid_map({}),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert count == 0
    assert offenders == []
    assert clock.sleeps == []
