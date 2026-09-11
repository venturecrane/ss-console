"""Voice-corpus tracer: turn the author's own writing into vault samples.

The sample-driven voice subsystem was built end-to-end but never wired: the
ingestion runner (``adapter.voice.pipeline.VoiceIngestionRunner``) is an
email-sent-folder watcher with no operational caller, so the per-customer R2
voice vault is never populated and the plugin stays INACTIVE. This module is
the tracer-bullet wiring: a one-shot path that turns a curated corpus of the
author's own messages into the exact structural-diff JSON the runtime reader
(``hermes-smd-voice`` ``R2VaultSampleReader``) consumes.

It deliberately reuses the REAL fidelity-critical primitive —
:func:`adapter.voice.extract_structural_diff` — and the REAL R2 key/format
contract. It does NOT drag in the email-watcher orchestration (cursor,
sent-folder, partner-authored filter); that machinery is irrelevant to a
curated one-shot corpus and belongs to the durable ingestion follow-up.

Privacy posture
---------------
``extract_structural_diff`` is content-free by construction: every field is a
count, a categorical enum (``greeting_style`` etc. are assigned
``_classify_*(body).value``), a numeric histogram, or the cohort tag. No raw
prose survives. :func:`assert_style_only` is the enforced invariant on that
guarantee — it fails the ingest if any string value in the emitted JSON falls
outside the closed style vocabulary, so a future change that let literal text
leak into a sample would break the build rather than ship the leak.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from adapter.voice import GreetingStyle, SignoffStyle, extract_structural_diff

# ---------------------------------------------------------------------------
# Leak invariant
# ---------------------------------------------------------------------------


class VoiceLeakError(Exception):
    """Raised when an emitted sample carries anything but style vocabulary.

    A content leak in a voice sample is a P0 — the whole point of the
    structural-diff format is that raw prose never reaches R2.
    """


def _style_vocabulary(cohort: str) -> frozenset[str]:
    """The closed set of string values a content-free diff may contain.

    Built from the enums at runtime so the allowlist tracks the schema
    automatically. ``recipient_cohort`` is the only free-form string the
    format carries, and it is a tag the caller controls (never derived
    from the body), so the specific cohort is allowed too.
    """
    return frozenset([g.value for g in GreetingStyle] + [s.value for s in SignoffStyle] + [cohort])


# Secret-shaped tokens that must never appear even inside an allowed field.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
]


def _iter_strings(value: object) -> Iterator[str]:
    """Yield every string anywhere in a JSON-ish structure."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str):
                yield k
            yield from _iter_strings(v)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def assert_style_only(diff_dict: dict, *, cohort: str) -> None:
    """Fail the ingest unless every string in ``diff_dict`` is style vocab.

    The tightest possible check given the content-free format: any string
    value outside the closed greeting/signoff vocabulary (plus the cohort
    tag and the JSON keys themselves) means raw content leaked into a
    field. Numeric fields are ignored. Also scans for secret-shaped tokens
    as defense in depth.

    Raises:
        VoiceLeakError: on any out-of-vocabulary string or secret pattern.
    """
    allowed = _style_vocabulary(cohort)
    known_keys = {
        "schema_version",
        "word_count",
        "sentence_count",
        "paragraph_count",
        "subject_word_count",
        "avg_sentence_length",
        "sentence_length_distribution",
        "greeting_style",
        "signoff_style",
        "opener_template",
        "closer_template",
        "punctuation_rhythm",
        "recipient_cohort",
        "lt_5",
        "lt_10",
        "lt_20",
        "lt_35",
        "gte_35",
        "period_per_100",
        "comma_per_100",
        "semicolon_per_100",
        "dash_per_100",
        "question_per_100",
        "exclamation_per_100",
    }
    for s in _iter_strings(diff_dict):
        for pat in _SECRET_PATTERNS:
            if pat.search(s):
                raise VoiceLeakError(f"secret-shaped token in sample: {pat.pattern!r}")
        if s in allowed or s in known_keys:
            continue
        raise VoiceLeakError(
            f"non-style string in sample: {s!r} (not in greeting/signoff vocab, "
            f"cohort {cohort!r}, or known JSON keys) — possible raw-content leak"
        )


# ---------------------------------------------------------------------------
# Sample construction (reuses the real differ)
# ---------------------------------------------------------------------------

VAULT_ROOT = "vaults/"


@dataclass
class BuiltSample:
    sample_id: str
    r2_key: str  # full bucket key, including the vaults/ root
    diff_bytes: bytes
    diff_dict: dict


def build_sample(text: str, *, slug: str, cohort: str = "unassigned") -> BuiltSample:
    """Run one corpus message through the real differ and the leak guard.

    The R2 key matches the runtime contract exactly:
    ``vaults/{slug}/voice/cohort/{cohort}/{sample_id}.json`` — the same
    shape ``pipeline._ingest_one`` writes and ``R2VaultSampleReader`` reads.
    """
    diff = extract_structural_diff(body_text=text, subject="", recipient_cohort=cohort)
    diff_dict = diff.as_dict()
    assert_style_only(diff_dict, cohort=cohort)  # fail-closed before any write
    sample_id = uuid.uuid4().hex
    r2_key = f"{VAULT_ROOT}{slug}/voice/cohort/{cohort}/{sample_id}.json"
    return BuiltSample(
        sample_id=sample_id,
        r2_key=r2_key,
        diff_bytes=diff.to_json_bytes(),
        diff_dict=diff_dict,
    )


# ---------------------------------------------------------------------------
# Corpus extraction (the author's role=user prose from CC transcripts)
# ---------------------------------------------------------------------------

# Markers of harness/tooling noise that is not the author's own writing.
_NOISE_MARKERS = (
    "<system-reminder>",
    "<local-command-stdout>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "[Request interrupted",
    "Caveat: The messages below",
    "stdout>",
)

# Second-person role/instruction openers. These are agent PROMPTS (often
# skill-authored, pasted as user turns), not the author's own voice — they
# read as formal instructions TO an agent and would teach the exact opposite
# of the author's terse first-person register. Matched case-insensitively
# against the start of the message.
_AGENT_PROMPT_OPENERS = (
    "you are ",
    "you're the ",
    "you're a ",
    "your task",
    "your job",
    "your role",
    "act as ",
    "you will be ",
    "output only",
    "return only",
    "respond only",
)


def _is_prose(text: str, *, min_words: int) -> bool:
    """Heuristic: is this the author's own prose, not tooling/code/noise?"""
    t = text.strip()
    if not t or any(m in t for m in _NOISE_MARKERS):
        return False
    if "```" in t:  # pasted code/fenced block
        return False
    if t.startswith("<") or t.startswith("{") or t.startswith("["):
        return False
    if t.startswith("# ") or t.startswith("## "):  # pasted markdown doc / skill definition
        return False
    if t.lower().startswith(_AGENT_PROMPT_OPENERS):  # agent role prompt, not the author
        return False
    if len(t.split()) < min_words:
        return False
    # Reject paste-heavy blobs: lots of lines that look like code/indentation.
    lines = [ln for ln in t.splitlines() if ln.strip()]
    if lines:
        codeish = sum(1 for ln in lines if ln.startswith(("    ", "\t")) or ln.rstrip().endswith((";", "{", "}", ")")))
        if codeish / len(lines) > 0.4:
            return False
    return True


def _text_from_content(content: object) -> str:
    """Pull the user-authored text from a transcript message's content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                txt = block.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
        return "\n".join(parts)
    return ""


def extract_user_messages(transcript_path: Path, *, min_words: int = 12) -> Iterator[str]:
    """Yield the author's own prose messages from one CC transcript .jsonl.

    Reads ``role == 'user'`` turns, takes only text blocks (never
    tool_result), and filters harness noise and pasted code. Defensive
    against malformed lines — a bad line is skipped, never fatal.
    """
    try:
        raw = transcript_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        message = obj.get("message") if isinstance(obj, dict) else None
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = _text_from_content(message.get("content"))
        if _is_prose(text, min_words=min_words):
            yield text.strip()


def extract_corpus(transcript_paths: Iterable[Path], *, min_words: int = 12, limit: int | None = None) -> list[dict]:
    """Collect curated corpus samples across transcripts.

    Returns a list of ``{"id", "source", "text"}`` dicts. De-duplicates
    on exact text so repeated boilerplate doesn't dominate the profile.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for path in transcript_paths:
        for text in extract_user_messages(path, min_words=min_words):
            key = text.strip()
            if key in seen:
                continue
            seen.add(key)
            out.append({"id": uuid.uuid4().hex, "source": path.name, "text": text})
            if limit is not None and len(out) >= limit:
                return out
    return out


# ---------------------------------------------------------------------------
# Cohort vocabulary (authored, not inferred) — shared by the fetch and ingest
# CLIs so the two cannot drift. Moved here 2026-08-10 (#2222): the ingest
# script had NO cohort gate, which is exactly how an unauthorized cohort
# directory reached a live vault through the one path with no check.
# ---------------------------------------------------------------------------

#: The vocabulary a seat accepts when it authors no ``voice_cohorts:`` block
#: (mirrors BASE_VOICE_COHORTS in src/lib/operator/customer-yaml/types.ts).
BASE_COHORTS = frozenset({"client", "opposing-counsel", "court", "internal"})


def load_cohort_vocabulary(customer_yaml: str | Path | None) -> frozenset[str]:
    """Read the seat's resolved cohort vocabulary.

    Mirrors ``resolveCohortVocabulary`` (sections-voice.ts): an authored
    ``voice_cohorts.cohorts`` list REPLACES the base vocabulary rather than
    extending it, so a seat that authors the block must list every cohort it
    intends to use. Absence means the base set.

    Parsing is deliberately narrow — the flat slug list only. The console-side
    validator owns the real schema; this is a pre-flight guard so a typo fails
    before a fetch or an ingest, not a competing validator.
    """
    if not customer_yaml:
        return BASE_COHORTS
    text = Path(customer_yaml).read_text(encoding="utf-8")
    authored: list[str] = []
    in_block = False
    in_list = False
    for line in text.splitlines():
        if re.match(r"^voice_cohorts:\s*$", line):
            in_block = True
            continue
        if not in_block:
            continue
        if line.strip() and not line.startswith((" ", "\t")):
            break  # dedented to the next top-level key
        if re.match(r"^\s+cohorts:\s*$", line):
            in_list = True
            continue
        if in_list:
            m = re.match(r"^\s*-\s*['\"]?([a-z0-9][a-z0-9-]{0,31})['\"]?\s*(?:#.*)?$", line)
            if m:
                authored.append(m.group(1))
            elif line.strip() and not line.lstrip().startswith("#"):
                in_list = False  # a sibling key (min_samples_per_cohort, etc.)
    return frozenset(authored) if authored else BASE_COHORTS
