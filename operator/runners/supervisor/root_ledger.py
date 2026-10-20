#!/usr/bin/env python3
"""Root's append-only ledgers on the agent's volume, without trusting the volume.

WHY (review 2026-10-20). /opt/data belongs to the agent uid, so anything root
keeps under it can be renamed aside and replaced while the Machine runs: a
directory of the agent's own, or one holding a symlink named like the ledger.
Root following that path with a shell `>>` or `chmod` would append to, or
re-mode, whatever the link names (a credential file, the audit ledger). The
gateway-liveness supervisor's kill ledger was exactly that.

So the ledger is reached the way overlay shared/audit_failure_counter.py
reaches root's loss tally: through a directory fd that must be a real directory
owned by this process's uid and writable by nobody else, with the file opened
relative to it, never through a link, never a FIFO or device, read at most a
bounded tail. A directory that fails the check is refused (exit 2) and the
caller treats the ledger as unavailable. Nothing is ever chowned.

    root_ledger.py append <dir> <name> <line>
    root_ledger.py count  <dir> <name> <cutoff-epoch>   # lines whose first field >= cutoff
"""

from __future__ import annotations

import os
import stat
import sys

_READ_CAP_BYTES = 1024 * 1024


def _open_dir(path: str) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    st = os.fstat(fd)
    if st.st_uid != os.geteuid() or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        os.close(fd)
        raise PermissionError(f"{path}: not owned by uid {os.geteuid()} or writable by others")
    return fd


def _open_file(dfd: int, name: str, flags: int) -> int:
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644, dir_fd=dfd)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise PermissionError(f"{name}: not a regular file")
    return fd


def append(directory: str, name: str, line: str) -> None:
    dfd = _open_dir(directory)
    try:
        fd = _open_file(dfd, name, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.fchmod(fd, 0o644)  # the gate (agent uid) reads it
            os.write(fd, line.rstrip("\n").encode() + b"\n")
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def count(directory: str, name: str, cutoff: int) -> int:
    dfd = _open_dir(directory)
    try:
        try:
            fd = _open_file(dfd, name, os.O_RDONLY)
        except FileNotFoundError:
            return 0
        with os.fdopen(fd, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if size > _READ_CAP_BYTES:
                fh.seek(size - _READ_CAP_BYTES)
                fh.readline()
            raw = fh.read(_READ_CAP_BYTES)
    finally:
        os.close(dfd)
    n = 0
    for line in raw.decode("ascii", errors="replace").splitlines():
        ts = line.split(" ", 1)[0]
        if ts.isdigit() and int(ts) >= cutoff:
            n += 1
    return n


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 4 and argv[0] == "append":
            append(argv[1], argv[2], argv[3])
            return 0
        if len(argv) == 4 and argv[0] == "count":
            print(count(argv[1], argv[2], int(argv[3])))
            return 0
    except (OSError, ValueError) as exc:
        print(f"root_ledger: refused: {exc}", file=sys.stderr)
        return 2
    print("usage: root_ledger.py append|count <dir> <name> <line|cutoff>", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
