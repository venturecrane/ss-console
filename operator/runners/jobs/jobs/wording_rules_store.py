"""The one reader of an installed ``wording.json``: the install letter and the job lanes ask here.

WHY (2026-11-06). A firm's confirmed wording rule ("never cite the medical
records in a brief") was installed into ``specs/classes/work_product/*.md`` and
read only by chat turns. Every document a firm actually receives is made by a
queued job, and no job read the rule, so the firm was told "recorded, but not yet
in effect" and SMD pasted rules into prompt files by hand. The seat's spec
applier now writes each confirmed ``work_product`` rule, with the document kinds
and the attorney it covers, to ``classes/work_product/wording.json`` beside
``layout.json``, under the same root-computed manifest. The drafting and demand
lanes read it here and put the rules in scope into what they send the model; the
install letter reads it here to say whether the rule is in effect.

THE FILE EXISTS TWICE, byte for byte, because the seat runs the overlay and the
jobs runner in different venvs and neither imports the other:
``operator/hermes-smd-overlay/shared/wording_rules_store.py`` and
``operator/runners/jobs/jobs/wording_rules_store.py``.
``tests/test_wording_rules_store_parity.py`` (both sides) fails if they differ.

THREE ANSWERS, the layout store's: ``NONE`` is a seat with no wording rules,
``OK`` is a file its manifest vouches for, ``UNUSABLE`` is a store that is
present and cannot be trusted, which is never read as "no rules".

WHICH RULES A DOCUMENT GETS is decided here, in code, by the document's kind and
the attorney whose name it goes out under (:func:`applicable`). A rule for
another kind or another attorney is never selected.

STDLIB ONLY: both venvs and both test suites load it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The schema of ``wording.json``. Bumped with the applier's renderer.
SCHEMA_VERSION = 1
#: The output class whose rules shape a filed document (ADR 0083).
RULE_CLASS = "work_product"
WORDING_NAME = "wording.json"
WORDING_REL = f"classes/{RULE_CLASS}/{WORDING_NAME}"
MANIFEST = "manifest.json"
#: The manifest's section naming this file and its digest.
MANIFEST_KEY = "wordings"

#: Every kind of document a wording rule may cover. The drafting lane's five
#: (``plugins/hermes-smd-lanes/drafting.py`` ``DOCUMENT_CLASSES``), the demand
#: lane's one, and the plain letter the chat-turn tool renders.
KINDS: tuple[str, ...] = (
    "mediation_brief",
    "discovery_set",
    "discovery_response",
    "memo",
    "depo_outline",
    "demand_letter",
    "letter",
)

#: The kinds a queued job makes and reads these rules for. ``letter`` is not one:
#: nothing that makes a letter reads this file, so a rule for letters alone is
#: never called in effect.
LANE_KINDS: frozenset[str] = frozenset(k for k in KINDS if k != "letter")

#: How each kind is said to a person, singular and plural.
KIND_WORDS: dict[str, tuple[str, str]] = {
    "mediation_brief": ("mediation brief", "mediation briefs"),
    "discovery_set": ("set of discovery requests", "sets of discovery requests"),
    "discovery_response": ("discovery response", "discovery responses"),
    "memo": ("memo", "memos"),
    "depo_outline": ("deposition outline", "deposition outlines"),
    "demand_letter": ("demand letter", "demand letters"),
    "letter": ("letter", "letters"),
}

#: Who a document is written for. A rule may be limited to one reader; a rule
#: with none covers every reader. The job lanes pass each document's reader, the
#: document-kind registry's (``operator_connector_sdk/library/readers.py``), so a
#: reader-scoped rule reaches only documents for that reader (:func:`applicable`).
READERS: tuple[str, ...] = (
    "client",
    "adjuster",
    "opposing_counsel",
    "mediator",
    "vendor",
    "internal",
    "court",
)

#: How each reader is said to a person.
READER_WORDS: dict[str, str] = {
    "client": "the client",
    "adjuster": "an adjuster",
    "opposing_counsel": "opposing counsel",
    "mediator": "the mediator",
    "vendor": "a vendor",
    "internal": "the firm's own people",
    "court": "the court",
}

NONE = "none"
OK = "ok"
UNUSABLE = "unusable"

#: The heading the rules arrive under in what a job sends the model.
BLOCK_HEADING = (
    "## THE FIRM'S STANDING RULES (said by the firm and confirmed by it; binding on this document. "
    "Where a rule and anything above disagree, the rule is the firm's later instruction and wins)"
)


@dataclass(frozen=True)
class Store:
    status: str
    rules: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


def _unusable(reason: str) -> Store:
    return Store(UNUSABLE, [], reason)


def name_key(name: Any) -> str:
    """An attorney's name as two sources are compared: spacing and case folded."""
    return " ".join(str(name or "").split()).casefold()


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)


def _rule_problems(rule: Any) -> list[str]:
    if not isinstance(rule, dict) or not isinstance(rule.get("rule_id"), str):
        return ["a rule has no id"]
    rid = rule["rule_id"]
    if not isinstance(rule.get("text"), str) or not rule["text"].strip():
        return [f"rule {rid} has no text"]
    if not _strings(rule.get("wording")):
        return [f"rule {rid} has no wording list"]
    kinds = rule.get("doc_kinds")
    if not _strings(kinds) or not kinds or any(k not in KINDS for k in kinds):
        return [f"rule {rid} names no known document kind"]
    attorney = rule.get("attorney")
    if attorney is not None and (not isinstance(attorney, str) or not attorney.strip()):
        return [f"rule {rid} has a malformed attorney"]
    reader = rule.get("reader")
    if reader is not None and reader not in READERS:
        return [f"rule {rid} names no known reader"]
    return []


def _manifest_entry(root: Path) -> tuple[Any, Store | None]:
    """``(entry, None)`` or ``(None, the answer)`` when the manifest settles it."""
    try:
        raw = (root / MANIFEST).read_text("utf-8")
    except FileNotFoundError:
        if (root / WORDING_REL).exists():
            return None, _unusable("wording.json has no manifest beside it")
        return None, Store(NONE)
    except (OSError, ValueError) as exc:
        return None, _unusable(f"the manifest cannot be read: {type(exc).__name__}")
    try:
        manifest = json.loads(raw)
    except ValueError:
        return None, _unusable("the manifest is not valid JSON")
    if not isinstance(manifest, dict):
        return None, _unusable("the manifest is not an object")
    section = manifest.get(MANIFEST_KEY) or {}
    return (section.get(WORDING_REL) if isinstance(section, dict) else None), None


def read(spec_dir: str | Path) -> Store:
    """What the spec store holds for the job lanes. Never raises."""
    root = Path(spec_dir)
    entry, settled = _manifest_entry(root)
    if settled is not None:
        return settled
    try:
        blob = (root / WORDING_REL).read_bytes()
    except FileNotFoundError:
        return _unusable("the manifest names wording.json and it is gone") if entry else Store(NONE)
    except OSError as exc:
        return _unusable(f"wording.json cannot be read: {type(exc).__name__}")
    if not isinstance(entry, dict):
        return _unusable("wording.json is present and the manifest does not name it")
    if entry.get("sha256") != hashlib.sha256(blob).hexdigest():
        return _unusable("wording.json does not match the digest in the manifest")
    try:
        doc = json.loads(blob.decode("utf-8"))
    except ValueError:
        return _unusable("wording.json is not valid JSON")
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        return _unusable("wording.json is not the schema this code reads")
    rules = doc.get("rules")
    if not isinstance(rules, list):
        return _unusable("wording.json has no rule list")
    found = [p for rule in rules for p in _rule_problems(rule)]
    if found:
        return _unusable("; ".join(found)[:200])
    return Store(OK, rules)


def applicable(
    rules: list[dict[str, Any]], kind: str, attorney: Any = None, reader: str | None = None
) -> list[dict[str, Any]]:
    """The rules a document of ``kind`` in ``attorney``'s name, for ``reader``, follows.

    A rule covers a document only when its kinds name the document's kind, when
    it names an attorney that attorney is the document's, and when it names a
    reader that reader is the document's. A document whose attorney or reader is
    unknown follows only the rules that name none, so a scoped rule never leaks
    into a document that cannot say it is in scope. A rule with nothing to say
    in words (every clause is layout, applied by code) is skipped. Oldest first.
    """
    who = name_key(attorney)
    out: list[dict[str, Any]] = []
    for rule in rules or ():
        if kind not in (rule.get("doc_kinds") or ()):
            continue
        named = rule.get("attorney")
        if named is not None and (not who or name_key(named) != who):
            continue
        scoped = rule.get("reader")
        if scoped is not None and scoped != reader:
            continue
        if not rule.get("wording"):
            continue
        out.append(rule)
    return out


def block(rules: list[dict[str, Any]]) -> str:
    """The rules as a job sends them to the model, or ``""`` for none."""
    if not rules:
        return ""
    lines = [BLOCK_HEADING, ""]
    for rule in rules:
        lines.append(
            f"- [rule {rule['rule_id']}] " + " ".join(str(w).strip() for w in rule["wording"])
        )
    return "\n".join(lines)


def kinds_said(kinds: list[str] | tuple[str, ...]) -> str:
    """``mediation briefs and memos``: the kinds in the registry's order, plural."""
    words = [KIND_WORDS[k][1] for k in KINDS if k in kinds]
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


__all__ = [
    "BLOCK_HEADING",
    "KINDS",
    "KIND_WORDS",
    "LANE_KINDS",
    "MANIFEST",
    "MANIFEST_KEY",
    "NONE",
    "OK",
    "READERS",
    "READER_WORDS",
    "RULE_CLASS",
    "SCHEMA_VERSION",
    "UNUSABLE",
    "WORDING_NAME",
    "WORDING_REL",
    "Store",
    "applicable",
    "block",
    "kinds_said",
    "name_key",
    "read",
]
