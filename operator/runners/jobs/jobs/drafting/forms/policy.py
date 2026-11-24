"""The firm's form-response policy: ``drafting-firm.yaml`` ``form_responses:``.

Everything a form interrogatory response says that is not a value from the
matter file is the FIRM's, authored once, so there are no defaults here: an
unconfigured firm stops before anything is paid (``policy_missing``), never
answers in our words. The section, every key required (a list or a map may be
empty)::

    form_responses:
      sentences:                {none, no_information, not_applicable}   one line each
      per_number:               {<number>: {<kind>: <sentence>}}         overrides, may be {}
      responding_role:          <role>                                   the side the firm answers for
      not_applicable_numbers:   {<role>: [<number>, ...]}                answered by code, no model call
      client_confirm_numbers:   [<number>, ...]                          always carry the client marker
      no_admissions_17_1:       <sentence>        17.1 when no requests for admission were served
      preamble:                 [<paragraph>, ...]                       may be [] (the shell holds it)
      closing:                  [<paragraph>, ...]                       may be []
      label:                    "FORM INTERROGATORY NO. {n}:"
      response_label:           "RESPONSE TO FORM INTERROGATORY NO. {n}:"
      client_marker:            <what the attorney confirms with the client, one line>
      skeleton:                 <path in inputs: the form-response shell, one {{FORM RESPONSES}} line>
      rules:                    {apply: [<rule id>], not_for_form_sets: [<rule id>]}   OPTIONAL overrides

WHICH OF THE FIRM'S WORDING RULES A FORM SET FOLLOWS (:func:`placed`) is read
from each rule's own record, not from this section (2026-11-23: a hand list per
rule id meant every rule the firm stated after the policy was written stopped
every form job ``rule_unplaced`` until SMD edited the vault). A frozen rule
applies when its recorded ``doc_kinds`` name ``discovery_response``, the kind
a response job freezes its rules for. ``rules`` stays as the firm's override in
both directions: ``not_for_form_sets`` keeps out a rule written for another set
(the "compilation" objection is for special interrogatories only), ``apply``
takes in one whose kinds do not say so. Only a rule that records no document
kind and that no list names stops the job ``rule_unplaced``. A misplaced rule
cannot write into a form answer either way: the answer call returns values
with cites only, and every sentence and objection on the paper is this policy's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .catalog import Catalogue, load
from .render_body import FormPolicy, PolicyError, policy_from_mapping

KEY = "form_responses"
BODY_SLOT = "{{FORM RESPONSES}}"
KEYS = (
    "sentences",
    "per_number",
    "responding_role",
    "not_applicable_numbers",
    "client_confirm_numbers",
    "no_admissions_17_1",
    "preamble",
    "closing",
    "label",
    "response_label",
    "client_marker",
    "skeleton",
    "rules",
)
#: Keys a policy may leave out.
OPTIONAL = ("rules",)
#: The document kind a response job's wording rules are frozen for (``firm_wording.freeze``).
RESPONSE_KIND = "discovery_response"
RULE_KEYS = ("apply", "not_for_form_sets")


@dataclass(frozen=True)
class ResponsePolicy:
    """The parsed section: ``form`` is what ``render_body`` and ``checks`` read."""

    form: FormPolicy
    responding_role: str
    not_applicable: tuple[str, ...]
    client_confirm: tuple[str, ...]
    no_admissions_17_1: str
    preamble: tuple[str, ...]
    closing: tuple[str, ...]
    client_marker: str
    skeleton: str
    rules_apply: tuple[str, ...]
    rules_skip: tuple[str, ...]

    @property
    def marker(self) -> str:
        """The lane's client-confirm marker (``outcome.MARKERS``), in the firm's words."""
        return "{{CLIENT: " + self.client_marker + "}}"


def _line(v: Any, where: str, out: list[str]) -> str:
    if not isinstance(v, str) or not v.strip() or "\n" in v or "{{" in v or "}}" in v:
        out.append(f"{KEY}.{where}: expected one line of text with no {{{{ }}}} marker")
        return ""
    return " ".join(v.split())


def _paragraphs(v: Any, where: str, out: list[str]) -> tuple[str, ...]:
    if not isinstance(v, list):
        out.append(f"{KEY}.{where}: expected a list of paragraphs (may be empty)")
        return ()
    return tuple(_line(p, f"{where}[{i}]", out) for i, p in enumerate(v))


def _numbers(v: Any, where: str, cat: Catalogue, out: list[str]) -> tuple[str, ...]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        out.append(f"{KEY}.{where}: expected a list of interrogatory numbers")
        return ()
    out += [f"{KEY}.{where}: {n} is not an interrogatory on {cat.form}" for n in v if not cat.has(n)]
    return tuple(v)


def _ids(v: Any, where: str, out: list[str]) -> tuple[str, ...]:
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        out.append(f"{KEY}.rules.{where}: expected a list of rule ids")
        return ()
    return tuple(v)


def _parse(body: Any, cat: Catalogue) -> tuple[ResponsePolicy | None, list[str]]:
    if not isinstance(body, Mapping):
        return None, [f"{KEY}: expected a map"]
    out = [f"{KEY}.{k}: unknown key (closed key set)" for k in body if k not in KEYS]
    out += [f"{KEY}.{k}: required" for k in KEYS if k not in body and k not in OPTIONAL]
    if out:
        return None, out
    try:
        form = policy_from_mapping({k: body[k] for k in ("sentences", "per_number", "label", "response_label")})
    except PolicyError as exc:
        return None, [f"{KEY}: {exc}"]
    out += [f"{KEY}.per_number: {n} is not an interrogatory on {cat.form}" for n in form.per_number if not cat.has(n)]
    role = _line(body["responding_role"], "responding_role", out)
    na = body["not_applicable_numbers"]
    if not isinstance(na, Mapping) or role not in na:
        out.append(f"{KEY}.not_applicable_numbers: expected a map with the responding role {role!r}")
        not_app: tuple[str, ...] = ()
    else:
        not_app = _numbers(na[role], f"not_applicable_numbers.{role}", cat, out)
    rules = body.get("rules", {})
    if not isinstance(rules, Mapping) or not set(rules) <= set(RULE_KEYS):
        out.append(f"{KEY}.rules: expected a map with only {list(RULE_KEYS)}")
        apply, skip = (), ()
    else:
        apply = _ids(rules.get("apply", []), "apply", out)
        skip = _ids(rules.get("not_for_form_sets", []), "not_for_form_sets", out)
        out += [f"{KEY}.rules: {r} is listed both to apply and not to" for r in sorted(set(apply) & set(skip))]
    skeleton = body["skeleton"]
    if not isinstance(skeleton, str) or not skeleton.strip():
        out.append(f"{KEY}.skeleton: expected a path in inputs")
    pol = ResponsePolicy(
        form=form,
        responding_role=role,
        not_applicable=not_app,
        client_confirm=_numbers(body["client_confirm_numbers"], "client_confirm_numbers", cat, out),
        no_admissions_17_1=_line(body["no_admissions_17_1"], "no_admissions_17_1", out),
        preamble=_paragraphs(body["preamble"], "preamble", out),
        closing=_paragraphs(body["closing"], "closing", out),
        client_marker=_line(body["client_marker"], "client_marker", out),
        skeleton=str(skeleton or ""),
        rules_apply=apply,
        rules_skip=skip,
    )
    return (None, out) if out else (pol, [])


def validate(body: Any, cat: Catalogue | None = None) -> list[str]:
    """Every problem in the section as ``form_responses.key: message`` (``firm.validate``)."""
    return _parse(body, cat or load())[1]


def validate_in(data: Any) -> list[str]:
    """The section's problems when a firm config carries it (``firm.validate``); else none."""
    return validate(data[KEY]) if isinstance(data, Mapping) and KEY in data else []


def referenced_paths(data: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(where, path) for the shell the section names, pinned in inputs like every file."""
    body = data.get(KEY)
    if isinstance(body, Mapping) and isinstance(body.get("skeleton"), str):
        return [(f"{KEY}.skeleton", str(body["skeleton"]))]
    return []


class PolicyMissing(ValueError):
    """The firm authored no usable form-response policy."""


def load_policy(firm_data: Mapping[str, Any], cat: Catalogue | None = None) -> ResponsePolicy:
    if KEY not in firm_data:
        raise PolicyMissing(f"the firm's {KEY} section is not authored")
    pol, problems = _parse(firm_data[KEY], cat or load())
    if pol is None:
        raise PolicyMissing("; ".join(problems)[:300])
    return pol


def authored(firm_data: Mapping[str, Any]) -> bool:
    """The firm wrote a form-response policy: its form sets are the Operator's
    (``lane_hook.by_operator``). A section present but broken still counts, so
    the job stops ``policy_missing`` for SMD rather than passing quietly to SMD."""
    return isinstance(firm_data, Mapping) and KEY in firm_data


def placed(policy: ResponsePolicy, rules: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """(the frozen rules a form set follows, the ids of rules nothing places).

    The firm's override lists win; otherwise a rule follows its own recorded
    ``doc_kinds``. Order is the frozen order (oldest first)."""
    apply, skip = set(policy.rules_apply), set(policy.rules_skip)
    keep: list[dict[str, Any]] = []
    left: list[str] = []
    for r in rules:
        rid = str(r.get("rule_id"))
        kinds = r.get("doc_kinds")
        if rid in skip:
            continue
        if rid in apply or (isinstance(kinds, list) and RESPONSE_KIND in kinds):
            keep.append(r)
        elif not kinds:
            left.append(rid)
    return keep, left
