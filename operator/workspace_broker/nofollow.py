"""Look and act below a root without following a link (stdlib only).

WHY (PR #3956 review). ``person_purge`` runs as root over a volume whose
contents the agent uid owns, so anything below the volume root may be a link
the agent planted, and a question asked of a path the caller may not read must
never come back "not there". Two rules, one helper each:

* COULD NOT LOOK IS NEVER ABSENT. :func:`look` calls ``os.lstat`` itself: only
  ``FileNotFoundError`` is absent; any other error raises
  :class:`CannotExamine`. ``Path.exists`` is not used, because on Python 3.14 it
  answers False for a path it was not allowed to look at.
* NOTHING BELOW THE ROOT IS FOLLOWED. :class:`Volume` opens each directory one
  component at a time with ``O_NOFOLLOW``, and every read, delete and rewrite
  here goes through that directory's descriptor. A link where a directory or
  file is expected raises :class:`CannotExamine`; :func:`remove_tree` unlinks a
  link inside a tree and never descends through one.

The root itself is the configured path (``/opt/data`` on a seat, whose parent
is root's) and is opened as given.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import stat
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class CannotExamine(Exception):
    """A location that could not be examined (or acted on). Never an absence."""

    def __init__(self, location: object, why: str) -> None:
        super().__init__(f"{location}: {why}")
        self.location = str(location)
        self.why = why


def why(exc: BaseException) -> str:
    detail = getattr(exc, "strerror", None) or str(exc)
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def look(name: str | Path, dir_fd: int | None, shown: object) -> os.stat_result | None:
    """``lstat`` of ``name``: ``None`` ONLY when it is absent. Anything else
    that stops the look (a permission error, an I/O error) raises
    :class:`CannotExamine`."""
    try:
        return os.lstat(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CannotExamine(shown, why(exc)) from exc


def scan(fd: int, shown: object) -> list[os.DirEntry[str]]:
    """Every entry of the open directory ``fd``; a listing that fails raises."""
    try:
        with os.scandir(fd) as entries:
            return sorted(entries, key=lambda e: e.name)
    except OSError as exc:
        raise CannotExamine(shown, why(exc)) from exc


def require(st: os.stat_result, shown: object, want: Callable[[int], bool], what: str) -> None:
    """Raise unless ``st`` is ``what`` (``want(mode)``); a link never is."""
    if stat.S_ISLNK(st.st_mode):
        raise CannotExamine(shown, "a symbolic link; refused, not followed")
    if not want(st.st_mode):
        raise CannotExamine(shown, f"not {what}; refused")


class Volume:
    """The open volume root, and descriptors for directories below it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        if look(root, None, root) is None:
            raise CannotExamine(root, "the volume root does not exist")
        try:
            self.fd = os.open(root, DIR_FLAGS)
        except OSError as exc:
            raise CannotExamine(root, why(exc)) from exc

    def close(self) -> None:
        os.close(self.fd)

    def parts(self, path: Path) -> tuple[str, ...]:
        try:
            parts = path.relative_to(self.root).parts
        except ValueError:
            raise CannotExamine(path, f"not under the volume root {self.root}; refused") from None
        if any(p in ("", ".", "..") for p in parts):
            raise CannotExamine(path, "not a normalized path; refused")
        return parts

    @contextlib.contextmanager
    def dir(self, path: Path) -> Iterator[int | None]:
        """The descriptor of directory ``path`` (under the root), or ``None``
        when some component of it is absent. A component that is a link, or
        not a directory, or cannot be opened, raises."""
        fd = os.dup(self.fd)
        here = self.root
        try:
            for part in self.parts(path):
                here = here / part
                st = look(part, fd, here)
                if st is None:
                    os.close(fd)
                    fd = -1
                    break
                require(st, here, stat.S_ISDIR, "a directory")
                try:
                    nxt = os.open(part, DIR_FLAGS | NOFOLLOW, dir_fd=fd)
                except OSError as exc:
                    raise CannotExamine(here, why(exc)) from exc
                os.close(fd)
                fd = nxt
            yield fd if fd >= 0 else None
        finally:
            if fd >= 0:
                os.close(fd)


def count_tree(parent: int, name: str, shown: Path) -> int:
    """Entries that are not directories under directory ``name`` of
    ``parent``, walked without following; an unreadable directory raises."""
    try:
        fd = os.open(name, DIR_FLAGS | NOFOLLOW, dir_fd=parent)
    except OSError as exc:
        raise CannotExamine(shown, why(exc)) from exc
    try:
        n = 0
        for entry in scan(fd, shown):
            st = look(entry.name, fd, shown / entry.name)
            if st is not None and stat.S_ISDIR(st.st_mode):
                n += count_tree(fd, entry.name, shown / entry.name)
            elif st is not None:
                n += 1
        return n
    finally:
        os.close(fd)


def remove_tree(parent: int, name: str) -> None:
    """Delete directory ``name`` of ``parent`` and everything in it. A link
    inside is unlinked, never followed; a directory swapped for a link while
    this runs fails the ``O_NOFOLLOW`` open and raises."""
    fd = os.open(name, DIR_FLAGS | NOFOLLOW, dir_fd=parent)
    try:
        with os.scandir(fd) as it:
            entries = list(it)
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                remove_tree(fd, entry.name)
            else:
                os.unlink(entry.name, dir_fd=fd)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent)


def read_json_at(fd: int, name: str, shown: Path) -> tuple[os.stat_result, Any]:
    """``(stat, parsed JSON or None)`` of regular file ``name`` of ``fd``; a
    link or a file that cannot be read raises. Unparseable JSON is ``None``."""
    st = look(name, fd, shown)
    if st is None:
        raise CannotExamine(shown, "vanished while being read")
    require(st, shown, stat.S_ISREG, "a regular file")
    try:
        handle = os.open(name, os.O_RDONLY | NOFOLLOW | _CLOEXEC, dir_fd=fd)
        with os.fdopen(handle, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise CannotExamine(shown, why(exc)) from exc
    try:
        return st, json.loads(data.decode("utf-8"))
    except ValueError:
        return st, None


def rewrite_json_at(fd: int, name: str, payload: Any, st: os.stat_result) -> None:
    """Replace ``name`` of ``fd`` atomically. The new file is created with
    ``O_EXCL | O_NOFOLLOW`` and given the old one's owner and mode by
    descriptor (``fchown``, ``fchmod``), so no path is followed."""
    tmp = f".purge-{secrets.token_hex(8)}.tmp"
    handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | _CLOEXEC, 0o600, dir_fd=fd)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            if os.geteuid() == 0:
                os.fchown(f.fileno(), st.st_uid, st.st_gid)
            os.fchmod(f.fileno(), st.st_mode & 0o777)
        os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp, dir_fd=fd)
        raise
