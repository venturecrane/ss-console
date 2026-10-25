"""Check a marketing seat's registers file with the advertising gate's own loader.

Usage: check-marketing-registers.py <marketing-firm.yaml>

Exit 0 when the file loads and normalizes exactly as the gate on the seat will
load it (operator/templates/drafting/advertising_gate_registers.py, from this
checkout); exit 1, with the reason on stderr, when the gate would refuse it.
Run by lib/stage-marketing-inputs.sh before the registers are uploaded, so a
file the gate refuses on every draft never reaches a seat. Needs PyYAML for a
YAML file (the stage step runs it under ``uv run --with pyyaml``): the loader
returns "no YAML reader" without it, which fails here too, never passes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

GATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "drafting"


def _registers_module():
    spec = importlib.util.spec_from_file_location(
        "smd_check_marketing_registers", GATE_DIR / "advertising_gate_registers.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {GATE_DIR / 'advertising_gate_registers.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(path: str) -> str | None:
    """None when the gate loads ``path``; otherwise why it would not."""
    registers = _registers_module()
    raw, error = registers.load_registers(path)
    if error:
        return error
    try:
        registers.normalize_registers(raw)
    except registers.RegistersError as exc:
        return f"{path}: {exc}"
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check-marketing-registers.py <marketing-firm.yaml>", file=sys.stderr)
        return 2
    problem = check(argv[1])
    if problem:
        print(f"marketing registers refused: {problem}", file=sys.stderr)
        return 1
    print(f"marketing registers OK: {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
