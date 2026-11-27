"""What a paper in the matter file shows about itself, by code (the ``find`` stage's half).

Split from ``served_find`` (2026-11-20, the second $0 replay on three real
matters). Each rule answers a question the finder asked wrongly there:

* :func:`was_served`: does a response paper show it was SERVED? Only a served
  response answers a set. SMD's own drafts awaiting attorney review and the
  firm's unserved drafts are Word response papers too (8 across the three
  matters), and counting them ended all three ``served_none``. A proof of
  service counts only when executed (:func:`executed_proof`): a firm's own
  drafts carry a blank template one (D8, 2026-11-26).
* :func:`draft_name` and :func:`draft_of`: a file named as a draft never
  answers, and a draft of a set still owed makes the job a revision.
* :func:`form_signature`: two copies of one served FORM set (an emailed copy
  beside the filed one) carry no text that tells them apart from two different
  forms; the $0 pixel read's checked numbers and form revision do.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from . import sets
from .served_slice import TITLES

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DATE = rf"(?:(?:{_MONTHS})\.?\s+\d{{1,2}},?\s+\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}})"
#: A proof of service heading, as a caption prints it.
_PROOF = re.compile(r"(?m)^[ \t|]*PROOF\s+OF\s+(?:ELECTRONIC\s+)?SERVICE\b")
#: "Dated: March 3, 2026" or "served on 3/3/2026", with a date.
_DATED = re.compile(rf"(?i)\b(?:dated|served)\b[^\n]{{0,40}}?{_DATE}")
#: A signature block after the date: "/s/", "By:", or "Attorneys for".
_SIGNED = re.compile(r"(?i)/s/|^[ \t|]*By:|\bAttorneys?\s+for\b", re.M)
_METHOD = (
    r"BY\s+(?:U\.?S\.?\s+)?(?:FIRST[\s-]+CLASS\s+)?"
    r"(?:MAIL|FAX|FACSIMILE|PERSONAL\s+(?:SERVICE|DELIVERY)|OVERNIGHT|FEDERAL\s+EXPRESS|E[\s-]?MAIL|ELECTRONIC)"
)
#: A service-method line marked as chosen: "[X] BY MAIL", "x By Electronic Mail",
#: "☒ BY FAX", or the method in capitals first on its line, no blank leader ("BY MAIL.").
#: A "____ BY MAIL.", "[ ] BY MAIL" or "☐ BY MAIL" line is a template's unchosen option.
_MARKED = re.compile(
    rf"(?m)^[ \t|]*(?:\[\s*[Xx✓✔]\s*\]|\(\s*[Xx✓✔]\s*\)|[Xx✓✔☒☑])[ \t._:-]*(?i:{_METHOD})|^[ \t|]*{_METHOD}"
)
#: "Served by email on October 30, 2026": a statement that service was made.
_SERVED_BY = re.compile(
    r"(?i)\bserved\s+(?:by|via)\s+(?:e-?mail|electronic\s+(?:mail|service)|(?:u\.?s\.?\s+)?mail|fax|facsimile"
    rf"|personal\s+(?:service|delivery)|overnight\s+\w+)\b[^\n]{{0,40}}?\bon\s+{_DATE}"
)
#: "/s/ Jane Example": an electronic signature with a name after it, never "/s/ ____".
_S_SIGNED = re.compile(r"/s/[ \t]*[A-Za-z]")
#: "Executed on November 17, 2026": a real date, never a blank.
_EXECUTED = re.compile(rf"(?i)\bExecuted\s+on\s+{_DATE}")
#: A declarant's typed name on its own line: two to four capitalized words.
_DECLARANT = re.compile(r"(?m)^[ \t|]*[A-Z][A-Za-z.'’-]+(?:[ \t]+[A-Z][A-Za-z.'’-]*){1,3}[ \t]*$")
#: A file NAMED as SMD's or the Operator's: "DRAFT ..." first, "for attorney review"
#: (``ours_written``: never wider than this, 2026-11-24 review).
_DRAFT_START = re.compile(r"(?i)^\s*DRAFT\b|for\s+attorney\s+review")
#: The Operator's own stamp, as its agent names a paper: "... by A&P Operator Agent".
STAMP = re.compile(r"(?i)\bby\s+(?:[A-Za-z0-9&.'’]+\s+){0,3}?Operator\s+Agent\b")
#: A draft by its name: DRAFT as its own token first or last, or followed by a
#: date or a version ("... Set One DRAFT 2026-09-23 by ..."); never "Draft Lease".
_DRAFT_NAME = re.compile(
    r"(?i)^\s*DRAFT\b|\bDRAFT\s*(?:\.[a-z0-9]{2,4})?\s*$"
    r"|\bDRAFT[\s_-]+(?:v(?:er(?:sion)?)?\.?\s*\d|\d{1,4}[-/.]\d{1,2})|for\s+attorney\s+review"
)
#: "DRAFT ... Responses to <kind>, Set One", with or without the comma (measured
#: 2026-11-20: 0 of 15 real draft names carried it), the number in digits or words.
_DRAFT_OF = re.compile(r"(?i)\bDRAFT\b.*?\bResponses?\s+to\s+(?P<kind>.+?),?\s+Set\s+(?:No\.?\s*)?(?P<n>\w+)")
_TITLE_WORDS = {k: re.compile(v) for k, v in TITLES.items()}
#: The attorney asks for amended, further or supplemental responses of OURS (code,
#: no model): the word qualifies the responses themselves ("amended responses",
#: "further responses to ...", "supplemental answers", "amend our responses"). The
#: other side's amended or supplemental SETS ("their amended RFPs") never match.
_AMENDED = re.compile(
    r"(?i)\b(?:amended|further|supplemental|supplementary)\s+(?:(?:verified|written|discovery)\s+)?"
    r"(?:responses?|answers?)\b"
    r"|\b(?:amend|supplement)\s+(?:our\s+|the\s+|its\s+|plaintiff'?s\s+|defendant'?s\s+)?(?:responses?|answers?)\b"
)
_WORDS = {w: i for i, w in enumerate("one two three four five six seven eight nine ten".split(), 1)}


def draft_name(name: str) -> bool:
    """A file NAMED as a draft ("DRAFT ...", "... DRAFT <date>", "... for attorney
    review ...") or stamped by the Operator's agent (``STAMP``)."""
    spaced = " ".join(re.sub(r"_+", " ", name or "").split())
    return bool(_DRAFT_NAME.search(spaced) or STAMP.search(spaced))


def executed_proof(text: str) -> bool:
    """A proof of service that shows service MADE, not the firm's blank template
    (D8, 2026-11-26: two of a firm's own response drafts carried "PROOF OF
    SERVICE ... I served the foregoing document" with every "____ BY MAIL." line
    blank and no signature, and counting them dropped two owed sets). Executed
    means, after the heading: a method line marked ("[X] BY ELECTRONIC MAIL",
    "X BY MAIL", "BY MAIL." with no blank leader), a "served by <method> on
    <date>" statement, an "/s/" signature, or "Executed on <date>" with a typed
    declarant name after it."""
    for m in _PROOF.finditer(text or ""):
        tail = text[m.end() :]
        if _MARKED.search(tail) or _SERVED_BY.search(tail) or _S_SIGNED.search(tail):
            return True
        executed = _EXECUTED.search(tail)
        if executed and _DECLARANT.search(tail, executed.end(), executed.end() + 400):
            return True
    return False


def was_served(name: str, text: str) -> bool:
    """A response paper that shows it was served: an executed proof of service
    (:func:`executed_proof`), or, with no proof of service heading on it, a dated
    ("Dated"/"served" with a date) signature block. A draft by name never is.
    A paper whose only proof of service is a blank template is not served: its
    proof says what was done, and nothing was."""
    if draft_name(name):
        return False
    if _PROOF.search(text or ""):
        return executed_proof(text)
    dated = _DATED.search(text or "")
    return bool(dated and _SIGNED.search(text, dated.end()))


def asks_amended(request: str) -> bool:
    """The request asks for amended, further or supplemental responses."""
    return bool(_AMENDED.search(request or ""))


def _kind(words: str) -> str | None:
    """The set type a "Responses to <...>" phrase names, by the same title
    vocabulary the slicer reads a caption with (``served_slice.TITLES``):
    "Defendant's Special Interrogatories", "Demand for Production" and the rest."""
    up = " ".join(words.upper().split())
    hits = [(m.end(), k) for k, rx in _TITLE_WORDS.items() for m in rx.finditer(up)]
    return max(hits)[1] if hits else None


def set_number(word: str) -> int | None:
    w = word.strip().lower()
    return int(w) if w.isdigit() else _WORDS.get(w)


def draft_of(name: str) -> tuple[str, int] | None:
    """(set type, set number) a file named "DRAFT ... Responses to <kind>, Set <n>" drafts."""
    m = _DRAFT_OF.search(name or "")
    if not m:
        return None
    kind, n = _kind(m.group("kind")), set_number(m.group("n"))
    return (kind, n) if kind and n else None


def _pixel(path: str, read: Callable[[Path], Any] | None) -> tuple[list[str], str] | None:
    """The $0 pixel read's (checked numbers, form revision); None when the file
    is not on disk, the read fails, or it finds nothing checked."""
    if not path or not Path(path).is_file():
        return None
    if read is None:
        from .forms import read_stage

        read = read_stage.pixel_read
    try:
        got = read(Path(path))
    except Exception:  # noqa: BLE001 - a form the pixel read cannot read has no pixel values; the caller says so
        return None
    checked = got.get("checked") if isinstance(got, dict) else None
    rev = got.get("form_revision") if isinstance(got, dict) else None
    if not isinstance(checked, list) or not checked or not rev:
        return None
    return [str(x) for x in checked], str(rev)


def form_signature(path: str, read: Callable[[Path], Any] | None = None) -> str:
    """The pixel read's checked numbers and form revision, as one string; ""
    when the file is not on disk or the read fails (then nothing folds)."""
    got = _pixel(path, read)
    return f"pixel:{got[1]}:" + ",".join(sorted(got[0])) if got else ""


def form_checked(path: str, read: Callable[[Path], Any] | None = None) -> list[str]:
    """The checked numbers the $0 pixel read finds on a served form; [] when it cannot read them."""
    got = _pixel(path, read)
    return got[0] if got else []


#: A folder the Operator delivers into (``finish._names``: "<title> <date> (Operator <job id tail>)").
_OPERATOR_FOLDER = re.compile(r"\(Operator [0-9A-Za-z]{6}\)")


def ours_written(name: str, folder: str) -> bool:
    """A file SMD wrote: named as a draft ("DRAFT ... Exhibit B"), or in a
    folder the Operator delivered into. Never a served paper or a scan of one."""
    return bool(_DRAFT_START.search(name or "")) or operator_folder(folder)


def operator_folder(folder: str) -> bool:
    """A folder the Operator delivered into: our own filing record."""
    return bool(_OPERATOR_FOLDER.search(folder or ""))


#: A sentence that asks for responses to be drafted ("draft responses to ...",
#: "prepare our answers", "respond to their RFPs").
_DRAFT_ASK = re.compile(
    r"(?i)\b(?:draft|prepare|write|do)\b[^\n]*?\b(?:responses?|answers?)\b|\brespond(?:ing)?\s+to\b"
    r"|\b(?:responses?|answers?)\s+(?:to|for)\b"
)
#: A reference to numbered requests ("Requests 5 and 8", "Interrogatory No. 3"):
#: a sentence that carries one talks about a paper, it does not name what to draft.
_NUMBERED = re.compile(r"(?i)\b(?:requests?|interrogator(?:y|ies)|rogs?|rf[ap]s?)\s+(?:nos?\.?\s*)?#?\d")
#: Clauses: a full stop that is not "No." or "Nos.", a semicolon, or a line break.
_SENTENCES = re.compile(r"(?<![Nn]o)(?<![Nn]os)[.!?]+(?=\s|$)|[;\n]+")


def draft_asks(request: str) -> str:
    """The request's sentences that ask for responses to be drafted, joined;
    a sentence naming numbered requests is about a paper and is left out
    (2026-11-21: "...on Requests 5 and 8 of the Response to Request for
    Production" named kinds in passing and narrowed the job to one set)."""
    keep = [s for s in _SENTENCES.split(request or "") if _DRAFT_ASK.search(s) and not _NUMBERED.search(s)]
    return " ".join(keep)


_FORM_REQUEST = re.compile(r"(?i)\bform\s+interrogator(?:y|ies)\b|\bf\.?\s?rogs?\b")


def draft_kinds(request: str) -> list[str]:
    """The kinds of set the request names as what to draft (``draft_asks``), in its order; empty: every set."""
    asks = draft_asks(request)
    kinds = [u.key for u in sets.variants(asks)]
    return [*kinds, *(["form_interrogatories"] if _FORM_REQUEST.search(asks) else [])]
