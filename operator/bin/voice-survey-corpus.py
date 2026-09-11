#!/usr/bin/env python3
"""Survey a Smokeball tenant for the firm's OWN authored letters.

The step before ``bin/voice-fetch-corpus.py``. That script fetches documents a
human NAMED; this one finds the candidates, so the firm never has to assemble a
list. It scans the tenant, decides which documents the firm WROTE (as opposed
to the far larger pile it RECEIVED), proposes an audience cohort for each, and
emits (a) a manifest the fetch bridge consumes unmodified and (b) a report a
human blesses or corrects.

Repo-side, Captain-run under Infisical. NOT shipped to a seat: nothing here is
a skill, a tool, or reachable by an agent.

Why classification is content-based
-----------------------------------

Probed live 2026-08-10: ``get_files_on_matter`` list items carry ``id``,
``versionId``, ``name``, ``fileExtension``, ``ownerId``, ``dateCreated``,
``dateModified``, ``sizeBytes``, ``downloadInfo``, ``isFavorite``,
``isUploaded``, ``isCancelled``, ``isDuplicate``, ``isDeleted``. ``ownerId``
identifies the CREATING USER OR APP (``S2S_``-prefixed for server-to-server
applications), NOT the author of the content — a paralegal who scans in
opposing counsel's letter owns that file. There is no authorship field
anywhere in the connector's surface.

So authorship is decided from the TEXT, and ``ownerId`` / ``isUploaded`` /
``dateCreated`` are recorded per document as ADVISORY signals only: they appear
in the report so a human can spot a pattern worth exploiting on a real tenant
(``isUploaded`` may well separate generated-in-Smokeball from
scanned-from-outside), and they are never a pass condition here. A change that
lets one of them decide a classification is a change to this docstring too.

There is likewise no tenant-name endpoint in the connector's 40 tools (checked
2026-08-10), so the firm's own name — the single most load-bearing input to the
classifier — is supplied with ``--firm-name`` rather than guessed.

Two phases, in this order for a reason
--------------------------------------

**Phase 1 (metadata only).** ``GET /matters`` paged, then per matter
``GET /matters/{id}/documents/files`` paged, plus ``GET /staff`` for the firm's
roster. Nothing here reads document content, so nothing here taints the caller.

**Phase 2 (bounded content reads).** Each candidate is downloaded and
text-extracted through the connector's OWN ``download_file`` + ``extract_text``
— the same pair ``voice-fetch-corpus.py`` and the agent's ``read_document``
use, so this path and the agent's cannot diverge in what they can read. Only
two windows of the extracted text are retained and classified: the head (where
letterhead, date, recipient block, RE line, and salutation live) and the tail
(where the closer, signature block, and any proof of service live). The whole
file is fetched because the API offers no ranged read; the windows bound what
is examined and kept.

Budgets are hard, and truncation is REPORTED. ``--max-matters``,
``--max-docs-per-matter``, and ``--max-reads`` all cap the scan; when any of
them bites, the report names what was not scanned. A survey that silently
looked at a third of the tenant and called the firm's library "three letters"
is worse than no survey.

Classification, and what each answer means
------------------------------------------

``firm_authored`` is ``true``, ``false``, or ``"unknown"`` — never a bare
boolean, because "we could not tell" is a real and common answer that must not
be laundered into "not theirs". Every row carries ``authorship_evidence``
naming the mechanism that decided it.

A document with no extractable text (an image-only PDF, a scan) is
``"unknown"`` with evidence ``no text layer`` — NEVER ``false``. An unreadable
document is not evidence about who wrote it, and the report counts the two
separately.

Received-paper evidence deliberately outranks a roster-name match in the
signature zone, because the commonest false positive is a letter the firm was
copied on: another firm's letterhead at the top, one of our attorneys named in
the cc line at the bottom. Letterhead wins.

Usage::

    cd operator
    infisical run --env=prod --path=/ss -- \\
      python bin/voice-survey-corpus.py \\
        --firm-name 'Brannock & Ferreira LLP' \\
        --customer-yaml customers/pilot-smokeball/customer.yaml \\
        --out-report /tmp/survey.json \\
        --out-manifest /tmp/exemplars.yaml
    # then, after a human blesses the proposed list:
    python bin/voice-fetch-corpus.py --manifest /tmp/exemplars.yaml \\
      --customer-yaml customers/pilot-smokeball/customer.yaml --out /tmp/corpus.jsonl
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
_OPERATOR = _HERE.parents[1]
sys.path.insert(0, str(_OPERATOR))  # operator/ on sys.path
sys.path.insert(0, str(_OPERATOR / "connectors" / "smokeball"))  # real extractor


def _load_sibling(file_name: str, module_name: str) -> Any:
    """Import a hyphenated sibling CLI by path (they are scripts, not modules).

    The manifest this script emits is consumed by ``voice-fetch-corpus.py``, and
    the cohort vocabulary both scripts enforce is the same seat-authored list.
    Importing rather than copying means a change to the vocabulary parser or the
    list-envelope probing lands on both at once — two copies would drift, and
    the failure mode of drift here is a manifest the bridge silently refuses.
    """
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, _OPERATOR / "bin" / file_name)
    if not spec or not spec.loader:  # pragma: no cover - packaging accident
        raise ImportError(f"cannot load {file_name}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves its class's module out of
    # sys.modules, and an unregistered spec-loaded module makes that lookup
    # return None (AttributeError at import time on 3.12+).
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_bridge = _load_sibling("voice-fetch-corpus.py", "voice_fetch_corpus")
load_cohort_vocabulary = _bridge.load_cohort_vocabulary
BASE_COHORTS = _bridge.BASE_COHORTS
_items = _bridge._items
_label = _bridge._label


class SurveyError(Exception):
    """A precondition the survey refuses to guess past."""


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------

DEFAULT_MAX_MATTERS = 50
DEFAULT_MAX_DOCS_PER_MATTER = 100
DEFAULT_MAX_READS = 200
PAGE_SIZE = 500

# What the classifier is allowed to look at. Letterhead, date, recipient block,
# RE line and salutation all sit inside the first few thousand characters; the
# closer, signature block and proof of service sit in the last few.
HEAD_CHARS = 4000
TAIL_CHARS = 3000


@dataclass
class Budget:
    """Hard caps plus the counters that make truncation reportable."""

    max_matters: int = DEFAULT_MAX_MATTERS
    max_docs_per_matter: int = DEFAULT_MAX_DOCS_PER_MATTER
    max_reads: int = DEFAULT_MAX_READS

    matters_seen: int = 0
    matters_truncated: bool = False
    matters_reported_total: int | None = None
    docs_truncated: dict[str, int] = field(default_factory=dict)
    reads_used: int = 0
    unread_candidates: list[dict] = field(default_factory=list)

    def reads_left(self) -> int:
        return max(0, self.max_reads - self.reads_used)

    def as_report(self) -> dict:
        return {
            "limits": {
                "max_matters": self.max_matters,
                "max_docs_per_matter": self.max_docs_per_matter,
                "max_reads": self.max_reads,
            },
            "matters_seen_at_least": self.matters_seen,
            "matters_reported_total": self.matters_reported_total,
            "matters_truncated": self.matters_truncated,
            "docs_truncated_by_matter": dict(self.docs_truncated),
            "reads_used": self.reads_used,
            "reads_budget_exhausted": self.reads_used >= self.max_reads,
            "unread_candidates": self.unread_candidates,
            "complete": (not self.matters_truncated and not self.docs_truncated and not self.unread_candidates),
        }


def _paged(client: Any, path: str, *, cap: int, **params: Any) -> tuple[list[dict], bool, int | None]:
    """Page a Smokeball list endpoint. Returns (rows, truncated, reported_total).

    ``truncated`` means the cap bit before the endpoint ran out, so the caller
    knows its view is partial. ``reported_total`` is the envelope's ``size``
    when the tenant supplies one — a real total beats our lower bound.
    """
    rows: list[dict] = []
    reported_total: int | None = None
    offset = 0
    while True:
        resp = client.get(path, Limit=PAGE_SIZE, Offset=offset, **params)
        if isinstance(resp, dict) and isinstance(resp.get("size"), int):
            reported_total = resp["size"]
        batch = _items(resp)
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < PAGE_SIZE or len(rows) > cap:
            break
        offset += len(batch)
    return rows[:cap], len(rows) > cap, reported_total


# ---------------------------------------------------------------------------
# Firm identity + staff roster (the untainted reference the classifier trusts)
# ---------------------------------------------------------------------------

# Tokens that identify no firm in particular. Dropped so "Brannock & Ferreira
# LLP" is matched on {brannock, ferreira} and an opposing "Trammell & Voss LLP"
# cannot match on the shared "LLP".
_GENERIC_FIRM_TOKENS = frozenset(
    {
        "llp",
        "llc",
        "lp",
        "pc",
        "plc",
        "pllc",
        "apc",
        "inc",
        "incorporated",
        "co",
        "company",
        "corp",
        "corporation",
        "law",
        "laws",
        "lawyer",
        "lawyers",
        "office",
        "offices",
        "attorney",
        "attorneys",
        "at",
        "and",
        "the",
        "of",
        "group",
        "firm",
        "legal",
        "associates",
        "partners",
        "professional",
        "a",
        "plc",
    }
)

_WORD_RE = re.compile(r"[a-z0-9']+")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def firm_tokens(firm_name: str) -> list[str]:
    """The distinctive words of a firm name (surnames, usually)."""
    toks = [t for t in _words(firm_name) if t not in _GENERIC_FIRM_TOKENS]
    return toks or _words(firm_name)


def zone_names_firm(zone: str, tokens: list[str]) -> bool:
    """True only when EVERY distinctive firm token appears in the zone.

    Strict on purpose. A single shared surname is exactly how a letter from
    opposing counsel gets mistaken for our own, and the cost of being strict is
    an ``unknown`` (recoverable, visible in the report) rather than a received
    letter teaching the model someone else's voice (silent, and it ships).
    """
    if not tokens:
        return False
    have = set(_words(zone))
    return all(t in have for t in tokens)


@dataclass
class Roster:
    """The firm's own people, from ``GET /staff`` — metadata, never content."""

    people: list[dict] = field(default_factory=list)  # {full, first, last}

    @classmethod
    def from_items(cls, items: list[dict]) -> "Roster":
        people: list[dict] = []
        for it in items:
            first = _label(it, ("firstName", "givenName"))
            last = _label(it, ("lastName", "surname", "familyName"))
            if not (first and last):
                whole = _label(it, ("name", "displayName", "fullName"))
                parts = whole.split()
                if len(parts) >= 2:
                    first, last = parts[0], parts[-1]
            if first and last:
                people.append({"full": f"{first} {last}", "first": first, "last": last})
        return cls(people)

    def match(self, text: str) -> str | None:
        """Return the roster member named in ``text``, or None.

        Requires first+last adjacency (or "Last, First", or the "D. Whitmore"
        initial form). A bare surname is never enough: "Draper" is an opposing
        party on the pilot tenant and a plausible staff surname anywhere.
        """
        for p in self.people:
            first = re.escape(p["first"])
            last = re.escape(p["last"])
            initial = re.escape(p["first"][:1])
            patterns = (
                rf"\b{first}\s+(?:[A-Z]\.\s*)?{last}\b",
                rf"\b{last},\s*{first}\b",
                rf"\b{initial}\.\s*{last}\b",
            )
            for pat in patterns:
                if re.search(pat, text, re.I):
                    return p["full"]
        return None


# ---------------------------------------------------------------------------
# Text windows and zones
# ---------------------------------------------------------------------------


@dataclass
class Windows:
    head: str
    tail: str

    @classmethod
    def of(cls, text: str) -> "Windows":
        return cls(head=text[:HEAD_CHARS], tail=text[-TAIL_CHARS:])

    def both(self) -> str:
        return f"{self.head}\n{self.tail}"


# A letterhead stops where the letter proper starts. Everything at or after the
# first of these belongs to the letter, not to whoever printed the paper.
_ZONE_STOP_RE = re.compile(r"^\s*\**\s*(via\s|re\s*:|attn\b|dear\b|to whom|personal and confidential\b)", re.I)
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_DATE_LINE_RE = re.compile(
    rf"^\s*\**\s*(?:({_MONTHS})\s+\d{{1,2}},\s*\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}})"
    r"\s*\**\s*$",
    re.I,
)
_LETTERHEAD_MAX_LINES = 10


def letterhead_zone(head: str) -> str:
    """The lines above the date/salutation — where a letterhead lives.

    Bounded at the first date, VIA/RE/ATTN/Dear line, or ten non-empty lines.
    The bound is what keeps a RECIPIENT block out of the zone: a firm letter
    with no letterhead of its own starts at the date, so its zone is empty and
    the addressee's company never gets read as the author's letterhead.
    """
    kept: list[str] = []
    for raw in head.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _ZONE_STOP_RE.match(line) or _DATE_LINE_RE.match(line):
            break
        kept.append(line)
        if len(kept) >= _LETTERHEAD_MAX_LINES:
            break
    return "\n".join(kept)


_SALUTATION_LINE_RE = re.compile(r"^\s*\**\s*(dear\b|to whom|counsel\s*:)", re.I)
_RECIPIENT_MAX_LINES = 40
# "Nakashima v. Cornerstone Market Holdings, LLC" — a line naming the parties to
# a case, not an organization that authored or received anything.
_CAPTION_PARTY_RE = re.compile(r"\bv(?:s?\.|ersus)\s", re.I)


def recipient_zone(head: str, tokens: list[str]) -> str:
    """Everything up to and including the salutation, minus our own letterhead.

    Two kinds of line are dropped. Our own letterhead, because it carries
    organizational words ("LLP", "Attorneys at Law") that otherwise read as
    evidence that the RECIPIENT is a law firm — the firm's own paper arguing it
    wrote to itself. And caption lines ("Nakashima v. Cornerstone Holdings,
    LLC"), because a caption names the PARTIES to the case, and reading a
    defendant's corporate suffix as the addressee's is how a mediation brief
    gets filed as a letter to opposing counsel.
    """
    kept: list[str] = []
    for raw in head.splitlines():
        line = raw.strip()
        if not line:
            continue
        drop = (tokens and zone_names_firm(line, tokens)) or _CAPTION_PARTY_RE.search(line)
        if not drop:
            kept.append(line)
        if _SALUTATION_LINE_RE.match(line) or len(kept) >= _RECIPIENT_MAX_LINES:
            break
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# Authorship classification (content-based; see the module docstring)
# ---------------------------------------------------------------------------

UNKNOWN = "unknown"

CLASSIFICATION_METHOD = (
    "content-based: firm letterhead zone, staff-roster signature match, "
    "proof-of-service declarant, and received-paper markers (another firm's "
    "letterhead, court-issued paper, carrier/medical/lien paper, litigation "
    "caption). ownerId, isUploaded and dateCreated are recorded per document "
    "as ADVISORY signals only and are never pass conditions."
)

# An organization line in a letterhead zone. " v. " excluded because a caption
# ("Okafor v. Grand Valley Market, Inc.") names parties, not the author.
_ORG_LINE_RE = re.compile(
    r"\b(ll[pc]|l\.l\.[pc]\.|inc\b\.?|p\.\s*c\.|pllc|apc|corp\b\.?|corporation|"
    r"company|attorneys? at law|law offices?|insurance|casualty|mutual|"
    r"indemnity|assurance|underwriters|health plan|foundation|capital|"
    r"department of|hospital|clinic|medical (center|group|associates)|"
    r"associates)\b",
    re.I,
)
_CONTACT_LINE_RE = re.compile(
    r"\(\d{3}\)\s*\d{3}-\d{4}|\b\d{3}-\d{3}-\d{4}\b|@[\w.-]+\.\w{2,}|"
    r"\b(suite|ste\.|p\.?\s*o\.\s*box|avenue|boulevard|blvd|street|road|drive)\b",
    re.I,
)

# Paper produced by somebody who is structurally not a law firm writing for a
# client: carriers, providers, lienholders, funders.
_THIRD_PARTY_PAPER_RE = re.compile(
    r"records production|notice of lien|lien assertion|payoff statement|"
    r"claims department|third party liability|explanation of benefits|"
    r"health care services|records custodian|billing statement",
    re.I,
)
# Paper the court issues. Scoped to the head window: a clerk's conformed stamp
# on OUR complaint is not the court authoring it.
_COURT_ISSUED_RE = re.compile(
    r"^\s*summons\b|citacion judicial|trial setting order|minute order|"
    r"\bit is (hereby )?ordered\b|notice of case (management|assignment)|"
    r"^\s*order\s+(granting|denying|to show cause)",
    re.I | re.M,
)
_CAPTION_RE = re.compile(
    r"propounding party|responding party|superior court of|"
    r"complaint for damages|notice of motion and motion|"
    r"^\s*(request for production|special interrogator|requests? for admission)",
    re.I | re.M,
)
_POS_RE = re.compile(r"proof of service", re.I)
_POS_SIGNER_RE = re.compile(r"/s/\s*([A-Z][\w.'\-]*(?:\s+[A-Z][\w.'\-]*){0,3})")


@dataclass
class Authorship:
    firm_authored: bool | str
    evidence: str


def _foreign_letterhead(zone: str, tokens: list[str]) -> str | None:
    """An organization heading the paper that is demonstrably not us."""
    lines = [ln for ln in zone.splitlines() if ln.strip()][:3]
    if not any(_ORG_LINE_RE.search(ln) and not _CAPTION_PARTY_RE.search(ln) for ln in lines):
        return None
    if zone_names_firm(zone, tokens):
        return None
    looks_like_letterhead = bool(_CONTACT_LINE_RE.search(zone)) or bool(_THIRD_PARTY_PAPER_RE.search(zone))
    if not looks_like_letterhead:
        return None
    return lines[0]


def classify_authorship(windows: Windows | None, *, firm_name: str, roster: Roster) -> Authorship:
    """Decide who wrote a document from its head and tail windows.

    Order is the whole design. Positive letterhead first (cheapest and
    strongest), then RECEIVED evidence, then a roster signature, then the
    inconclusive cases. Received evidence sits ABOVE the signature match on
    purpose: a letter we were copied on carries another firm's letterhead at the
    top and one of our attorneys' names in the cc line at the bottom, and only
    this ordering gets that document right.
    """
    if windows is None:
        return Authorship(UNKNOWN, "no text layer")

    tokens = firm_tokens(firm_name)
    head, tail = windows.head, windows.tail
    lh = letterhead_zone(head)

    # 1. Ours, on our own paper.
    if zone_names_firm(lh, tokens):
        return Authorship(True, f"firm letterhead in the letterhead zone ({firm_name})")

    # 1b. Served by our own staff.
    pos_present = bool(_POS_RE.search(tail)) or bool(_POS_RE.search(head))
    pos_signers = _POS_SIGNER_RE.findall(tail) + _POS_SIGNER_RE.findall(head)
    if pos_present:
        for signer in pos_signers:
            hit = roster.match(signer)
            if hit:
                return Authorship(True, f"proof of service declared by staff-roster member {hit}")

    # 2. Received paper, strongest markers first.
    if _COURT_ISSUED_RE.search(head):
        return Authorship(False, "court-issued paper (summons/order) in the head window")
    if pos_present and _CAPTION_RE.search(head):
        who = pos_signers[0] if pos_signers else "an unnamed declarant"
        return Authorship(
            False,
            f"litigation caption served under a proof of service declared by {who}, who is not on the staff roster",
        )
    foreign = _foreign_letterhead(lh, tokens)
    if foreign:
        return Authorship(False, f"another organization's letterhead: {foreign!r}")
    if _THIRD_PARTY_PAPER_RE.search(lh):
        return Authorship(False, "carrier / medical / lien paper markers in the letterhead zone")

    # 3. Ours, on paper whose letterhead did not survive extraction.
    signer = roster.match(tail)
    if signer:
        return Authorship(True, f"signature block names staff-roster member {signer}")

    # 4. Inconclusive. A caption with no firm signal is NOT called received:
    #    which side of the caption we are on is not determinable from content.
    if _CAPTION_RE.search(head):
        return Authorship(
            UNKNOWN,
            "litigation caption with no firm letterhead, roster signature, or "
            "matching proof-of-service declarant — side of the caption not "
            "determinable from content",
        )
    return Authorship(UNKNOWN, "no letterhead, roster signature, or received-paper marker found")


# ---------------------------------------------------------------------------
# Audience cohort proposal
# ---------------------------------------------------------------------------

_ADJUSTER_TITLE_RE = re.compile(
    r"claims? (representative|adjuster|examiner|specialist|analyst|department)|"
    r"\badjuster\b|\bclaims unit\b",
    re.I,
)
_CARRIER_ORG_RE = re.compile(r"\b(insurance|casualty|mutual|indemnity|assurance|underwriters)\b", re.I)
_COUNSEL_RE = re.compile(
    r"\besq\b\.?|attorneys? for (defendant|plaintiff|respondent)|dear counsel|"
    r"counsel of record|law offices?\b|\bll[pc]\b|\bapc\b",
    re.I,
)
_NEUTRAL_RE = re.compile(
    r"\bmediator\b|\bneutral\b|\barbitrator\b|\bhon\.\s|\((ret\.?)\)|"
    r"clerk of the (superior )?court|honorable\b",
    re.I,
)
_CLAIM_NO_RE = re.compile(r"claim\s*(no\.?|number|#)\s*[:.]?\s*\S", re.I)
_CLIENT_SALUTATION_RE = re.compile(r"^\s*\**\s*dear\s+[A-Z][\w'\-]*\s*[,:]", re.I | re.M)
_CLIENT_RE_LINE_RE = re.compile(r"re\s*:.*\byour (case|claim|matter|settlement)\b|our client\s*:", re.I)


def propose_audience(windows: Windows, *, firm_name: str) -> tuple[str | None, str]:
    """Propose the audience of a firm-authored document, as a base cohort slug.

    Reads the RECIPIENT zone (letterhead through salutation), not the body: a
    demand letter's body is full of the client's name, and "our client: Marisol
    Duarte" in the RE block is precisely the string that would misfile a
    demand-to-an-adjuster as a letter to the client.

    Order is adversarial-first: the adjuster and opposing-counsel signals are
    checked before the client signal, so specific evidence beats the generic
    "Dear <first name>" that a letter to anyone might carry.
    """
    tokens = firm_tokens(firm_name)
    zone = recipient_zone(windows.head, tokens)

    if _ADJUSTER_TITLE_RE.search(zone):
        return "adjuster", "recipient block names a claims role"
    if _CARRIER_ORG_RE.search(zone):
        return "adjuster", "recipient block names an insurance carrier"
    if _COUNSEL_RE.search(zone):
        return "opposing-counsel", "recipient block names another law firm or counsel"
    if _CLAIM_NO_RE.search(zone):
        return "adjuster", "RE block carries a carrier claim number"
    if _NEUTRAL_RE.search(zone) or _NEUTRAL_RE.search(windows.head[:800]):
        return "court", "addressed to a neutral, judicial officer, or the court"
    if _CLIENT_SALUTATION_RE.search(zone) or _CLIENT_RE_LINE_RE.search(zone):
        return "client", "first-name salutation to an individual with no organization"
    return None, "no audience signal in the recipient block"


def map_cohort(proposed: str | None, reason: str, vocabulary: frozenset[str]) -> tuple[str | None, str]:
    """Map a proposed audience into the SEAT's authored cohort vocabulary.

    An unmapped proposal is left null with its reason rather than coerced into
    the nearest authored cohort: a sample in the wrong cohort teaches the wrong
    register to a real recipient, and a null is a question a human can answer.
    """
    if proposed is None:
        return None, reason
    if proposed in vocabulary:
        return proposed, reason
    return None, (
        f"proposed audience {proposed!r} ({reason}) is not in the seat's authored "
        f"cohort vocabulary {sorted(vocabulary)}"
    )


# ---------------------------------------------------------------------------
# Document type (closed vocabulary from the seed corpus frontmatter)
# ---------------------------------------------------------------------------

UNCLASSIFIED = "unclassified"

# Ordered: the first pattern that matches wins, so the specific beat the
# generic. A client status letter routinely says "the demand went out"; only
# the demand itself says "this is her demand".
_DOC_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("meet_and_confer_letter", re.compile(r"meet and confer", re.I)),
    (
        "declination_letter",
        re.compile(
            r"declin(e|ing|ation)\b|we (will|are) not (be able to )?(represent|take)|"
            r"not accepting (your|this) (case|matter)|no attorney-client relationship",
            re.I,
        ),
    ),
    (
        "engagement_cover_letter",
        re.compile(
            r"retainer agreement|engagement (letter|agreement)|"
            r"pleased to represent|fee agreement enclosed",
            re.I,
        ),
    ),
    ("mediation_brief", re.compile(r"mediation brief|confidential mediation", re.I)),
    (
        "demand_letter",
        re.compile(
            r"this (is|constitutes) (a|our|her|his|their|the) demand|"
            r"policy limits demand|settlement demand|this demand (is|remains) open",
            re.I,
        ),
    ),
    (
        "negotiation_letter",
        re.compile(
            r"your offer of|the offer of \$|we reject|counter(offer|-offer| to your)|"
            r"inadequate offer|we decline the offer",
            re.I,
        ),
    ),
    (
        "discovery_responses",
        re.compile(
            r"response to (request|interrogatory|form interrogatory) no\.|"
            r"responses to .{0,40}(interrogatories|requests for)",
            re.I,
        ),
    ),
    (
        "pleading",
        re.compile(
            r"complaint for damages|notice of motion|"
            r"memorandum of points and authorities|petition for|separate statement",
            re.I,
        ),
    ),
    (
        "client_status_letter",
        re.compile(
            r"where (things|the case|we) (actually )?stand|here is where|"
            r"status of your (case|matter)|update on your (case|matter)",
            re.I,
        ),
    ),
)


def classify_doc_type(windows: Windows, file_name: str = "") -> str:
    haystack = f"{file_name}\n{windows.both()}"
    for name, pattern in _DOC_TYPE_PATTERNS:
        if pattern.search(haystack):
            return name
    return UNCLASSIFIED


# Ranking floor: a type seen on one matter is one lawyer's one letter, not a
# thing the firm produces.
DOC_TYPE_MIN_MATTERS = 2


def rank_doc_types(rows: list[dict]) -> list[dict]:
    """Rank firm-authored document types by count, floored at 2 matters."""
    buckets: dict[str, dict] = {}
    for r in rows:
        if r["firm_authored"] is not True:
            continue
        dt = r["doc_type"]
        if dt == UNCLASSIFIED:
            continue
        b = buckets.setdefault(dt, {"count": 0, "matters": set(), "examples": []})
        b["count"] += 1
        b["matters"].add(r["matter_id"])
        if len(b["examples"]) < 3:
            b["examples"].append(r["file_name"])
    ranked = [
        {
            "type": dt,
            "count": b["count"],
            "matters": len(b["matters"]),
            "examples": b["examples"],
        }
        for dt, b in buckets.items()
        if len(b["matters"]) >= DOC_TYPE_MIN_MATTERS
    ]
    ranked.sort(key=lambda d: (-d["count"], d["type"]))
    return ranked


# ---------------------------------------------------------------------------
# Phase 1 + Phase 2
# ---------------------------------------------------------------------------


def _matter_label(m: dict) -> str:
    return _label(m, ("name", "title", "caption", "matterName", "description", "number"))


def _file_label(f: dict) -> str:
    return _label(f, ("name", "fileName", "title"))


def _advisory(f: dict) -> dict:
    """Signals recorded for a human's pattern-spotting. Never pass conditions."""
    return {
        "ownerId": f.get("ownerId"),
        "isUploaded": f.get("isUploaded"),
        "dateCreated": f.get("dateCreated"),
        "dateModified": f.get("dateModified"),
    }


def scan_metadata(client: Any, budget: Budget) -> list[dict]:
    """Phase 1: matters and their file lists. No document content is read."""
    matters, truncated, total = _paged(client, "/matters", cap=budget.max_matters)
    budget.matters_seen = len(matters)
    budget.matters_truncated = truncated
    budget.matters_reported_total = total

    candidates: list[dict] = []
    for m in matters:
        matter_id = str(m.get("id", ""))
        if not matter_id:
            continue
        files, file_trunc, _ = _paged(
            client,
            f"/matters/{matter_id}/documents/files",
            cap=budget.max_docs_per_matter,
        )
        if file_trunc:
            budget.docs_truncated[matter_id] = budget.max_docs_per_matter
        for f in files:
            if f.get("isDeleted") or f.get("isCancelled"):
                continue
            candidates.append(
                {
                    "matter_id": matter_id,
                    "matter_name": _matter_label(m),
                    "file_id": str(f.get("id", "")),
                    "file_name": _file_label(f),
                    "advisory": _advisory(f),
                }
            )
    return candidates


def read_windows(client: Any, cand: dict) -> tuple[Windows | None, str]:
    """Phase 2 for one candidate. Returns (windows or None, note).

    Any failure to READ is reported as a read failure, never as a conclusion
    about authorship — the caller turns both into ``unknown``.
    """
    from smokeball_connector.extract import UnsupportedDocumentError, extract_text

    try:
        info, blob = client.download_file(cand["matter_id"], cand["file_id"])
    except Exception as exc:  # noqa: BLE001 - one bad file must not end the survey
        return None, f"download failed: {exc}"
    try:
        text = extract_text(
            blob,
            file_name=str(info.get("name") or cand["file_name"]),
            file_extension=str(info.get("fileExtension") or ""),
        )
    except UnsupportedDocumentError as exc:
        return None, f"no text layer ({exc})"
    except Exception as exc:  # noqa: BLE001 - any extraction-library failure is recorded per document as "extraction failed" so the survey continues
        return None, f"extraction failed: {exc}"
    if not text.strip():
        return None, "no text layer"
    return Windows.of(text), ""


def survey(
    client: Any,
    *,
    firm_name: str,
    vocabulary: frozenset[str],
    budget: Budget | None = None,
    generated_at: str | None = None,
) -> tuple[dict, list[dict]]:
    """Run both phases. Returns (report, manifest_entries).

    ``manifest_entries`` are the rows ``voice-fetch-corpus.py --manifest``
    consumes, by ID, and hold ONLY documents classified firm-authored whose
    proposed audience mapped into the seat's authored vocabulary. Everything
    else lands in the report's ``excluded`` list with its reason.
    """
    budget = budget or Budget()
    generated_at = generated_at or _dt.datetime.now(_dt.timezone.utc).isoformat()

    roster = Roster.from_items(_paged(client, "/staff", cap=2000)[0])
    candidates = scan_metadata(client, budget)

    rows: list[dict] = []
    excluded: list[dict] = []
    entries: list[dict] = []

    for cand in candidates:
        if budget.reads_left() <= 0:
            budget.unread_candidates.append(
                {
                    "matter_id": cand["matter_id"],
                    "file_id": cand["file_id"],
                    "file_name": cand["file_name"],
                }
            )
            continue
        budget.reads_used += 1
        windows, note = read_windows(client, cand)
        auth = classify_authorship(windows, firm_name=firm_name, roster=roster)
        if windows is None and note:
            auth = Authorship(UNKNOWN, note)

        if windows is not None and auth.firm_authored is True:
            proposed, why = propose_audience(windows, firm_name=firm_name)
            cohort, why = map_cohort(proposed, why, vocabulary)
            doc_type = classify_doc_type(windows, cand["file_name"])
        else:
            proposed, cohort, why = None, None, "not firm-authored"
            doc_type = UNCLASSIFIED

        row = {
            "matter_id": cand["matter_id"],
            "matter_name": cand["matter_name"],
            "file_id": cand["file_id"],
            "file_name": cand["file_name"],
            "firm_authored": auth.firm_authored,
            "authorship_evidence": auth.evidence,
            "cohort_proposal": cohort,
            "cohort_reason": why,
            "doc_type": doc_type,
            "advisory": cand["advisory"],
        }
        rows.append(row)

        if auth.firm_authored is True and cohort:
            entries.append(
                {
                    "matter": cand["matter_id"],
                    "file": cand["file_id"],
                    "cohort": cohort,
                    "_matter_name": cand["matter_name"],
                    "_file_name": cand["file_name"],
                }
            )
        else:
            excluded.append(
                {
                    "file": cand["file_name"],
                    "file_id": cand["file_id"],
                    "matter_id": cand["matter_id"],
                    "reason": (auth.evidence if auth.firm_authored is not True else why),
                }
            )

    unreadable = sum(1 for r in rows if r["authorship_evidence"].startswith("no text layer"))
    cohort_counts: dict[str, int] = {}
    for e in entries:
        cohort_counts[e["cohort"]] = cohort_counts.get(e["cohort"], 0) + 1

    report = {
        "generated_at": generated_at,
        "tenant": {
            "firm_name": firm_name,
            "firm_name_source": "--firm-name (no tenant-name endpoint in the connector surface)",
            "matters": budget.matters_seen,
            "staff": len(roster.people),
            "documents_listed": len(candidates),
        },
        "classification": {
            "method": CLASSIFICATION_METHOD,
            "scanned": len(rows),
            "firm_authored": sum(1 for r in rows if r["firm_authored"] is True),
            "received": sum(1 for r in rows if r["firm_authored"] is False),
            "unreadable": unreadable,
            "inconclusive": sum(1 for r in rows if r["firm_authored"] == UNKNOWN) - unreadable,
        },
        "budgets": budget.as_report(),
        "documents": rows,
        "cohort_proposal": [{"cohort": c, "count": n} for c, n in sorted(cohort_counts.items())],
        "doc_types": rank_doc_types(rows),
        "excluded": excluded,
    }
    return report, entries


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _yaml_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render_manifest(entries: list[dict], *, firm_name: str, generated_at: str) -> str:
    """Render the manifest ``voice-fetch-corpus.py --manifest`` consumes.

    IDs, not names: ``resolve_one`` matches an id before it tries a name
    substring, so an id can never be the ambiguous match that makes the bridge
    refuse. The human-readable names ride along as comments.
    """
    out = [
        "# Proposed voice corpus - generated by bin/voice-survey-corpus.py",
        f"# firm: {firm_name}",
        f"# generated_at: {generated_at}",
        "# PROPOSED, not blessed. Review against the survey report, delete what",
        "# the firm did not write, then feed to bin/voice-fetch-corpus.py.",
        "entries:",
    ]
    for e in entries:
        out.append(f"  - matter: {_yaml_quote(e['matter'])}   # {e.get('_matter_name', '')}")
        out.append(f"    file: {_yaml_quote(e['file'])}   # {e.get('_file_name', '')}")
        out.append(f"    cohort: {e['cohort']}")
    if not entries:
        out.append("  []  # nothing classified as firm-authored with a mapped cohort")
    return "\n".join(out) + "\n"


def write_manifest(entries: list[dict], path: str, *, firm_name: str, generated_at: str) -> None:
    clean = [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.suffix.lower() == ".json":
        dest.write_text(json.dumps({"entries": clean}, indent=2), encoding="utf-8")
        return
    dest.write_text(
        render_manifest(entries, firm_name=firm_name, generated_at=generated_at),
        encoding="utf-8",
    )


def write_report(report: dict, path: str) -> None:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def _summarize(report: dict, entries: list[dict]) -> str:
    c = report["classification"]
    lines = [
        f"firm: {report['tenant']['firm_name']}",
        f"matters {report['tenant']['matters']}  documents listed "
        f"{report['tenant']['documents_listed']}  read {c['scanned']}",
        f"firm-authored {c['firm_authored']}   received {c['received']}   "
        f"unreadable {c['unreadable']}   inconclusive {c['inconclusive']}",
        f"manifest entries {len(entries)}",
    ]
    for d in report["doc_types"]:
        lines.append(f"  {d['type']:26s} {d['count']:3d} across {d['matters']} matters")
    b = report["budgets"]
    if not b["complete"]:
        lines.append("")
        lines.append("PARTIAL SCAN - the report's `budgets` block names what was skipped:")
        if b["matters_truncated"]:
            lines.append(f"  matters capped at {b['limits']['max_matters']}")
        if b["docs_truncated_by_matter"]:
            lines.append(f"  documents capped on {len(b['docs_truncated_by_matter'])} matter(s)")
        if b["unread_candidates"]:
            lines.append(
                f"  {len(b['unread_candidates'])} candidate(s) never read "
                f"(read budget {b['limits']['max_reads']} exhausted)"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Survey a Smokeball tenant for the firm's own authored letters.")
    p.add_argument(
        "--firm-name",
        required=True,
        help="The firm's own name, exactly as it appears on its letterhead. "
        "Required: the connector exposes no tenant-name endpoint.",
    )
    p.add_argument("--out-report", required=True, help="Survey report JSON.")
    p.add_argument("--out-manifest", help="Proposed corpus manifest (.yaml or .json).")
    p.add_argument("--customer-yaml", help="Seat customer.yaml for the cohort vocabulary.")
    p.add_argument("--max-matters", type=int, default=DEFAULT_MAX_MATTERS)
    p.add_argument("--max-docs-per-matter", type=int, default=DEFAULT_MAX_DOCS_PER_MATTER)
    p.add_argument("--max-reads", type=int, default=DEFAULT_MAX_READS)
    p.add_argument(
        "--generated-at",
        help="ISO timestamp stamped into the report (default: now, UTC). Passing it makes a run reproducible.",
    )
    args = p.parse_args(argv)

    if not args.firm_name.strip():
        print("REFUSED: --firm-name is empty", file=sys.stderr)
        return 2

    try:
        vocabulary = load_cohort_vocabulary(args.customer_yaml)
        from smokeball_connector.client import build_client_from_env

        report, entries = survey(
            build_client_from_env(),
            firm_name=args.firm_name,
            vocabulary=vocabulary,
            budget=Budget(
                max_matters=args.max_matters,
                max_docs_per_matter=args.max_docs_per_matter,
                max_reads=args.max_reads,
            ),
            generated_at=args.generated_at,
        )
    except (SurveyError, ValueError, OSError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2

    write_report(report, args.out_report)
    if args.out_manifest:
        write_manifest(
            entries,
            args.out_manifest,
            firm_name=args.firm_name,
            generated_at=report["generated_at"],
        )

    print(_summarize(report, entries))
    print(f"\nreport   -> {args.out_report}")
    if args.out_manifest:
        print(f"manifest -> {args.out_manifest}")
    print(
        "\nThe manifest is a PROPOSAL. Every row was classified from document "
        "text, not from authorship metadata (there is none). Review it before "
        "feeding it to bin/voice-fetch-corpus.py."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
