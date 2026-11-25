"""What in a firm's filed discovery paper is a case fact, and what is the firm's
own fixed wording (``frame_make``'s reading of each paragraph). Pure.

MEASURED ON THE FIRM'S OWN PAPERS (2026-11-25, 16 filed papers, token classes
only): the first reading refused every paper. The causes, by paragraph count:
matter-tailored defined terms 90, dates 39, ordinary capitalised legal words 30,
party names 30, other (addresses, places, vehicle makes, providers, single
letters) 34, the firm's attorneys 9, the firm's identity run together in a
signature 7, the county 4, Code of Civil Procedure sections 3. So:

* the parties come from the identification block AND the caption, each with
  its variants (the whole name, the name without a corporate suffix, each
  distinctive word of it), and become marked places wherever they appear;
* the firm's identity (name, address, bar numbers, attorneys) is matched with
  its spacing ignored, so words run together still match;
* the court's name, the county, a code's section numbers and the abbreviations
  of codes are fixed words, and so is a closed vocabulary of procedure;
* anything else capitalised, any other date, money, email, telephone or case
  number is a case fact: its paragraph alone is dropped (``frame_make``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..drafting import firm_frame as F

_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
DATE = re.compile(rf"(?i)\b{_MONTH}\s+\d{{1,2}},?\s+\d{{4}}\b|\b\d{{1,2}}/\d{{1,2}}/\d{{2,4}}\b")
_MONEY = re.compile(r"\$\s*\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")
_CASE_NO = re.compile(r"(?i)\bcase\s+(?:no\.?|number)\s*:?\s*\S*\d")
WORD = re.compile(r"[A-Za-z][A-Za-z'’]*")
#: Fixed by their nature: the court, the county, a code section and its
#: abbreviations. Removed before the word check, never marked.
_FIXED_PHRASES = (
    re.compile(r"(?i)\bsuperior\s+court\s+of\s+(?:the\s+)?state\s+of\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"),
    re.compile(r"(?i)\b(?:city\s+and\s+)?county\s+of\s+[A-Z][\w.]*(?:\s+[A-Z][\w.]*){0,2}"),
    re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+County\b|\b[A-Z]+(?:\s+[A-Z]+)?\s+COUNTY\b"),
    re.compile(r"(?i)\b(?:C\.\s*C\.\s*P\.?|CCP|Cal\.|Evid\.|Civ\.|Proc\.|Gov\.|Bus\.|Prof\.|Ins\.|Veh\.|Pen\.)"),
    re.compile(r"§+\s*[\d.]+(?:\([a-z0-9]+\))*"),
)
_CORPORATE = re.compile(r"(?i),?\s*\b(?:inc|llc|llp|lp|ltd|corp|co|corporation|company|pc|apc)\b\.?$")
_ROLE_WORD = r"(?:plaintiffs?|defendants?|cross[- ](?:complainants?|defendants?)|petitioners?|respondents?)"

#: The words a firm's standard wording may carry capitalised: procedure, the
#: parts of a discovery paper, and plain English. Closed: a capitalised word not
#: here and not in the firm's identity is a case fact.
VOCAB = frozenset(
    """
    a about above according accordingly act action actions additional admission admissions admit admits admitted
    after against all also amended amendment america an and answer answering answers any app appear applicable are
    article as at attorney attorneys authority based be because been before being bound burdensome business but
    by calif california can case cases civil code codes communication communications complainant complainants
    complaint concerning confer confidential consistent constitution contained contend contention control copying
    corp correct counsel county court cross cross-complainant cross-complaint cross-defendant custody damages date
    dated declaration declare declares defendant defendants defined definition definitions demand department dept
    deposition describe described describing diligent discovery doctrine document documents does doe does each
    electronically employee employees employer entities entity esi esq esquire et evid evidence except executed
    exhibit exhibits expert experts extent fact facts federal fifth first five following for foregoing form forth
    four fourth from further furthermore general given has have having he health hereby herein hereinafter hereto
    however i ibid id identify if in include includes including individually information injuries injury inquiry
    inspection instruction instructions insurance interrogatories interrogatory into is it its judge jur knowledge
    law laws limitation llc llp ltd made mailing matter matters may me meaning means medical meet my name nine ninth
    no nos not nothing notice notwithstanding number numbers objection objections objects of office offices on one
    or order other our overbroad oppressive page pages paragraph paragraphs part parties party pc penalty perjury
    person persons petitioner plaintiff plaintiffs pleading pleadings policy possession preliminary privacy
    privilege privileged procedure proc produce produced product production propound propounded propounding
    proportional provide provided provider providers pursuant reasonably record records regarding relevant
    relevance representative representatives request requested requesting requests reserve reserves respectfully
    respond responded respondent responding response responses right rights roe roes rule rules said search second
    section sections seek seeks served service set seven seventh shall she signature signed six sixth special
    specifically state statement states stored subdivision subd subject submitted subpoena such superior supplement
    supplemental supra that the their them there thereto these they third this those three to true two type under
    undersigned united upon us usc vague ambiguous harass harassing verification verified was we were what when
    where whether which who whom will with within without witness witnesses work writing writings you your am behalf
    apc inc civ gov bus prof ins veh pen rptr supp ten eight eighth tenth dates sbn bar venue jurisdiction
    although during finally hence here moreover nevertheless otherwise rather since still then therefore thus
    unless until whereas while yet please note such accordingly additionally only should would could must also
    """.split()
)


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def spaced(piece: str) -> re.Pattern[str]:
    """``piece`` as a pattern that ignores its spacing: words run together match."""
    chars = [re.escape(c) for c in piece if not c.isspace()]
    return re.compile(r"\s*".join(chars), re.I)


def caption_parties(lines: list[str]) -> dict[str, list[str]]:
    """The caption's parties by role word ("plaintiff", "defendant"): the names
    before "Plaintiff," / "Defendant." on the lines above the identification block."""
    out: dict[str, list[str]] = {}
    for line in lines:
        for side in re.split(r"(?i)\s+vs?\.\s+", _norm(line.replace("\t", " "))):
            m = re.match(rf"(?i)^(.+?),?\s+({_ROLE_WORD})[,.;]?\s*$", side)
            if m and m.group(1).strip(" ,;") and len(m.group(1)) <= 120:
                role = re.sub(r"s$", "", m.group(2).lower().split("-")[-1].split()[-1])
                out.setdefault(role, []).append(m.group(1).strip(" ,;"))
    return out


def variants(name: str) -> list[str]:
    """A party's name and the shorter forms a paper uses for it, longest first."""
    base = _CORPORATE.sub("", name).strip(" ,")
    out = {name, base}
    out |= {w for w in WORD.findall(base) if len(w) >= 3 and w.lower() not in VOCAB}
    return sorted((v for v in out if v), key=len, reverse=True)


def party_values(lines: list[str], id_end: int) -> dict[str, str]:
    """The identification block's values, with a name taken from the caption
    when the block names only the role."""
    values, _end = F.identification(lines)
    caption = caption_parties(lines[: max(id_end, 0)])
    for role in ("responding", "propounding"):
        whole = values.get(f"{role}_party", "")
        if whole and not values.get(f"{role}_name"):
            word = re.sub(r"s$", "", (re.findall(rf"(?i){_ROLE_WORD}", whole) or [""])[0].lower())
            named = caption.get(word.split("-")[-1].split()[-1] if word else "", [])
            if named:
                values[f"{role}_name"] = named[0]
    return values


def name_marks(values: dict[str, str], caption: dict[str, list[str]]) -> list[tuple[str, str]]:
    """(text, mark) pairs, longest text first: each party's whole value and
    every variant of its name; a caption name sharing a word with a party is it."""
    pairs: list[tuple[str, str]] = []
    for role in ("responding", "propounding"):
        if values.get(f"{role}_party"):
            pairs.append((values[f"{role}_party"], f"[[v.{role}_party]]"))
        name = values.get(f"{role}_name", "")
        if not name:
            continue
        mine = {w.lower() for w in variants(name)}
        names = [name] + [c for cs in caption.values() for c in cs if {w.lower() for w in variants(c)} & mine]
        pairs += [(v, f"[[v.{role}_name]]") for n in names for v in variants(n)]
    seen: dict[str, str] = {}
    for text, mark in pairs:
        if text:
            seen.setdefault(text.lower(), mark)
    return sorted(((t, m) for t, m in seen.items()), key=lambda p: len(p[0]), reverse=True)


def mark_names(text: str, pairs: list[tuple[str, str]]) -> str:
    for name, mark in pairs:
        text = re.sub(rf"(?i)(?<![\w\[]){re.escape(name)}(?![\w\]])", mark, text)
    return text


def is_case_fact(text: str, allowed: frozenset[str], identity: Iterable[re.Pattern[str]]) -> bool:
    """True when the marked paragraph still holds what may be a case fact."""
    left = F.VALUE.sub(" ", text)
    for pat in identity:
        left = pat.sub(" ", left)
    for pat in _FIXED_PHRASES:
        left = pat.sub(" ", left)
    if DATE.search(left) or _MONEY.search(left) or _EMAIL.search(left) or _PHONE.search(left):
        return True
    if _CASE_NO.search(left):
        return True
    for w in WORD.findall(left):
        low = re.sub(r"(?:['’]s?|s['’])$", "", w.lower())
        if w != w.lower() and len(w) > 1 and low not in VOCAB and low.rstrip("s") not in VOCAB and low not in allowed:
            return True
    return False


__all__ = ["DATE", "VOCAB", "caption_parties", "is_case_fact", "mark_names", "name_marks", "party_values", "spaced"]
