"""The child process's sandbox: redirects, side-effect blockers, a frozen clock.

Installed once in the child before the site's module is imported:

  * any path whose basename is customer.yaml / customer.yml is redirected to
    the current input file, and any path ending .sock (and every AF_UNIX
    connect) to the current socket target, so a site reading a hard-coded
    seat path reads the synthetic input instead;
  * a subprocess, an exec, a fork, a network connect or DNS lookup, and a
    write outside the run's scratch root raise `Blocked`, a BaseException so a
    site's `except Exception` cannot swallow it and pass it off as behaviour;
  * the clock reads 2026-01-15T12:00:00Z, so a site that dates its answer
    digests the same every day;
  * every environment read is recorded by name, and (when `STATE.track` is
    on) every key read from a customer.yaml load is recorded by path, which is
    how the block a site reads is found by running it rather than reading it.
"""

from __future__ import annotations

import builtins
import datetime as _dt
import io
import os
import socket
import sqlite3
import subprocess
import time
from typing import Any

FROZEN = _dt.datetime(2026, 1, 15, 12, 0, 0, tzinfo=_dt.UTC)
YAML_NAMES = ("customer.yaml", "customer.yml")


class Blocked(BaseException):
    """A side effect the capture refuses to let a site perform."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class _State:
    def __init__(self) -> None:
        self.tmp = ""
        self.yaml_target: str | None = None
        self.yaml_text: str | None = None
        self.sock_target: str | None = None
        self.reads = 0
        self.unix_connects: list[str] = []
        self.blocked: list[str] = []
        self.env_reads: set[str] = set()
        self.track = False
        self.accessed: dict[tuple, str] = {}


STATE = _State()
_ORIG: dict[str, Any] = {}


def _block(kind: str) -> Blocked:
    STATE.blocked.append(kind)
    return Blocked(kind)


def redirect(p: Any) -> Any:
    if isinstance(p, int):
        return p
    try:
        s = os.fspath(p)
    except TypeError:
        return p
    if isinstance(s, bytes):
        return p
    base = s.rsplit("/", 1)[-1]
    if base in YAML_NAMES and STATE.yaml_target is not None and s != STATE.yaml_target:
        return STATE.yaml_target
    if base.endswith(".sock") and STATE.sock_target is not None and s != STATE.sock_target:
        return STATE.sock_target
    return p


def _inside_tmp(p: Any) -> bool:
    try:
        s = os.path.abspath(os.fspath(p))
    except TypeError:
        return True
    if isinstance(s, bytes):
        s = s.decode("utf-8", "replace")
    return bool(STATE.tmp) and (s == STATE.tmp or s.startswith(STATE.tmp + os.sep) or s == os.devnull)


def _check_write(p: Any) -> None:
    if not isinstance(p, int) and not _inside_tmp(p):
        raise _block("write")


def _open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
    target = redirect(file)
    if any(c in mode for c in "wax+"):
        _check_write(target)
    elif STATE.yaml_target is not None and not isinstance(target, int) and os.fspath(target) == STATE.yaml_target:
        STATE.reads += 1
    return _ORIG["open"](target, mode, *args, **kwargs)


_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
    target = redirect(path)
    if flags & _WRITE_FLAGS:
        _check_write(target)
    return _ORIG["os.open"](target, flags, *args, **kwargs)


def _path_fn(name: str) -> Any:
    def wrapper(path: Any, *args: Any, **kwargs: Any) -> Any:
        return _ORIG[name](redirect(path), *args, **kwargs)

    return wrapper


class _SortedScan:
    """os.scandir in name order: directory order differs between filesystems, the table may not."""

    def __init__(self, entries: list[Any]) -> None:
        self._it = iter(entries)

    def __iter__(self) -> Any:
        return self

    def __next__(self) -> Any:
        return next(self._it)

    def __enter__(self) -> Any:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def close(self) -> None:
        return None


def _listdir(path: Any = ".") -> list[Any]:
    return sorted(_ORIG["listdir"](redirect(path)))


def _scandir(path: Any = ".") -> _SortedScan:
    with _ORIG["scandir"](redirect(path)) as it:
        return _SortedScan(sorted(it, key=lambda e: e.name))


def _mutating(name: str) -> Any:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        for a in args[:2]:
            if isinstance(a, str | bytes | os.PathLike):
                _check_write(a)
        return _ORIG[name](*args, **kwargs)

    return wrapper


def _refuse(kind: str) -> Any:
    def wrapper(*_a: Any, **_k: Any) -> Any:
        raise _block(kind)

    return wrapper


def _connect(self: socket.socket, address: Any) -> Any:
    if self.family == getattr(socket, "AF_UNIX", None):
        STATE.unix_connects.append(str(address))
        target = STATE.sock_target if STATE.sock_target is not None else redirect(address)
        return _ORIG["connect"](self, target)
    raise _block("network")


def _connect_ex(self: socket.socket, address: Any) -> int:
    try:
        _connect(self, address)
    except OSError as exc:
        return exc.errno or 1
    return 0


def _sqlite_connect(database: Any, *args: Any, **kwargs: Any) -> Any:
    if isinstance(database, str | bytes | os.PathLike):
        name = os.fspath(database)
        if name not in (":memory:", b":memory:", "") and not _inside_tmp(database):
            raise _block("write")
    return _ORIG["sqlite3.connect"](database, *args, **kwargs)


def _environ_getitem(self: Any, key: Any) -> Any:
    if isinstance(key, str):
        STATE.env_reads.add(key)
    return _ORIG["environ.getitem"](self, key)


class _FrozenDateTime(_dt.datetime):
    @classmethod
    def now(cls, tz: Any = None) -> Any:
        base = cls.fromtimestamp(FROZEN.timestamp(), tz=_dt.UTC)
        return base.astimezone(tz) if tz is not None else base.replace(tzinfo=None)

    @classmethod
    def utcnow(cls) -> Any:
        return cls.fromtimestamp(FROZEN.timestamp(), tz=_dt.UTC).replace(tzinfo=None)

    @classmethod
    def today(cls) -> Any:
        return cls.utcnow()


class _FrozenDate(_dt.date):
    @classmethod
    def today(cls) -> Any:
        return cls(FROZEN.year, FROZEN.month, FROZEN.day)


def _install_files() -> None:
    _ORIG["open"] = builtins.open
    builtins.open = _open  # type: ignore[assignment]
    io.open = _open  # type: ignore[assignment]
    _ORIG["os.open"] = os.open
    os.open = _os_open  # type: ignore[assignment]
    for name in ("stat", "lstat", "access"):
        _ORIG[name] = getattr(os, name)
        setattr(os, name, _path_fn(name))
    _ORIG["listdir"], _ORIG["scandir"] = os.listdir, os.scandir
    os.listdir = _listdir  # type: ignore[assignment]
    os.scandir = _scandir  # type: ignore[assignment]
    for name in (
        "mkdir",
        "makedirs",
        "remove",
        "unlink",
        "rmdir",
        "rename",
        "replace",
        "chmod",
        "symlink",
        "link",
        "utime",
        "truncate",
    ):
        if hasattr(os, name):
            _ORIG[name] = getattr(os, name)
            setattr(os, name, _mutating(name))
    _ORIG["sqlite3.connect"] = sqlite3.connect
    sqlite3.connect = _sqlite_connect  # type: ignore[assignment]


def _install_processes() -> None:
    subprocess.Popen.__init__ = _refuse("subprocess")  # type: ignore[method-assign]
    for name in (
        "system",
        "popen",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "execl",
        "execlp",
        "spawnv",
        "spawnve",
        "posix_spawn",
        "posix_spawnp",
    ):
        if hasattr(os, name):
            setattr(os, name, _refuse("subprocess"))


def _bind(self: socket.socket, address: Any) -> Any:
    if self.family == getattr(socket, "AF_UNIX", None):
        return _ORIG["bind"](self, address)
    raise _block("listen")


def _install_network() -> None:
    _ORIG["connect"] = socket.socket.connect
    _ORIG["bind"] = socket.socket.bind
    socket.socket.bind = _bind  # type: ignore[method-assign]
    socket.socket.connect = _connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _refuse("network")  # type: ignore[assignment]
    socket.gethostbyname = _refuse("network")  # type: ignore[assignment]


def _install_clock_and_env() -> None:
    _dt.datetime = _FrozenDateTime  # type: ignore[misc]
    _dt.date = _FrozenDate  # type: ignore[misc]
    stamp = FROZEN.timestamp()
    time.time = lambda: stamp  # type: ignore[assignment]
    time.time_ns = lambda: int(stamp * 1e9)  # type: ignore[assignment]
    environ_cls = type(os.environ)
    _ORIG["environ.getitem"] = environ_cls.__getitem__
    environ_cls.__getitem__ = _environ_getitem  # type: ignore[method-assign]


def install(tmp: str) -> None:
    """Install every patch. Call once, before the site's module is imported."""
    STATE.tmp = os.path.realpath(tmp)
    _install_files()
    _install_processes()
    _install_network()
    _install_clock_and_env()


def block_fork() -> None:
    """In a per-input child: the site may not fork further."""
    os.fork = _refuse("subprocess")  # type: ignore[assignment]


def original(name: str) -> Any:
    return _ORIG[name]
