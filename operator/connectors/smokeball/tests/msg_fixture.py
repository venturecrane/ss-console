"""A real Outlook .msg, built in memory from invented content.

The job lanes' tests fake ``extract_msg.Message`` because no test had an OLE
writer. This is one: a minimal Compound File Binary (version 3, 512-byte
sectors, a mini stream for the small streams) holding the MAPI streams an
Outlook message carries, so ``read_document`` is tested against the library
it runs on the seat, not against a stand-in of its shape. Every value in it is
made up; no client text.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone

_FREE, _END, _FATSECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
_NOSTREAM = 0xFFFFFFFF
_SECTOR, _MINI, _CUTOFF = 512, 64, 4096


@dataclass
class _Node:
    name: str
    kind: int  # 1 storage, 2 stream, 5 root
    data: bytes = b""
    kids: list[_Node] = field(default_factory=list)
    index: int = 0
    start: int = _END


def _chain(fat: list[int], first: int, count: int) -> None:
    for i in range(count):
        fat[first + i] = first + i + 1 if i < count - 1 else _END


def _cfb(root: _Node) -> bytes:
    """Serialize a storage tree as a compound file."""
    nodes: list[_Node] = []

    def walk(n: _Node) -> None:
        n.index = len(nodes)
        nodes.append(n)
        for k in n.kids:
            walk(k)

    walk(root)
    # The mini stream: every stream under the cutoff, in 64-byte mini sectors.
    mini = bytearray()
    minifat: list[int] = []
    big: list[_Node] = []
    for n in nodes:
        if n.kind != 2 or not n.data:
            continue
        if len(n.data) >= _CUTOFF:
            big.append(n)
            continue
        count = -(-len(n.data) // _MINI)
        n.start = len(minifat)
        minifat.extend([0] * count)
        _chain(minifat, n.start, count)
        mini += n.data + b"\0" * (count * _MINI - len(n.data))
    n_dir = -(-len(nodes) // 4)
    n_minifat = -(-len(minifat) // 128) if minifat else 0
    n_mini = -(-len(mini) // _SECTOR)
    n_big = sum(-(-len(b.data) // _SECTOR) for b in big)
    body = n_dir + n_minifat + n_mini + n_big
    n_fat = 1
    while (body + n_fat) > n_fat * 128:
        n_fat += 1
    fat = [_FREE] * (n_fat * 128)
    at = 0
    dir_start = at
    _chain(fat, at, n_dir)
    at += n_dir
    minifat_start = at if n_minifat else _END
    _chain(fat, at, n_minifat)
    at += n_minifat
    mini_start = at if n_mini else _END
    _chain(fat, at, n_mini)
    at += n_mini
    for b in big:
        count = -(-len(b.data) // _SECTOR)
        b.start = at
        _chain(fat, at, count)
        at += count
    fat_start = at
    for i in range(n_fat):
        fat[at + i] = _FATSECT
    root.start, root.data = mini_start, bytes(mini)

    def entry(n: _Node) -> bytes:
        name = n.name.encode("utf-16-le") + b"\0\0"
        kids = n.kids
        child = kids[0].index if kids else _NOSTREAM
        return (
            name.ljust(64, b"\0")
            + struct.pack("<HBB", len(name), n.kind, 1)
            + struct.pack("<III", _NOSTREAM, _NOSTREAM, child)
            + b"\0" * 16
            + b"\0" * 4
            + b"\0" * 16
            + struct.pack("<IQ", n.start if (n.data or n.kind == 5) else _END, len(n.data))
        )

    # Siblings are a right-hand chain under each storage.
    raw_entries = []
    for n in nodes:
        e = bytearray(entry(n))
        raw_entries.append(e)
    for n in nodes:
        for a, b in zip(n.kids, n.kids[1:]):
            struct.pack_into("<I", raw_entries[a.index], 72, b.index)
    directory = b"".join(raw_entries)
    directory += b"\0" * (n_dir * _SECTOR - len(directory))
    # Unused directory slots are empty entries with no siblings.
    for i in range(len(nodes), n_dir * 4):
        off = i * 128
        directory = directory[: off + 68] + struct.pack("<III", _NOSTREAM, _NOSTREAM, _NOSTREAM) + directory[off + 80 :]
    mf = b"".join(struct.pack("<I", x) for x in minifat)
    mf += struct.pack("<I", _FREE) * (n_minifat * 128 - len(minifat))
    bigdata = b"".join(b.data + b"\0" * (-len(b.data) % _SECTOR) for b in big)
    header = (
        bytes.fromhex("d0cf11e0a1b11ae1")
        + b"\0" * 16
        + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6)
        + b"\0" * 6
        + struct.pack("<IIIIIIIII", 0, n_fat, dir_start, 0, _CUTOFF, minifat_start, n_minifat, _END, 0)
    )
    difat = [fat_start + i for i in range(n_fat)] + [_FREE] * (109 - n_fat)
    header += b"".join(struct.pack("<I", x) for x in difat)
    fatbytes = b"".join(struct.pack("<I", x) for x in fat)
    return header + directory + mf + bytes(mini) + b"\0" * (n_mini * _SECTOR - len(mini)) + bigdata + fatbytes


def build_ole(streams: dict[str, bytes]) -> bytes:
    """Any other OLE file (a legacy .doc or .xls is one): these streams at the root."""
    return _cfb(_Node("Root Entry", 5, kids=[_Node(name, 2, data) for name, data in streams.items()]))


def _text(prop: int, value: str) -> _Node:
    return _Node(f"__substg1.0_{prop:04X}001F", 2, value.encode("utf-16-le"))


def _props(header: bytes, entries: list[bytes]) -> _Node:
    return _Node("__properties_version1.0", 2, header + b"".join(entries))


def _prop_entry(tag: int, value: bytes) -> bytes:
    return struct.pack("<II", tag, 0x6) + value.ljust(8, b"\0")


def _filetime(when: datetime) -> bytes:
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    return struct.pack("<Q", int((when - epoch).total_seconds() * 10_000_000))


def _recipient(i: int, kind: int, name: str, email: str) -> _Node:
    """One recipient (kind 1 To, 2 Cc), as the reader builds To and Cc from."""
    return _Node(
        f"__recip_version1.0_#{i:08X}",
        1,
        kids=[
            _props(b"\0" * 8, [_prop_entry(0x0C150003, struct.pack("<I", kind))]),
            _text(0x3001, name),
            _text(0x3002, "SMTP"),
            _text(0x3003, email),
            _text(0x39FE, email),
        ],
    )


def build_msg(
    *,
    subject: str,
    sender: tuple[str, str],
    to: list[tuple[str, str]],
    body: str,
    sent: datetime,
    cc: list[tuple[str, str]] = (),  # type: ignore[assignment]
    attachments: list[tuple[str, bytes]] = (),  # type: ignore[assignment]
) -> bytes:
    """An Outlook message (IPM.Note) as Outlook saves it, from invented values.
    ``sender`` and each recipient are (display name, address)."""
    when = _filetime(sent)
    people = [(1, *p) for p in to] + [(2, *p) for p in cc]
    kids = [
        # The named-property map Outlook always writes; empty here, as no named
        # property is used, but the reader refuses a message without it.
        _Node(
            "__nameid_version1.0",
            1,
            kids=[_Node(f"__substg1.0_000{n}0102", 2) for n in (2, 3, 4)],
        ),
        _props(
            b"\0" * 8 + struct.pack("<IIII", len(people), len(attachments), len(people), len(attachments)) + b"\0" * 8,
            [_prop_entry(0x00390040, when), _prop_entry(0x0E060040, when)],
        ),
        _text(0x001A, "IPM.Note"),
        _text(0x0037, subject),
        _text(0x1000, body),
        _text(0x0C1A, sender[0]),
        _text(0x0C1E, "SMTP"),
        _text(0x0C1F, sender[1]),
        _text(0x5D01, sender[1]),
    ]
    kids += [_recipient(i, kind, name, email) for i, (kind, name, email) in enumerate(people)]
    for i, (name, data) in enumerate(attachments):
        kids.append(
            _Node(
                f"__attach_version1.0_#{i:08X}",
                1,
                kids=[
                    _props(b"\0" * 8, [_prop_entry(0x37050003, struct.pack("<I", 1))]),
                    _text(0x3707, name),
                    _text(0x3704, name[:12]),
                    _Node("__substg1.0_37010102", 2, data),
                ],
            )
        )
    return _cfb(_Node("Root Entry", 5, kids=kids))
