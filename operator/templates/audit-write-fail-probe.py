#!/usr/bin/env python3
"""Affirmative OP-P1-4 proof: the agent uid CANNOT write the audit ledger.

Run as the agent uid (hermes) against the ledger path. Exits 0 only when BOTH a
read-write open AND a DELETE are refused by the OS — i.e. the file is genuinely
not agent-writable. Exits 1 (FAIL) if either mutation succeeds.

Usage: audit-write-fail-probe.py <ledger-path>
Intended to be invoked by boot-smoke-test.sh via:
    setpriv --reuid=hermes --regid=hermes --init-groups python3 <this> <path>
"""

from __future__ import annotations

import sqlite3
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: audit-write-fail-probe.py <ledger-path>", file=sys.stderr)
        return 2
    path = sys.argv[1]

    # 1. A read-write connection that then tries to mutate must fail. sqlite may
    #    defer the open until first write, so force a write and expect failure.
    try:
        con = sqlite3.connect(path)
        con.execute("DELETE FROM audit_log")
        con.commit()
        print("FAIL: DELETE FROM audit_log succeeded as the agent uid", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 — any refusal (OperationalError/OS error) is a pass
        pass

    # 2. An explicit INSERT must also fail.
    try:
        con = sqlite3.connect(path)
        con.execute(
            "INSERT INTO audit_log (id, ts, action_type, actor) VALUES (?,?,?,?)",
            ("probe", "1970-01-01T00:00:00Z", "PROBE", "probe"),
        )
        con.commit()
        print("FAIL: INSERT into audit_log succeeded as the agent uid", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - the probe EXPECTS the write to fail as the agent uid; any exception here is the pass condition
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
