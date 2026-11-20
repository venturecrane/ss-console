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
      preamble:                 [<paragraph>, ...]
      closing:                  [<paragraph>, ...]
      label:                    "FORM INTERROGATORY NO. {n}:"
      response_label:           "RESPONSE TO FORM INTERROGATORY NO. {n}:"
      client_marker:            <what the attorney confirms with the client, one line>
      skeleton:                 <path in inputs: the form-response shell, one {{FORM RESPONSES}} line>
      rules:                    {apply: [<rule id>], not_for_form_sets: [<rule id>]}

``rules`` places each of the firm's installed wording rules: a rule the firm
wrote for special interrogatories (the "compilation" objection) is not a form
set's rule, so the form path applies only the rules listed under ``apply``,
and a frozen rule in neither list stops the job ``rule_unplaced`` before spend.
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
    if not isinstance(v, list) or not v:
        out.append(f"{KEY}.{where}: expected a non-empty list of paragraphs")
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
    out += [f"{KEY}.{k}: required" for k in KEYS if k not in body]
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
    rules = body["rules"]
    if not isinstance(rules, Mapping) or sorted(rules) != sorted(RULE_KEYS):
        out.append(f"{KEY}.rules: expected exactly {list(RULE_KEYS)}")
        apply, skip = (), ()
    else:
        apply, skip = _ids(rules["apply"], "apply", out), _ids(rules["not_for_form_sets"], "not_for_form_sets", out)
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


def unplaced(policy: ResponsePolicy, rule_ids: list[str]) -> list[str]:
    """The frozen rules the policy places nowhere (each stops the job before spend)."""
    placed = set(policy.rules_apply) | set(policy.rules_skip)
    return [r for r in rule_ids if r not in placed]
