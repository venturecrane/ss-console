"""Record every input the existing tests feed the text checkers, into the text-rule goldens.

Run from ``operator/`` with the jobs venv (minutes; never in CI):

    python runners/jobs/tests/text_rule_harvest.py

It wraps each checker in ``text_rule_kit.TARGETS`` with a recorder, runs every
test file that names one of them (pytest, in this process), and writes
``goldens/text_rules/<module>.json``: per checker, each distinct call's stored
arguments (at most ``MAX_CALLS``, the first by sha256; none over
``MAX_ARG_CHARS``) and the checker's answer, computed afterwards by the
unwrapped checker. ``test_text_rule_goldens.py`` replays those inputs and fails
on any different answer. Re-run this only to widen the corpus; then read the
diff, which should add inputs and change no existing answer.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import goldens_kit as gk  # noqa: E402 - run as a script, so the tests dir goes on sys.path just above
import text_rule_kit as kit  # noqa: E402 - run as a script, so the tests dir goes on sys.path just above

OUT = gk.GOLDENS / "text_rules"


def _test_files() -> list[str]:
    names = sorted({fn for _m, fn in kit.TARGETS})
    rx = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b")
    me = {Path(__file__).name} | {p.name for p in HERE.glob("test_*_goldens.py")}
    return [
        str(p) for p in sorted(HERE.glob("test_*.py")) if p.name not in me and rx.search(p.read_text(encoding="utf-8"))
    ]


def main() -> int:
    import pytest

    seen: dict[tuple[str, str], dict[str, dict[str, Any]]] = {t: {} for t in kit.TARGETS}
    reals: dict[tuple[str, str], Callable[..., Any]] = {}

    def recorder(key: tuple[str, str], real: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(real)
        def wrapped(*a: Any, **kw: Any) -> Any:
            try:
                stored = {"args": kit.encode(list(a)), "kwargs": kit.encode(dict(kw))}
                text = json.dumps(stored, sort_keys=True)
                if len(text) <= kit.MAX_ARG_CHARS:
                    seen[key].setdefault(hashlib.sha256(text.encode()).hexdigest(), stored)
            except kit.Unstorable:
                pass
            return real(*a, **kw)

        return wrapped

    wrappers: dict[int, Callable[..., Any]] = {}
    for key in kit.TARGETS:
        mod = kit.module(key[0])
        real = getattr(mod, key[1])
        reals[key] = real
        w = recorder(key, real)
        wrappers[id(real)] = w
        setattr(mod, key[1], w)
    # A module that imported a checker by name before the wrap holds the
    # original: point it at the recorder too.
    for m in list(sys.modules.values()):
        if not getattr(m, "__name__", "").startswith("jobs"):
            continue
        for name, val in list(vars(m).items()):
            if callable(val) and id(val) in wrappers:
                setattr(m, name, wrappers[id(val)])
    files = _test_files()
    print(f"harvesting from {len(files)} test files", file=sys.stderr)
    pytest.main(
        ["-c", str(HERE.parent / "pyproject.toml"), "-q", "-p", "no:cacheprovider", "-p", "no:warnings", *files]
    )
    for key, real in reals.items():
        setattr(kit.module(key[0]), key[1], real)
    for m in list(sys.modules.values()):
        if getattr(m, "__name__", "").startswith("jobs"):
            for name, val in list(vars(m).items()):
                for k, real in reals.items():
                    if val is wrappers.get(id(real)):
                        setattr(m, name, real)
    by_module: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for (mod, fn), calls in seen.items():
        kept = [calls[h] for h in sorted(calls)[: kit.MAX_CALLS]]
        by_module.setdefault(mod, {})[fn] = [{**c, **kit.call(mod, fn, c)} for c in kept]
    OUT.mkdir(parents=True, exist_ok=True)
    for mod, doc in sorted(by_module.items()):
        (OUT / f"{mod}.json").write_text(gk.dump(doc), encoding="utf-8")
        print(f"{mod}: " + ", ".join(f"{fn} {len(v)}" for fn, v in sorted(doc.items())), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
