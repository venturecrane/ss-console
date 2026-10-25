#!/usr/bin/env python3
"""California attorney-advertising gate for a law-firm marketing seat (ADR 0098).

CANONICAL SOURCE: ``operator/templates/drafting/advertising_gate_check.py``.
Stdlib only (PyYAML is used when it imports, for YAML registers), Python 3.10+.

What this gate decides
----------------------
Every public piece a marketing seat drafts for a California law firm (a blog
post, a social post, a YouTube description, a Business Profile post, an owner
reply to a Google review, a press release) is an advertisement under Bus. &
Prof. Code 6157(b) as rewritten by SB 37 (effective 2026-01-01). This checker
answers the machine-checkable part of the rule list in the 2026-09-28 research
memo (rule ids R-01..R-48, stable; renumber only by appending):

* patterns that are never allowed (guarantees, quick cash, notario, paid
  reviews, pre-settlement funding, the firm's own complaint blocklist);
* required disclosures that must appear in the item, in the item's language
  (results, testimonial, case costs, blog disclaimer, dramatization), taken
  verbatim from the firm's authored registers;
* provenance: a dollar figure, a case result, a record claim, a testimonial,
  an award, a certification, a fee percentage, a lawyer's name and a language
  claim must each trace to the firm's authored registers. An empty register
  means nothing of that kind may be claimed (fail closed);
* per-channel rules: the review-reply allowlist (R-27..R-36), Business Profile
  (R-37..R-39), blog (R-40..R-43), and the name-and-office requirement (R-14).

What it deliberately does NOT decide
------------------------------------
Legal accuracy, truth of the registers against reality, scope of client
consent, whether a reviewer was a client, whether a matter is concluded,
whether an award satisfies 6157.2(a)(6), the overall impression of a post, and
tone beyond the obvious words (memo section 3). Those are the human reviewer's.
A clean run means the item cleared the mechanical floor, never that it is
compliant. Skipped as not machine-checkable from the item text alone: R-33
(needs the review text), R-38 (profile field edits), R-47 (a system rule), R-48
(paid boosting is a channel decision, not item text).

Severities
----------
* ``BLOCK``: the draft does not reach the reviewer until fixed.
* ``FLAG``: it reaches the reviewer with the finding; the message is the
  question the reviewer must answer. R-28 (denying a reviewer was a client) and
  R-43 (name the reviewing lawyer) are hard holds that belong to the human; here
  they are FLAGs.

The draft-block convention (one email, several drafts)
------------------------------------------------------
A marketing seat emails drafts to the firm for approval, so one email can carry
a cover note plus several drafts. Each draft is wrapped, on lines of their own::

    --- DRAFT <channel> ---
    ...the draft...
    --- END DRAFT ---

:func:`check_document` checks each block with its channel's rules and the text
outside the blocks with the ``note`` rules only (the general rules that hold in
any text the seat writes), so the required-disclosure rules never misfire on a
cover note. An unknown channel, a nested block or an unclosed block is a BLOCK.
Text with no markers is one item on the caller's channel.

Exit behavior (CLI)
-------------------
  0  no BLOCK findings (FLAGs may be present)
  1  one or more BLOCK findings, including registers missing or unparseable
  2  usage or IO error on the item itself

``--json`` prints ``{"verdict": "pass"|"block", "findings": [{"rule", "severity",
"span", "message"}]}``.

Registers schema (the firm's authored provenance inputs, schema 1)
------------------------------------------------------------------
::

    schema: 1
    firm: {names: [..], office_cities: [..], site_urls: [..], phones: [..]}
    roster: [{name, title, lawyer: bool, languages: [..]}]
    disclosures: {results: {en, es}, testimonial: {en, es}, costs: {en, es},
                  blog: {en, es}, dramatization: {en, es}}  # each may be null
    results: [{id, text_match: [..], liability_basis, injury, consent: bool}]
    testimonials: [{text, source, date, consent: bool}]
    awards: [{name, not_membership_based: bool, no_fee: bool}]
    certifications: [{lawyer, field, body, expires}]
    fee_terms: {percentages: [..], no_fee_phrases_allowed: bool}
    blocklist: [..]
    review_reply: {offline_contact: {name, phone, email}}
    channels: {<channel>: {identity_in_template: bool}}

A null disclosure (or a null language inside one) means the firm has not
authored that sentence yet: any item that needs it BLOCKs. Record claims that
are not case results (years of experience, case counts) are authored as
``results`` rows with null ``liability_basis`` and ``injury``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable


def _sibling(name: str) -> Any:
    """Load a sibling module of the advertising gate by path, once per directory.

    The gate is run as a script (``python3 advertising_gate_check.py``) and
    loaded by path from the overlay, so its siblings cannot rely on sys.path.
    """
    here = Path(__file__).resolve().parent
    key = "_smd_adgate_" + re.sub(r"\W", "_", str(here)) + "_" + name
    module = sys.modules.get(key)
    if module is None:
        spec = importlib.util.spec_from_file_location(key, here / f"{name}.py")
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return module


_core = _sibling("advertising_gate_core")
_registers = _sibling("advertising_gate_registers")
_a = _sibling("advertising_rules_results")
_bc = _sibling("advertising_rules_identity")
_df = _sibling("advertising_rules_posts")
_g = _sibling("advertising_rules_reviews")
_hj = _sibling("advertising_rules_channels")

BLOCK = _core.BLOCK
FLAG = _core.FLAG
CHANNELS = _core.CHANNELS
AD_CHANNELS = _core.AD_CHANNELS
ALL_CHANNELS = _core.ALL_CHANNELS
_NOTE_AND_ADS = _core._NOTE_AND_ADS
Finding = _core.Finding
Item = _core.Item
detect_languages = _core.detect_languages
_clip = _core._clip
RegistersError = _registers.RegistersError
normalize_registers = _registers.normalize_registers
load_registers = _registers.load_registers
r01, r02, r03, r04_r06, r07_r08, r09, r10, r11 = (
    _a.r01,
    _a.r02,
    _a.r03,
    _a.r04_r06,
    _a.r07_r08,
    _a.r09,
    _a.r10,
    _a.r11,
)
r12, r13, r14, r15, r16, r17, r18 = _bc.r12, _bc.r13, _bc.r14, _bc.r15, _bc.r16, _bc.r17, _bc.r18
r19_r20, r21, r22, r23, r24, r25_r26 = _df.r19_r20, _df.r21, _df.r22, _df.r23, _df.r24, _df.r25_r26
r27, r28, r29, r30_r32, r31, r34_r35, r36 = _g.r27, _g.r28, _g.r29, _g.r30_r32, _g.r31, _g.r34_r35, _g.r36
r37, r39, r40, r41, r42, r43, r44, r46, blocklist = (
    _hj.r37,
    _hj.r39,
    _hj.r40,
    _hj.r41,
    _hj.r42,
    _hj.r43,
    _hj.r44,
    _hj.r46,
    _hj.blocklist,
)


# ---------------------------------------------------------------------------
# Applicability
# ---------------------------------------------------------------------------

_REVIEW = frozenset({"review-reply"})
_ADS_WITH_IDENTITY = AD_CHANNELS - _REVIEW

RULES: tuple[tuple[Callable[[Any], list], frozenset[str]], ...] = (
    (r01, _NOTE_AND_ADS),
    (r02, _NOTE_AND_ADS),
    (r03, AD_CHANNELS),
    (r04_r06, AD_CHANNELS),
    (r07_r08, AD_CHANNELS),
    (r09, AD_CHANNELS),
    (r10, AD_CHANNELS),
    (r11, _ADS_WITH_IDENTITY),
    (r12, AD_CHANNELS),
    (r13, AD_CHANNELS),
    (r14, _ADS_WITH_IDENTITY),
    (r15, AD_CHANNELS),
    (r16, AD_CHANNELS),
    (r17, AD_CHANNELS),
    (r18, AD_CHANNELS),
    (r19_r20, AD_CHANNELS),
    (r21, _REVIEW),
    (r22, _NOTE_AND_ADS),
    (r23, AD_CHANNELS),
    (r24, _NOTE_AND_ADS),
    (r25_r26, AD_CHANNELS),
    (r27, _REVIEW),
    (r28, _REVIEW),
    (r29, _REVIEW),
    (r30_r32, _REVIEW),
    (r31, _REVIEW),
    (r34_r35, _REVIEW),
    (r36, _REVIEW),
    (r37, frozenset({"gbp"})),
    (r39, _NOTE_AND_ADS),
    (r40, frozenset({"blog"})),
    (r41, frozenset({"blog"})),
    (r42, frozenset({"blog"})),
    (r43, frozenset({"blog"})),
    (r44, AD_CHANNELS),
    (r46, _NOTE_AND_ADS),
    (blocklist, _NOTE_AND_ADS),
)


def _dedupe(findings: Iterable[Any]) -> list:
    seen: set[tuple[str, str, str]] = set()
    out: list = []
    for f in findings:
        key = (f.rule, f.severity, f.span)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def check(text: str, channel: str, registers: Any, lang: str = "auto") -> list:
    """Every finding for one item on one channel. Never raises; fails closed."""
    if channel not in ALL_CHANNELS:
        return [Finding("USAGE", BLOCK, "", f"unknown channel {channel!r}")]
    try:
        regs = normalize_registers(registers)
    except RegistersError as exc:
        return [
            Finding(
                "REGISTERS",
                BLOCK,
                "",
                f"registers unavailable: {exc}. Nothing can be checked against the firm's "
                "authored records, so nothing passes.",
            )
        ]
    try:
        item = Item(text or "", channel, regs, detect_languages(text or "", lang))
        findings: list = []
        for rule, channels in RULES:
            if channel in channels:
                findings.extend(rule(item))
    except Exception as exc:  # noqa: BLE001 - a crashing checker must not read as a pass
        return [
            Finding(
                "CHECKER",
                BLOCK,
                "",
                f"the checker raised while evaluating this item ({exc.__class__.__name__}); treat it as not checked.",
            )
        ]
    return _dedupe(findings)


# ---------------------------------------------------------------------------
# Draft blocks
# ---------------------------------------------------------------------------

_OPEN = re.compile(r"^[ \t]*-{3}[ \t]*DRAFT[ \t]+(?P<ch>[A-Za-z-]+)[ \t]*-{3}[ \t]*$")
_CLOSE = re.compile(r"^[ \t]*-{3}[ \t]*END DRAFT[ \t]*-{3}[ \t]*$")


def split_drafts(text: str) -> tuple[str, list[tuple[str, str]], list]:
    """``(cover text, [(channel, draft)], structural findings)``."""
    cover: list[str] = []
    drafts: list[tuple[str, str]] = []
    problems: list = []
    current: list[str] | None = None
    channel = ""
    for line in (text or "").splitlines():
        opened = _OPEN.match(line)
        if opened:
            if current is not None:
                problems.append(Finding("MARKERS", BLOCK, _clip(line), "A draft block opens inside another one."))
                drafts.append((channel, "\n".join(current)))
            channel = opened.group("ch").lower()
            current = []
            if channel not in ALL_CHANNELS or channel == "note":
                problems.append(
                    Finding(
                        "MARKERS",
                        BLOCK,
                        _clip(line),
                        f"A draft block names an unknown channel {channel!r}; use one of "
                        f"{', '.join(c for c in CHANNELS if c != 'note')}.",
                    )
                )
            continue
        if _CLOSE.match(line):
            if current is None:
                problems.append(Finding("MARKERS", BLOCK, _clip(line), "A draft block closes but never opened."))
            else:
                drafts.append((channel, "\n".join(current)))
                current = None
            continue
        (cover if current is None else current).append(line)
    if current is not None:
        problems.append(
            Finding(
                "MARKERS",
                BLOCK,
                _clip(f"--- DRAFT {channel} ---"),
                "A draft block is never closed with '--- END DRAFT ---'.",
            )
        )
        drafts.append((channel, "\n".join(current)))
    return "\n".join(cover), drafts, problems


def check_document(text: str, channel: str, registers: Any, lang: str = "auto") -> list:
    """Check ``text``, honoring the draft-block convention (module docstring)."""
    cover, drafts, problems = split_drafts(text)
    if not drafts and not problems:
        return check(text, channel, registers, lang)
    findings = list(problems)
    if cover.strip():
        findings.extend(check(cover, "note", registers, lang))
    for i, (ch, body) in enumerate(drafts, 1):
        if ch not in ALL_CHANNELS or ch == "note":
            continue
        for f in check(body, ch, registers, lang):
            findings.append(f._replace(message=f"In the {ch} draft (block {i}): {f.message}"))
    return _dedupe(findings)


def verdict(findings: Iterable[Any]) -> str:
    return "block" if any(f.severity == BLOCK for f in findings) else "pass"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="advertising_gate_check.py",
        description=(
            "California attorney-advertising gate. Exit 0 no BLOCK, 1 BLOCK (including "
            "unavailable registers), 2 usage or IO error."
        ),
    )
    parser.add_argument("--item", required=True, help="path to the item text")
    parser.add_argument("--channel", required=True, choices=CHANNELS)
    parser.add_argument("--registers", required=True, help="the firm's registers, YAML or JSON")
    parser.add_argument("--lang", default="auto", choices=("auto", "en", "es"))
    parser.add_argument("--json", action="store_true", help="emit machine JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    try:
        text = Path(args.item).expanduser().read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - an unreadable item is a usage error, exit 2
        sys.stderr.write(f"[advertising-gate] cannot read item {args.item}: {exc}\n")
        return 2
    registers, error = load_registers(args.registers)
    if error:
        findings = [
            Finding(
                "REGISTERS",
                BLOCK,
                "",
                f"registers unavailable: {error}. Nothing can be checked against the firm's "
                "authored records, so nothing passes.",
            )
        ]
    else:
        findings = check_document(text, args.channel, registers, args.lang)
    result = verdict(findings)
    if args.json:
        print(
            json.dumps(
                {"verdict": result, "findings": [f._asdict() for f in findings]},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"advertising gate: {result} ({len(findings)} finding(s)) on {args.channel}")
        for f in findings:
            print(f"  [{f.severity}] {f.rule}: {f.message}")
            if f.span:
                print(f"      > {f.span}")
    return 1 if result == "block" else 0


if __name__ == "__main__":
    sys.exit(main())
