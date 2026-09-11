#!/usr/bin/env python3
"""Fetch named matter documents into a voice corpus JSONL (the read-in-place bridge).

The missing link in the voice chain. The ingester
(``bin/voice-ingest-corpus.py``) turns a corpus JSONL into content-free
structural-diff samples, and the Smokeball connector can read a matter
document's text (``client.download_file`` + ``extract.extract_text``), but
nothing joined the two: there was no path from "the documents the firm points
at" to a corpus the ingester accepts. This script is that path.

The client commitment it serves (A&P letters 07/10): the firm never assembles
or hands over writing samples. They NAME documents on their matters in plain
English; we read them IN PLACE through the already-authorized connector. This
script mechanizes exactly that naming step and nothing more.

Two input modes
---------------

``--manifest FILE.yaml`` (the real path)
    A list of ``{matter, file, cohort}`` entries. ``matter`` and ``file`` are
    plain-English names (substring, case-insensitive) or ids. Each entry is
    resolved against the live account, downloaded, and text-extracted.

``--from-md DIR`` (the adapter)
    Strips YAML frontmatter from repo-side markdown corpora and emits their
    bodies. CI-testable and useful for fixtures; it is NOT a substitute for
    the fetch path when proving the chain, because extraction output (PDF and
    DOCX prose) differs materially from clean markdown.

Refusal posture
---------------

A name that matches more than one matter or more than one file is NOT
guessed. The script lists the candidates and exits non-zero, the same posture
the Operator itself takes when a matter cannot be matched cleanly.

Cohorts are validated against the seat's authored ``voice_cohorts`` vocabulary
(``--customer-yaml``) before anything is fetched, so a typo cannot mint an
orphan cohort directory in the vault that no profile loader will ever read.

Provenance
----------

The ingester keys every emitted sample by a fresh uuid and drops the corpus
``id``/``source`` fields, so a fingerprint in R2 cannot otherwise be tied back
to the document it came from. ``--provenance FILE.json`` records the mapping
(document -> corpus id, matter, file id, cohort, char count) locally so a
verification artifact can state exactly which documents produced which
samples.

Usage::

    cd operator
    # 1. fetch (writes one JSONL per cohort next to --out)
    infisical run --env=prod --path=/ss -- \\
      python bin/voice-fetch-corpus.py --manifest /tmp/exemplars.yaml \\
        --customer-yaml customers/pilot-smokeball/customer.yaml \\
        --out /tmp/corpus.jsonl --provenance /tmp/provenance.json
    # 2. ingest each cohort file (unmodified ingester)
    python bin/voice-ingest-corpus.py --corpus /tmp/corpus.client.jsonl \\
      --slug pilot-smokeball --cohort client --r2

Manifest format::

    entries:
      - matter: Nakashima            # substring of the matter name, or a matter id
        file: client status June     # substring of the file name, or a file id
        cohort: client
      - matter: Boyle
        file: deposition prep letter
        cohort: client
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

_HERE = Path(__file__).resolve()
_OPERATOR = _HERE.parents[1]
sys.path.insert(0, str(_OPERATOR))  # operator/ on sys.path
# The Smokeball connector is a standalone installable package; put its source
# root on the path so the REAL text extractor is reachable without installing
# it. `smokeball_connector.extract` imports only stdlib at module level (PDF and
# DOCX deps load lazily inside their branches), so this stays importable in a
# bare pytest venv and the CI suite exercises the same extractor the agent uses.
sys.path.insert(0, str(_OPERATOR / "connectors" / "smokeball"))


class ResolutionError(Exception):
    """A name matched zero or several candidates. Never resolved by guessing."""


# ---------------------------------------------------------------------------
# Cohort vocabulary (authored, not inferred)
# ---------------------------------------------------------------------------

# Shared with voice-ingest-corpus.py (moved to the lib 2026-08-10, #2222, so
# the fetch and ingest gates cannot drift). Re-exported here so callers and
# tests keep their `vfc.load_cohort_vocabulary` / `vfc.BASE_COHORTS` handles.
from bin.lib.voice_corpus import BASE_COHORTS, load_cohort_vocabulary  # noqa: E402,F401 - after the path shim; re-exported so vfc.load_cohort_vocabulary keeps working


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass
class ManifestEntry:
    matter: str
    file: str
    cohort: str


def load_manifest(path: str) -> list[ManifestEntry]:
    """Parse the manifest. YAML if PyYAML is importable, else JSON."""
    raw = Path(path).read_text(encoding="utf-8")
    data: Any
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw)
    except ImportError:  # pragma: no cover - environment-dependent
        data = json.loads(raw)
    entries = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: no `entries` list found")
    out: list[ManifestEntry] = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            raise ValueError(f"{path}: entry {i} is not a mapping")
        missing = [k for k in ("matter", "file", "cohort") if not e.get(k)]
        if missing:
            raise ValueError(f"{path}: entry {i} missing {', '.join(missing)}")
        out.append(ManifestEntry(matter=str(e["matter"]), file=str(e["file"]), cohort=str(e["cohort"])))
    return out


def validate_cohorts(entries: Iterable[ManifestEntry], vocabulary: frozenset[str]) -> None:
    """Fail before any fetch if a manifest names a cohort the seat has not authored."""
    unknown = sorted({e.cohort for e in entries if e.cohort not in vocabulary})
    if unknown:
        raise ValueError(
            f"cohort(s) {unknown} are not in the seat's authored vocabulary "
            f"{sorted(vocabulary)}. Author them in customer.yaml `voice_cohorts:` "
            "first — an unauthored cohort writes samples to a vault path no "
            "profile loader reads."
        )


# ---------------------------------------------------------------------------
# Name resolution (refuses ambiguity)
# ---------------------------------------------------------------------------


def _items(resp: Any) -> list[dict]:
    """Smokeball list responses nest under `value`/`items`/`results` by endpoint."""
    if isinstance(resp, list):
        return [r for r in resp if isinstance(r, dict)]
    if isinstance(resp, dict):
        for key in ("value", "items", "results", "data", "matters", "files"):
            v = resp.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
    return []


def _label(rec: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        v = rec.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def resolve_one(
    candidates: list[dict],
    needle: str,
    *,
    id_key: str,
    name_keys: tuple[str, ...],
    kind: str,
) -> dict:
    """Match exactly one candidate by id or case-insensitive name substring.

    Never guesses. Zero matches and several matches are both errors, and the
    several-matches error names the candidates so the caller can be specific.
    """
    for c in candidates:
        if str(c.get(id_key, "")).strip() == needle.strip():
            return c
    n = needle.strip().lower()
    hits = [c for c in candidates if n in _label(c, name_keys).lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ResolutionError(
            f"no {kind} matches {needle!r}. Available: {[_label(c, name_keys) for c in candidates][:12]}"
        )
    raise ResolutionError(
        f"{needle!r} matches {len(hits)} {kind}s — refusing to guess. "
        f"Candidates: {[_label(c, name_keys) for c in hits]}. "
        "Name it more specifically (or pass the id)."
    )


# ---------------------------------------------------------------------------
# Letter normalization (corpus hygiene before the differ sees the text)
# ---------------------------------------------------------------------------

# The differ classifies the greeting from the FIRST line and the signoff from
# the LAST THREE non-empty lines (adapter/voice/diff.py :: _classify_greeting,
# _classify_signoff) — correct for the email bodies it was built on. A law
# firm's exemplars are LETTERS: letterhead, address block, date, and RE: line
# sit above the salutation, and a contact block sits below the closer. Feed one
# in raw and both classifiers return `none`, so the profile loses the two
# fields the transform actually rewrites (verified on the rehearsal corpus
# 2026-07-30: all five client samples came back greeting_style=none,
# signoff_style=none).
#
# So the corpus step trims a letter to its voice-bearing body, the same way the
# differ internally strips quoted replies from an email. Conservative by
# construction: if no salutation or no closer is recognized, that end is left
# exactly as it was rather than guessed at.

_SALUTATION_RE = re.compile(r"^\**\s*(dear|hi|hello|hey|good (morning|afternoon|evening))\b", re.I)
_CLOSER_RE = re.compile(
    r"^\**\s*(yours|sincerely|best|thanks|thank you|regards|warm(ly| regards)|"
    r"cordially|respectfully|very truly yours)\b[\s,.]*\**\s*$",
    re.I,
)
# How far in to look. A letterhead + address block + date + RE: line runs well
# under 40 lines; scanning further would risk catching a "Dear" inside quoted
# body text.
_SALUTATION_SCAN_LINES = 40
_CLOSER_SCAN_LINES = 15
# Lines kept after the closer: the differ's own signoff window is the last
# three non-empty lines, so keeping the printed-name line preserves the
# NAMED/INITIAL fallback without dragging the contact block back in.
_LINES_AFTER_CLOSER = 1


def normalize_letter(text: str) -> str:
    """Trim a letter document to the prose between salutation and closer.

    Returns the text unchanged when neither marker is found — an unrecognized
    shape is left alone rather than truncated on a guess.
    """
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines[:_SALUTATION_SCAN_LINES]):
        if _SALUTATION_RE.match(line.strip()):
            start = i
            break

    end = len(lines)
    tail_start = max(start, len(lines) - _CLOSER_SCAN_LINES)
    for j in range(len(lines) - 1, tail_start - 1, -1):
        if _CLOSER_RE.match(lines[j].strip()):
            # Keep the closer plus the printed-name line that follows it.
            kept = 0
            k = j + 1
            while k < len(lines) and kept < _LINES_AFTER_CLOSER:
                if lines[k].strip():
                    kept += 1
                k += 1
            end = k
            break

    trimmed = "\n".join(lines[start:end]).strip()
    return trimmed or text.strip()


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


@dataclass
class FetchedDoc:
    corpus_id: str
    cohort: str
    text: str
    matter_name: str
    matter_id: str
    file_name: str
    file_id: str
    source: str = field(default="")


def fetch_entries(entries: list[ManifestEntry], client: Any) -> list[FetchedDoc]:
    """Resolve, download, and text-extract each manifest entry.

    ``client`` is a SmokeballClient (or a test double exposing ``get`` and
    ``download_file``). Extraction reuses the connector's own ``extract_text``
    so this path and the agent's ``read_document`` path cannot diverge.
    """
    from smokeball_connector.extract import extract_text  # lazy: connector on path

    out: list[FetchedDoc] = []
    for entry in entries:
        matters = _items(client.get("/matters", Search=entry.matter, Limit=100))
        matter = resolve_one(
            matters,
            entry.matter,
            id_key="id",
            name_keys=("name", "title", "caption", "matterName"),
            kind="matter",
        )
        matter_id = str(matter.get("id", ""))
        files = _items(client.get(f"/matters/{matter_id}/documents/files", Limit=500, Offset=0))
        f = resolve_one(
            files,
            entry.file,
            id_key="id",
            name_keys=("name", "fileName", "title"),
            kind="file",
        )
        file_id = str(f.get("id", ""))
        info, blob = client.download_file(matter_id, file_id)
        text = extract_text(
            blob,
            file_name=str(info.get("name", "")),
            file_extension=str(info.get("fileExtension", "")),
        )
        if not text.strip():
            raise ResolutionError(
                f"{entry.file!r} on {entry.matter!r} extracted to empty text — "
                "needs manual review rather than an empty voice sample"
            )
        out.append(
            FetchedDoc(
                corpus_id=uuid.uuid4().hex,
                cohort=entry.cohort,
                text=normalize_letter(text),
                matter_name=_label(matter, ("name", "title", "caption", "matterName")),
                matter_id=matter_id,
                file_name=_label(f, ("name", "fileName", "title")),
                file_id=file_id,
                source=f"smokeball:{matter_id}/{file_id}",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Markdown adapter (fixtures / repo corpora)
# ---------------------------------------------------------------------------

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def strip_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Split a frontmatter markdown file into (fields, body).

    Deliberately a flat scalar reader: the seed corpora carry `key: value`
    lines only, and a full YAML parse here would invite structure this path
    has no use for.
    """
    m = _FRONTMATTER.match(raw)
    if not m:
        return {}, raw.strip()
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip().strip("'\"")
    return fields, raw[m.end() :].strip()


def load_markdown_dir(path: str, *, cohort: str, audience_map: dict[str, str] | None = None) -> list[FetchedDoc]:
    """Emit corpus docs from a directory (or single file) of frontmatter markdown.

    ``audience_map`` maps a frontmatter ``audience`` value (or any prefix of
    it) to a cohort id; entries whose audience maps to nothing fall back to
    ``cohort``. Mapping is explicit by design — matching long audience phrases
    on substring luck is how a corpus silently lands in the wrong cohort.
    """
    p = Path(path)
    files = sorted(p.glob("*.md")) if p.is_dir() else [p]
    out: list[FetchedDoc] = []
    for f in files:
        fields, body = strip_frontmatter(f.read_text(encoding="utf-8"))
        if not body.strip():
            continue
        resolved = cohort
        audience = fields.get("audience", "")
        if audience_map:
            for prefix, target in audience_map.items():
                if audience.lower().startswith(prefix.lower()):
                    resolved = target
                    break
            else:
                continue  # unmapped audience: excluded on purpose, never defaulted
        out.append(
            FetchedDoc(
                corpus_id=uuid.uuid4().hex,
                cohort=resolved,
                text=normalize_letter(body),
                matter_name=fields.get("matter", ""),
                matter_id="",
                file_name=f.name,
                file_id=fields.get("sample_id", ""),
                source=f"file:{f.name}",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_corpus(docs: list[FetchedDoc], out_path: str) -> dict[str, str]:
    """Write one JSONL per cohort. Returns {cohort: path}.

    Per-cohort files because the ingester takes a single ``--cohort`` per run
    (its samples are keyed by cohort in the vault), so splitting here keeps
    that script untouched.
    """
    base = Path(out_path)
    stem = base.stem or "corpus"
    written: dict[str, str] = {}
    by_cohort: dict[str, list[FetchedDoc]] = {}
    for d in docs:
        by_cohort.setdefault(d.cohort, []).append(d)
    for cohort, group in by_cohort.items():
        dest = base.with_name(f"{stem}.{cohort}{base.suffix or '.jsonl'}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8") as fh:
            for d in group:
                fh.write(
                    json.dumps(
                        {"id": d.corpus_id, "source": d.source, "text": d.text},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        written[cohort] = str(dest)
    return written


def write_provenance(docs: list[FetchedDoc], path: str, corpus_files: dict[str, str]) -> None:
    """Record document -> corpus-id mapping so fingerprints stay traceable."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(
            {
                "corpus_files": corpus_files,
                "documents": [
                    {
                        "corpus_id": d.corpus_id,
                        "cohort": d.cohort,
                        "matter_name": d.matter_name,
                        "matter_id": d.matter_id,
                        "file_name": d.file_name,
                        "file_id": d.file_id,
                        "source": d.source,
                        "chars": len(d.text),
                    }
                    for d in docs
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fetch named matter documents into a voice corpus JSONL.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--manifest", help="YAML/JSON manifest of {matter, file, cohort}.")
    src.add_argument("--from-md", help="Directory (or file) of frontmatter markdown.")
    p.add_argument("--out", required=True, help="Base path; one JSONL per cohort.")
    p.add_argument("--provenance", help="Write the document -> corpus-id map here.")
    p.add_argument("--customer-yaml", help="Seat customer.yaml for cohort validation.")
    p.add_argument("--cohort", default="unassigned", help="Cohort for --from-md.")
    p.add_argument(
        "--audience-map",
        help="JSON {audience-prefix: cohort} for --from-md; unmapped are EXCLUDED.",
    )
    args = p.parse_args(argv)

    vocabulary = load_cohort_vocabulary(args.customer_yaml)

    try:
        if args.manifest:
            entries = load_manifest(args.manifest)
            validate_cohorts(entries, vocabulary)
            from smokeball_connector.client import build_client_from_env

            docs = fetch_entries(entries, build_client_from_env())
        else:
            amap = json.loads(args.audience_map) if args.audience_map else None
            docs = load_markdown_dir(args.from_md, cohort=args.cohort, audience_map=amap)
            validate_cohorts(
                [ManifestEntry(matter="", file="", cohort=d.cohort) for d in docs],
                vocabulary,
            )
    except (ResolutionError, ValueError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2

    if not docs:
        print("no documents produced (check the manifest or audience map)", file=sys.stderr)
        return 2

    written = write_corpus(docs, args.out)
    if args.provenance:
        write_provenance(docs, args.provenance, written)

    for cohort, path in sorted(written.items()):
        n = sum(1 for d in docs if d.cohort == cohort)
        floor = "" if n >= 5 else "  <-- BELOW the 5-sample profile floor; will not shape drafts"
        print(f"{cohort:20s} {n:3d} docs -> {path}{floor}")
    print(
        "\nNext: ingest each cohort file, then restart the Machine to adopt:\n"
        "  python bin/voice-ingest-corpus.py --corpus <file> --slug <seat> "
        "--cohort <cohort> --r2"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
