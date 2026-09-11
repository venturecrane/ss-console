"""Every skill is bound to an output class, and every class declares all four
of its properties with an explicit disposition.

WHY THIS GATE EXISTS (ADR 0083). The four properties an output needs — voice,
format, gates, delivery — lived as prose scattered across 30 references/voice.md
and 37 references/output-format.md files, readable by people and by no code.
Nothing checked that a skill had them, that they agreed, or that a NEW skill got
them at all. This is the same forcing function as customer-yaml-blocks.yaml: a
skill nobody classified fails CI, so classification happens once, consciously,
instead of never.

It also pins two claims that were previously prose only:

  * `outbound: none` — 25 skills state in their bodies that they never send
    ("It sends nothing", "never sends", "writes ONLY an internal memo"). That is
    now an assertion checked against the skill's own declared action_class.
  * work_product placement — three of the four work_product drafters put the
    draft in a Smokeball memo and mail a citation-free pointer, because the mail
    channel's citation gate would refuse the draft. mediation-brief-drafter does
    the opposite. That deviation must be DECLARED, not discovered later as a
    delivery failure.

Run::

    cd operator && python3 -m pytest bin/tests/test_output_class_conformance.py -v
"""

from __future__ import annotations

from pathlib import Path

import yaml

_OP = Path(__file__).resolve().parents[2]
_REGISTRY = _OP / "contracts" / "output-classes.yaml"
_SKILLS = _OP / "skills"

_REQUIRED_PROPERTIES = ("voice", "format", "gates", "delivery")
_DISPOSITIONS = {"unauthored-ok", "fails-closed"}
_VOICE_PROVENANCE = {"persona", "firm", "court-register", "none"}
_FORMAT_PROVENANCE = {"customer", "rules", "convention", "none"}
_OUTBOUND = {"derived", "none"}


def _registry() -> dict:
    return yaml.safe_load(_REGISTRY.read_text(encoding="utf-8")) or {}


def _skill_dirs() -> set[str]:
    dirs = {p.name for p in _SKILLS.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()}
    assert dirs, "no skill directories found to check"
    return dirs


def _smd_metadata(skill: str) -> dict:
    """Parse the SKILL.md frontmatter's metadata.smd block.

    action_class and content_ceiling are NESTED under metadata.smd, not
    top-level — a detail worth pinning here because reading them as top-level
    keys silently yields nothing and every assertion below would pass vacuously.
    """
    text = (_SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    _, _, rest = text.partition("---")
    front, _, _ = rest.partition("\n---")
    parsed = yaml.safe_load(front) or {}
    return ((parsed.get("metadata") or {}).get("smd")) or {}


def _bindings() -> dict:
    bindings = _registry().get("skill_bindings") or {}
    assert bindings, "registry declares no skill_bindings"
    return bindings


# ---------------------------------------------------------------------------
# The classes themselves
# ---------------------------------------------------------------------------


def test_every_class_declares_all_four_properties() -> None:
    for name, spec in (_registry().get("classes") or {}).items():
        assert isinstance(spec, dict), f"class {name}: entry is not a mapping"
        for prop in _REQUIRED_PROPERTIES:
            assert prop in spec, f"class {name}: missing `{prop}`. All four are required."


def test_voice_and_format_carry_explicit_provenance_and_disposition() -> None:
    for name, spec in (_registry().get("classes") or {}).items():
        for prop, vocabulary in (("voice", _VOICE_PROVENANCE), ("format", _FORMAT_PROVENANCE)):
            block = spec[prop]
            provenance = block.get("provenance")
            values = provenance if isinstance(provenance, list) else [provenance]
            for value in values:
                assert value in vocabulary, (
                    f"class {name}.{prop}: provenance {value!r} is outside the declared vocabulary "
                    f"{sorted(vocabulary)}. A new provenance is an ADR change, not a config change."
                )
            assert block.get("disposition") in _DISPOSITIONS, (
                f"class {name}.{prop}: disposition must be one of {sorted(_DISPOSITIONS)}. "
                "An unauthored property needs a NAMED outcome — never an invented default "
                "(ADR 0037 tenet 3)."
            )


# ---------------------------------------------------------------------------
# The bindings
# ---------------------------------------------------------------------------


def test_every_skill_is_bound_exactly_once() -> None:
    bound = set(_bindings())
    dirs = _skill_dirs()
    assert not (dirs - bound), (
        f"skills with no output-class binding: {sorted(dirs - bound)}. "
        "A new skill must be classified in operator/contracts/output-classes.yaml — "
        "this is the gate that stops an unclassified output from shipping."
    )
    assert not (bound - dirs), f"registry binds skills that do not exist: {sorted(bound - dirs)}"


def test_every_bound_class_exists() -> None:
    classes = set(_registry().get("classes") or {})
    for skill, binding in _bindings().items():
        for artifact in binding.get("internal") or []:
            assert artifact.get("artifact"), f"{skill}: an internal entry has no artifact slug"
            assert artifact.get("seam"), f"{skill}: artifact {artifact.get('artifact')!r} names no seam"
            assert artifact["class"] in classes, (
                f"{skill}: artifact {artifact['artifact']!r} references unknown class {artifact['class']!r}"
            )


def test_outbound_is_an_explicit_assertion() -> None:
    for skill, binding in _bindings().items():
        assert binding.get("outbound") in _OUTBOUND, (
            f"{skill}: `outbound` must be exactly one of {sorted(_OUTBOUND)}. "
            "It is an assertion about whether this skill may address a human, not a description."
        )


def test_recipient_derived_classes_are_never_bound_by_a_skill() -> None:
    """A skill must not declare who will read its outbound artifact.

    The recipient classifier resolves that at the send site with better evidence
    than any authored guess, and the trust decision already keys on it. A skill
    that hard-codes its audience would let a stale guess outrank a live fact.
    """
    classes = _registry().get("classes") or {}
    derived = {n for n, s in classes.items() if s.get("derived_from_recipient")}
    for skill, binding in _bindings().items():
        for artifact in binding.get("internal") or []:
            if artifact["class"] in derived and artifact["class"] != "staff":
                raise AssertionError(
                    f"{skill}: internal artifact {artifact['artifact']!r} is bound to "
                    f"{artifact['class']!r}, which is resolved from the recipient at send time. "
                    "An internal artifact has no recipient; bind it to work_product, record, or staff."
                )


# ---------------------------------------------------------------------------
# Cross-checks against what the skill says about itself
# ---------------------------------------------------------------------------


def test_a_send_capable_skill_does_not_claim_it_never_sends() -> None:
    for skill, binding in _bindings().items():
        action_class = str(_smd_metadata(skill).get("action_class") or "")
        if "external_send" in action_class and binding.get("outbound") == "none":
            raise AssertionError(
                f"{skill}: action_class declares external_send but the binding says outbound: none. "
                "One of the two is wrong, and the disagreement is exactly what this check exists "
                "to surface — a skill that can send while the registry believes it cannot is a "
                "gate applied to the wrong set."
            )


def test_work_product_drafts_have_a_declared_destination() -> None:
    for skill, binding in _bindings().items():
        if _smd_metadata(skill).get("content_ceiling") != "work_product":
            continue
        internal = {a["class"] for a in binding.get("internal") or []}
        if "work_product" in internal or binding.get("work_product_outbound"):
            continue
        raise AssertionError(
            f"{skill}: content_ceiling is work_product but the binding places the draft nowhere. "
            "Declare an internal work_product artifact, or set work_product_outbound: true if the "
            "draft leaves by email (mediation-brief-drafter does; the other three drafters mail a "
            "citation-free pointer instead, because the citation gate would refuse the draft)."
        )


# ---------------------------------------------------------------------------
# The seat's copy of this list (ss-console#2546 follow-up)
# ---------------------------------------------------------------------------


def test_the_class_list_is_the_one_the_seat_refuses_against() -> None:
    """The overlay pins these same six in ``shared/output_classes.py``.

    WHY THE SEAT HAS A COPY AT ALL. Live on the pilot, 2026-08-22, four firm
    rules were recorded against classes that are not here: one on
    ``demand_letter`` and three on ``letter``, one of which was explicitly about
    "internal emails to our own staff" and so belonged to ``staff``. The broker
    validates that a slug is well-formed and the intake writes wherever the slug
    points, so each one produced a real file in a directory nothing reads, a
    real install, and a real "your rule is in effect" letter about a rule that
    can never bind to any output. Membership is a question about THIS file, so
    the seat carries the answer and refuses.

    WHY THE GUARD LIVES HERE rather than in the overlay's CI. The overlay has no
    ss-console checkout, so its own drift test can only run where the registry
    happens to be reachable. This side always has the file, and a class added or
    renamed here is exactly the change that must not ship without moving the
    overlay's copy. If this fails, update
    ``hermes-smd-overlay:shared/output_classes.py`` (the slug AND its plain-words
    meaning) in the same wave, then this list.
    """
    assert set(_registry()["classes"]) == {
        "staff",
        "work_product",
        "record",
        "outbound_client",
        "outbound_vendor",
        "outbound_external",
    }


def test_workspace_is_a_skill_binding_and_not_a_seventh_class() -> None:
    """THE PLAUSIBLE SEVENTH. ``workspace`` is a key under ``skill_bindings:``
    naming the workspace skill. Reading this file's keys without reading its
    structure produces a class that does not exist, which is the same error the
    model made with ``letter``, one level up. Pinned so that a future reader
    checking "is workspace a class" gets an answer instead of a guess."""
    registry = _registry()
    assert "workspace" not in registry["classes"]
    assert "workspace" in registry["skill_bindings"]
