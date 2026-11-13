"""The one seat reader (shared/seat_config.py) reads exactly what each
expression it replaced read.

Wave 2, B1 moved the slug reads and customer.yaml parses onto one module. Each
call names what a missing value does, so this file holds every named choice to
the literal expression it stands for, over every environment state the caller
table (operator/contracts/seat-config-callers.json) runs its sites in, and the
file reader to ``yaml.safe_load(Path.read_text())`` over every file state.
The per-site proof is the caller table itself; this is the reader's own.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

SOURCE = Path(__file__).resolve().parents[2] / "hermes-smd-overlay" / "shared" / "seat_config.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("seat_config_under_test", SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sc = _load()
VAR, ALT = "CUSTOMER_SLUG", "SMD_CUSTOMER_SLUG"

#: (CUSTOMER_SLUG, SMD_CUSTOMER_SLUG); None is unset.
STATES: dict[str, tuple[str | None, str | None]] = {
    "unset": (None, None),
    "empty": ("", None),
    "valid": ("seat-one", None),
    "whitespace": ("   ", None),
    "padded": ("  seat-one  ", None),
    "alt-only": (None, "seat-two"),
    "alt-empty": ("seat-one", ""),
    "both": ("seat-one", "seat-two"),
    "both-empty": ("", ""),
}

#: The expression each (on_missing, alternate) stands for, written as the sites wrote it.
EXPRESSIONS: dict[tuple[str, str], Callable[[], Any]] = {
    ("none", "none"): lambda: os.environ.get(VAR),
    ("empty", "none"): lambda: os.environ.get(VAR, ""),
    ("raise", "none"): lambda: os.environ[VAR],
    ("none", "first"): lambda: os.environ.get(ALT) or os.environ.get(VAR),
    ("none", "after"): lambda: os.environ.get(VAR) or os.environ.get(ALT),
    ("empty", "first"): lambda: os.environ.get(ALT) or os.environ.get(VAR, ""),
    ("raise", "first"): lambda: os.environ.get(ALT) or os.environ[VAR],
}


def _outcome(fn: Callable[[], Any]) -> tuple[str, Any]:
    try:
        return "return", fn()
    except Exception as exc:  # noqa: BLE001 - the raised type is what is compared
        return "raise", type(exc).__name__


@pytest.mark.parametrize("state", sorted(STATES))
@pytest.mark.parametrize("choice", sorted(EXPRESSIONS), ids=lambda c: "-".join(c))
def test_each_named_slug_read_is_the_expression_it_replaced(
    monkeypatch: pytest.MonkeyPatch, state: str, choice: tuple[str, str]
) -> None:
    primary, alternate = STATES[state]
    for name, value in ((VAR, primary), (ALT, alternate)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    on_missing, alt = choice
    assert _outcome(lambda: sc.slug(on_missing=on_missing, alternate=alt)) == _outcome(EXPRESSIONS[choice])


def test_on_missing_has_no_default_and_an_unknown_choice_is_refused() -> None:
    with pytest.raises(TypeError):
        sc.slug()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="on_missing"):
        sc.slug(on_missing="fallback")
    with pytest.raises(ValueError, match="alternate"):
        sc.slug(on_missing="none", alternate="sometimes")
    with pytest.raises(TypeError):
        sc.load("customer.yaml", encoding="utf-8")  # type: ignore[call-arg]


# ------------------------------------------------------------ the file reader


def _files(tmp_path: Path) -> dict[str, Path]:
    good = tmp_path / "good.yaml"
    good.write_text("customer_id: seat-one\nmemory: {enabled: true}\n", encoding="utf-8")
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [unclosed\n", encoding="utf-8")
    listy = tmp_path / "list.yaml"
    listy.write_text("- one\n- two\n", encoding="utf-8")
    undecodable = tmp_path / "latin1.yaml"
    undecodable.write_bytes(b"name: caf\xe9\n")
    directory = tmp_path / "dir.yaml"
    directory.mkdir()
    return {
        "good": good,
        "empty": empty,
        "bad": bad,
        "list": listy,
        "undecodable": undecodable,
        "directory": directory,
        "missing": tmp_path / "absent.yaml",
    }


@pytest.mark.parametrize("encoding", ["utf-8", None])
@pytest.mark.parametrize("name", ["good", "empty", "bad", "list", "undecodable", "directory", "missing"])
def test_load_raise_is_safe_load_of_read_text(tmp_path: Path, name: str, encoding: str | None) -> None:
    path = _files(tmp_path)[name]
    want = _outcome(lambda: yaml.safe_load(path.read_text(encoding=encoding)))
    assert _outcome(lambda: sc.load(path, encoding=encoding, on_missing="raise")) == want
    assert _outcome(lambda: sc.load(str(path), encoding=encoding, on_missing="raise")) == want


def test_load_names_what_an_absent_file_reads_as(tmp_path: Path) -> None:
    files = _files(tmp_path)
    assert sc.load(files["missing"], encoding="utf-8", on_missing="none") is None
    assert sc.load(files["missing"], encoding="utf-8", on_missing="empty") == {}
    # Only an ABSENT file: a directory still raises whatever on_missing says.
    with pytest.raises(IsADirectoryError):
        sc.load(files["directory"], encoding="utf-8", on_missing="empty")
    with pytest.raises(ValueError, match="on_missing"):
        sc.load(files["good"], encoding="utf-8", on_missing="ignore")


def test_parse_is_safe_load_for_text_and_streams(tmp_path: Path) -> None:
    files = _files(tmp_path)
    text = files["good"].read_text(encoding="utf-8")
    assert sc.parse(text) == yaml.safe_load(text)
    with open(files["good"], encoding="utf-8") as fh:
        assert sc.parse(fh) == {"customer_id": "seat-one", "memory": {"enabled": True}}
    with pytest.raises(yaml.YAMLError):
        sc.parse("a: [unclosed\n")
    # safe: a python tag is refused, as yaml.safe_load refuses it
    with pytest.raises(yaml.YAMLError):
        sc.parse("!!python/object/apply:os.getcwd []\n")
