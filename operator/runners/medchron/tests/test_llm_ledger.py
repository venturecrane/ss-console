"""The paid doorway and its ledger, no network. Every real call shape in the
pipeline is asserted (ported from the frozen tree's own test_llm.py), plus the
two shapes that used to bypass the doorway (the audit image verdict and the
scanned-page classifier) and the two defects the port fixes (a timed-out batch
is never resubmitted; the audit is never batched)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from medchron import budget as budget_mod, config as config_mod, ledger as ledger_mod, llm

IMG = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
TOOL = {"name": "record_verdict", "input_schema": {"type": "object"}}


def marked(block: dict) -> bool:
    return isinstance(block, dict) and "cache_control" in block


def user_blocks(params: dict) -> list:
    c = params["messages"][-1]["content"]
    return c if isinstance(c, list) else [{"type": "text", "text": c}]


# ---- request shapes ------------------------------------------------------------
def test_compose_marks_system_only_and_leaves_the_chunk_alone() -> None:
    p = llm.build_params("compose", model="claude-opus-5", max_tokens=128000, system="SYS",
                         messages=[{"role": "user", "content": "chunk"}])
    assert marked(p["system"][0]) and p["messages"][0]["content"] == "chunk"
    assert "output_config" not in p and "thinking" not in p


def test_audit_image_verdict_shape_goes_through_the_doorway() -> None:
    """The shape audit_citations.verify() built by hand against the SDK: images
    first, claim last, forced tool, adaptive thinking, no system."""
    msgs = [{"role": "user", "content": [IMG, IMG, {"type": "text", "text": "claim"}]}]
    p = llm.build_params("audit", model="claude-sonnet-5", max_tokens=4000, messages=msgs, tools=[TOOL],
                         tool_choice={"type": "tool", "name": "record_verdict"}, thinking={"type": "adaptive"},
                         cache_blocks=("user:0",))
    ub = user_blocks(p)
    assert marked(ub[0]) and not marked(ub[1]) and not marked(ub[2])
    assert "system" not in p and p["tools"] == [TOOL] and p["tool_choice"]["name"] == "record_verdict"
    assert p["thinking"] == {"type": "adaptive"} and p["output_config"] == {"effort": "medium"}
    assert not marked(msgs[0]["content"][0])  # caller's messages never mutated
    p = llm.build_params("audit", model="claude-sonnet-5", max_tokens=4000, messages=msgs, tools=[TOOL])
    assert not any(marked(b) for b in user_blocks(p))


def test_classify_shape_is_a_plain_call_with_a_system_prompt() -> None:
    """classify_scanned's hand-built request: a system prompt and twelve labelled
    page images in one user turn."""
    content = []
    for i in range(12):
        content += [{"type": "text", "text": f"page Ex{i}p1:"}, IMG]
    p = llm.build_params("classify", model="claude-sonnet-5", max_tokens=800, system="SYS",
                         messages=[{"role": "user", "content": content}])
    assert marked(p["system"][0]) and not any(marked(b) for b in user_blocks(p))
    assert "output_config" not in p
    assert ledger_mod.count_pages(p["messages"]) == 12


def test_vision_billing_merge_shapes() -> None:
    p = llm.build_params("vision", model="claude-sonnet-5", max_tokens=8000, system="SYS",
                         messages=[{"role": "user", "content": [IMG, {"type": "text", "text": "Transcribe this page."}]}])
    assert marked(p["system"][0]) and not any(marked(b) for b in user_blocks(p)) and "output_config" not in p
    p = llm.build_params("billing", model="claude-sonnet-5", max_tokens=16000,
                         messages=[{"role": "user", "content": [{"type": "text", "text": "Document"}, IMG]}])
    assert not any(marked(b) for b in user_blocks(p)) and "system" not in p
    p = llm.build_params("merge", model="claude-sonnet-5", max_tokens=32000, system="SYS",
                         messages=[{"role": "user", "content": "text"}])
    assert p["messages"][0]["content"] == "text" and p["output_config"] == {"effort": "low"}


def test_user0_on_str_content_and_caching_off() -> None:
    p = llm.build_params("audit", model="m", max_tokens=100, messages=[{"role": "user", "content": "window"}],
                         cache_blocks=("user:0",))
    assert p["messages"][0]["content"][0]["text"] == "window" and marked(p["messages"][0]["content"][0])
    msgs = [{"role": "user", "content": [IMG, {"type": "text", "text": "claim"}]}]
    p, n = llm.build_params_marked("audit", model="m", max_tokens=100, system="SYS", messages=msgs,
                                   cache_blocks=("system", "user:0"), caching=False)
    assert p["system"] == "SYS" and not any(marked(b) for b in user_blocks(p)) and n == 0
    _, n = llm.build_params_marked("audit", model="m", max_tokens=1, system="S", messages=msgs,
                                   cache_blocks=("system", "user:0"))
    assert n == 2


def test_thinking_validation() -> None:
    for bad in ({"type": "disabled"}, {"type": "enabled", "budget_tokens": 1}, {"type": "x"}, "adaptive"):
        with pytest.raises(ValueError):
            llm.build_params("audit", model="m", max_tokens=1, messages=[], thinking=bad)


def test_empty_detection() -> None:
    msg = NS(content=[NS(type="text", text="ok")], stop_reason="end_turn", usage=None)
    assert llm.make_result(msg, [{"role": "user", "content": "x" * 1000}]).empty
    assert not llm.make_result(msg, [{"role": "user", "content": "x" * 50}]).empty
    msg.stop_reason = "max_tokens"
    assert not llm.make_result(msg, [{"role": "user", "content": "x" * 1000}]).empty


def test_batch_split_by_bytes_and_count() -> None:
    reqs = [{"custom_id": str(i), "params": {"x": "y" * 100}} for i in range(10)]
    assert [len(b) for b in llm._split_requests(reqs, byte_budget=350)] == [2, 2, 2, 2, 2]
    assert [len(b) for b in llm._split_requests(reqs, count_budget=4)] == [4, 4, 2]


def test_model_for_reads_the_authored_tiers(firm_config_path: Path) -> None:
    cfg = config_mod.load(str(firm_config_path))
    assert llm.model_for(cfg, "composition") == "claude-opus-5"
    with pytest.raises(llm.DoorwayError):
        llm.model_for(cfg, "oracle")


# ---- a scripted client ----------------------------------------------------------
class Usage:
    def __init__(self, i=10, o=5, cr=0, cw=0):
        self.input_tokens, self.output_tokens = i, o
        self.cache_read_input_tokens, self.cache_creation_input_tokens = cr, cw


def mk_msg(text="hello", stop="end_turn", **u):
    return NS(content=[NS(type="text", text=text)], stop_reason=stop, usage=Usage(**u))


class Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class Messages:
    def __init__(self, reply=None, batches=None):
        self.reply, self.calls, self.batches = reply, [], batches

    def create(self, **kw):
        self.calls.append(kw)
        return self.reply

    def stream(self, **kw):
        self.calls.append(kw)
        return Stream(self.reply)


class Batches:
    """results[bid] scripted by a function of (bid, created); `ended` says
    which ids are finished (default: all)."""

    def __init__(self, script, ended=None):
        self.script, self.created, self.n, self.ended = script, [], 0, ended

    def create(self, requests):
        self.created.append(requests)
        self.n += 1
        return NS(id=f"b{self.n}")

    def retrieve(self, bid):
        done = self.ended is None or bid in self.ended
        return NS(processing_status="ended" if done else "in_progress")

    def results(self, bid):
        return self.script(bid, self.created)


def client_for(reply=None, batches=None):
    return NS(messages=Messages(reply, batches))


@pytest.fixture
def ledger(tmp_path: Path) -> ledger_mod.Ledger:
    return ledger_mod.Ledger(tmp_path / "runs" / "u" / "usage-ledger.jsonl")


def rows(ledger: ledger_mod.Ledger) -> list[dict]:
    return ledger_mod.read_rows(ledger.path)


# ---- call() --------------------------------------------------------------------
def test_call_streams_and_writes_a_row_with_pages_and_custom_id(ledger: ledger_mod.Ledger) -> None:
    c = client_for(mk_msg("out", cw=7, cr=3))
    d = llm.Doorway(ledger, client=c, log=lambda *_: None)
    r = d.call("compose", model="claude-opus-5", system="S", max_tokens=10,
               messages=[{"role": "user", "content": [IMG, {"type": "text", "text": "in"}]}], stream=True,
               custom_id="chunk-01")
    assert r.text == "out" and r.stop_reason == "end_turn" and r.batch is False
    assert "system" in c.messages.calls[0]
    row = rows(ledger)[-1]
    assert row["cache_write"] == 7 and row["cache_read"] == 3 and row["effort"] is None
    assert row["batch"] is False and row["cache"] is True and row["pages"] == 1 and row["custom_id"] == "chunk-01"


class Boom:
    def __init__(self, exc, then):
        self.exc, self.then, self.calls = exc, then, 0

    def create(self, **kw):
        self.calls += 1
        if self.calls == 1:
            raise self.exc
        return self.then


def test_call_reraises_4xx_at_once_and_retries_5xx(ledger: ledger_mod.Ledger) -> None:
    import anthropic
    import httpx

    req = httpx.Request("POST", "https://x")
    bad = anthropic.BadRequestError("Could not process image", response=httpx.Response(400, request=req),
                                    body={"error": {"type": "invalid_request_error"}})
    c = NS(messages=Boom(bad, mk_msg()))
    d = llm.Doorway(ledger, client=c, log=lambda *_: None)
    with pytest.raises(anthropic.BadRequestError):
        d.call("audit", model="m", max_tokens=1, messages=[{"role": "user", "content": "x"}])
    assert c.messages.calls == 1 and rows(ledger) == []
    over = anthropic.InternalServerError("overloaded", response=httpx.Response(529, request=req), body=None)
    c = NS(messages=Boom(over, mk_msg("recovered")))
    d = llm.Doorway(ledger, client=c, log=lambda *_: None)
    r = d.call("audit", model="m", max_tokens=1, backoff=0, messages=[{"role": "user", "content": "x"}])
    assert r.text == "recovered" and c.messages.calls == 2 and len(rows(ledger)) == 1


# ---- batch_call ----------------------------------------------------------------
def _items():
    return [llm.Item("a", [{"role": "user", "content": "A"}]), llm.Item("b", [{"role": "user", "content": "B"}]),
            llm.Item("c", [{"role": "user", "content": "C"}])]


def test_batch_call_is_serial_unless_the_lever_names_the_stage(ledger: ledger_mod.Ledger, tmp_path: Path) -> None:
    got = []
    d = llm.Doorway(ledger, client=client_for(mk_msg("serial")), log=lambda *_: None)
    s = d.batch_call("vision", _items()[:2], lambda it, r, e: got.append((it.custom_id, r.text if r else e)),
                     model="claude-sonnet-5", max_tokens=5, batch_dir=tmp_path / "b")
    assert got == [("a", "serial"), ("b", "serial")] and s.ok == ["a", "b"] and s.batch_ids == []
    assert [r["custom_id"] for r in rows(ledger)] == ["a", "b"]


def _script(bid, created):
    reqs = created[int(bid[1:]) - 1]
    out = []
    for rq in reqs:
        cid = rq["custom_id"]
        if cid == "a":
            out.append(NS(custom_id="a", result=NS(type="succeeded", message=mk_msg("nope", stop="refusal"))))
        elif cid == "b":
            out.append(NS(custom_id="b", result=NS(type="succeeded", message=mk_msg("B-out"))))
        elif cid == "c":
            out.append(NS(custom_id="c", result=NS(type="errored")) if bid == "b1"
                       else NS(custom_id="c", result=NS(type="succeeded", message=mk_msg("C-out"))))
    return out


def test_batch_mode_retries_errors_and_refusals_once_and_persists_files(ledger: ledger_mod.Ledger, tmp_path: Path) -> None:
    got, flags = {}, {}

    def keep(it, r, e):
        got[it.custom_id] = (r.text if r else None, e)
        if r:
            flags[it.custom_id] = r.batch

    b = Batches(_script)
    d = llm.Doorway(ledger, batch_stages=frozenset({"vision"}), poll_s=0, client=client_for(batches=b),
                    log=lambda *_: None)
    bdir = tmp_path / "batchdir"
    s = d.batch_call("vision", _items(), keep, model="claude-sonnet-5", max_tokens=5, batch_dir=bdir)
    assert got == {"b": ("B-out", None), "c": ("C-out", None), "a": (None, "refusal")}
    assert len(b.created) == 2 and sorted(r["custom_id"] for r in b.created[1]) == ["a", "c"]
    assert "output_config" not in b.created[0][0]["params"] and "system" not in b.created[0][0]["params"]
    assert sorted(p.name for p in bdir.iterdir()) == ["batch-vision-0.json", "batch-vision-1-retry.json"]
    rec = json.loads((bdir / "batch-vision-0.json").read_text())
    assert set(rec) == {"id", "custom_ids", "submitted_ts"} and rec["custom_ids"] == ["a", "b", "c"]
    assert s.ok == ["b", "c"] and s.failed == {"a": "refusal"} and s.timed_out == [] and s.batch_ids == ["b1", "b2"]
    brows = [r for r in rows(ledger) if r["batch"] is True]
    assert len(brows) == 5 and any(r.get("error") == "errored" and r["in"] == 0 and r["custom_id"] == "c" for r in brows)
    assert flags == {"b": True, "c": True}


def test_batch_resume_never_resubmits_a_claimed_item(ledger: ledger_mod.Ledger, tmp_path: Path) -> None:
    b2 = Batches(lambda bid, created: [NS(custom_id="b", result=NS(type="succeeded", message=mk_msg("resumed")))]
                 if bid == "keep" else [])
    rdir = tmp_path / "resume"
    rdir.mkdir()
    (rdir / "batch-vision-0.json").write_text(json.dumps({"id": "keep", "custom_ids": ["b"], "submitted_ts": "t"}))
    got = {}
    d = llm.Doorway(ledger, batch_stages=frozenset({"vision"}), poll_s=0, client=client_for(batches=b2),
                    log=lambda *_: None)
    d.batch_call("vision", [_items()[1]], lambda it, r, e: got.__setitem__(it.custom_id, (r.text if r else None, e)),
                 model="claude-sonnet-5", max_tokens=5, batch_dir=rdir)
    assert b2.created == [] and got["b"] == ("resumed", None)


def test_a_timed_out_batch_is_reported_not_resubmitted(ledger: ledger_mod.Ledger, tmp_path: Path) -> None:
    """The frozen tree re-queued a still-processing batch's items into a fresh
    submission, paying twice. Now they are listed as timed_out, the file stays
    claimed, and nothing is resubmitted."""
    b = Batches(_script, ended=set())          # nothing ever ends
    d = llm.Doorway(ledger, batch_stages=frozenset({"vision"}), poll_s=0, max_wait_s=0,
                    client=client_for(batches=b), log=lambda *_: None)
    fired = []
    bdir = tmp_path / "slow"
    s = d.batch_call("vision", _items(), lambda it, r, e: fired.append(it.custom_id), model="claude-sonnet-5",
                     max_tokens=5, batch_dir=bdir)
    assert len(b.created) == 1 and fired == [] and s.ok == [] and s.failed == {}
    assert s.timed_out == ["a", "b", "c"] and (bdir / "batch-vision-0.json").is_file()
    assert rows(ledger) == []


def test_audit_is_never_a_batch_job(ledger: ledger_mod.Ledger, tmp_path: Path, firm_config_path: Path) -> None:
    cfg = config_mod.load(str(firm_config_path))
    d = llm.Doorway.from_config(cfg, ledger, client=client_for(mk_msg("v")), log=lambda *_: None)
    assert d.batch_stages == frozenset() and d.caching is True
    d2 = llm.Doorway(ledger, batch_stages=frozenset({"audit"}), client=client_for(), log=lambda *_: None)
    with pytest.raises(llm.DoorwayError, match="never a batch job"):
        d2.batch_call("audit", _items(), lambda *_: None, model="m", max_tokens=1, batch_dir=tmp_path)


# ---- the ledger report ------------------------------------------------------------
def test_ledger_report_prices_from_the_shared_table(ledger: ledger_mod.Ledger, pricing_path: Path) -> None:
    ledger.record("compose", "claude-opus-5", Usage(i=1_000_000, o=100_000, cr=200_000, cw=50_000), effort=None,
                  cache=True, batch=False, pages=0)
    ledger.record("vision", "claude-sonnet-5", Usage(i=10_000, o=1_000), effort=None, cache=True, batch=True, pages=3)
    ledger.record("mystery", "claude-mystery-7", Usage(i=1, o=1), effort=None, cache=False, batch=False)
    pricing = budget_mod.Pricing.load(pricing_path)
    out = ledger_mod.report(ledger.path, pricing)
    assert "TOTAL" in out and "unknown-model rows (unpriced): 1" in out
    blob = json.loads(out.strip().splitlines()[-1])
    assert set(blob) == {"rate_card", "tokens_by_stage", "dollars_by_stage", "dollars_total"}
    # compose: 1M in @$5 + 50k write @1.25 + 200k read @0.1 + 100k out @$25 = 5 + .3125 + .1 + 2.5
    assert blob["dollars_by_stage"]["compose"] == pytest.approx(7.9125, abs=1e-4)
    assert blob["dollars_by_stage"]["vision"] == pytest.approx((0.02 + 0.01) * 0.5, abs=1e-6)
    assert blob["tokens_by_stage"]["vision"]["pages"] == 3
