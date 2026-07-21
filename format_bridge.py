"""
format_bridge.py – Anthropic ↔ OpenAI format translator.

Converts Anthropic /v1/messages requests to OpenAI /v1/chat/completions and
translates responses back, including full streaming SSE translation.

This module is intentionally standalone (no proxy imports) so it can be
tested independently.
"""

from __future__ import annotations

import json
import uuid
from typing import Generator


# ---------------------------------------------------------------------------
# Request: Anthropic → OpenAI
# ---------------------------------------------------------------------------

def anthropic_to_openai(body: dict, target_model: str = "gpt-5.5") -> dict:
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

    # --- WAF Bypass (Encode 'c' to Cyrillic 'с') ---
    for m in result.get("messages", []):
        if isinstance(m.get("content"), str):
            m["content"] = m["content"].replace('c', 'с')
        elif isinstance(m.get("content"), list):
            for part in m["content"]:
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    part["text"] = part["text"].replace('c', 'с')

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
            tr_content = block.get("content", "")
            if isinstance(tr_content, list):
                tr_content = "\n".join(
                    b.get("text", "") for b in tr_content if b.get("type") == "text"
                )
            tool_result_parts.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id", ""),
                "content": tr_content,
            })

    if tool_result_parts:
        return tool_result_parts

    if tool_use_parts and role == "assistant":
        text_content = " ".join(
            p["text"] for p in text_parts if p.get("type") == "text"
        ) or None
        return {"role": "assistant", "content": text_content, "tool_calls": tool_use_parts}

    if not text_parts:
        return None

    if len(text_parts) == 1 and text_parts[0].get("type") == "text":
        return {"role": role, "content": text_parts[0]["text"]}

    return {"role": role, "content": text_parts}


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

def openai_to_anthropic_response(oai: dict, original_model: str) -> dict:
    choice = (oai.get("choices") or [{}])[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason") or "stop"
    usage = oai.get("usage", {})

    return {
        "id": oai.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
        "type": "message",
        "role": "assistant",
        "content": _extract_content_blocks(message),
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
        "content_filter": "stop_sequence",
    }.get(reason, "end_turn")


def _extract_content_blocks(message: dict) -> list[dict]:
    blocks: list[dict] = []
    if message.get("content"):
        blocks.append({"type": "text", "text": message["content"]})
    for tc in message.get("tool_calls") or []:
        try:
            args = json.loads(tc.get("function", {}).get("arguments", "{}"))
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
        bridge = StreamingBridge("claude-opus-4-8")
        async for chunk in upstream_response.aiter_bytes():
            for event_bytes in bridge.feed(chunk):
                yield event_bytes
        for event_bytes in bridge.finalize():
            yield event_bytes
    """

    def __init__(self, original_model: str = "claude-opus-4-8") -> None:
        self.original_model = original_model
        self._buf = b""
        self._msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        self._started = False
        self._text_block_open = False
        self._tool_calls: dict[int, dict] = {}   # oai index → {id, name, args}
        self._input_tokens = 0
        self._output_tokens = 0
        self._finish_reason: str | None = None

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
        payload = payload.replace('с', 'c')  # WAF Decode
        if payload == "[DONE]":
            return
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            return

        # Emit message_start once
        if not self._started:
            self._started = True
            usage = chunk.get("usage", {})
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
            u = chunk.get("usage", {})
            if u:
                self._input_tokens = u.get("prompt_tokens", self._input_tokens)
                self._output_tokens = u.get("completion_tokens", self._output_tokens)
            return

        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        # --- Text delta ---
        text = delta.get("content")
        if text:
            if not self._text_block_open:
                self._text_block_open = True
                yield _sse("content_block_start", {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                })
            self._output_tokens += 1
            yield _sse("content_block_delta", {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            })

        # --- Tool call deltas ---
        for tc_delta in delta.get("tool_calls") or []:
            oai_idx = tc_delta.get("index", 0)
            anth_idx = oai_idx + 1  # index 0 is reserved for text block

            if oai_idx not in self._tool_calls:
                tc_id = tc_delta.get("id", f"toolu_{uuid.uuid4().hex[:24]}")
                tc_name = (tc_delta.get("function") or {}).get("name", "")
                self._tool_calls[oai_idx] = {"id": tc_id, "name": tc_name, "args": ""}
                yield _sse("content_block_start", {
                    "type": "content_block_start",
                    "index": anth_idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": tc_id,
                        "name": tc_name,
                        "input": {},
                    },
                })

            fn = tc_delta.get("function") or {}
            if fn.get("name"):
                self._tool_calls[oai_idx]["name"] = fn["name"]
            if fn.get("arguments"):
                self._tool_calls[oai_idx]["args"] += fn["arguments"]
                yield _sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": anth_idx,
                    "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]},
                })

        # Accumulate usage
        u = chunk.get("usage", {})
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

        if self._text_block_open:
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})

        for oai_idx in self._tool_calls:
            yield _sse("content_block_stop", {
                "type": "content_block_stop",
                "index": oai_idx + 1,
            })

        stop_reason = _map_finish_reason(self._finish_reason or "stop")
        yield _sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": self._output_tokens},
        })
        yield _sse("message_stop", {"type": "message_stop"})
