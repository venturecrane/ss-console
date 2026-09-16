"""The daemon reads the runner's STDOUT as its JSON verdict (daemon.py `_report`),
so nothing a stage imports or runs may write there. Live 2026-09-16: `import
fitz` printed a one-line deprecation notice to stdout inside the exhibit build,
the verdict failed to parse, and a recoverable refusal was recorded `failed`,
a ledger state with no way back."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


def test_importing_every_stage_and_rendering_an_image_writes_nothing_to_stdout(tmp_path: Path) -> None:
    png = tmp_path / "p.png"
    script = textwrap.dedent(
        f"""
        import importlib, pkgutil, pymupdf
        import medchron.stages as stages
        for m in pkgutil.iter_modules(stages.__path__):
            importlib.import_module(f"medchron.stages.{{m.name}}")
        import medchron.driver, medchron.daemon, medchron.rehearsal, medchron.audit.page_text
        pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False).save({str(png)!r})
        from medchron.stages.exhibits import _reader
        r = _reader({str(png)!r}, ".png")
        assert len(r.pages) == 1
        """
    )
    proc = subprocess.run(  # noqa: S603 - argv is this interpreter and a literal script; no untrusted input
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    assert proc.stdout == "", f"stdout must stay the verdict's; got: {proc.stdout[:200]!r}"
