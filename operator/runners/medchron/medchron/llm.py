"""The one doorway every paid call in the runner walks through.

Ported from the frozen tree's `llm.py`, which exists because thirteen delivered
ledgers showed prompt caching on for zero of 5,785 calls, the Batch API never
used, and thinking billed as output on stages that transcribe a page: each
script talked to the SDK on its own, so no lever could be pulled in one place.
This module owns the request shape (effort, caching, thinking), the retry
policy, the batch lifecycle, and the ledger row; a stage decides WHAT to ask
and nothing else. The two paths that bypassed it in the frozen tree (the audit
image verdict, the scanned-page classifier) are plain `call` shapes here, and
the tests pin them.

The one rule of caching: a block unique to a single call is never cache-marked.
A write costs 1.25x and only a later call with the same prefix earns the 0.1x
read, so marking a per-call body (a chunk, a page image, a claim) is a pure
loss. The caller names the blocks that repeat (`cache_blocks`); the default
marks only the system prompt.

Two things changed from the frozen tree, both defects the 2026-08-29 review
named. A batch that is still processing when the wall-clock ceiling passes is
NOT resubmitted: its items come back as `timed_out`, its file stays claimed for
the next run, and the stage exits non-zero so the driver resumes it later. And
levers arrive as arguments from the firm config, never from the environment;
the audit stage refuses the Batch API no matter what the levers say.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import FirmConfig
from .ledger import Ledger, count_pages
from .limits import LimitHold

# Only rows a call site actually leaves to the table. Every other stage passes
# effort itself or ships at the API default: every effort reading on
# 2026-08-27 failed its content bar (vision/billing low: 9-10 of 17 pages under
# 0.99 text similarity and a truncated date anchor; compose high: regression
# not whole), so a table row for them would be dead text that reads as policy.
EFFORT_DEFAULTS = {"audit": "medium", "repair": "medium", "merge": "low", "condense": "low"}
NEVER_BATCHED = {"audit"}     # the audit is the gate; it is never a batch job

BATCH_BYTE_BUDGET = 200_000_000     # API ceiling is 256 MB; leave headroom
BATCH_COUNT_BUDGET = 10_000         # API ceiling is 100k; keep batches restartable
_CACHE_MARK = {"type": "ephemeral"}


class DoorwayError(RuntimeError):
    pass


def model_for(cfg: FirmConfig, tier: str) -> str:
    """The model for a tier (transcription, mechanical, composition, audit,
    judgment), from the firm config's authored table."""
    tiers = cfg.get("models", "tiers") or {}
    if tier not in tiers:
        raise DoorwayError(f"models.tiers has no row for {tier!r}")
    return str(tiers[tier])


# ---- request shape -----------------------------------------------------------
def _mark(block: Any) -> tuple[Any, bool]:
    if not isinstance(block, dict) or block.get("type") not in ("text", "image"):
        return block, False
    b = dict(block)
    b["cache_control"] = dict(_CACHE_MARK)
    return b, True


def _validate_thinking(thinking: Any) -> dict[str, str] | None:
    if thinking is None:
        return None
    if not isinstance(thinking, dict):
        raise ValueError("thinking must be a dict or None")
    if thinking.get("type") == "disabled":
        raise ValueError("thinking disabled is never sent: lower effort instead")
    if "budget_tokens" in thinking:
        raise ValueError("budget_tokens is rejected by current models; use effort")
    if thinking.get("type") != "adaptive":
        raise ValueError(f"unsupported thinking config: {thinking}")
    return {"type": "adaptive"}


def build_params_marked(stage: str, *, model: str, messages: list[dict[str, Any]], max_tokens: int,
                        system: Any = None, effort: str | None = None, cache_blocks: tuple[str, ...] = ("system",),
                        tools: Any = None, tool_choice: Any = None, thinking: Any = None,
                        caching: bool = True) -> tuple[dict[str, Any], int]:
    """The kwargs for client.messages.create / .stream, and how many cache
    markers were placed. Never mutates its inputs."""
    cache_blocks = tuple(cache_blocks or ())
    markers = 0
    params: dict[str, Any] = {"model": model, "max_tokens": max_tokens}
    if system is not None:
        if "system" in cache_blocks and caching:
            if isinstance(system, str):
                params["system"] = [{"type": "text", "text": system, "cache_control": dict(_CACHE_MARK)}]
                markers += 1
            else:
                blocks = list(system)
                if blocks:
                    blocks[-1], ok = _mark(blocks[-1])
                    markers += int(ok)
                params["system"] = blocks
        else:
            params["system"] = system
    # only the LAST user message can carry user:<i> marks
    wanted = {int(cb.split(":", 1)[1]) for cb in cache_blocks if cb.startswith("user:")}
    out_msgs = list(messages)
    if wanted and caching:
        last_user = next((i for i in range(len(out_msgs) - 1, -1, -1) if out_msgs[i].get("role") == "user"), None)
        if last_user is not None:
            m = dict(out_msgs[last_user])
            content = m.get("content")
            content = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
            for i in sorted(wanted):
                if 0 <= i < len(content):
                    content[i], ok = _mark(content[i])
                    markers += int(ok)
            m["content"] = content
            out_msgs[last_user] = m
    params["messages"] = out_msgs
    e = effort if effort is not None else EFFORT_DEFAULTS.get(stage)
    if e:
        params["output_config"] = {"effort": e}
    t = _validate_thinking(thinking)
    if t is not None:
        params["thinking"] = t
    if tools is not None:
        params["tools"] = tools
    if tool_choice is not None:
        params["tool_choice"] = tool_choice
    return params, markers


def build_params(stage: str, **kw: Any) -> dict[str, Any]:
    return build_params_marked(stage, **kw)[0]


# ---- results -----------------------------------------------------------------
@dataclass
class Result:
    text: str
    message: Any
    stop_reason: str
    usage: Any
    batch: bool = False
    empty: bool = False


def _text_of(message: Any) -> str:
    parts = []
    for b in getattr(message, "content", None) or []:
        if getattr(b, "type", None) == "text":
            parts.append(getattr(b, "text", "") or "")
        elif isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", "") or "")
    return "".join(parts)


def _input_chars(messages: list[dict[str, Any]]) -> int:
    n = 0
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            n += len(c)
        else:
            n += sum(len(b.get("text", "") or "") for b in c or [] if isinstance(b, dict) and b.get("type") == "text")
    return n


def make_result(message: Any, messages: list[dict[str, Any]], batch: bool = False) -> Result:
    text = _text_of(message)
    stop = getattr(message, "stop_reason", None) or ""
    empty = stop == "end_turn" and len(text.strip()) < 0.02 * _input_chars(messages)
    return Result(text=text, message=message, stop_reason=stop, usage=getattr(message, "usage", None),
                  batch=batch, empty=empty)


class _ZeroUsage:
    input_tokens = 0
    output_tokens = 0
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


# ---- retry -------------------------------------------------------------------
def classify_error(exc: BaseException) -> str:
    """'retry' for transport/429/5xx, 'raise' for 4xx invalid requests."""
    try:
        import anthropic
    except Exception:  # noqa: BLE001 - pragma: no cover
        return "raise" if "invalid_request" in str(exc) else "retry"
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.RateLimitError)):
        return "retry"
    if isinstance(exc, anthropic.APIStatusError):
        code = getattr(exc, "status_code", 0) or 0
        return "retry" if (code == 429 or code >= 500 or code in (408, 409)) else "raise"
    return "retry"


# ---- batch items -------------------------------------------------------------
@dataclass
class Item:
    custom_id: str
    messages: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchSummary:
    ok: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    timed_out: list[str] = field(default_factory=list)
    batch_ids: list[str] = field(default_factory=list)


def _split_requests(reqs: list[dict[str, Any]], byte_budget: int = BATCH_BYTE_BUDGET,
                    count_budget: int = BATCH_COUNT_BUDGET) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    size = 0
    for r in reqs:
        n = len(json.dumps(r).encode("utf-8"))
        if cur and (size + n > byte_budget or len(cur) >= count_budget):
            batches.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += n
    if cur:
        batches.append(cur)
    return batches


def _batch_files(batch_dir: Path, stage: str) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    if not batch_dir.is_dir():
        return files
    for p in batch_dir.iterdir():
        if p.name.startswith(f"batch-{stage}-") and p.suffix == ".json":
            try:
                files[p.name] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
    return files


def _next_index(existing: dict[str, Any], stage: str) -> int:
    n = 0
    for fn in existing:
        core = fn[len(f"batch-{stage}-"):-len(".json")].split("-")[0]
        if core.isdigit():
            n = max(n, int(core) + 1)
    return n


# ---- the doorway -------------------------------------------------------------
@dataclass
class Doorway:
    """Levers come from the firm config, the ledger is the unit's, the client is
    injectable (tests never touch the network)."""
    ledger: Ledger
    caching: bool = True
    batch_stages: frozenset[str] = frozenset()
    poll_s: float = 30.0
    max_wait_s: float = 86400.0
    client: Any = None
    log: Callable[[str], None] = print
    #: Called with the stage name before every interactive call, BEFORE any
    #: money moves. The driver binds the cost limits here; raising from it is
    #: how a cap or a monthly budget stops a run inside a long stage instead
    #: of at the next stage boundary.
    before_request: Callable[[str], None] | None = None

    @classmethod
    def from_config(cls, cfg: FirmConfig, ledger: Ledger, *, client: Any = None,
                    log: Callable[[str], None] = print,
                    before_request: Callable[[str], None] | None = None) -> "Doorway":
        return cls(ledger=ledger, caching=bool(cfg.get("levers", "cache", True)),
                   batch_stages=frozenset(cfg.batch_stages) - NEVER_BATCHED, client=client, log=log,
                   before_request=before_request)

    def _client(self, timeout: float | None) -> Any:
        if self.client is None:
            import anthropic

            self.client = anthropic.Anthropic(timeout=600.0, max_retries=0)
        if timeout is not None and hasattr(self.client, "with_options"):
            return self.client.with_options(timeout=timeout)
        return self.client

    def call(self, stage: str, *, model: str, messages: list[dict[str, Any]], max_tokens: int, system: Any = None,
             effort: str | None = None, cache_blocks: tuple[str, ...] = ("system",), stream: bool = False,
             tools: Any = None, tool_choice: Any = None, thinking: Any = None, attempts: int = 3,
             backoff: float = 20.0, timeout: float | None = None, custom_id: str | None = None) -> Result:
        """One interactive call. Retries transport/429/5xx; re-raises 4xx at
        once. Always writes a ledger row, so a paid call is never invisible."""
        if self.before_request is not None:
            self.before_request(stage)
        params, markers = build_params_marked(stage, model=model, messages=messages, max_tokens=max_tokens,
                                              system=system, effort=effort, cache_blocks=cache_blocks, tools=tools,
                                              tool_choice=tool_choice, thinking=thinking, caching=self.caching)
        e = params.get("output_config", {}).get("effort")
        client = self._client(timeout)
        last: BaseException | None = None
        for n in range(attempts):
            try:
                if stream:
                    with client.messages.stream(**params) as st:
                        msg = st.get_final_message()
                else:
                    msg = client.messages.create(**params)
            except Exception as exc:  # noqa: BLE001 - classified below
                last = exc
                if classify_error(exc) == "raise":
                    raise
                if n + 1 < attempts:
                    time.sleep(backoff * (n + 1))
                continue
            self.ledger.record(stage, model, msg.usage, effort=e, cache=bool(markers), batch=False,
                               pages=count_pages(messages), custom_id=custom_id)
            return make_result(msg, params["messages"])
        raise last if last else DoorwayError(f"{stage}: exhausted attempts")

    # ---- batch -----------------------------------------------------------
    def batch_call(self, stage: str, items: list[Item], on_result: Callable[[Item, Result | None, str | None], None],
                   *, model: str, max_tokens: int, batch_dir: Path, system: Any = None, effort: str | None = None,
                   cache_blocks: tuple[str, ...] = ("system",), tools: Any = None, tool_choice: Any = None,
                   thinking: Any = None) -> BatchSummary:
        """Every item through the Batch API when the levers name the stage,
        else serially through call(). on_result fires once per item that
        finished, in both modes; timed-out items fire nothing and are listed."""
        summary = BatchSummary()
        if self.before_request is not None:
            # Batch mode has no per-item doorway, so the limits get their one
            # look before the submission; serial mode is checked per item below.
            self.before_request(stage)
        if stage not in self.batch_stages:
            for it in items:
                try:
                    r = self.call(stage, model=model, system=system, messages=it.messages, max_tokens=max_tokens,
                                  effort=effort, cache_blocks=cache_blocks, tools=tools, tool_choice=tool_choice,
                                  thinking=thinking, custom_id=it.custom_id)
                except LimitHold:
                    # A limit is not one item's failure: it stops the stage.
                    # Swallowing it here would turn the gate into a page of
                    # "failed" transcriptions and keep spending.
                    raise
                except Exception as exc:  # noqa: BLE001 - one item's failure is one result
                    summary.failed[it.custom_id] = str(exc)
                    on_result(it, None, str(exc))
                    continue
                summary.ok.append(it.custom_id)
                on_result(it, r, None)
            return summary
        if stage in NEVER_BATCHED:
            raise DoorwayError(f"{stage} is never a batch job")
        by_id = {it.custom_id: it for it in items}
        need = self._round(stage, items, by_id, summary, on_result, retry=False, model=model,
                           max_tokens=max_tokens, batch_dir=batch_dir, system=system, effort=effort,
                           cache_blocks=cache_blocks, tools=tools, tool_choice=tool_choice, thinking=thinking)
        if need:
            self.log(f"  batch: retrying {len(need)} item(s) once")
            again = self._round(stage, [by_id[c] for c in need], by_id, summary, on_result, retry=True,
                                model=model, max_tokens=max_tokens, batch_dir=batch_dir, system=system,
                                effort=effort, cache_blocks=cache_blocks, tools=tools, tool_choice=tool_choice,
                                thinking=thinking)
            for cid, err in again.items():
                summary.failed[cid] = err
                on_result(by_id[cid], None, err)
        return summary

    def _round(self, stage: str, todo: list[Item], by_id: dict[str, Item], summary: BatchSummary,
               on_result: Callable, *, retry: bool, model: str, max_tokens: int, batch_dir: Path,
               **shape: Any) -> dict[str, str]:
        """Submit (or resume) batches for `todo`. Returns {custom_id: error}
        for items that need a retry; a timed-out batch's items go to
        summary.timed_out and are never resubmitted."""
        client = self._client(None)
        existing = _batch_files(batch_dir, stage)
        claimed: dict[str, str] = {}
        for fn, rec in existing.items():
            if fn.endswith("-retry.json") != retry:
                continue
            for cid in rec.get("custom_ids", []):
                claimed[cid] = rec["id"]
        ids: list[str] = []
        for bid in sorted(set(claimed.values())):
            ids.append(bid)
            self.log(f"  batch: resuming {bid}")
        fresh: list[dict[str, Any]] = []
        e, markers = None, 0
        for it in todo:
            if it.custom_id in claimed:
                continue
            p, mk = build_params_marked(stage, model=model, messages=it.messages, max_tokens=max_tokens,
                                        caching=self.caching, **shape)
            e, markers = p.get("output_config", {}).get("effort"), mk
            fresh.append({"custom_id": it.custom_id, "params": p})
        n = _next_index(existing, stage)
        for group in _split_requests(fresh):
            path = batch_dir / f"batch-{stage}-{n}{'-retry' if retry else ''}.json"
            b = client.messages.batches.create(requests=group)
            batch_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"id": b.id, "custom_ids": [r["custom_id"] for r in group],
                                        "submitted_ts": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
            self.log(f"  batch: submitted {b.id} ({len(group)} requests)")
            ids.append(b.id)
            n += 1
        summary.batch_ids.extend(ids)
        timed_out = self._poll(client, ids)
        need: dict[str, str] = {}
        seen: set[str] = set()
        for bid in ids:
            if bid in timed_out:
                continue
            for res in client.messages.batches.results(bid):
                cid = res.custom_id
                it = by_id.get(cid)
                if it is None or cid in seen:
                    continue
                seen.add(cid)
                rtype = res.result.type
                if rtype == "succeeded":
                    msg = res.result.message
                    self.ledger.record(stage, model, msg.usage, effort=e, cache=bool(markers), batch=True,
                                       pages=count_pages(it.messages), custom_id=cid)
                    if getattr(msg, "stop_reason", None) == "refusal":
                        need[cid] = "refusal"
                        continue
                    summary.ok.append(cid)
                    on_result(it, make_result(msg, it.messages, batch=True), None)
                else:
                    self.ledger.record(stage, model, _ZeroUsage(), effort=e, cache=bool(markers), batch=True,
                                       pages=count_pages(it.messages), custom_id=cid, error=rtype)
                    need[cid] = rtype
        for it in todo:
            if it.custom_id in seen or it.custom_id in need:
                continue
            # Its batch is still processing (or its result never came back).
            # The file stays claimed; the next run resumes the same id. It is
            # NOT resubmitted: the original batch bills whether we wait or not.
            summary.timed_out.append(it.custom_id)
        if summary.timed_out:
            self.log(f"  batch: {len(summary.timed_out)} item(s) still processing; resume later, not resubmitted")
        return need

    def _poll(self, client: Any, batch_ids: list[str]) -> set[str]:
        """Block until every id is 'ended'. Returns the set that timed out."""
        pending = set(batch_ids)
        t0 = time.time()
        while pending:
            for bid in list(pending):
                st = client.messages.batches.retrieve(bid)
                if getattr(st, "processing_status", None) == "ended":
                    pending.discard(bid)
            if not pending:
                break
            if time.time() - t0 > self.max_wait_s:
                return pending
            self.log(f"  batch: {len(pending)} still processing")
            time.sleep(self.poll_s)
        return set()
