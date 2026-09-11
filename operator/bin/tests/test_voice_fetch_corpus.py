"""Tests for bin/voice-fetch-corpus.py — the read-in-place bridge.

Covers the four things that make this path safe and traceable:

* Ambiguity is REFUSED, never guessed — a name matching several matters or
  files raises with the candidates named, which is the same posture the
  Operator takes when it cannot match a matter cleanly.
* Cohorts are validated against the seat's authored vocabulary BEFORE any
  fetch, so a typo cannot mint an orphan vault directory no loader reads.
* The emitted JSONL is exactly what the (untouched) ingester consumes, split
  one file per cohort, and provenance ties every fingerprint-to-be back to
  the document it came from.
* The markdown adapter strips frontmatter and EXCLUDES unmapped audiences
  rather than defaulting them into the wrong cohort.

Run::

    cd operator && python -m pytest bin/tests/test_voice_fetch_corpus.py -v
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_OPERATOR = _HERE.parents[2]
sys.path.insert(0, str(_OPERATOR))

# The script is hyphenated (a CLI, not a module), so load it by path.
_spec = importlib.util.spec_from_file_location("voice_fetch_corpus", _OPERATOR / "bin" / "voice-fetch-corpus.py")
assert _spec and _spec.loader
vfc = importlib.util.module_from_spec(_spec)
# Register before exec: @dataclass resolves its class's module out of
# sys.modules, and a spec-loaded module that is not registered makes that
# lookup return None (AttributeError at import time on 3.12+).
sys.modules["voice_fetch_corpus"] = vfc
_spec.loader.exec_module(vfc)


# ---------------------------------------------------------------------------
# Refusal posture
# ---------------------------------------------------------------------------


def test_resolve_one_matches_by_id_exactly():
    cands = [{"id": "m-1", "name": "Nakashima v. Cornerstone"}, {"id": "m-2", "name": "Boyle"}]
    got = vfc.resolve_one(cands, "m-2", id_key="id", name_keys=("name",), kind="matter")
    assert got["id"] == "m-2"


def test_resolve_one_matches_by_case_insensitive_substring():
    cands = [{"id": "m-1", "name": "Nakashima v. Cornerstone Market Holdings, LLC"}]
    got = vfc.resolve_one(cands, "nakashima", id_key="id", name_keys=("name",), kind="matter")
    assert got["id"] == "m-1"


def test_ambiguous_name_refuses_and_names_candidates():
    cands = [
        {"id": "f-1", "name": "Client status letter June"},
        {"id": "f-2", "name": "Client status letter August"},
    ]
    with pytest.raises(vfc.ResolutionError) as exc:
        vfc.resolve_one(cands, "client status", id_key="id", name_keys=("name",), kind="file")
    msg = str(exc.value)
    assert "refusing to guess" in msg
    assert "June" in msg and "August" in msg  # candidates named, so the caller can be specific


def test_no_match_refuses():
    cands = [{"id": "m-1", "name": "Boyle"}]
    with pytest.raises(vfc.ResolutionError):
        vfc.resolve_one(cands, "nakashima", id_key="id", name_keys=("name",), kind="matter")


# ---------------------------------------------------------------------------
# Cohort vocabulary
# ---------------------------------------------------------------------------


def test_unauthored_cohort_is_rejected_before_any_fetch():
    entries = [vfc.ManifestEntry(matter="X", file="Y", cohort="adjustor")]  # typo
    with pytest.raises(ValueError) as exc:
        vfc.validate_cohorts(entries, vfc.BASE_COHORTS)
    assert "adjustor" in str(exc.value)


def test_authored_cohorts_replace_the_base_vocabulary(tmp_path: Path):
    """Mirrors resolveCohortVocabulary: an authored list REPLACES the base set."""
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(
        "customer_id: pilot\n"
        "voice_cohorts:\n"
        "  cohorts:\n"
        "    - client\n"
        "    - adjuster       # comment tolerated\n"
        "    - opposing-counsel\n"
        "  min_samples_per_cohort: 5\n"
        "memory:\n"
        "  d1_namespace: pilot\n",
        encoding="utf-8",
    )
    vocab = vfc.load_cohort_vocabulary(str(yaml_path))
    assert vocab == frozenset({"client", "adjuster", "opposing-counsel"})
    assert "court" not in vocab  # base member NOT authored -> not in the vocabulary
    vfc.validate_cohorts([vfc.ManifestEntry("m", "f", "adjuster")], vocab)  # no raise
    with pytest.raises(ValueError):
        vfc.validate_cohorts([vfc.ManifestEntry("m", "f", "court")], vocab)


def test_no_customer_yaml_falls_back_to_base_vocabulary():
    assert vfc.load_cohort_vocabulary(None) == vfc.BASE_COHORTS


# ---------------------------------------------------------------------------
# Fetch (mocked client — no live calls in CI)
# ---------------------------------------------------------------------------


_DOCX_TEXT = b"Dear Errol,\n\nThe demand went out May 12.\n\nYours,\nLuisa"


class _FakeClient:
    """Minimal stand-in exposing the two methods the bridge uses."""

    def __init__(self, matters, files, blob=_DOCX_TEXT):
        self._matters = matters
        self._files = files
        self._blob = blob
        self.downloaded: list[tuple[str, str]] = []

    def get(self, path, **params):
        if path == "/matters":
            return {"value": self._matters}
        return {"value": self._files}

    def download_file(self, matter_id, file_id):
        self.downloaded.append((matter_id, file_id))
        return ({"name": "status.txt", "fileExtension": "txt"}, self._blob)


def test_fetch_entries_resolves_downloads_and_extracts():
    client = _FakeClient(
        matters=[{"id": "m-1", "name": "Nakashima v. Cornerstone"}],
        files=[{"id": "f-9", "name": "Client status letter June 8"}],
    )
    docs = vfc.fetch_entries(
        [vfc.ManifestEntry(matter="Nakashima", file="Client status", cohort="client")],
        client,
    )
    assert len(docs) == 1
    d = docs[0]
    assert client.downloaded == [("m-1", "f-9")]
    assert "The demand went out May 12." in d.text
    assert d.cohort == "client"
    assert d.source == "smokeball:m-1/f-9"
    assert d.matter_name == "Nakashima v. Cornerstone"


def test_empty_extraction_refuses_rather_than_emitting_an_empty_sample():
    client = _FakeClient(
        matters=[{"id": "m-1", "name": "Boyle"}],
        files=[{"id": "f-1", "name": "scan"}],
        blob=b"   \n  ",
    )
    with pytest.raises(vfc.ResolutionError) as exc:
        vfc.fetch_entries([vfc.ManifestEntry("Boyle", "scan", "client")], client)
    assert "manual review" in str(exc.value)


# ---------------------------------------------------------------------------
# Markdown adapter
# ---------------------------------------------------------------------------


def _write_md(path: Path, audience: str, body: str) -> None:
    path.write_text(
        f"---\nsample_id: 04\ndoc_type: client_status_letter\n"
        f"audience: {audience}\nmatter: Nakashima\nsynthetic: true\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_strip_frontmatter_splits_fields_and_body(tmp_path: Path):
    f = tmp_path / "04.md"
    _write_md(f, "client (post-demand, pre-suit)", "Dear Errol,\n\nHere is where things stand.")
    fields, body = vfc.strip_frontmatter(f.read_text(encoding="utf-8"))
    assert fields["audience"].startswith("client")
    assert fields["sample_id"] == "04"
    assert body.startswith("Dear Errol,")
    assert "---" not in body


def test_audience_map_routes_by_prefix_and_excludes_unmapped(tmp_path: Path):
    _write_md(tmp_path / "04.md", "client (post-demand, pre-suit)", "Dear Errol, one.")
    _write_md(tmp_path / "01.md", "claims adjuster (commercial auto)", "Dear Adjuster, two.")
    _write_md(tmp_path / "08.md", "neutral (mediator)", "Statement of facts, three.")
    docs = vfc.load_markdown_dir(
        str(tmp_path),
        cohort="unassigned",
        audience_map={"client": "client", "claims adjuster": "adjuster"},
    )
    cohorts = sorted(d.cohort for d in docs)
    assert cohorts == ["adjuster", "client"]  # the mediator sample is EXCLUDED, not defaulted


def test_frontmatterless_file_still_yields_its_body(tmp_path: Path):
    f = tmp_path / "plain.md"
    f.write_text("Dear Errol,\n\nNo frontmatter here.", encoding="utf-8")
    docs = vfc.load_markdown_dir(str(f), cohort="client")
    assert len(docs) == 1 and docs[0].text.startswith("Dear Errol,")


# ---------------------------------------------------------------------------
# Output contract (what the ingester consumes) + provenance
# ---------------------------------------------------------------------------


def test_write_corpus_splits_per_cohort_in_ingester_format(tmp_path: Path):
    docs = [
        vfc.FetchedDoc("id-1", "client", "Dear Errol, one.", "Nakashima", "m-1", "a.docx", "f-1", "smokeball:m-1/f-1"),
        vfc.FetchedDoc("id-2", "client", "Dear Marguerite, two.", "Boyle", "m-2", "b.docx", "f-2", "smokeball:m-2/f-2"),
        vfc.FetchedDoc(
            "id-3", "adjuster", "Dear Adjuster, three.", "Duarte", "m-3", "c.docx", "f-3", "smokeball:m-3/f-3"
        ),
    ]
    written = vfc.write_corpus(docs, str(tmp_path / "corpus.jsonl"))
    assert set(written) == {"client", "adjuster"}

    rows = [json.loads(ln) for ln in Path(written["client"]).read_text().splitlines() if ln.strip()]
    assert len(rows) == 2
    # The ingester reads `text` only, but id/source must be present for review.
    assert set(rows[0]) == {"id", "source", "text"}
    assert rows[0]["text"] == "Dear Errol, one."


def test_provenance_ties_documents_to_corpus_ids(tmp_path: Path):
    docs = [
        vfc.FetchedDoc("id-1", "client", "Dear Errol.", "Nakashima", "m-1", "june.docx", "f-1", "smokeball:m-1/f-1")
    ]
    written = vfc.write_corpus(docs, str(tmp_path / "corpus.jsonl"))
    prov_path = tmp_path / "prov.json"
    vfc.write_provenance(docs, str(prov_path), written)
    prov = json.loads(prov_path.read_text())
    entry = prov["documents"][0]
    assert entry["corpus_id"] == "id-1"
    assert entry["file_name"] == "june.docx"
    assert entry["matter_id"] == "m-1"
    assert entry["chars"] == len("Dear Errol.")
    assert prov["corpus_files"]["client"].endswith("corpus.client.jsonl")


# ---------------------------------------------------------------------------
# End of the bridge: the emitted corpus survives the real ingester's guard
# ---------------------------------------------------------------------------


def test_emitted_corpus_feeds_the_real_ingester_leak_guard(tmp_path: Path):
    """The bridge's output must produce content-free samples through the REAL
    differ + leak invariant — the join this whole script exists to make."""
    from bin.lib.voice_corpus import build_sample

    docs = [
        vfc.FetchedDoc(
            "id-1",
            "client",
            "Dear Errol,\n\nThe demand went out May 12. Bayline has it.\n\nYours,\nLuisa",
            "Nakashima",
            "m-1",
            "june.docx",
            "f-1",
            "smokeball:m-1/f-1",
        )
    ]
    written = vfc.write_corpus(docs, str(tmp_path / "corpus.jsonl"))
    row = json.loads(Path(written["client"]).read_text().splitlines()[0])
    sample = build_sample(row["text"], slug="pilot-smokeball", cohort="client")
    assert sample.r2_key.startswith("vaults/pilot-smokeball/voice/cohort/client/")
    blob = sample.diff_bytes.decode()
    for leaked in ("Errol", "Bayline", "Luisa", "demand"):
        assert leaked not in blob
