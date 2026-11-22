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
    """Records every request; `refuse(params)` returns an exception to raise."""

    def __init__(self, refuse: Callable[[dict], Exception | None] = lambda _p: None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.refuse = refuse
        self.messages = NS(create=self._create, stream=None, batches=None)

    def _create(self, **params: Any) -> Any:
        self.sent.append(params)
        exc = self.refuse(params)
        if exc is not None:
            raise exc
        text = _reply(params["messages"][0]["content"])
        return NS(content=[NS(type="text", text=text)], stop_reason="end_turn", usage=Usage())


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


def test_over_the_limit_date_pieces_each_under_it_every_date_once(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if an oversize update is sent whole, a piece is over the limit,
    a date is cut or repeated, or the joined block is not one heading over
    every piece's text in date order."""
    log: list[str] = []
    client, entries = Scripted(), _entries()
    sr = _sr(job_dir, firm_config_path, data_root, client, entries, log)
    system = prompts.load("summarize-system", sr.cfg)
    limit = SP.estimate(system, entries[:5])
    monkeypatch.setattr(SP, "TOKENS_MAX", limit)
    assert UC.run_summary(sr) == 0
    assert len(client.sent) > 1
    assert all(SP.estimate(system, [_source(p)]) <= limit for p in client.sent)
    seen = [d for p in client.sent for d in _days(p)]
    assert sorted(seen) == DAYS  # every date, exactly once
    pmh = _pmh(sr).splitlines()
    assert pmh[0] == "Additional prior history from records filed through 10-01-26"
    cited = [int(p) for p in re.findall(r"\(Exhibit 2 - p\. (\d+)\)", "\n".join(pmh[1:]))]
    assert cited == list(range(1, len(entries) + 1))  # in date order, none lost
    assert "Prior Medical History" not in "\n".join(pmh)
    assert any("date pieces" in m for m in log)


def test_a_too_long_refusal_splits_instead_of_failing(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    """Fails if the API's "prompt is too long" 400 fails the stage."""
    log: list[str] = []
    client = Scripted(lambda p: _too_long() if len(_days(p)) > 2 else None)
    entries = _entries()
    sr = _sr(job_dir, firm_config_path, data_root, client, entries, log)
    assert UC.run_summary(sr) == 0
    assert _source(client.sent[0]) == "\n\n".join(entries)  # the one request first, as before
    answered = [p for p in client.sent if len(_days(p)) <= 2]
    assert sorted(d for p in answered for d in _days(p)) == DAYS
    assert any("refused for its size" in m for m in log)
    assert len(re.findall(r"\(Exhibit 2 - p\. \d+\)", _pmh(sr))) == len(entries)


def test_one_date_too_large_alone_holds_with_a_plain_reason_and_sends_nothing(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if a single oversize date is sent (it would be refused) or cut."""
    log: list[str] = []
    client = Scripted()
    entries = [_entry(DAYS[0], p, p) for p in range(1, 7)] + [_entry(DAYS[1], 7, 7)]
    sr = _sr(job_dir, firm_config_path, data_root, client, entries, log)
    monkeypatch.setattr(SP, "TOKENS_MAX", SP.estimate(prompts.load("summarize-system", sr.cfg), entries[:2]))
    with pytest.raises(StageRefusal, match="larger than a single reading allows.*nothing was charged"):
        UC.run_summary(sr)
    assert client.sent == []


def test_one_date_refused_by_the_api_holds(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    log: list[str] = []
    client = Scripted(lambda _p: _too_long())
    sr = _sr(job_dir, firm_config_path, data_root, client, [_entry(DAYS[0], 1, 1), _entry(DAYS[0], 2, 2)], log)
    with pytest.raises(StageRefusal, match=DAYS[0]):
        UC.run_summary(sr)
    assert len(client.sent) == 1


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
    monkeypatch.setattr(SP, "TOKENS_MAX", SP.estimate(prompts.load("summarize-system", sr.cfg), entries[:5]))
    with pytest.raises(anthropic.AuthenticationError):
        UC.run_summary(sr)
    paid = [p for p in first.sent if DAYS[-1] not in _days(p)]
    assert paid
    second = Scripted()
    sr2 = _sr(job_dir, firm_config_path, data_root, second, entries, log)
    assert UC.run_summary(sr2) == 0
    assert all(DAYS[-1] in _days(p) for p in second.sent)  # only the unanswered piece
    assert len(re.findall(r"\(Exhibit 2 - p\. \d+\)", _pmh(sr2))) == len(entries)


def test_halves_never_cut_a_date_and_end() -> None:
    groups = SP.by_date(_entries())
    a, b = SP.halves(groups) or ([], [])
    assert a + b == groups and a and b
    assert SP.halves(groups[:1]) is None
