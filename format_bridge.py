"""
format_bridge.py – Anthropic ↔ OpenAI format translator.

Converts Anthropic /v1/messages requests to OpenAI /v1/chat/completions and
translates responses back, including full streaming SSE translation.

This module is intentionally standalone: no network I/O, no imports from the
proxy, and no knowledge of the WAF bypass. Text post-processing (undoing the
Cyrillic homoglyph swap) is injected by the caller via `decoder_factory`, so
this file stays unit-testable on its own.
"""

from __future__ import annotations

import json
import uuid
from typing import Callable, Generator, Protocol


class TextDecoder(Protocol):
    """Stateful text filter applied to streamed fragments (see WafStreamDecoder)."""

    def feed(self, text: str) -> str: ...

    def flush(self) -> str: ...


DecoderFactory = Callable[[], TextDecoder]


class _NullDecoder:
    """Pass-through decoder used when the caller injects nothing."""

    def feed(self, text: str) -> str:
        return text

    def flush(self) -> str:
        return ""


# ---------------------------------------------------------------------------
# Request: Anthropic → OpenAI
# ---------------------------------------------------------------------------

def anthropic_to_openai(body: dict, target_model: str = "gpt-5.6-sol") -> dict:
    """Convert an Anthropic /v1/messages body to OpenAI /v1/chat/completions format."""
    messages: list[dict] = []

    # System prompt → OpenAI system message
    system = body.get("system")
    if system:
        if isinstance(system, str):
            messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            text = "\n".join(
                b.get("text", "") for b in system if b.get("type") == "text"
            )
            if text:
                messages.append({"role": "system", "content": text})

    for msg in body.get("messages", []):
        translated = _translate_message(msg)
        if isinstance(translated, list):
            messages.extend(translated)
        elif translated is not None:
            messages.append(translated)

    result: dict = {
        "model": target_model,
        "messages": messages,
        "stream": body.get("stream", False),
    }

    if "max_tokens" in body:
        result["max_tokens"] = body["max_tokens"]

    for key in ("temperature", "top_p"):
        if key in body:
            result[key] = body[key]

    if "stop_sequences" in body:
        result["stop"] = body["stop_sequences"]

    if "tools" in body:
        result["tools"] = [_translate_tool_def(t) for t in body["tools"]]

    if "tool_choice" in body:
        result["tool_choice"] = _translate_tool_choice(body["tool_choice"])

    return result


def _translate_message(msg: dict) -> dict | list[dict] | None:
    role = msg.get("role", "user")
    content = msg.get("content")

    if isinstance(content, str):
        return {"role": role, "content": content}

    if not isinstance(content, list):
        return None

    text_parts: list[dict] = []
    tool_use_parts: list[dict] = []
    tool_result_parts: list[dict] = []

    for block in content:
        btype = block.get("type")

        if btype == "text":
            text_parts.append({"type": "text", "text": block.get("text", "")})

        elif btype == "image":
            source = block.get("source", {})
            if source.get("type") == "base64":
                url = f"data:{source['media_type']};base64,{source['data']}"
            elif source.get("type") == "url":
                url = source["url"]
            else:
                continue
            text_parts.append({"type": "image_url", "image_url": {"url": url}})

        elif btype in ("thinking", "redacted_thinking"):
            thinking_text = block.get("thinking", "")
            if thinking_text:
                text_parts.append({"type": "text", "text": f"[Thinking: {thinking_text}]"})

        elif btype == "tool_use":
            tool_use_parts.append({
                "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {})),
                },
            })

        elif btype == "tool_result":
            tool_result_parts.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id", ""),
                "content": _flatten_tool_result(block.get("content", "")),
            })

    if tool_result_parts:
        # A single Anthropic user turn can carry tool results *and* new text
        # (Claude Code does this on every follow-up). The text must survive as
        # its own message, otherwise the user's actual instruction is dropped.
        trailing = _pack_parts(role, text_parts)
        return tool_result_parts + ([trailing] if trailing else [])

    if tool_use_parts and role == "assistant":
        text_content = " ".join(
            p["text"] for p in text_parts if p.get("type") == "text"
        ) or None
        return {"role": "assistant", "content": text_content, "tool_calls": tool_use_parts}

    return _pack_parts(role, text_parts)


def _pack_parts(role: str, text_parts: list[dict]) -> dict | None:
    """Collapse translated content parts into one OpenAI message (or None)."""
    if not text_parts:
        return None
    if len(text_parts) == 1 and text_parts[0].get("type") == "text":
        return {"role": role, "content": text_parts[0]["text"]}
    return {"role": role, "content": text_parts}


def _flatten_tool_result(tr_content: object) -> str:
    """Anthropic allows a tool_result body to be a string or a block list."""
    if isinstance(tr_content, list):
        return "\n".join(
            b.get("text", "")
            for b in tr_content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    if isinstance(tr_content, str):
        return tr_content
    return json.dumps(tr_content, ensure_ascii=False)


def _translate_tool_def(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {}),
        },
    }


def _translate_tool_choice(tc: dict | str) -> str | dict:
    if isinstance(tc, str):
        return tc
    tc_type = tc.get("type")
    if tc_type == "any":
        return "required"
    if tc_type == "none":
        return "none"
    if tc_type == "tool":
        return {"type": "function", "function": {"name": tc.get("name", "")}}
    return "auto"  # "auto" and unknown


# ---------------------------------------------------------------------------
# Response: OpenAI → Anthropic (non-streaming)
# ---------------------------------------------------------------------------

def openai_to_anthropic_response(
    oai: dict,
    original_model: str,
    decoder_factory: DecoderFactory | None = None,
) -> dict:
    choice = (oai.get("choices") or [{}])[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason") or "stop"
    usage = oai.get("usage", {})

    return {
        "id": oai.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
        "type": "message",
        "role": "assistant",
        "content": _extract_content_blocks(message, decoder_factory),
        "model": original_model,
        "stop_reason": _map_finish_reason(finish_reason),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def _map_finish_reason(reason: str) -> str:
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
        "content_filter": "stop_sequence",
    }.get(reason, "end_turn")


def _decode_once(text: str, decoder_factory: DecoderFactory | None) -> str:
    if not decoder_factory or not text:
        return text
    decoder = decoder_factory()
    return decoder.feed(text) + decoder.flush()


def _extract_content_blocks(
    message: dict,
    decoder_factory: DecoderFactory | None = None,
) -> list[dict]:
    blocks: list[dict] = []
    if message.get("content"):
        blocks.append({
            "type": "text",
            "text": _decode_once(message["content"], decoder_factory),
        })
    for tc in message.get("tool_calls") or []:
        raw_args = tc.get("function", {}).get("arguments", "{}")
        try:
            args = json.loads(_decode_once(raw_args, decoder_factory))
        except (json.JSONDecodeError, TypeError):
            args = {}
        blocks.append({
            "type": "tool_use",
            "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
            "name": tc.get("function", {}).get("name", ""),
            "input": args,
        })
    return blocks


# ---------------------------------------------------------------------------
# Response: OpenAI → Anthropic (streaming SSE)
# ---------------------------------------------------------------------------

def _sse(event_type: str, data: dict) -> bytes:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


class StreamingBridge:
    """
    Stateful translator: OpenAI SSE byte stream → Anthropic SSE byte stream.

    Usage:
        bridge = StreamingBridge("claude-opus-5")
        async for chunk in upstream_response.aiter_bytes():
            for event_bytes in bridge.feed(chunk):
                yield event_bytes
        for event_bytes in bridge.finalize():
            yield event_bytes

    `decoder_factory` builds one independent text filter per content block, so
    a chunk-split character in the text stream cannot leak into a tool-call
    argument stream.
    """

    def __init__(
        self,
        original_model: str = "claude-opus-5",
        decoder_factory: DecoderFactory | None = None,
    ) -> None:
        self.original_model = original_model
        self._decoder_factory = decoder_factory or _NullDecoder
        self._buf = b""
        self._msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        self._started = False
        self._next_index = 0
        self._text_index: int | None = None
        self._text_decoder: TextDecoder | None = None
        self._tool_calls: dict[int, dict] = {}   # oai index → {id, name, index, decoder}
        self._input_tokens = 0
        self._output_tokens = 0
        self._finish_reason: str | None = None

    def _alloc_index(self) -> int:
        idx = self._next_index
        self._next_index += 1
        return idx

    def feed(self, chunk: bytes) -> Generator[bytes, None, None]:
        self._buf += chunk
        while True:
            sep = self._buf.find(b"\n\n")
            if sep == -1:
                break
            raw, self._buf = self._buf[:sep], self._buf[sep + 2:]
            for line in raw.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    yield from self._handle_data(line[5:].strip())

    def _handle_data(self, payload: str) -> Generator[bytes, None, None]:
        if payload == "[DONE]":
            return
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            return
        if not isinstance(chunk, dict):
            return

        # Emit message_start once
        if not self._started:
            self._started = True
            usage = chunk.get("usage") or {}
            self._input_tokens = usage.get("prompt_tokens", 0)
            yield _sse("message_start", {
                "type": "message_start",
                "message": {
                    "id": self._msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": self.original_model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": self._input_tokens, "output_tokens": 1},
                },
            })
            yield _sse("ping", {"type": "ping"})

        choices = chunk.get("choices") or []
        if not choices:
            # Usage-only chunk (some providers send this at the end)
            u = chunk.get("usage") or {}
            if u:
                self._input_tokens = u.get("prompt_tokens", self._input_tokens)
                self._output_tokens = u.get("completion_tokens", self._output_tokens)
            return

        choice = choices[0]
        delta = choice.get("delta") or {}
        finish_reason = choice.get("finish_reason")

        # --- Text delta ---
        text = delta.get("content")
        if text:
            if self._text_index is None:
                self._text_index = self._alloc_index()
                self._text_decoder = self._decoder_factory()
                yield _sse("content_block_start", {
                    "type": "content_block_start",
                    "index": self._text_index,
                    "content_block": {"type": "text", "text": ""},
                })
            self._output_tokens += 1
            assert self._text_decoder is not None
            decoded = self._text_decoder.feed(text)
            if decoded:
                yield _sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": self._text_index,
                    "delta": {"type": "text_delta", "text": decoded},
                })

        # --- Tool call deltas ---
        for tc_delta in delta.get("tool_calls") or []:
            oai_idx = tc_delta.get("index", 0)

            if oai_idx not in self._tool_calls:
                tc_id = tc_delta.get("id") or f"toolu_{uuid.uuid4().hex[:24]}"
                tc_name = (tc_delta.get("function") or {}).get("name", "")
                self._tool_calls[oai_idx] = {
                    "id": tc_id,
                    "name": tc_name,
                    "index": self._alloc_index(),
                    "decoder": self._decoder_factory(),
                }
                yield _sse("content_block_start", {
                    "type": "content_block_start",
                    "index": self._tool_calls[oai_idx]["index"],
                    "content_block": {
                        "type": "tool_use",
                        "id": tc_id,
                        "name": tc_name,
                        "input": {},
                    },
                })

            state = self._tool_calls[oai_idx]
            fn = tc_delta.get("function") or {}
            if fn.get("name"):
                state["name"] = fn["name"]
            if fn.get("arguments"):
                decoded = state["decoder"].feed(fn["arguments"])
                if decoded:
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": state["index"],
                        "delta": {"type": "input_json_delta", "partial_json": decoded},
                    })

        # Accumulate usage
        u = chunk.get("usage") or {}
        if u:
            self._input_tokens = u.get("prompt_tokens", self._input_tokens)
            self._output_tokens = u.get("completion_tokens", self._output_tokens)

        if finish_reason:
            self._finish_reason = finish_reason

    def finalize(self) -> Generator[bytes, None, None]:
        """Emit closing Anthropic SSE events after upstream stream ends."""
        if not self._started:
            yield _sse("message_start", {
                "type": "message_start",
                "message": {
                    "id": self._msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": self.original_model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            })

        if self._text_index is not None:
            # Flush whatever the decoder held back for right-hand context.
            tail = self._text_decoder.flush() if self._text_decoder else ""
            if tail:
                yield _sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": self._text_index,
                    "delta": {"type": "text_delta", "text": tail},
                })
            yield _sse("content_block_stop", {
                "type": "content_block_stop",
                "index": self._text_index,
            })

        for state in sorted(self._tool_calls.values(), key=lambda s: s["index"]):
            tail = state["decoder"].flush()
            if tail:
                yield _sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": state["index"],
                    "delta": {"type": "input_json_delta", "partial_json": tail},
                })
            yield _sse("content_block_stop", {
                "type": "content_block_stop",
                "index": state["index"],
            })

        stop_reason = _map_finish_reason(self._finish_reason or "stop")
        yield _sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": self._output_tokens},
        })
        yield _sse("message_stop", {"type": "message_stop"})
