"""The form interrogatory response body, laid out by code.

For each checked number, in the form's order: the label, the official text
restated (the stem, then each lettered subpart on its own line), then the
response. A ``none``, ``no_information`` or ``not_applicable`` answer is the
firm's own sentence for that kind (a per-number sentence wins). An answered
number gives, per item, its name on its own line and then EVERY lettered
subpart on its own line in the form's order, "(a) ...", with the firm's
no-information sentence on a subpart that has no value. No model text decides
any of this layout, and no sentence is supplied here: the sentences are the
firm's (``FormPolicy``), so an unconfigured firm gets an error, not a default.

The output is a list of ``Block``s; ``to_markdown`` is the text the lane's
Word renderer takes (``drafting/render.py`` -> ``smokeball_connector.
docx_format.render_document``, which parses it with ``docgrammar``: each block
one paragraph, a label line styled as an item label by ``docx_classes``'
discovery label pattern). ``to_plain`` is the same lines without markup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .answers import Answer, Item
from .catalog import Catalogue, load

LABEL = "FORM INTERROGATORY NO. {n}:"
RESPONSE_LABEL = "RESPONSE TO FORM INTERROGATORY NO. {n}:"
FIXED_KINDS = ("none", "no_information", "not_applicable")
#: Block roles. "label" and "response_label" are bold label lines.
ROLES = ("label", "stem", "subpart", "response_label", "item_name", "value", "fixed")


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class FormPolicy:
    """The firm's fixed sentences and labels (its form-responses policy)."""

    sentences: Mapping[str, str]  # kind -> sentence, for every FIXED_KINDS kind
    per_number: Mapping[str, Mapping[str, str]] = field(default_factory=dict)  # number -> kind -> sentence
    label: str = LABEL
    response_label: str = RESPONSE_LABEL

    def sentence(self, number: str, kind: str) -> str:
        return self.per_number.get(number, {}).get(kind) or self.sentences[kind]


def _sentence(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not raw.strip() or "\n" in raw:
        raise PolicyError(f"{where}: must be one line of text")
    return " ".join(raw.split())


def _template(raw: Any, default: str, where: str) -> str:
    if raw is None:
        return default
    t = _sentence(raw, where)
    if t.count("{n}") != 1 or "{" in t.replace("{n}", ""):
        raise PolicyError(f"{where}: must contain {{n}} once and no other braces")
    return t


def policy_from_mapping(raw: Mapping[str, Any]) -> FormPolicy:
    """Parse the firm's mapping: ``sentences`` (one per kind, all required),
    optional ``per_number`` overrides, optional ``label`` / ``response_label``."""
    if not isinstance(raw, Mapping):
        raise PolicyError("the form-responses policy must be a mapping")
    extra = sorted(set(raw) - {"sentences", "per_number", "label", "response_label"})
    if extra:
        raise PolicyError(f"keys not in the policy: {extra}")
    s = raw.get("sentences")
    if not isinstance(s, Mapping) or sorted(s) != sorted(FIXED_KINDS):
        raise PolicyError(f"sentences must give exactly {list(FIXED_KINDS)}")
    sentences = {k: _sentence(s[k], f"sentences.{k}") for k in FIXED_KINDS}
    per: dict[str, dict[str, str]] = {}
    for n, kinds in (raw.get("per_number") or {}).items():
        if not isinstance(kinds, Mapping) or any(k not in FIXED_KINDS for k in kinds):
            raise PolicyError(f"per_number.{n}: must map some of {list(FIXED_KINDS)} to sentences")
        per[str(n)] = {k: _sentence(v, f"per_number.{n}.{k}") for k, v in kinds.items()}
    return FormPolicy(
        sentences,
        per,
        _template(raw.get("label"), LABEL, "label"),
        _template(raw.get("response_label"), RESPONSE_LABEL, "response_label"),
    )


@dataclass(frozen=True)
class Block:
    role: str
    text: str
    number: str
    letter: str = ""


def _restated(n: str, cat: Catalogue, policy: FormPolicy) -> list[Block]:
    it = cat.get(n)
    out = [Block("label", policy.label.format(n=n), n), Block("stem", it.stem, n)]
    out += [Block("subpart", f"({k}) {t}", n, k) for k, t in it.subparts]
    return out


def _item_blocks(n: str, item: Item, letters: tuple[str, ...], policy: FormPolicy) -> list[Block]:
    out = [Block("item_name", item.name, n)] if item.name else []
    if not letters:
        return out + [Block("value", item.parts[""].text, n)]
    for k in letters:
        v = item.parts.get(k)
        text = v.text if v else policy.sentence(n, "no_information")
        out.append(Block("value" if v else "fixed", f"({k}) {text}", n, k))
    return out


def render(
    checked: list[str] | tuple[str, ...],
    answers: list[Answer] | tuple[Answer, ...],
    policy: FormPolicy,
    cat: Catalogue | None = None,
) -> list[Block]:
    """Blocks for every checked number in form order. An answer for an
    unchecked number, or a checked number with no answer, is an error."""
    cat = cat or load()
    by = {a.number: a for a in answers}
    stray = sorted(set(by) - set(checked), key=cat.position)
    missing = [n for n in checked if n not in by]
    if stray or missing:
        raise ValueError(f"answers for unchecked numbers {stray}; checked numbers without an answer {missing}")
    out: list[Block] = []
    for n in sorted(checked, key=cat.position):
        a = by[n]
        out += _restated(n, cat, policy)
        out.append(Block("response_label", policy.response_label.format(n=n), n))
        if a.kind in FIXED_KINDS:
            out.append(Block("fixed", policy.sentence(n, a.kind), n))
            continue
        for item in a.items:
            out += _item_blocks(n, item, cat.get(n).letters, policy)
    return out


def to_markdown(blocks: list[Block]) -> str:
    """One paragraph per block; labels bold, so ``docx_format`` styles them as item labels."""
    lines = [f"**{b.text}**" if b.role in ("label", "response_label") else b.text for b in blocks]
    return "\n\n".join(lines) + "\n"


def to_plain(blocks: list[Block]) -> str:
    return "\n".join(b.text for b in blocks) + "\n"
