"""The update's added prior history never fails or is refused for its size.
Under the limit it is the one request it always was; over it, date pieces
each under the limit, every date in exactly one; an API size refusal splits
instead of failing; one date too large alone holds the job with a plain
reason; a job stopped mid-split never pays twice. Through the real stage, the
real doorway and a scripted client; every name, date and value is invented."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any, Callable

import anthropic
import httpx
import pytest

from jobs import llm
from jobs.medchron import config as config_mod, job as job_mod, prompts
from jobs.medchron.update import summary_pieces as SP
from jobs.stages import update_collect as UC
from jobs.stages.base import StageRefusal, StageRun
import update_testkit as K

DAYS = ["2025-03-04", "2025-04-11", "2025-05-20", "2025-06-02", "2025-08-19", "2025-10-07"]


def _entry(day: str, page: int, item: int) -> str:
    y, m, d = day.split("-")
    return (
        f"{m}/{d}/{y}\nExample Clinic | Office Visit\n\nOffice Visit\n"
        f"Patient seen for example item {item}, noted at length. (Exhibit 2 - p. {page})"
    )


def _entries(per_day: int = 2) -> list[str]:
    out, page = [], 1
    for day in DAYS:
        for _ in range(per_day):
            out.append(_entry(day, page, page))
            page += 1
    return out


class Usage:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 100, 20, 0, 0


def _reply(source: str) -> str:
    """A cited sentence per page the source cites, worded unlike its entry."""
    pages = re.findall(r"\(Exhibit 2 - p\. (\d+)\)", source)
    return "Prior Medical History\n" + " ".join(
        f"Before the incident, item {p} was on file. (Exhibit 2 - p. {p})" for p in pages
    )


def _too_long() -> anthropic.BadRequestError:
    req = httpx.Request("POST", "https://x")
    return anthropic.BadRequestError(
        "prompt is too long: 1100000 tokens > 1000000 maximum",
        response=httpx.Response(400, request=req),
        body={"error": {"type": "invalid_request_error"}},
    )


class Scripted:
    """Records every request; `refuse(params)` returns an exception to raise;
    `merge` replaces the merge call's reply; `pad` wraps every reply in blank lines."""

    def __init__(
        self,
        refuse: Callable[[dict], Exception | None] = lambda _p: None,
        merge: str | None = None,
        pad: bool = False,
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self.refuse, self.merge, self.pad = refuse, merge, pad
        self.messages = NS(create=self._create, stream=None, batches=None)

    def _create(self, **params: Any) -> Any:
        self.sent.append(params)
        exc = self.refuse(params)
        if exc is not None:
            raise exc
        text = _reply(params["messages"][0]["content"])
        if self.merge is not None and _is_merge(params):
            text = self.merge
        if self.pad:
            text = "\n\n" + text + "\n\n"
        return NS(content=[NS(type="text", text=text)], stop_reason="end_turn", usage=Usage())

    def pieces(self) -> list[dict[str, Any]]:
        return [p for p in self.sent if _source(p).startswith(SP.PIECE_NOTE)]

    def merges(self) -> list[dict[str, Any]]:
        return [p for p in self.sent if _is_merge(p)]


def _is_merge(params: dict) -> bool:
    return _source(params).startswith(SP.MERGE_NOTE)


def _sr(job_dir: Path, firm: Path, data_root: Path, client: Scripted, entries: list[str], log: list[str]) -> StageRun:
    job = job_mod.load(job_dir)
    sr = StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=log.append,
        seat_factory=lambda: None,  # type: ignore[arg-type, return-value]
        client_factory=lambda: client,
    )
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    ud.mkdir(parents=True, exist_ok=True)
    (ud / "base.md").write_text(K.base_md())
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": []}))
    (ud / "new_exhibits.final.json").write_text(json.dumps([{"exhibit": 2, "id": "n1", "title": "Exhibit 2 - X"}]))
    (ud / "new_entries.md").write_text("\n\n".join(entries) + "\n")
    (sr.slug_dir / "manifest.json").write_text(json.dumps([{"id": "n1", "created": "2026-10-01T15:04:00Z"}]))
    return sr


def _source(params: dict) -> str:
    return params["messages"][0]["content"]


def _days(params: dict) -> list[str]:
    return sorted({f"{y}-{m}-{d}" for m, d, y in re.findall(r"(?m)^(\d\d)/(\d\d)/(\d{4})$", _source(params))})


def _pmh(sr: StageRun) -> str:
    return (sr.slug_dir / "runs" / "alpha" / "update" / "pmh.md").read_text()


def test_under_the_limit_one_request_byte_identical_to_before(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """Fails if the common case's request changes in any byte."""
    log: list[str] = []
    client, entries = Scripted(), _entries()
    sr = _sr(job_dir, firm_config_path, data_root, client, entries, log)
    assert UC.run_summary(sr) == 0
    assert len(client.sent) == 1
    want, _ = llm.build_params_marked(
        "summarize",
        model=llm.model_for(sr.cfg, "judgment"),
        system=prompts.load("summarize-system", sr.cfg),
        max_tokens=16000,
        messages=[{"role": "user", "content": "\n\n".join(entries)}],
        effort="high",
        caching=sr.doorway.caching,
    )
    assert client.sent[0] == want
    ledger = (sr.slug_dir / "runs" / "alpha" / "usage-ledger.jsonl").read_text().splitlines()
    assert [json.loads(r)["custom_id"] for r in ledger] == ["update-summarize"]
    assert not (sr.slug_dir / "runs" / "alpha" / "update" / SP.CACHE).exists()


def _split(sr: StageRun, monkeypatch: pytest.MonkeyPatch, entries: list[str], n: int = 5) -> int:
    """Sets the limit to the estimate of the first `n` entries; returns it."""
    limit = SP.estimate(prompts.load("summarize-system", sr.cfg), entries[:n])
    monkeypatch.setattr(SP, "TOKENS_MAX", limit)
    return limit


def _piece_source(params: dict) -> str:
    return _source(params).removeprefix(SP.PIECE_NOTE + "\n\n")


def _cites(text: str) -> list[int]:
    return [int(p) for p in re.findall(r"\(Exhibit 2 - p\. (\d+)\)", text)]


def test_over_the_limit_date_pieces_then_one_merge(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if an oversize update is sent whole, a piece is over the limit,
    a date is cut or repeated, a piece is not told it is one range, or the
    delivered text is anything but ONE merge reply over every piece."""
    log: list[str] = []
    client, entries = Scripted(), _entries()
    sr = _sr(job_dir, firm_config_path, data_root, client, entries, log)
    limit = _split(sr, monkeypatch, entries)
    system = prompts.load("summarize-system", sr.cfg)
    assert UC.run_summary(sr) == 0
    pieces, merges = client.pieces(), client.merges()
    assert len(pieces) > 1 and len(merges) == 1 and len(client.sent) == len(pieces) + 1
    assert client.sent[-1] is merges[0]  # the merge comes after every piece
    assert all(SP.estimate(system, [_piece_source(p)]) <= limit for p in pieces)
    assert sorted(d for p in pieces for d in _days(p)) == DAYS  # every date, exactly once
    for word in ("absent", "first", "last", "latest", "began", "baseline"):
        assert word in SP.PIECE_NOTE
    merge_in = _source(merges[0])
    assert "Before the incident, item 1 was on file." in merge_in  # the pieces' answers, not the entries
    assert "Office Visit" not in merge_in
    same = {k: v for k, v in merges[0].items() if k != "messages"}
    assert same == {k: v for k, v in pieces[0].items() if k != "messages"}  # model, system, effort, ceiling
    pmh = _pmh(sr).splitlines()
    assert pmh[0] == "Additional prior history from records filed through 10-01-26"
    assert _cites("\n".join(pmh[1:])) == list(range(1, len(entries) + 1))  # date order, none lost or twice
    ledger = (sr.slug_dir / "runs" / "alpha" / "usage-ledger.jsonl").read_text().splitlines()
    assert json.loads(ledger[-1])["custom_id"] == SP.MERGE_ID


def test_the_merge_reply_is_checked_against_the_full_source(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if the merge reply is delivered unchecked: a page the entries never cite is refused."""
    log: list[str] = []
    stray = "Prior Medical History\nAn example condition was on file. (Exhibit 2 - p. 90)"
    client, entries = Scripted(merge=stray), _entries()
    sr = _sr(job_dir, firm_config_path, data_root, client, entries, log)
    _split(sr, monkeypatch, entries)
    assert UC.run_summary(sr) == 1
    assert any("REJECTED" in m and "beyond its source" in m for m in log)
    assert not (sr.slug_dir / "runs" / "alpha" / "update" / "pmh.md").exists()


def test_a_too_long_refusal_splits_instead_of_failing(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    """Fails if the API's "prompt is too long" 400 fails the stage."""
    log: list[str] = []
    client = Scripted(lambda p: _too_long() if len(_days(p)) > 2 else None)
    entries = _entries()
    sr = _sr(job_dir, firm_config_path, data_root, client, entries, log)
    assert UC.run_summary(sr) == 0
    assert _source(client.sent[0]) == "\n\n".join(entries)  # the one request first, as before
    answered = [p for p in client.pieces() if len(_days(p)) <= 2]
    assert sorted(d for p in answered for d in _days(p)) == DAYS
    assert len(client.merges()) == 1
    assert any("refused for its size" in m for m in log)
    assert len(_cites(_pmh(sr))) == len(entries)


def test_one_date_over_the_estimate_is_still_sent(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if a date is held on its ESTIMATE alone: only a real refusal holds it."""
    log: list[str] = []
    client = Scripted()
    entries = [_entry(DAYS[0], p, p) for p in range(1, 7)] + [_entry(DAYS[1], 7, 7)]
    sr = _sr(job_dir, firm_config_path, data_root, client, entries, log)
    _split(sr, monkeypatch, entries, 2)
    assert UC.run_summary(sr) == 0
    big = [p for p in client.pieces() if _days(p) == [DAYS[0]]]
    assert len(big) == 1 and len(client.merges()) == 1
    assert len(_cites(_pmh(sr))) == len(entries)


def test_a_single_date_update_over_the_estimate_is_the_one_request(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log: list[str] = []
    client = Scripted()
    entries = [_entry(DAYS[0], 1, 1), _entry(DAYS[0], 2, 2)]
    sr = _sr(job_dir, firm_config_path, data_root, client, entries, log)
    _split(sr, monkeypatch, entries, 1)
    assert UC.run_summary(sr) == 0
    assert [_source(p) for p in client.sent] == ["\n\n".join(entries)]


def test_one_date_refused_by_the_api_holds_with_a_true_plain_reason(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """Fails if the hold claims nothing was charged (an earlier piece was paid) or carries a number."""
    log: list[str] = []
    client = Scripted(lambda p: _too_long() if DAYS[1] in _days(p) else None)
    entries = [_entry(DAYS[0], 1, 1), _entry(DAYS[1], 2, 2), _entry(DAYS[1], 3, 3)]
    sr = _sr(job_dir, firm_config_path, data_root, client, entries, log)
    with pytest.raises(StageRefusal) as got:
        UC.run_summary(sr)
    assert str(got.value) == "One part of the added records is larger than a single reading allows; SMD is on it."
    assert "charged" not in str(got.value) and not re.search(r"\d", str(got.value))
    assert [_days(p) for p in client.pieces()][0] == [DAYS[0]]  # a piece was paid before the hold


def test_a_stopped_split_resumes_without_paying_twice(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if a rerun after a stop mid-split re-sends a piece already answered."""
    req = httpx.Request("POST", "https://x")
    denied = anthropic.AuthenticationError("invalid x-api-key", response=httpx.Response(401, request=req), body=None)
    entries = _entries()
    first = Scripted(lambda p: denied if DAYS[-1] in _days(p) else None)
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, first, entries, log)
    _split(sr, monkeypatch, entries)
    with pytest.raises(anthropic.AuthenticationError):
        UC.run_summary(sr)
    assert [p for p in first.pieces() if DAYS[-1] not in _days(p)]
    second = Scripted()
    sr2 = _sr(job_dir, firm_config_path, data_root, second, entries, log)
    assert UC.run_summary(sr2) == 0
    assert second.pieces() and all(DAYS[-1] in _days(p) for p in second.pieces())  # only the unanswered piece
    assert len(second.merges()) == 1
    assert len(_cites(_pmh(sr2))) == len(entries)


def test_a_reply_with_blank_lines_around_it_is_still_cached(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if an accepted piece is checked unstripped (its heading then reads
    as missing) and so never cached: a rerun would pay for it again."""
    entries: list[str] = _entries()
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, Scripted(pad=True), entries, log)
    _split(sr, monkeypatch, entries)
    assert UC.run_summary(sr) == 0
    second = Scripted(pad=True)
    sr2 = _sr(job_dir, firm_config_path, data_root, second, entries, log)
    assert UC.run_summary(sr2) == 0
    assert second.pieces() == [] and len(second.merges()) == 1


@pytest.mark.parametrize("change", ["effort", "max_tokens", "note"])
def test_the_piece_cache_is_keyed_by_effort_ceiling_and_note(
    change: str, job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if a piece answered under another effort, output ceiling or piece note is reused."""
    entries: list[str] = _entries()
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, Scripted(), entries, log)
    _split(sr, monkeypatch, entries)
    assert UC.run_summary(sr) == 0
    if change == "effort":
        monkeypatch.setattr(UC, "SUMMARY_EFFORT", "medium")
    elif change == "max_tokens":
        monkeypatch.setattr(UC, "SUMMARY_MAX_TOKENS", 12000)
    else:
        monkeypatch.setattr(SP, "PIECE_NOTE", SP.PIECE_NOTE + " Example.")
    second = Scripted()
    sr2 = _sr(job_dir, firm_config_path, data_root, second, entries, log)
    assert UC.run_summary(sr2) == 0
    assert sorted(d for p in second.pieces() for d in _days(p)) == DAYS  # every piece asked again


def test_halves_never_cut_a_date_and_end() -> None:
    groups = SP.by_date(_entries())
    a, b = SP.halves(groups) or ([], [])
    assert a + b == groups and a and b
    assert SP.halves(groups[:1]) is None
