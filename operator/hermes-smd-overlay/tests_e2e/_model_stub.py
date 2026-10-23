"""A scripted model for the real Hermes agent loop.

Two transports, one script:

* ``AnthropicMessagesStub`` is a local HTTP server that speaks the Anthropic
  Messages API, streaming included (Hermes' main turn calls
  ``messages.stream()``). Seats run ``api_mode: anthropic_messages``, so this is
  the adapter a seat actually exercises: the real ``anthropic`` SDK, the real
  Hermes request builder, the real stream accumulator.
* ``FakeOpenAIClient`` stands in for the ``openai.OpenAI`` client on the
  ``chat_completions`` path, patched at the symbol Hermes' own tests patch
  (``agent.process_bootstrap.OpenAI`` at the pin), returning the documented
  chat-completion shape.

Both draw from a ``ModelScript``: a queue of scripted assistant turns. A request
that offers tools (the main loop) takes the next scripted turn, or a closing
text turn when the queue is empty, so one scripted tool call is one tool
dispatch followed by the end of the turn. A request that offers no tools is a
side call (the session title, a summary) and is answered with plain text
without consuming the script. Every main-loop request is recorded with the tool
names it offered and the tool results it carried back, which is what the model
would have seen.
"""

from __future__ import annotations

import itertools
import json
import threading
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

CLOSING_TEXT = "Done."
SIDE_CALL_TEXT = "Harness session"


@dataclass
class ScriptedTurn:
    """One assistant turn: zero or more tool calls, then optional text."""

    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    text: str = ""


@dataclass
class RecordedRequest:
    transport: str
    tools: list[str]
    tool_results: list[dict[str, Any]]
    body: dict[str, Any]


class ModelScript:
    """The shared queue both transports read from. Thread-safe: Hermes may call
    the model from a worker thread.

    Only the turn under test may take a scripted turn. Hermes runs work of its
    own on daemon threads after a turn ends (the background memory and skill
    review forks the agent and replays that turn, tools and all), and such a
    request can land while the NEXT scripted turn is waiting in the queue.
    Each driven turn therefore carries a unique ``marker`` in its user message;
    a tool-offering request whose messages lack the active marker is recorded
    in ``foreign`` and answered with closing text, never with the script."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: deque[ScriptedTurn] = deque()
        self.requests: list[RecordedRequest] = []
        self.foreign: list[RecordedRequest] = []
        self.foreign_total = 0
        self.side_calls = 0
        self.marker = ""
        self._ids = itertools.count(1)

    def push(self, turn: ScriptedTurn) -> None:
        with self._lock:
            self._queue.append(turn)

    def call_tool(self, name: str, args: dict[str, Any]) -> None:
        self.push(ScriptedTurn(tool_calls=[(name, args)]))

    def reset(self, marker: str = "") -> None:
        with self._lock:
            self._queue.clear()
            self.requests.clear()
            self.foreign.clear()
            self.side_calls = 0
            self.marker = marker

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def next_id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids):06d}"

    def take(self, record: RecordedRequest, messages: Any) -> ScriptedTurn:
        """The scripted turn for a tool-offering request, or closing text for a
        request that is not part of the turn under test."""
        wire = json.dumps(messages, default=str)
        with self._lock:
            if self.marker and self.marker not in wire:
                self.foreign.append(record)
                self.foreign_total += 1
                return ScriptedTurn(text=CLOSING_TEXT)
            self.requests.append(record)
            if self._queue:
                return self._queue.popleft()
        return ScriptedTurn(text=CLOSING_TEXT)

    def note_side_call(self) -> None:
        with self._lock:
            self.side_calls += 1

    def last_tool_results(self) -> list[dict[str, Any]]:
        """Tool results carried by the most recent main-loop request."""
        with self._lock:
            for req in reversed(self.requests):
                if req.tool_results:
                    return list(req.tool_results)
        return []


# --------------------------------------------------------------------------
# Anthropic Messages API (the transport seats run)
# --------------------------------------------------------------------------


def _anthropic_tool_results(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``tool_result`` blocks in the LAST user message: the results of the
    tool calls the previous assistant turn made."""
    messages = body.get("messages") or []
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            return []
        out = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                raw = block.get("content")
                if isinstance(raw, list):
                    text = "".join(
                        b.get("text", "") for b in raw if isinstance(b, dict) and "text" in b
                    )
                else:
                    text = str(raw or "")
                out.append({"tool_use_id": block.get("tool_use_id"), "content": text})
        return out
    return []


def _anthropic_message(script: ModelScript, turn: ScriptedTurn, model: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if turn.text:
        content.append({"type": "text", "text": turn.text})
    for name, args in turn.tool_calls:
        content.append(
            {"type": "tool_use", "id": script.next_id("toolu_e2e"), "name": name, "input": args}
        )
    return {
        "id": script.next_id("msg_e2e"),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": "tool_use" if turn.tool_calls else "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 50, "output_tokens": 10},
    }


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _anthropic_stream(message: dict[str, Any]) -> bytes:
    start = {**message, "content": [], "stop_reason": None}
    start["usage"] = {"input_tokens": message["usage"]["input_tokens"], "output_tokens": 1}
    out = [_sse("message_start", {"type": "message_start", "message": start})]
    for index, block in enumerate(message["content"]):
        if block["type"] == "text":
            out.append(
                _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            )
            delta = {"type": "text_delta", "text": block["text"]}
        else:
            out.append(
                _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {
                            "type": "tool_use",
                            "id": block["id"],
                            "name": block["name"],
                            "input": {},
                        },
                    },
                )
            )
            delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
        out.append(
            _sse(
                "content_block_delta",
                {"type": "content_block_delta", "index": index, "delta": delta},
            )
        )
        out.append(_sse("content_block_stop", {"type": "content_block_stop", "index": index}))
    out.append(
        _sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
                "usage": {"output_tokens": message["usage"]["output_tokens"]},
            },
        )
    )
    out.append(_sse("message_stop", {"type": "message_stop"}))
    return b"".join(out)


class AnthropicMessagesStub:
    """``POST /v1/messages`` on 127.0.0.1, streaming or not, from a ``ModelScript``."""

    def __init__(self, script: ModelScript) -> None:
        self.script = script
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:  # silence the default stderr log
                return

            def do_POST(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's contract
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not self.path.rstrip("/").endswith("/messages"):
                    self._reply(404, b'{"type":"error","error":{"type":"not_found_error"}}')
                    return
                tools = [t.get("name", "") for t in body.get("tools") or []]
                if tools:
                    turn = stub.script.take(
                        RecordedRequest(
                            transport="anthropic_messages",
                            tools=tools,
                            tool_results=_anthropic_tool_results(body),
                            body=body,
                        ),
                        body.get("messages"),
                    )
                else:
                    stub.script.note_side_call()
                    turn = ScriptedTurn(text=SIDE_CALL_TEXT)
                message = _anthropic_message(stub.script, turn, str(body.get("model") or ""))
                if body.get("stream"):
                    self._reply(200, _anthropic_stream(message), "text/event-stream")
                else:
                    self._reply(200, json.dumps(message).encode())

            def _reply(self, status: int, payload: bytes, ctype: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(payload)))
                self.send_header("request-id", "req_e2e")
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="e2e-model-stub", daemon=True
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        """The ``model.base_url`` a seat config carries. The ``/anthropic`` path
        is load-bearing: Hermes honours a configured base_url for native
        Anthropic only when it plausibly speaks the Messages protocol
        (``_anthropic_base_url_override_ok`` at the pin), and otherwise falls
        back to ``https://api.anthropic.com``. Without it the first probe went
        to the real API (and was refused for the fake key)."""
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/anthropic"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


# --------------------------------------------------------------------------
# OpenAI chat completions (Hermes' default transport), patched the way
# Hermes' own tests patch it.
# --------------------------------------------------------------------------


def _chat_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in reversed(messages):
        if msg.get("role") == "tool":
            content = msg.get("content")
            if isinstance(content, list):
                content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
            out.insert(0, {"tool_use_id": msg.get("tool_call_id"), "content": str(content or "")})
            continue
        break
    return out


def _chat_completion(script: ModelScript, turn: ScriptedTurn, model: str) -> SimpleNamespace:
    tool_calls = [
        SimpleNamespace(
            id=script.next_id("call_e2e"),
            type="function",
            function=SimpleNamespace(name=name, arguments=json.dumps(args)),
        )
        for name, args in turn.tool_calls
    ] or None
    message = SimpleNamespace(
        role="assistant",
        content=turn.text or None,
        tool_calls=tool_calls,
        refusal=None,
        reasoning=None,
        reasoning_content=None,
    )
    return SimpleNamespace(
        id=script.next_id("chatcmpl_e2e"),
        model=model,
        object="chat.completion",
        choices=[
            SimpleNamespace(
                index=0,
                message=message,
                finish_reason="tool_calls" if tool_calls else "stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=50,
            completion_tokens=10,
            total_tokens=60,
            prompt_tokens_details=None,
            completion_tokens_details=None,
        ),
    )


def _chat_chunks(completion: SimpleNamespace) -> list[SimpleNamespace]:
    """The same completion as a stream: one content chunk, one chunk per tool
    call (whole arguments), and a final chunk carrying finish_reason + usage."""
    choice = completion.choices[0]
    msg = choice.message
    chunks: list[SimpleNamespace] = []

    def chunk(delta: SimpleNamespace, finish: str | None = None, usage: Any = None):
        return SimpleNamespace(
            id=completion.id,
            model=completion.model,
            object="chat.completion.chunk",
            choices=[SimpleNamespace(index=0, delta=delta, finish_reason=finish)],
            usage=usage,
        )

    if msg.content:
        chunks.append(
            chunk(SimpleNamespace(role="assistant", content=msg.content, tool_calls=None))
        )
    for index, call in enumerate(msg.tool_calls or []):
        chunks.append(
            chunk(
                SimpleNamespace(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=index,
                            id=call.id,
                            type="function",
                            function=SimpleNamespace(
                                name=call.function.name, arguments=call.function.arguments
                            ),
                        )
                    ],
                )
            )
        )
    chunks.append(
        chunk(
            SimpleNamespace(role=None, content=None, tool_calls=None),
            finish=choice.finish_reason,
            usage=completion.usage,
        )
    )
    return chunks


class _Stream:
    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self._chunks = chunks
        self.response = SimpleNamespace(headers={}, status_code=200)

    def __iter__(self):
        return iter(self._chunks)

    def close(self) -> None:
        return

    def __enter__(self):
        return self

    def __exit__(self, *_exc: Any) -> None:
        return


class FakeOpenAIClient:
    """Constructed in place of ``openai.OpenAI(...)``; answers from the script."""

    def __init__(self, script: ModelScript, **kwargs: Any) -> None:
        self.script = script
        self.api_key = kwargs.get("api_key", "e2e")
        self.base_url = kwargs.get("base_url", "http://127.0.0.1:1/v1")
        self._client_kwargs = kwargs
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.is_closed = False

    def _create(self, **kwargs: Any) -> Any:
        tools = [
            (t.get("function") or {}).get("name", "")
            for t in kwargs.get("tools") or []
            if isinstance(t, dict)
        ]
        model = str(kwargs.get("model") or "")
        if tools:
            turn = self.script.take(
                RecordedRequest(
                    transport="chat_completions",
                    tools=tools,
                    tool_results=_chat_tool_results(list(kwargs.get("messages") or [])),
                    body=kwargs,
                ),
                kwargs.get("messages"),
            )
        else:
            self.script.note_side_call()
            turn = ScriptedTurn(text=SIDE_CALL_TEXT)
        completion = _chat_completion(self.script, turn, model)
        if kwargs.get("stream"):
            return _Stream(_chat_chunks(completion))
        return completion

    def close(self) -> None:
        self.is_closed = True

    def with_options(self, **_kwargs: Any) -> FakeOpenAIClient:
        return self

    def copy(self, **_kwargs: Any) -> FakeOpenAIClient:
        return self
