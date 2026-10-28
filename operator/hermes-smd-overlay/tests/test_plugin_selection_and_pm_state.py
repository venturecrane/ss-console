"""bootstrap.plugin_selection and bootstrap.pm_state (ss-console#3564 re-promotion).

The reverted #3567 booted smd-staging with a package-manager environment that
`hermes plugins enable` had committed under /opt/data/installs: every later
hermes process dropped the image venv's site-packages (pm/environments.py
activate_dependencies @ v0.22.5). These pin the two replacements: the overlay
is selected by writing config.yaml, and a recorded selection is purged before
the gateway exec and asserted absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from bootstrap import plugin_selection, pm_state

KEY = "hermes-smd-overlay"


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def test_apply_creates_a_missing_root_config_with_the_selection(tmp_path):
    cfg = tmp_path / "config.yaml"
    assert plugin_selection.main(["x", str(cfg)]) == 0
    doc = _read(cfg)
    assert doc["plugins"]["enabled"] == [KEY]
    assert doc["plugins"]["entries"][KEY] == {"allow_tool_override": False}
    assert doc["security"] == {"allow_lazy_installs": False}
    assert cfg.stat().st_mode & 0o777 == 0o600


def test_apply_merges_and_is_idempotent(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "curator: {enabled: false}\n"
        "plugins: {enabled: [other], disabled: [hermes-smd-overlay, x], load_timeout_seconds: 0,\n"
        "          entries: {hermes-smd-overlay: {allow_tool_override: true, note: kept}}}\n"
        "security: {redact_secrets: true, allow_lazy_installs: true}\n"
    )
    cfg.chmod(0o640)
    assert plugin_selection.apply(cfg) is True
    doc = _read(cfg)
    assert doc["curator"] == {"enabled": False}
    assert doc["plugins"]["enabled"] == ["other", KEY]
    assert doc["plugins"]["disabled"] == ["x"], "a stale disable vetoes the loader's selection"
    assert doc["plugins"]["load_timeout_seconds"] == 0
    assert doc["plugins"]["entries"][KEY] == {"allow_tool_override": False, "note": "kept"}
    assert doc["security"] == {"redact_secrets": True, "allow_lazy_installs": False}
    assert cfg.stat().st_mode & 0o777 == 0o640
    before = cfg.read_bytes()
    assert plugin_selection.apply(cfg) is False
    assert cfg.read_bytes() == before


def test_check_fails_on_each_missing_key(tmp_path):
    cfg = tmp_path / "config.yaml"
    plugin_selection.apply(cfg)
    assert plugin_selection.main(["x", "--check", str(cfg)]) == 0
    good = _read(cfg)
    mutations = (
        lambda d: d["plugins"].update(enabled=[]),
        lambda d: d["plugins"].update(disabled=[KEY]),
        lambda d: d["plugins"]["entries"][KEY].update(allow_tool_override=True),
        lambda d: d["security"].pop("allow_lazy_installs"),
    )
    for mutate in mutations:
        doc = json.loads(json.dumps(good))
        mutate(doc)
        cfg.write_text(yaml.safe_dump(doc))
        assert plugin_selection.main(["x", "--check", str(cfg)]) == 1
    assert plugin_selection.main(["x", "--check", str(tmp_path / "absent.yaml")]) == 1


def _install(root: Path, key: str, facts: object) -> Path:
    d = root / "installs" / key
    (d / "bootstrap").mkdir(parents=True)
    (d / "bootstrap" / "operator.json").write_text("{}")
    if facts is not None:
        (d / "facts.json").write_text(facts if isinstance(facts, str) else json.dumps(facts))
    return d


def test_purge_removes_only_a_recorded_selection_and_its_store(tmp_path, capsys):
    root = tmp_path
    selected = _install(
        root,
        "aaaaaaaaaaaaaaaa",
        {"packages": {"venv": {"environment": "/opt/data/installs/a/environments/g1"}}},
    )
    (selected / "environments" / "g1").mkdir(parents=True)
    clean = _install(root, "bbbbbbbbbbbbbbbb", {"packages": {"python": {"entry": "cpython-3.14"}}})
    bare = _install(root, "cccccccccccccccc", None)
    (root / "tools" / "cpython").mkdir(parents=True)
    assert pm_state.main(["x", "check", str(root)]) == 1
    assert pm_state.main(["x", "purge", str(root)]) == 0
    out = capsys.readouterr().out
    assert "aaaaaaaaaaaaaaaa" in out and "tools" in out
    assert not selected.exists() and not (root / "tools").exists()
    assert clean.exists() and bare.exists(), "state with no venv selection is not ours to delete"
    assert pm_state.main(["x", "check", str(root)]) == 0


def test_a_tools_only_volume_is_purged_and_then_passes_check(tmp_path, capsys):
    # A store with no selection (an interrupted boot, or a selection removed by
    # hand, as on smd-staging) is still harmful: cron runs pre_run scripts on its
    # Python. Leaving it for check to refuse would fail every boot closed, a crash
    # loop. FALSIFIER: keep tools unless a selection went with it, and check fails.
    (tmp_path / "tools" / "cpython-3.14" / "bin").mkdir(parents=True)
    (tmp_path / "installs" / "ffffffffffffffff" / "bootstrap").mkdir(parents=True)
    assert pm_state.main(["x", "check", str(tmp_path)]) == 1
    assert pm_state.main(["x", "purge", str(tmp_path)]) == 0
    assert "with no selection recorded" in capsys.readouterr().out
    assert not (tmp_path / "tools").exists()
    assert (tmp_path / "installs" / "ffffffffffffffff").exists(), (
        "no selection there: not ours to delete"
    )
    assert pm_state.main(["x", "check", str(tmp_path)]) == 0


def test_a_tools_symlink_is_unlinked_not_followed(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep").write_text("x")
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "tools").symlink_to(target)
    pm_state.purge(tmp_path / "root")
    assert not (tmp_path / "root" / "tools").is_symlink()
    assert (target / "keep").exists()
    assert pm_state.main(["x", "check", str(tmp_path / "root")]) == 0


def test_unreadable_facts_count_as_a_selection(tmp_path):
    # pm.environments._recorded_venv raises on an unreadable facts.json, so the
    # gateway could not boot past it either.
    d = _install(tmp_path, "dddddddddddddddd", "{not json")
    assert pm_state.recorded_selections(tmp_path) == [(d, "unreadable (JSONDecodeError)")]
    pm_state.purge(tmp_path)
    assert not d.exists()


def test_a_clean_or_absent_root_is_clean(tmp_path):
    assert pm_state.main(["x", "check", str(tmp_path)]) == 0
    _install(tmp_path, "eeeeeeeeeeeeeeee", {"packages": {"venv": {"environment": None}}})
    assert pm_state.main(["x", "check", str(tmp_path)]) == 0
    assert pm_state.purge(tmp_path) == []
