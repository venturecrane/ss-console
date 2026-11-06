"""Opening an untrusted Word package as root: a bounded zip, a parser that cannot expand or fetch.

WHY. The no-client-text check (``blank_form.py``) runs in the root intake on a
``.docx`` that came out of a firm's case system, so it reads a file an outsider
can influence, as root. Two classes of input must never get the chance to work:

* **XML that expands or fetches.** An entity bomb, an external entity, a DTD
  that pulls a file or a URL. The overlay has neither ``defusedxml`` nor
  ``lxml`` (``operator/requirements/hermes-overlay.txt``), and its existing
  answer to untrusted XML (``shared/sealed_reader.py``) is to refuse any
  DOCTYPE or ENTITY before parsing. This module does the same structurally: it
  drives the expat parser itself, with a handler on every declaration that
  REFUSES the document (a DOCTYPE, an entity declaration, an unparsed entity, a
  notation, an external entity reference) and parameter entities switched off.
  A Word part never declares any of these, so a part that does is refused with
  a plain reason, never parsed around. It builds its own small tree, so no
  library parser that resolves anything is ever handed the bytes.
* **A zip that expands.** Every bound below is checked against the zip's
  directory BEFORE any member is read: member count, each member's
  uncompressed size, the total, the compression ratio, an encrypted member, a
  duplicate name, and a name that is absolute or climbs out with ``..``. A
  member whose header lies about its size fails the zip's own CRC check on
  read (the reader stops at the declared size), so the directory's numbers are
  the ceiling.

THE LIMITS, sized for a blank form: the real accepted files are 20 to 90 KB, a
dozen to thirty parts, the largest a letterhead picture.
"""

from __future__ import annotations

import io
import pyexpat
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field

MAX_PACKAGE_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 200
MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 12 * 1024 * 1024
#: Uncompressed over compressed. XML deflates about ten to one; a bomb is a
#: thousand to one. Checked on members past the floor, where it can matter.
MAX_RATIO = 100
RATIO_FLOOR_BYTES = 64 * 1024

_DRIVE = re.compile(r"\A[A-Za-z]:")


class PackageRefused(ValueError):
    """The package, or one part of it, is refused. The message is the reason."""


# ---- the zip ------------------------------------------------------------------------


def _name_problem(name: str) -> str:
    if not name or name.startswith(("/", "\\")) or _DRIVE.match(name) or "\\" in name:
        return "a part's name is absolute"
    if ".." in name.split("/"):
        return "a part's name climbs out of the package (..)"
    return ""


def _member_problem(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x1:
        return "a part is encrypted"
    if info.file_size > MAX_MEMBER_BYTES:
        return f"a part expands to {info.file_size} bytes, over {MAX_MEMBER_BYTES}"
    if info.file_size and not info.compress_size:
        return "a part claims content with no stored bytes"
    if info.file_size > RATIO_FLOOR_BYTES and info.file_size > MAX_RATIO * info.compress_size:
        return f"a part compresses more than {MAX_RATIO} to one (a zip bomb's shape)"
    return ""


def _directory_problems(infos: list[zipfile.ZipInfo]) -> str:
    if len(infos) > MAX_MEMBERS:
        return f"the package has {len(infos)} parts, over {MAX_MEMBERS}"
    names = [i.filename for i in infos]
    if len(set(names)) != len(names):
        return "the package names one part twice"
    for info in infos:
        why = _name_problem(info.filename) or _member_problem(info)
        if why:
            return why
    total = sum(i.file_size for i in infos)
    if total > MAX_TOTAL_BYTES:
        return f"the package expands to {total} bytes, over {MAX_TOTAL_BYTES}"
    return ""


@dataclass
class Package:
    """A zip whose directory passed every bound. Read parts through :meth:`read`."""

    _zip: zipfile.ZipFile
    names: frozenset[str] = field(default_factory=frozenset)

    def namelist(self) -> list[str]:
        return sorted(self.names)

    def read(self, name: str) -> bytes:
        try:
            return self._zip.read(name)
        except (KeyError, zipfile.BadZipFile, RuntimeError, OSError, EOFError) as exc:
            raise PackageRefused(f"the part cannot be read ({type(exc).__name__})") from exc


def open_package(blob: bytes) -> Package:
    """``blob`` as a bounded package; raises PackageRefused. Reads no member."""
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        raise PackageRefused("the file is empty")
    if len(blob) > MAX_PACKAGE_BYTES:
        raise PackageRefused(f"the file is {len(blob)} bytes, over {MAX_PACKAGE_BYTES}")
    try:
        z = zipfile.ZipFile(io.BytesIO(bytes(blob)))
        infos = z.infolist()
    except (zipfile.BadZipFile, OSError, EOFError, ValueError) as exc:
        raise PackageRefused("the file is not a Word package (not a zip)") from exc
    why = _directory_problems(infos)
    if why:
        raise PackageRefused(why)
    return Package(z, frozenset(i.filename for i in infos))


# ---- the XML ------------------------------------------------------------------------


class Node:
    """One element: ``tag`` as ``{namespace}local``, ``attrib``, the ``text``
    before its first child, and its children. The subset of ElementTree's
    element the check reads."""

    __slots__ = ("attrib", "children", "tag", "text")

    def __init__(self, tag: str, attrib: dict[str, str]) -> None:
        self.tag = tag
        self.attrib = attrib
        self.text: str | None = None
        self.children: list[Node] = []

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.attrib.get(key, default)

    def __iter__(self) -> Iterator[Node]:
        return iter(self.children)

    def __len__(self) -> int:
        return len(self.children)

    def iter(self, tag: str | None = None) -> Iterator[Node]:
        stack: list[Node] = [self]
        while stack:
            node = stack.pop()
            if tag is None or node.tag == tag:
                yield node
            stack.extend(reversed(node.children))


def _qname(name: str) -> str:
    return "{" + name if "}" in name else name


def _refuse(*_args: object) -> None:
    raise PackageRefused("the part declares a DTD or an entity, which no Word part does")


def _external(*_args: object) -> int:
    raise PackageRefused("the part references an external entity")


def parse_xml(data: bytes) -> Node:
    """The part's element tree. Raises PackageRefused for a DTD, any entity
    declaration or reference beyond XML's five, or XML that does not parse."""
    parser = pyexpat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(pyexpat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.buffer_text = True
    for handler in (
        "StartDoctypeDeclHandler",
        "EntityDeclHandler",
        "UnparsedEntityDeclHandler",
        "NotationDeclHandler",
        "ElementDeclHandler",
        "AttlistDeclHandler",
    ):
        setattr(parser, handler, _refuse)
    parser.ExternalEntityRefHandler = _external
    stack: list[Node] = []
    roots: list[Node] = []

    def start(name: str, attrs: dict[str, str]) -> None:
        node = Node(_qname(name), {_qname(k): v for k, v in attrs.items()})
        (stack[-1].children if stack else roots).append(node)
        stack.append(node)

    def end(_name: str) -> None:
        stack.pop()

    def chars(text: str) -> None:
        if stack and not stack[-1].children:
            stack[-1].text = (stack[-1].text or "") + text

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    try:
        parser.Parse(data, True)
    except pyexpat.ExpatError as exc:
        raise PackageRefused(f"the part is not well-formed XML (line {exc.lineno})") from exc
    if len(roots) != 1:
        raise PackageRefused("the part has no single root element")
    return roots[0]


__all__ = [
    "MAX_MEMBERS",
    "MAX_MEMBER_BYTES",
    "MAX_PACKAGE_BYTES",
    "MAX_RATIO",
    "MAX_TOTAL_BYTES",
    "RATIO_FLOOR_BYTES",
    "Node",
    "Package",
    "PackageRefused",
    "open_package",
    "parse_xml",
]
