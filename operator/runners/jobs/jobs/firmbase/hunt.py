"""Finding the file a class's base should be made from. Read-only; it adopts nothing.

Why: the base for a class was chosen by hand, from file ids a 2026-09-21 run happened
to name. The hunt asks the document system itself: every ``.docx`` on the firm's
matters, the ones whose NAME says they are of the class, then what the FILE says.

* Out: a file with Operator lineage (``adopt.operator_authored``), unless it is an
  accepted save recorded for exactly these bytes. A document system's history names
  staff as the creator of files the Operator filed, so only the file is read.
* Ranked by firm authorship (a staff member named in the core properties, or in the
  signature, the last lines), by recency, and by whether its length is in the class's
  band. An accepted save outranks every firm file, whatever they score.
* "Could not look" is never "none": a matter whose listing failed and a file that
  would not open are counted in ``Hunted.counts``.

Names here carry client names: a report of a hunt prints file ids, never ``name``.
"""

from __future__ import annotations

import hashlib
import io
import re
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from lxml import etree
from smokeball_connector.library import ListingFailed, list_matter_files

from .adopt import CORE, may_adopt, operator_authored
from .base import DOCUMENT, W, Source

#: Above any score a firm file can reach.
ACCEPTED = 1000
BY_PROPERTIES = 40
BY_SIGNATURE = 20
IN_BAND = 30
RECENT = 25
RECENT_YEARS = 5
SIGNATURE_LINES = 15
#: The document system's download refuses more than this, and no form is this large.
MAX_BYTES = 25 * 1024 * 1024
_MATTERS_PAGE = 500


@dataclass(frozen=True)
class ClassRule:
    """``names``: what a file of the class is called; ``not_names``: what also matches
    and is something else; ``words``: the band of a real one's length."""

    names: re.Pattern[str]
    not_names: re.Pattern[str]
    words: tuple[int, int]


RULES = {
    "mediation_brief": ClassRule(
        re.compile(r"(?i)mediat|settlement\s*conf|(?<![a-z])msc(?![a-z])"),
        re.compile(
            r"(?i)(?<![a-z])(notice|ntc|stip\w*|invoice|bill\w*|agreement|confirm\w*|order|mediators?|cv|bio|"
            r"fees?|email|letter|ltr|transcript|calendar\w*|conflict\w*|disclosure|attendance|checklist|"
            r"rules?|proposal|request|memo|memorandum|deposit|payment|receipt)(?![a-z])"
        ),
        (1500, 15000),
    ),
    "memo": ClassRule(
        # A firm's memo is not always called one: two that its administrator named by id
        # on 2026-09-21 are a "case summary" and a "liability analysis".
        re.compile(
            r"(?i)(?<![a-z])memo(randum)?s?(?![a-z])"
            r"|case[\s_-]*(evaluation|eval|analysis|assessment|review|strategy|summary|overview)"
            r"|liability[\s_-]*(analysis|assessment|evaluation|eval|review|summary|opinion|report)"
            r"|(legal|causation|damages|coverage)[\s_-]*analysis"
        ),
        re.compile(
            r"(?i)points?\s*(and|&)\s*auth|mediation[\s_-]*memorandum|(?<![a-z])(p\s*&\s*a|mpa|costs?|motion|opposition|oppo|reply|"
            r"demurrer|msj|understanding|mou|ex\s*parte|summary\s*judgment)(?![a-z])"
        ),
        (500, 12000),
    ),
}


@dataclass(frozen=True)
class Candidate:
    """A file the hunt would put forward, its score and why. ``name`` is the document
    system's, so it carries a client's name."""

    file_id: str
    matter_id: str
    name: str
    score: int
    reasons: tuple[str, ...]
    sha256: str = ""
    saved_at: str = ""


@dataclass
class Hunted:
    """The ranking, best first, and the count of everything that did not reach it."""

    candidates: list[Candidate] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    #: The file ids behind a count, for the counts that name files (a candidate
    #: the kind's plan refused, by code). Ids only, never a name.
    files: dict[str, list[str]] = field(default_factory=dict)

    def bump(self, key: str, by: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + by

    def note(self, key: str, file_id: str) -> None:
        """Count ``key`` and record which file it was."""
        self.bump(key)
        self.files.setdefault(key, []).append(file_id)


def classify(cls: str, name: str) -> bool:
    """The file's name says it is of the class."""
    rule = RULES[cls]
    return bool(rule.names.search(name)) and not rule.not_names.search(name)


def _items(resp: Any) -> list[dict[str, Any]]:
    return (resp.get("value") or []) if isinstance(resp, dict) else []


def firm_matters(client: Any, statuses: Iterable[str] = ("Open", "Pending"), closed_since: str = "") -> list[str]:
    """The ids of the firm's matters, leads aside; closed ones when closed on or after
    ``closed_since``, since a firm's closed matters run to thousands."""
    out: list[str] = []
    for status in [*statuses, *(["Closed"] if closed_since else [])]:
        offset = 0
        while True:
            page = _items(client.get("/matters", Status=status, IsLead=False, Limit=_MATTERS_PAGE, Offset=offset))
            for m in page:
                if status != "Closed" or str(m.get("closedDate") or "") >= closed_since:
                    out.append(str(m.get("id")))
            if len(page) < _MATTERS_PAGE:
                break
            offset += _MATTERS_PAGE
    return out


def staff_names(client: Any) -> list[str]:
    """The firm's people as its document system lists them."""
    rows = _items(client.get("/staff", Limit=_MATTERS_PAGE, Offset=0))
    names = (" ".join(str(p.get(k) or "").strip() for k in ("firstName", "lastName")).strip() for p in rows)
    return sorted({n for n in names if " " in n})


def docx_rows(client: Any, matter_ids: Iterable[str], hunted: Hunted, pause: float = 0.0) -> list[dict[str, Any]]:
    """Every live ``.docx`` on these matters, its row as listed, with its matter."""
    rows: list[dict[str, Any]] = []
    for mid in matter_ids:
        hunted.bump("matters_listed")
        try:
            files = list_matter_files(client, mid)
        except ListingFailed:
            hunted.bump("matters_unlisted")
            continue
        for f in files:
            live = not (f.get("isDeleted") or f.get("isCancelled"))
            if live and str(f.get("fileExtension") or "").lower() == ".docx":
                rows.append({**f, "matter_id": mid})
        time.sleep(pause)
    hunted.bump("docx_listed", len(rows))
    return rows


def _person(name: str) -> re.Pattern[str]:
    """A person by first and last name, "Chris" for "Christopher" and either way round,
    a middle initial allowed."""
    first, last = name.split()[0], name.split()[-1]
    return re.compile(rf"(?i)(?<![a-z]){re.escape(first[:4])}[a-z]*\.?\s+([a-z]\.?\s+)?{re.escape(last)}(?![a-z])")


def _core_people(z: zipfile.ZipFile) -> str:
    root = etree.fromstring(z.read(CORE))
    wanted = ("creator", "lastModifiedBy")
    return " | ".join(e.text or "" for e in root if etree.QName(e).localname in wanted)


def _paragraphs(z: zipfile.ZipFile) -> list[str]:
    body = etree.fromstring(z.read(DOCUMENT)).find(f"{W}body")
    lines = ("".join(t.text or "" for t in p.iter(f"{W}t")).strip() for p in body.iter(f"{W}p"))
    return [x for x in lines if x]


def _recency(saved_at: str, now: datetime) -> tuple[int, str]:
    try:
        then = datetime.fromisoformat(saved_at.replace("Z", "+00:00"))
    except ValueError:
        return 0, "no save date"
    age = max(0.0, (now - then.astimezone(timezone.utc)).days / 365.25)
    points = round(RECENT * max(0.0, 1 - age / RECENT_YEARS))
    return points, f"saved {age:.1f} years ago"


def score(cls: str, blob: bytes, saved_at: str, staff: Iterable[str], now: datetime) -> tuple[int, list[str]]:
    """Firm authorship, recency, length, and the reason for each. Reasons name no one."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    people = [_person(n) for n in staff]
    paragraphs = _paragraphs(z)
    total, reasons = 0, []
    core = _core_people(z)
    if any(p.search(core) for p in people):
        total += BY_PROPERTIES
        reasons.append("firm staff in the core properties")
    tail = "\n".join(paragraphs[-SIGNATURE_LINES:])
    if any(p.search(tail) for p in people):
        total += BY_SIGNATURE
        reasons.append("firm staff in the signature")
    if not reasons:
        reasons.append("no firm staff named")
    points, age = _recency(saved_at, now)
    total += points
    reasons.append(age)
    words = sum(len(x.split()) for x in paragraphs)
    lo, hi = RULES[cls].words
    total += IN_BAND if lo <= words <= hi else 0
    reasons.append(f"{words} words, {'in' if lo <= words <= hi else 'outside'} the band {lo}-{hi}")
    return total, reasons


def names_person(blob: bytes, name: str) -> bool:
    """The file names this person in its core properties or in its signature, the
    last lines: what makes a file one attorney's own (``rank(person=...)``)."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    who = _person(name)
    return bool(who.search(_core_people(z)) or who.search("\n".join(_paragraphs(z)[-SIGNATURE_LINES:])))


def _operator_lineage(blob: bytes) -> bool | None:
    """None: the file would not open as a Word file."""
    try:
        return operator_authored(blob)
    except (KeyError, zipfile.BadZipFile, etree.XMLSyntaxError):
        return None


def _accepted_candidates(
    accepted: Iterable[Source], read: Callable[[str, str], bytes], saved: dict[str, str], now: datetime, hunted: Hunted
) -> list[Candidate]:
    """A recorded acceptance counts for exactly its bytes: a file saved since then is
    no longer what was accepted, and competes as whatever the file now is."""
    out = []
    for s in accepted:
        blob = read(s.matter_id, s.file_id)
        if not (s.version and s.accepted and may_adopt(blob, s) and hashlib.sha256(blob).hexdigest() == s.sha256):
            hunted.bump("accepted_not_these_bytes")
            continue
        at = saved.get(s.file_id, "")
        points, age = _recency(at, now)
        reasons = (f"accepted save, version {s.version}", age)
        out.append(Candidate(s.file_id, s.matter_id, s.name, ACCEPTED + points, reasons, s.sha256, at))
    return out


def _read_row(
    row: dict[str, Any], accepted_ids: set[str], read: Callable[[str, str], bytes], hunted: Hunted
) -> bytes | None:
    """The bytes of a row worth scoring; None, counted, for the rest."""
    fid = str(row.get("id"))
    if fid in accepted_ids:
        hunted.bump("already_accepted")
        return None
    try:
        blob = read(str(row["matter_id"]), fid)
    except Exception:  # noqa: BLE001 - any transport or refusal: could not look, never "none"
        hunted.bump("unreadable")
        return None
    lineage = _operator_lineage(blob)
    if lineage is None:
        hunted.bump("unreadable")
        return None
    if lineage:
        hunted.bump("operator_authored")
        return None
    return blob


def rank(
    cls: str,
    rows: list[dict[str, Any]],
    read: Callable[[str, str], bytes],
    *,
    staff: Iterable[str],
    accepted: Iterable[Source] = (),
    now: datetime | None = None,
    max_reads: int = 200,
    hunted: Hunted | None = None,
    person: str | None = None,
) -> Hunted:
    """The ranking of ``rows`` for ``cls``. ``read(matter_id, file_id)``: the file's
    bytes. Newest first up to ``max_reads``; what was left unread is counted.
    ``person``: only files that name this person, by their case-system name, in
    the core properties or the signature (one attorney's own files)."""
    out = hunted if hunted is not None else Hunted()
    now = now or datetime.now(timezone.utc)
    staff = list(staff)
    saved_at = {str(r.get("id")): str(r.get("dateModified") or "") for r in rows}
    best = _accepted_candidates(accepted, read, saved_at, now, out)
    taken = {c.file_id for c in best}
    named = [r for r in rows if classify(cls, str(r.get("name") or "")) and (r.get("sizeBytes") or 0) <= MAX_BYTES]
    out.bump("name_matched", len(named))
    named.sort(key=lambda r: str(r.get("dateModified") or ""), reverse=True)
    out.bump("not_read", max(0, len(named) - max_reads))
    seen = {c.sha256 for c in best}
    for row in named[:max_reads]:
        blob = _read_row(row, taken, read, out)
        if blob is None:
            continue
        digest = hashlib.sha256(blob).hexdigest()
        if digest in seen:
            out.bump("same_bytes")
            continue
        seen.add(digest)
        if person and not names_person(blob, person):
            out.bump("not_this_person")
            continue
        saved = str(row.get("dateModified") or "")
        points, reasons = score(cls, blob, saved, staff, now)
        best.append(
            Candidate(
                str(row.get("id")),
                str(row["matter_id"]),
                str(row.get("name") or ""),
                points,
                tuple(reasons),
                digest,
                saved,
            )
        )
    out.candidates = sorted(best, key=lambda c: (c.score, c.saved_at), reverse=True)
    return out


def hunt(
    cls: str,
    client: Any,
    *,
    accepted: Iterable[Source] = (),
    closed_since: str = "",
    now: datetime | None = None,
    pause: float = 0.0,
    person: str | None = None,
) -> Hunted:
    """The whole hunt through the connector's metadata and download calls: reads only."""

    def read(matter_id: str, file_id: str) -> bytes:
        return client.download_file(matter_id, file_id)[1]

    out = Hunted()
    rows = docx_rows(client, firm_matters(client, closed_since=closed_since), out, pause)
    staff = staff_names(client)
    return rank(cls, rows, read, staff=staff, accepted=accepted, now=now, hunted=out, person=person)
