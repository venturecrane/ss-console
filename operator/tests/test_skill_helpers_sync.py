"""The shared pre_run helpers have one home, and every copy of it is byte-identical.

CANONICAL SOURCE is ``operator/templates/skill_helpers.py``. Each substantive
skill carries a vendored copy at ``skills/<skill>/skill_helpers.py`` because
the scheduler stages a skill directory alone; the copies are discovered by
GLOB, not by a hand-maintained tuple, so a seventh skill that adopts the
helpers is under the gate the moment its copy exists, and a copy that drifts
turns red.

The second half is the reason the module exists: before 2026-09-11 fifteen
helper bodies were duplicated byte-for-byte across six ``pre_run.py`` files
(code review 2026-09-10, Architecture 4). Any ``pre_run.py`` that defines a
function whose body equals one the canonical exports is a private copy that
has crept back, and fails here. The one-line wrappers the skills keep under
the old private names (``_source_id_of`` calling ``_H.source_id_of``) are not
copies: their bodies are a delegation, not the helper.

Run::

    cd operator && python3 -m pytest tests/test_skill_helpers_sync.py -q
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

_OPERATOR_ROOT = Path(__file__).resolve().parents[1]
_CANONICAL = _OPERATOR_ROOT / "templates" / "skill_helpers.py"
_SKILLS = _OPERATOR_ROOT / "skills"

#: The loader every adopting pre_run.py carries to find its vendored copy. It
#: is the bootstrap for the shared module, so it cannot itself live there.
_BOOTSTRAP = "_load_skill_helpers"


def _vendored_copies() -> list[Path]:
    return sorted(_SKILLS.glob("*/skill_helpers.py"))


def _adopting_pre_runs() -> list[Path]:
    return sorted(p.parent / "pre_run.py" for p in _vendored_copies() if (p.parent / "pre_run.py").is_file())


def _body_hash(src: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Hash of the function body with the name stripped, so a renamed copy still matches."""
    segment = ast.get_source_segment(src, node) or ""
    segment = segment.replace(node.name, "<name>", 1)
    return hashlib.sha256(segment.encode("utf-8")).hexdigest()


def _canonical_body_hashes() -> dict[str, str]:
    src = _CANONICAL.read_text(encoding="utf-8")
    tree = ast.parse(src)
    return {
        node.name: _body_hash(src, node)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_canonical_exists_and_exports_the_shared_set() -> None:
    assert _CANONICAL.is_file(), f"canonical skill_helpers.py missing at {_CANONICAL}"
    names = set(_canonical_body_hashes())
    for expected in (
        "emit_suppress",
        "extract_items",
        "find_skill_settings",
        "first_date",
        "handoff_values",
        "hold_active",
        "is_iso_day",
        "matter_id_of",
        "matter_number_of",
        "is_probe_subject",
        "next_scheduled_at",
        "parse_iso_date",
        "plan_counts_total",
        "plan_counts_capped",
        "pos_int",
        "pos_int_or_none",
        "source_id_of",
        "try_write_emitted_wake",
        "write_pre_run_handoff",
        "writer_factory",
        "warn_observability_failure",
    ):
        assert expected in names, f"canonical no longer exports {expected}"


def test_every_vendored_copy_is_byte_identical_to_the_canonical() -> None:
    canonical = _CANONICAL.read_bytes()
    copies = _vendored_copies()
    assert len(copies) >= 6, f"expected the six adopting skills to carry a copy; found {len(copies)}"
    drifted = [str(p.relative_to(_OPERATOR_ROOT)) for p in copies if p.read_bytes() != canonical]
    assert not drifted, (
        f"vendored skill_helpers.py drifted from templates/skill_helpers.py: {drifted}. "
        "Edit the canonical and copy it over every vendored file byte-for-byte."
    )


def test_every_adopting_pre_run_loads_the_shared_module_and_names_its_own_dir() -> None:
    for pre_run in _adopting_pre_runs():
        src = pre_run.read_text(encoding="utf-8")
        assert f"def {_BOOTSTRAP}(" in src, f"{pre_run} carries a vendored copy but never loads it"
        assert f'_SKILL_DIRNAME = "{pre_run.parent.name}"' in src, (
            f"{pre_run} must name its own directory so the seat-side lookup resolves the right copy"
        )


def test_no_pre_run_carries_a_private_copy_of_a_shared_helper() -> None:
    canonical_hashes = {h: name for name, h in _canonical_body_hashes().items()}
    offenders: list[str] = []
    for pre_run in sorted(_SKILLS.glob("*/pre_run.py")):
        src = pre_run.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            match = canonical_hashes.get(_body_hash(src, node))
            if match:
                offenders.append(
                    f"{pre_run.relative_to(_OPERATOR_ROOT)}::{node.name} is a copy of skill_helpers.{match}"
                )
    assert not offenders, "private copies of shared helpers crept back:\n" + "\n".join(offenders)


def test_the_gate_can_fail(tmp_path: Path) -> None:
    """A planted private copy is detected: the hash comparison is not vacuous."""
    src = _CANONICAL.read_text(encoding="utf-8")
    tree = ast.parse(src)
    pos_int = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "pos_int")
    planted = (ast.get_source_segment(src, pos_int) or "").replace("pos_int", "_pos_int", 1)
    planted_tree = ast.parse(planted)
    planted_node = planted_tree.body[0]
    assert isinstance(planted_node, ast.FunctionDef)
    assert _body_hash(planted, planted_node) == _canonical_body_hashes()["pos_int"]
