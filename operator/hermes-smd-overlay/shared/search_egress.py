"""Search egress: what may leave the seat in a web query (ADR 0099).

A web search sends its query to a third party (the search provider, and through
a fetched URL, the site). A query that carries a client's name, a case number or
a matter number tells that party who the firm represents and what about. ABA
Formal Opinion 512 asks a lawyer to know where client information goes when a
tool is used; this module is the code half of that answer.

Two layers, both evaluated in the trust plugin's ``pre_tool_call`` beside the
matter-mixing fence (``enforce._evaluate_tool_call``), before exposure, for the
three tools in :data:`SEARCH_TOOLS`:

Layer 1, structural. On a seat that authors a client-data connector (a
practice-management backend), a session that has READ client material may not
search at all. "Read client material" means the session is matter-bound
(``shared.matter_binding``) or its provenance register is non-empty
(``shared.provenance``: identifiers read from the tenant's own records). No
recall is required of anyone: the rule does not try to spot client facts in the
query, it keeps the two activities in different sessions.

Layer 2, screen, every seat. The query (for ``web_extract`` the URL with its
query string; for ``web_research`` the question and channel id) is refused when
it carries any of:

* an identifier SHAPE (``shared.identifier_filter``: SSN, A-number, receipt,
  case or matter number), whether or not it was read;
* a token the session's provenance register holds (dates excluded);
* a matter number or party email from ``shared.matter_binding``;
* a party NAME from :func:`record_party_names`, seeded only from structured
  Smokeball fields whose role is client, claimant, plaintiff or party (never an
  insurer, provider, court or counsel), matched as whole words of 4+ chars.

The refusal reason names KINDS only, never the matched value, and the audit row
carries the query length and the verdict, never the text and never a hash.

Everything here is process-local and LRU-bounded, the same shape as
``shared.provenance``. A fault in the screen refuses (the safe direction for an
egress control); a fault in Layer 1's seat check reads as a client-data seat.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: The tools whose arguments leave the seat as a web query or fetch.
SEARCH_TOOLS: frozenset[str] = frozenset({"web_search", "web_extract", "web_research"})

VERDICT_PASS = "pass"
VERDICT_REFUSED_STRUCTURAL = "refused_structural"
VERDICT_REFUSED_SCREEN = "refused_screen"

KIND_IDENTIFIER = "identifier_shape"
KIND_REGISTER = "read_identifier"
KIND_MATTER = "matter_number"
KIND_PARTY_EMAIL = "party_email"
KIND_PARTY_NAME = "party_name"

_MAX_SESSIONS = 256
_MAX_NAMES_PER_SESSION = 512
_MAX_QUERY_CHARS = 20_000
_MIN_NAME_CHARS = 4

#: Role words that make a contact a client-side person whose name must not be
#: searched. Whole-word match against the contact's Role tags.
_CLIENT_ROLE_WORDS: frozenset[str] = frozenset({"client", "claimant", "plaintiff", "party"})
#: Role words that disqualify, even beside a client word ("Client's Insurer").
_EXCLUDED_ROLE_WORDS: frozenset[str] = frozenset(
    {
        "insurer",
        "insurance",
        "adjuster",
        "carrier",
        "provider",
        "doctor",
        "physician",
        "hospital",
        "medical",
        "court",
        "judge",
        "counsel",
        "attorney",
        "lawyer",
        "firm",
        "staff",
        "defendant",
        "opposing",
        "witness",
        "expert",
        "vendor",
    }
)

_lock = threading.Lock()
_names: OrderedDict[str, set[str]] = OrderedDict()
_client_contacts: OrderedDict[str, set[str]] = OrderedDict()
_pending_names: OrderedDict[str, dict[str, list[str]]] = OrderedDict()
_verdicts: OrderedDict[str, tuple[int, str]] = OrderedDict()


@dataclass(frozen=True)
class EgressVerdict:
    """One search call's egress decision. ``reason`` names kinds only."""

    verdict: str
    query_len: int
    reason: str = ""

    @property
    def refused(self) -> bool:
        return self.verdict != VERDICT_PASS


# ---------------------------------------------------------------------------
# The query text
# ---------------------------------------------------------------------------


def _strings(node: Any, depth: int = 0) -> Iterator[str]:
    if depth > 4:
        return
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value, depth + 1)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _strings(value, depth + 1)


def query_text(args: Any) -> str:
    """Every string in the call's arguments, joined.

    Deliberately every string, not a named field: the pinned Hermes tools call
    theirs ``query`` / ``urls`` today, and a renamed argument must not become an
    unscreened one. A URL is screened whole, query string included.
    """
    joined = "\n".join(_strings(args))
    return joined[:_MAX_QUERY_CHARS]


# ---------------------------------------------------------------------------
# Layer 1 inputs
# ---------------------------------------------------------------------------


def seat_has_client_data_connector() -> bool:
    """True when this seat authors a practice-management (client-data) backend.

    A synthetic backend is not one. Any fault reads True: the structural rule
    then applies, which can only withhold a search, never release client data.
    """
    try:
        from shared.customer_config import CustomerConfig

        connectors = CustomerConfig.from_volume().connectors
    except Exception:  # noqa: BLE001 - unreadable config: assume client data
        return True
    for key, conn in connectors.items():
        if not isinstance(conn, dict) or conn.get("enabled") is False:
            continue
        backend = str(conn.get("backend") or "").lower()
        if "smokeball" in backend:
            return True
        if key == "PracticeManagement" and backend and not backend.startswith("synthetic:"):
            return True
    return False


def session_read_client_material(session_id: str) -> bool:
    """True when this session is matter-bound or its provenance register holds
    anything read from the tenant's records."""
    from shared import matter_binding, provenance

    reg = provenance.peek(session_id)
    if reg is not None and bool(reg):
        return True
    membership = matter_binding.peek(session_id)
    if membership is None:
        return False
    return bool(
        membership.known_matters()
        or membership.content_read_matters()
        or membership.known_numbers()
    )


# ---------------------------------------------------------------------------
# Party names (seeded from structured Smokeball fields only)
# ---------------------------------------------------------------------------


def _touch(store: OrderedDict, session_id: str, factory: Any) -> Any:
    existing = store.get(session_id)
    if existing is None:
        existing = factory()
        store[session_id] = existing
        while len(store) > _MAX_SESSIONS:
            store.popitem(last=False)
    else:
        store.move_to_end(session_id)
    return existing


def _iter_dicts(node: Any, depth: int = 0) -> Iterator[dict]:
    if depth > 6:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dicts(value, depth + 1)
    elif isinstance(node, list):
        for value in node[:500]:
            yield from _iter_dicts(value, depth + 1)


def _as_payload(result: Any) -> Any:
    if isinstance(result, (dict, list)):
        return result
    if isinstance(result, str):
        text = result.strip()
        if text[:1] in ("{", "["):
            import json

            try:
                return json.loads(text)
            except ValueError:
                return None
    return None


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z]+", value.lower()))


def is_client_role(role: Any) -> bool:
    """A role names a client-side person: a client word and no excluded word."""
    if not isinstance(role, str):
        return False
    words = _words(role)
    return bool(words & _CLIENT_ROLE_WORDS) and not (words & _EXCLUDED_ROLE_WORDS)


def _role_names(node: dict) -> list[str]:
    out: list[str] = []
    for tag in node.get("tags") or []:
        if isinstance(tag, dict) and (tag.get("type") or "") == "Role":
            name = tag.get("name")
            if isinstance(name, str):
                out.append(name)
    roles = node.get("roles")
    if isinstance(roles, list):
        out.extend(r for r in roles if isinstance(r, str))
    for key in ("role", "roleName"):
        value = node.get(key)
        if isinstance(value, str):
            out.append(value)
    return out


def _clean_name_part(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned or len(cleaned) > 80 or re.search(r"\d", cleaned):
        return ""
    return cleaned


def _contact_names(node: dict) -> list[str]:
    """The searchable name forms of one contact record: full name and surname
    for a person, the name for a company. Each kept only at 4+ characters."""
    names: list[str] = []
    person = node.get("person")
    company = node.get("company")
    if isinstance(person, dict):
        first = _clean_name_part(person.get("firstName"))
        last = _clean_name_part(person.get("lastName"))
        if first and last:
            names.append(f"{first} {last}")
        if last:
            names.append(last)
    elif isinstance(company, dict):
        cname = _clean_name_part(company.get("name"))
        if cname:
            names.append(cname)
    return [n for n in names if len(n) >= _MIN_NAME_CHARS]


def _contact_id(node: dict) -> str:
    value = node.get("id")
    return value.strip() if isinstance(value, str) else ""


def record_party_names(session_id: str, tool_name: str, result: Any) -> None:
    """Seed this session's party-name set from a Smokeball read RESULT.

    Only ``mcp_smokeball_*`` results, and only structured fields:

    * a matter's ``clientIds`` and its ``parties`` entries with ``side ==
      client`` mark those contact ids as client-side;
    * a contact record (``person`` / ``company``) is seeded when its own Role
      tags name a client, claimant, plaintiff or party (and no excluded role),
      or when a matter read this session marked its id client-side;
    * a role record that names a client role and nests a contact is seeded the
      same way.

    A contact read BEFORE the matter that marks it is held and seeded when the
    mark arrives, so read order does not matter. Never raises.
    """
    if not session_id or not isinstance(tool_name, str):
        return
    if not tool_name.strip().lower().startswith("mcp_smokeball_"):
        return
    try:
        payload = _as_payload(result)
        if payload is None:
            return
        with _lock:
            names = _touch(_names, session_id, set)
            clients = _touch(_client_contacts, session_id, set)
            pending = _touch(_pending_names, session_id, dict)

            def _seed(found: list[str]) -> None:
                for n in found:
                    if len(names) < _MAX_NAMES_PER_SESSION:
                        names.add(n.lower())

            for node in _iter_dicts(payload):
                for cid in node.get("clientIds") or []:
                    if isinstance(cid, str) and cid:
                        clients.add(cid)
                        _seed(pending.pop(cid, []))
                parties = node.get("parties")
                if isinstance(parties, list):
                    for p in parties:
                        if not isinstance(p, dict):
                            continue
                        cid = p.get("contact_id")
                        client_side = p.get("side") == "client" or any(
                            is_client_role(r) for r in _role_names(p)
                        )
                        if isinstance(cid, str) and cid and client_side:
                            clients.add(cid)
                            _seed(pending.pop(cid, []))
                if "person" in node or "company" in node:
                    found = _contact_names(node)
                    if not found:
                        continue
                    cid = _contact_id(node)
                    if any(is_client_role(r) for r in _role_names(node)) or (
                        cid and cid in clients
                    ):
                        _seed(found)
                    elif cid and len(pending) < _MAX_NAMES_PER_SESSION:
                        pending[cid] = found
                # A role record: {name/roleName: "Plaintiff", contact: {...}}.
                contact = node.get("contact")
                if isinstance(contact, dict) and any(
                    is_client_role(r) for r in _role_names(node) + [str(node.get("name") or "")]
                ):
                    _seed(_contact_names(contact))
    except Exception:  # noqa: BLE001 - seeding must never perturb the tool path
        logger.debug("search_egress: party-name seed failed", exc_info=True)


def party_names(session_id: str) -> frozenset[str]:
    with _lock:
        return frozenset(_names.get(session_id) or ())


# ---------------------------------------------------------------------------
# Layer 2 screen
# ---------------------------------------------------------------------------


def _contains_word(text_lower: str, phrase: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text_lower) is not None


def screen_kinds(session_id: str, text: str) -> list[str]:
    """The kinds of client-identifying content in ``text`` (empty when clean)."""
    from shared import identifier_filter, matter_binding, provenance

    kinds: list[str] = []
    if not text:
        return kinds
    lower = text.lower()
    for hit in identifier_filter._extract(text, include_names=False):  # noqa: SLF001
        if hit.kind is not identifier_filter.IdKind.DATE:
            kinds.append(KIND_IDENTIFIER)
            break
    reg = provenance.peek(session_id)
    if reg is not None and reg.contains_text(text):
        kinds.append(KIND_REGISTER)
    membership = matter_binding.peek(session_id)
    if membership is not None:
        upper = text.upper()
        if any(
            re.search(r"(?<![A-Z0-9])" + re.escape(num) + r"(?![A-Z0-9])", upper)
            for num in membership.known_numbers()
            if num
        ):
            kinds.append(KIND_MATTER)
        if any(email and email in lower for email in membership.all_party_emails()):
            kinds.append(KIND_PARTY_EMAIL)
    if any(_contains_word(lower, name) for name in party_names(session_id)):
        kinds.append(KIND_PARTY_NAME)
    return kinds


def evaluate(session_id: str, tool_name: str, args: Any) -> EgressVerdict:
    """The egress verdict for one search call. Pass for any non-search tool."""
    if tool_name not in SEARCH_TOOLS:
        return EgressVerdict(VERDICT_PASS, 0)
    text = query_text(args)
    length = len(text)
    try:
        if seat_has_client_data_connector() and session_read_client_material(session_id):
            return EgressVerdict(
                VERDICT_REFUSED_STRUCTURAL,
                length,
                f"{tool_name} refused: this session read client material on a seat that "
                "holds client data, and a web query cannot leave a session that has. "
                "Search from a new conversation that has not opened client records.",
            )
        kinds = screen_kinds(session_id, text)
    except Exception:  # noqa: BLE001 - an egress screen that cannot look refuses
        logger.warning("search_egress: screen failed; refusing", exc_info=True)
        kinds = ["screen_unavailable"]
    if kinds:
        return EgressVerdict(
            VERDICT_REFUSED_SCREEN,
            length,
            f"{tool_name} refused: the query carries client-identifying content "
            f"({', '.join(sorted(set(kinds)))}). Rephrase it without names, numbers or "
            "addresses from the firm's records.",
        )
    return EgressVerdict(VERDICT_PASS, length)


# ---------------------------------------------------------------------------
# Hand-off to the audit row (length + verdict only)
# ---------------------------------------------------------------------------


def record_verdict(tool_call_id: str, verdict: EgressVerdict) -> None:
    """Hold this call's (length, verdict) for the audit row of the same call."""
    if not tool_call_id:
        return
    with _lock:
        _verdicts[tool_call_id] = (verdict.query_len, verdict.verdict)
        while len(_verdicts) > 1024:
            _verdicts.popitem(last=False)


def pop_verdict(tool_call_id: str) -> tuple[int, str] | None:
    if not tool_call_id:
        return None
    with _lock:
        return _verdicts.pop(tool_call_id, None)


def audit_fields(tool_name: str, tool_call_id: str) -> dict[str, Any]:
    """``web_query_len`` and ``web_query_verdict`` for a search tool's audit
    row; empty for anything else. Never the text, never a hash."""
    if tool_name not in SEARCH_TOOLS:
        return {}
    held = pop_verdict(tool_call_id)
    if held is None:
        return {}
    length, verdict = held
    return {"web_query_len": int(length), "web_query_verdict": verdict}


def _reset_for_tests() -> None:
    with _lock:
        _names.clear()
        _client_contacts.clear()
        _pending_names.clear()
        _verdicts.clear()
