"""skills-manifest.py and skill_needs: the skills release manifest CI publishes.

What a skill NEEDS from its image is extracted only from contexts where a name is
a reference: a false need refuses a good release, a missed one weakens the gate.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

from bin.lib import skill_needs

OPERATOR = Path(__file__).resolve().parents[2]
REPO = OPERATOR.parent
SCRIPT = OPERATOR / "bin/skills-manifest.py"

ACTION_CLASSES_SRC = """
import enum
class ActionClass(str, enum.Enum):
    READ = "read"
    INTERNAL_WRITE = "internal_write"
    EXTERNAL_SEND = "external_send"
BANNED_TOOLS: frozenset[str] = frozenset({"mcp_agentmail_send_message"})
_RAW_TOOL_ACTION_CLASS_MAP: dict = {
    "email_search": ActionClass.READ,
    "mcp_smokeball_create_memo": ActionClass.INTERNAL_WRITE,
}
"""


def _script():
    spec = importlib.util.spec_from_file_location("skills_manifest", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _skill(root: Path) -> Path:
    d = root / "skills" / "demo-skill"
    (d / "references").mkdir(parents=True)
    (d / "tests").mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: demo-skill\nmetadata:\n  smd:\n    action_class: read + internal_write + one reply # prose\n---\n"
        "Call `email_search`, then `mcp_smokeball_create_memo`. Never `mcp_agentmail_send_message`.\n"
        "Submit with `note_job_status`. To execute the plan, check health.\n"
        "Run `python3 operator/templates/drafting/drafting_gate_check.py` and read /opt/data/x and /app/skills/y.\n"
    )
    (d / "references" / "r.md").write_text("Uses /opt/connectors/smokeball/.venv/bin/python.\n")
    (d / "pre_run.py").write_text('BROKER = {"action": "medchron_job_status"}\nprint("execute")\n')
    (d / "tests" / "test_x.py").write_text('X = "/app/never/a/need"\nY = "demand_job_submit"\n')
    return d


def test_needs_are_references_not_prose(tmp_path: Path) -> None:
    d = _skill(tmp_path)
    tools, classes = skill_needs.tool_universe_from_source(ACTION_CLASSES_SRC)
    verbs = {"note_job_status", "medchron_job_status", "demand_job_submit", "execute", "health"}
    needs = skill_needs.needs_for(d, tools, verbs, classes)
    assert needs["tools"] == ["email_search", "mcp_smokeball_create_memo"]  # banned tool is no need
    assert needs["verbs"] == ["medchron_job_status", "note_job_status"]  # prose "execute"/"health" are not
    assert needs["action_classes"] == ["internal_write", "read"]
    assert needs["paths"] == [
        "/app/operator/templates/drafting/drafting_gate_check.py",
        "/opt/connectors/smokeball/.venv/bin/python",
    ]


def test_tool_universe_excludes_banned_tools() -> None:
    tools, classes = skill_needs.tool_universe_from_source(ACTION_CLASSES_SRC)
    assert tools == {"email_search", "mcp_smokeball_create_memo"}
    assert classes == {"read", "internal_write", "external_send"}


def test_manifest_shape_objects_and_pins(tmp_path: Path) -> None:
    d = _skill(tmp_path)
    (d / "pre_run.py").chmod(0o755)
    (d / "__pycache__").mkdir()
    (d / "__pycache__" / "x.pyc").write_bytes(b"no")
    ac = tmp_path / "ac.py"
    ac.write_text(ACTION_CLASSES_SRC)
    retired = tmp_path / "retired.txt"
    retired.write_text("# header\nold-skill\n\n")
    out = tmp_path / "m.json"
    objects = tmp_path / "objects"
    objects.mkdir()
    rc = _script().main([
        "--skills", str(tmp_path / "skills"), "--version", "202610191200-abcdef12", "--source-sha", "c" * 40,
        "--retired", str(retired), "--verbs", str(OPERATOR / "workspace_broker/verbs.py"), "--action-classes", str(ac),
        "--out", str(out), "--objects-dir", str(objects),
    ])  # fmt: skip
    assert rc == 0
    m = json.loads(out.read_text())
    assert m["version"] == "202610191200-abcdef12" and m["retired"] == ["old-skill"]
    assert "demo-skill/__pycache__/x.pyc" not in m["files"]
    assert m["files"]["demo-skill/pre_run.py"]["mode"] == "0755"
    assert "mode" not in m["files"]["demo-skill/SKILL.md"]
    for entry in m["files"].values():
        assert (objects / entry["sha256"]).is_file()
    # the pin is the overlay boot hash over EVERY file (the __pycache__ too, as the overlay hashes the dir)
    digest = hashlib.sha256()
    for path in sorted(d.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(d).as_posix().encode() + b"\x00" + path.read_bytes() + b"\x00")
    assert m["pins"]["demo-skill"] == digest.hexdigest()[:6]


def test_bad_version_refused(tmp_path: Path) -> None:
    rc = _script().main(
        [
            "--skills",
            "x",
            "--version",
            "latest",
            "--source-sha",
            "c" * 40,
            "--retired",
            "r",
            "--verbs",
            "v",
            "--action-classes",
            "a",
            "--out",
            "o",
        ]
    )
    assert rc == 2


def test_runtime_read_prefixes_match_the_shipped_paths_guard() -> None:
    """The extractor's runtime-read set IS tests/shipped-runtime-paths.test.ts's."""
    src = (REPO / "tests/shipped-runtime-paths.test.ts").read_text()
    entries = re.findall(r"prefix:\s*'([^']+)',\s*role:\s*'([a-z-]+)'", src)
    assert entries, "could not read CITED_TREE_POLICY"
    runtime = sorted(p for p, role in entries if role == "runtime-read")
    assert runtime == sorted(skill_needs.RUNTIME_READ_PREFIXES)


def test_real_catalog_needs_are_satisfiable_by_a_tree_shaped_like_the_image() -> None:
    """Every path a shipped skill needs is one the Dockerfile COPYs (or a connector venv)."""
    dockerfile = (OPERATOR / "templates/Dockerfile").read_text()
    for d in sorted((OPERATOR / "skills").iterdir()):
        if not d.is_dir():
            continue
        for need in skill_needs.paths_needed(d):
            if need.startswith("/opt/connectors/"):
                assert "/opt/connectors" in dockerfile, need
            else:
                assert need.startswith("/app/operator/templates/drafting"), f"{d.name} needs {need}: classify it"


def test_contract_problems_mirror_the_applier_limits() -> None:
    mod = _script()
    good = {
        "files": {"a-skill/SKILL.md": {"sha256": "x", "size": 3}},
        "needs": {"a-skill": {"paths": ["/app/x"]}},
        "retired": [],
    }
    assert mod.contract_problems(good) == []
    bad = {
        "files": {"a-skill/.hidden": {"sha256": "x", "size": 3}, "Bad/SKILL.md": {"sha256": "x", "size": 3}},
        "needs": {"ghost": {"paths": ["relative/path"]}},
        "retired": ["a-skill"],
    }
    problems = " | ".join(mod.contract_problems(bad))
    assert ".hidden" in problems and "Bad/SKILL.md" in problems
    assert "ships no files" in problems and "not absolute" in problems and "also shipped" in problems


def test_real_catalog_passes_the_applier_contract(tmp_path: Path) -> None:
    rc = _script().main([
        "--skills", str(OPERATOR / "skills"), "--version", "202610191200-abcdef12", "--source-sha", "c" * 40,
        "--retired", str(OPERATOR / "templates/retired-skills.txt"), "--verbs", str(OPERATOR / "workspace_broker/verbs.py"),
        "--action-classes", str(_ac(tmp_path)), "--out", str(tmp_path / "m.json"),
    ])  # fmt: skip
    assert rc == 0


def _ac(tmp_path: Path) -> Path:
    ac = tmp_path / "ac.py"
    ac.write_text(ACTION_CLASSES_SRC)
    return ac
