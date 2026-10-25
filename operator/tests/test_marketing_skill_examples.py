"""The marketing skills' worked examples are drafts the firm could actually send.

Each marketing skill ships a worked example per channel under
``references/example-*.md``. The model copies the shape of what it is shown,
so an example that breaks a rule teaches the rule's breach. Two layers:

* **Shape, always.** Every draft block opens with ``--- DRAFT <channel> ---``
  on its own line and closes with ``--- END DRAFT ---``; the channel is one the
  outbound advertising gate knows; no block holds an em dash, an exclamation
  mark, or (outside a review reply, which never names the firm) a draft
  without the firm's name and office city. Every draft channel has at least
  one worked example, so a skill cannot lose its example silently.
* **The gate itself, when it is in the tree.** Each block, and each cover-only
  example (the slate, the analytics note), runs through
  ``operator/templates/drafting/advertising_gate_check.py`` with the synthetic
  firm's registers (``fixtures/marketing-registers.example-partners.yaml``)
  and must exit 0 (no BLOCK; FLAGs are questions for the reviewer, not
  refusals).

Run::

    cd operator && python3 -m pytest tests/test_marketing_skill_examples.py -q
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_OPERATOR = Path(__file__).resolve().parents[1]
_SKILLS = _OPERATOR / "skills"
_GATE = _OPERATOR / "templates" / "drafting" / "advertising_gate_check.py"
_REGISTERS = Path(__file__).resolve().parent / "fixtures" / "marketing-registers.example-partners.yaml"

MARKETING_SKILLS = (
    "marketing-topic-slate",
    "blog-post-draft",
    "social-post-draft",
    "youtube-episode-post",
    "business-profile-post",
    "review-reply-draft",
    "press-release-draft",
    "marketing-analytics-note",
    "marketing-request-router",
)
DRAFT_CHANNELS = ("blog", "social", "youtube", "gbp", "review-reply", "press")
FIRM = "Example & Partners LLP"
CITY = "Fresno"

_OPEN = re.compile(r"^--- DRAFT (?P<channel>[a-z-]+) ---$")
_CLOSE = "--- END DRAFT ---"
_FENCE = re.compile(r"^```[a-z]*\n(?P<body>.*?)^```$", re.M | re.S)

#: Cover-only examples: the whole fenced block is a note, checked as cover text.
COVER_EXAMPLES = {
    "marketing-topic-slate": "example-slate.md",
    "marketing-analytics-note": "example-note.md",
}


def _example_files() -> list[Path]:
    return sorted(p for s in MARKETING_SKILLS for p in (_SKILLS / s / "references").glob("example-*.md"))


def draft_blocks(text: str) -> list[tuple[str, str]]:
    """Every ``(channel, body)`` between the markers; raises on a malformed block."""
    out: list[tuple[str, str]] = []
    channel: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        opened = _OPEN.match(line)
        if opened:
            if channel is not None:
                raise AssertionError(f"nested draft block at {line!r}")
            channel, body = opened.group("channel"), []
        elif line == _CLOSE:
            if channel is None:
                raise AssertionError("END DRAFT with no open block")
            out.append((channel, "\n".join(body)))
            channel = None
        elif channel is not None:
            body.append(line)
    if channel is not None:
        raise AssertionError(f"unclosed {channel} block")
    return out


def _all_blocks() -> list[tuple[Path, str, str]]:
    return [(path, ch, body) for path in _example_files() for ch, body in draft_blocks(path.read_text())]


def _cover_examples() -> list[tuple[Path, str]]:
    out = []
    for skill, name in COVER_EXAMPLES.items():
        path = _SKILLS / skill / "references" / name
        for match in _FENCE.finditer(path.read_text()):
            out.append((path, match.group("body")))
    return out


def test_finds_the_examples() -> None:
    # A check that cannot fail measured nothing.
    assert len(_example_files()) >= 8
    assert len(_all_blocks()) >= 9
    assert len(_cover_examples()) >= 3


def test_every_draft_channel_has_a_worked_example() -> None:
    seen = {ch for _, ch, _ in _all_blocks()}
    assert set(DRAFT_CHANNELS) <= seen, f"no worked example for {sorted(set(DRAFT_CHANNELS) - seen)}"


def test_blocks_use_only_channels_the_gate_knows() -> None:
    bad = [(p.parent.parent.name, ch) for p, ch, _ in _all_blocks() if ch not in DRAFT_CHANNELS]
    assert bad == []


def test_no_block_carries_an_em_dash_or_exclamation_mark() -> None:
    bad = [(p.parent.parent.name, ch) for p, ch, body in _all_blocks() if "—" in body or "!" in body]
    assert bad == []


def test_every_public_draft_names_the_firm_and_its_city() -> None:
    bad = [
        (p.parent.parent.name, ch)
        for p, ch, body in _all_blocks()
        if ch != "review-reply" and not (FIRM in body and CITY in body)
    ]
    assert bad == []


def test_the_parser_refuses_a_malformed_block() -> None:
    with pytest.raises(AssertionError):
        draft_blocks("--- DRAFT blog ---\ntext\n")
    with pytest.raises(AssertionError):
        draft_blocks("text\n--- END DRAFT ---\n")


def _gate(tmp_path: Path, text: str, channel: str) -> subprocess.CompletedProcess:
    item = tmp_path / "item.txt"
    item.write_text(text, encoding="utf-8")
    return subprocess.run(  # noqa: S603 - the repo's own checker, fixed argv
        [sys.executable, str(_GATE), "--item", str(item), "--channel", channel, "--registers", str(_REGISTERS)],
        capture_output=True,
        text=True,
        timeout=60,
    )


_needs_gate = pytest.mark.skipif(not _GATE.is_file(), reason="advertising gate not in this tree yet")


@_needs_gate
@pytest.mark.parametrize(
    "block",
    _all_blocks(),
    ids=[f"{p.parent.parent.name}-{ch}-{i}" for i, (p, ch, _) in enumerate(_all_blocks())],
)
def test_each_worked_draft_passes_the_gate(tmp_path: Path, block: tuple[Path, str, str]) -> None:
    path, channel, body = block
    result = _gate(tmp_path, body, channel)
    assert result.returncode == 0, f"{path.relative_to(_SKILLS)} [{channel}]\n{result.stdout}\n{result.stderr}"


@_needs_gate
def test_cover_only_examples_pass_the_gate(tmp_path: Path) -> None:
    for path, body in _cover_examples():
        result = _gate(tmp_path, body, "note")
        assert result.returncode == 0, f"{path.relative_to(_SKILLS)}\n{result.stdout}\n{result.stderr}"


@_needs_gate
def test_the_gate_can_fail_here(tmp_path: Path) -> None:
    # The falsifier: the same harness refuses a guarantee.
    result = _gate(tmp_path, f"{FIRM} in {CITY} guarantees you will win your case.", "social")
    assert result.returncode == 1, result.stdout
