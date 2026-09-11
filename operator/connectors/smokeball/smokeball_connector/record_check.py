"""Run the drafting discipline's ten mechanical gates against a filled draft.

WHY THIS EXISTS (ss-console#2258). ``operator/templates/drafting/
drafting_gate_check.py`` is 1574 lines of zero-invention, quote-verbatim,
citation-integrity and privilege-leakage checks, and until now **nothing ran it
on the delivery path**. In the overlay it appears only as a presence probe for
the establishment compilers; the plugin that documents itself as the drafting
lane's exit explicitly disclaims the record checks. The discipline doc said the
checker ran "harness-side on the delivery path", every drafting skill read that
sentence verbatim, and a demand draft surfaced on the pilot 2026-08-12 believing
it had been gated downstream. It had not.

WHY IT ATTACHES HERE and not to ``smd_deliver_draft``. That tool is the lane's
declared exit for the SPEC gate, but ``demand-letter-drafter``'s SKILL.md never
calls it — it names ``create_memo``, ``agentmail`` and ``create_task``, and
``smd_deliver_draft`` appears only in that skill's ``references/voice.md``.
Hooking there would have reported the gate wired while card 18's own path stayed
ungated, and a test that invoked the hooked tool directly would have passed.
``render_docx_draft`` is the seam that produces the artifact the attorney opens.

FAIL-CLOSED ON EVERY NON-PASS, and the exit-2 row is why this is a table rather
than a sentence. The checker walks ``.md/.txt/.markdown/.text`` and raises
``GateUsageError`` -> **exit 2** when it finds no readable sources. A Smokeball
matter is PDFs and DOCX blobs, so "no readable sources" is the LIKELY runtime
case, not the exotic one. A rule of "refuse on FAIL" would read exit 2 as
not-a-FAIL and deliver an unchecked draft, which is the precise failure this
module exists to end.

    exit 0, no FAIL findings   -> render and file, carrying WARN/INFO forward
    exit 1 (FAIL findings)     -> REFUSE, returning the checker's own findings
    exit 2 (usage / no sources)-> REFUSE
    timeout                    -> REFUSE
    checker file absent        -> REFUSE, loudly (the missing_compilers shape)
    a source that would not extract -> REFUSE, naming it

That last row is COVERAGE, not presence, and partial extraction is worse than
none: gate 2a requires a quotation to appear verbatim in a source, so a quote
drawn from the one document that failed to extract is reported as fabrication.
That teaches the model to stop quoting the record, which is the same dynamic
``hermes-smd-trust/outbound.py`` already documents from the establishment corpus
("a gate that cannot be satisfied honestly teaches the model to satisfy it
dishonestly").

WARN and INFO ride forward into the caller's result rather than being dropped.
An attorney should see them, and a WARN that vanishes is worse than a noisy one.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

#: Where the checker lives on a provisioned seat. The establishment intake pins
#: the same path (``establish_intake/gates.py``), which is also the reason the
#: file is present at all: it is imported relative to ``spec_leak_check``.
CHECKER_PATH_ENV = "SMD_DRAFTING_GATE_CHECK"
_CHECKER_DEFAULT = Path("/opt/smd/operator/templates/drafting/drafting_gate_check.py")

#: The compilers run under a 180s ceiling in ``establish_intake/gates.py``; this
#: matches it. A hung checker must not wedge a drafting turn forever, and a
#: timeout REFUSES rather than passing.
DEFAULT_TIMEOUT_SECONDS = 180

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def checker_path() -> Path:
    """The checker to run. Env override exists for tests and for a dev checkout;
    production seats have the default path."""
    override = os.environ.get(CHECKER_PATH_ENV)
    return Path(override) if override else _CHECKER_DEFAULT


@dataclass(frozen=True)
class RecordCheckResult:
    """The verdict, plus everything the caller needs to explain it.

    ``passed`` is the ONLY field a caller should branch on. Every non-pass path
    sets it False, including the ones that are not FAIL findings — that
    conflation is deliberate and is the whole point of the disposition table.
    """

    passed: bool
    disposition: str
    refusals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    checked_sources: int = 0


def _safe(name: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", name).strip("._-")
    return (cleaned or "document")[:120]


def _materialize(
    root: Path,
    sources: list[tuple[str, str]],
    held_out_names: set[str],
    vision_names: set[str] | None = None,
) -> tuple[Path, Path | None]:
    """Write the extracted source texts to disk for the checker to walk.

    The checker takes DIRECTORIES and only reads ``.md/.txt/.markdown/.text``,
    so every source is written as ``.txt`` regardless of what it was in
    Smokeball — the extraction already happened upstream and what lands here is
    text either way.

    A source whose text is a machine transcription of a scan is written as
    ``NNN-VISION-<name>.txt``. The filename is the provenance an attorney sees
    on the artifact itself, so it survives being copied out of the report.
    """
    vision_names = vision_names or set()
    src_dir = root / "sources"
    src_dir.mkdir()
    held_dir: Path | None = None
    for index, (name, text) in enumerate(sources):
        target_dir = src_dir
        if name in held_out_names:
            if held_dir is None:
                held_dir = root / "held-out"
                held_dir.mkdir()
            target_dir = held_dir
        prefix = "VISION-" if name in vision_names else ""
        (target_dir / f"{index:03d}-{prefix}{_safe(name)}.txt").write_text(text, encoding="utf-8")
    return src_dir, held_dir


def run_record_check(
    draft_markdown: str,
    sources: list[tuple[str, str]],
    *,
    held_out_names: set[str] | None = None,
    unextractable: list[str] | None = None,
    vision_sources: list[tuple[str, str]] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> RecordCheckResult:
    """Check ``draft_markdown`` against the matter's record. Never raises.

    ``sources`` is ``(document_name, extracted_text)`` for every matter document
    that extracted from its own text layer. ``vision_sources`` is the same shape
    for documents whose text is a MACHINE TRANSCRIPTION of a scan (ss#2464):
    they count as record — a quotation must still trace to one — but the result
    WARNS and names every one of them, because nobody has read those pages. A
    warning rides forward into the caller's result (see the module docstring),
    which is the point: the attorney opening the draft is told which parts of
    the record behind it are a machine's reading of a photograph.

    ``unextractable`` names the documents that did not extract at all — a
    non-empty list REFUSES before the checker runs, because a partial record
    turns honest quotation into reported fabrication.
    """
    transcribed = list(vision_sources or ())
    result = _run_checked(
        draft_markdown,
        list(sources) + transcribed,
        held_out_names=held_out_names,
        unextractable=unextractable,
        vision_names={name for name, _text in transcribed},
        timeout=timeout,
    )
    if not transcribed:
        return result
    total = len(sources) + len(transcribed)
    warning = (
        f"{len(transcribed)} of {total} sources are machine transcriptions of scanned "
        "documents and have not been read by a human: "
        + ", ".join(sorted(name for name, _text in transcribed))
        + ". Cite them as transcriptions and verify any passage you quote against the "
        "scan itself; [illegible] marks a token the transcriber could not read."
    )
    return replace(result, warnings=[warning] + list(result.warnings))


def _run_checked(
    draft_markdown: str,
    sources: list[tuple[str, str]],
    *,
    held_out_names: set[str] | None = None,
    unextractable: list[str] | None = None,
    vision_names: set[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> RecordCheckResult:
    """The disposition table itself, over the combined source list."""
    if unextractable:
        return RecordCheckResult(
            passed=False,
            disposition="source_unextractable",
            refusals=[
                "Refused: the record could not be read in full, so this draft cannot be "
                "checked against it. Documents that would not extract: "
                + ", ".join(sorted(unextractable))
                + ". A partial record makes a correctly quoted passage look fabricated, "
                "so the check refuses rather than reporting that."
            ],
        )

    checker = checker_path()
    if not checker.is_file():
        return RecordCheckResult(
            passed=False,
            disposition="checker_absent",
            refusals=[
                f"Refused: the drafting gate checker is not present at {checker}. No draft "
                "surfaces ungated (drafting-discipline.md, variant C). This is a seat "
                "provisioning fault, not a defect in the draft."
            ],
        )

    if not sources:
        return RecordCheckResult(
            passed=False,
            disposition="no_sources",
            refusals=[
                "Refused: this matter has no readable documents, so there is nothing to "
                "check the draft's quotations and figures against. A draft checked "
                "against an empty record has not been checked."
            ],
        )

    with tempfile.TemporaryDirectory(prefix="record-check-") as tmp:
        root = Path(tmp)
        draft_path = root / "draft.md"
        draft_path.write_text(draft_markdown, encoding="utf-8")
        src_dir, held_dir = _materialize(root, sources, held_out_names or set(), vision_names)

        argv = [
            sys.executable,
            str(checker),
            "--draft",
            str(draft_path),
            "--sources",
            str(src_dir),
            "--json",
        ]
        if held_dir is not None:
            argv += ["--held-out", str(held_dir)]

        try:
            proc = subprocess.run(  # noqa: S603 — fixed argv, no shell, paths we wrote
                argv, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            return RecordCheckResult(
                passed=False,
                disposition="timeout",
                refusals=[
                    f"Refused: the drafting gate checker did not finish within {timeout}s. "
                    "A check that did not complete is not a check that passed."
                ],
                checked_sources=len(sources),
            )
        except Exception as exc:  # noqa: BLE001 — any launch fault refuses
            return RecordCheckResult(
                passed=False,
                disposition="checker_error",
                refusals=[f"Refused: could not run the drafting gate checker ({exc!r})."],
                checked_sources=len(sources),
            )

        # EXIT 2 IS THE LIKELY CASE, NOT THE EXOTIC ONE. See the module
        # docstring: the checker exits 2 on usage faults including "no readable
        # source files", and a rule of "refuse on FAIL" would read that as
        # not-a-FAIL and deliver an unchecked draft.
        if proc.returncode == 2:
            return RecordCheckResult(
                passed=False,
                disposition="checker_usage_error",
                refusals=[
                    "Refused: the drafting gate checker could not evaluate this draft "
                    f"({(proc.stderr or '').strip() or 'usage error'}). An indeterminate "
                    "check refuses."
                ],
                checked_sources=len(sources),
            )

        try:
            payload = json.loads(proc.stdout or "{}")
            findings = payload.get("findings") or []
        except (ValueError, AttributeError):
            return RecordCheckResult(
                passed=False,
                disposition="unparseable_report",
                refusals=[
                    "Refused: the drafting gate checker's report could not be parsed, so "
                    "its verdict is unknown. An unreadable verdict is not a pass."
                ],
                checked_sources=len(sources),
            )

        def _lines(severity: str) -> list[str]:
            return [
                f"[{f.get('gate')}] {f.get('message')}" + (f" — {f.get('detail')}" if f.get("detail") else "")
                for f in findings
                if f.get("severity") == severity
            ]

        fails = _lines("FAIL")
        if proc.returncode != 0 or fails:
            return RecordCheckResult(
                passed=False,
                disposition="fail_findings",
                refusals=fails
                or [
                    "Refused: the drafting gate checker exited non-zero without naming a "
                    f"finding (exit {proc.returncode}). Treat as a failure."
                ],
                warnings=_lines("WARN"),
                infos=_lines("INFO"),
                checked_sources=len(sources),
            )

        return RecordCheckResult(
            passed=True,
            disposition="pass",
            warnings=_lines("WARN"),
            infos=_lines("INFO"),
            checked_sources=len(sources),
        )
