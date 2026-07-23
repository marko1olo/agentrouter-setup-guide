#!/usr/bin/env python3
"""
Локальный асинхронный прокси для agentrouter.org.

Задача:
  - Перехватывать запросы локальных ИИ-агентов (Cline в VS Code, Claude Code CLI и т.п.)
    на http://127.0.0.1:8318 и прозрачно проксировать их на https://agentrouter.org.
  - Маскироваться под разрешённого клиента (codex_cli_rs), чтобы обойти WAF.
  - Отдавать Server-Sent Events (SSE) стриминг клиенту чанк-в-чанк без буферизации.
  - Быть отказоустойчивым: не падать при таймаутах, обрывах связи (BrokenPipe),
    504/500 от целевого сервера и т.д.

Стек: FastAPI + httpx (async) + uvicorn.

Запуск:
    python3 agentrouter_proxy.py
или:
    uvicorn agentrouter_proxy:app --host 127.0.0.1 --port 8318
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import codecs
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

# --------------------------------------------------------------------------- #
# Конфигурация
# --------------------------------------------------------------------------- #

UPSTREAM_BASE = os.environ.get("AGENTROUTER_UPSTREAM", "https://agentrouter.org").rstrip("/")
LISTEN_HOST = os.environ.get("AGENTROUTER_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("AGENTROUTER_PORT", "8318"))

# Заголовки маскировки под разрешённого клиента (обход WAF).
MASK_USER_AGENT = os.environ.get("AGENTROUTER_UA", "codex_cli_rs/0.101.0")
MASK_ORIGINATOR = os.environ.get("AGENTROUTER_ORIGINATOR", "codex_cli_rs")

# Прозрачный авто-retry при gateway-ошибках (502/503/504/522/524).
RETRY_ON_GATEWAY_ERRORS = os.environ.get("AGENTROUTER_RETRY_GATEWAY", "true").lower() in ("true", "1", "yes")
RETRY_MAX_ATTEMPTS = int(os.environ.get("AGENTROUTER_RETRY_MAX", "2"))
RETRY_BACKOFF_BASE = float(os.environ.get("AGENTROUTER_RETRY_BACKOFF", "2.0"))

# HTTP-статусы шлюза, при которых имеет смысл прозрачно повторить запрос.
RETRYABLE_STATUS_CODES = {502, 503, 504, 522, 524}

# Только идемпотентные (в контексте LLM-чата) методы повторяем автоматически.
RETRYABLE_METHODS = {"GET", "HEAD", "POST"}

# --------------------------------------------------------------------------- #
# Мост Anthropic → OpenAI
# --------------------------------------------------------------------------- #

# BRIDGE_ENABLED=true:  /v1/messages переводится в /v1/chat/completions (gpt-5.5 и т.п.)
# BRIDGE_ENABLED=false: /v1/messages проксируется напрямую с WAF-байпасом
BRIDGE_ENABLED = os.environ.get("AGENTROUTER_BRIDGE", "false").lower() in ("true", "1", "yes")

# Целевая модель, которую мост будет использовать на стороне AgentRouter.
BRIDGE_TARGET_MODEL = os.environ.get("AGENTROUTER_BRIDGE_MODEL", "gpt-5.5")

# Таймауты httpx.
# ВАЖНО: read=None (без таймаута на чтение), чтобы долгие "рассуждения" модели
# (adaptive thinking) не обрывались клиентским прокси раньше времени.
HTTPX_TIMEOUT = httpx.Timeout(
    connect=float(os.environ.get("AGENTROUTER_CONNECT_TIMEOUT", "30")),
    write=float(os.environ.get("AGENTROUTER_WRITE_TIMEOUT", "60")),
    read=None,      # без таймаута на чтение потока
    pool=None,
)

# Заголовки запроса клиента, которые НЕ пробрасываем на upstream.
DROP_REQUEST_HEADERS = {
    "host",
    "content-length",
    "transfer-encoding",
    "user-agent",
    "accept-encoding",
    "originator",
    "connection",
    "proxy-connection",
    "keep-alive",
}

# Заголовки ответа upstream, которые НЕ пробрасываем клиенту.
DROP_RESPONSE_HEADERS = {
    "content-length",
    "transfer-encoding",
    "connection",
    "proxy-connection",
    "keep-alive",
}

logging.basicConfig(
    level=os.environ.get("AGENTROUTER_LOGLEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("agentrouter-proxy")

# Ленивый импорт моста (не ломает запуск если файл отсутствует)
try:
    from format_bridge import (
        StreamingBridge,
        anthropic_to_openai,
        openai_to_anthropic_response,
    )
    _BRIDGE_MODULE_OK = True
except ImportError:
    _BRIDGE_MODULE_OK = False
    log.warning("[BRIDGE] format_bridge.py не найден — мост отключён")

# --------------------------------------------------------------------------- #
# Приложение
# --------------------------------------------------------------------------- #

_client: httpx.AsyncClient | None = None


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Инициализация/закрытие общего httpx-клиента."""
    global _client
    _client = httpx.AsyncClient(
        timeout=HTTPX_TIMEOUT,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        http2=False,
    )
    log.info("Локальный прокси AgentRouter запущен на http://%s:%s -> %s",
             LISTEN_HOST, LISTEN_PORT, UPSTREAM_BASE)
    try:
        yield
    finally:
        if _client is not None:
            await _client.aclose()
            _client = None


app = FastAPI(
    title="AgentRouter Local Proxy",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


def _build_upstream_headers(request: Request) -> dict[str, str]:
    """Собирает заголовки для запроса на upstream, применяя маскировку."""
    headers: dict[str, str] = {}
    for key, val in request.headers.items():
        if key.lower() in DROP_REQUEST_HEADERS:
            continue
        headers[key] = val

    # Маскируемся под разрешённого клиента.
    headers["User-Agent"] = MASK_USER_AGENT
    headers["Originator"] = MASK_ORIGINATOR
    headers["Accept-Encoding"] = "identity"
    return headers


def _is_latin1(value: str) -> bool:
    try:
        value.encode("latin-1")
        return True
    except UnicodeEncodeError:
        return False


def _filter_response_headers(upstream_headers: httpx.Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, val in upstream_headers.items():
        if key.lower() in DROP_RESPONSE_HEADERS:
            continue
        if not (_is_latin1(key) and _is_latin1(val)):
            log.debug("[HEADERS] пропущен не-latin1 заголовок: %r", key)
            continue
        out[key] = val
    return out


@app.options("/{full_path:path}")
async def preflight(full_path: str) -> Response:
    return Response(status_code=204)


async def _open_upstream_stream(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
) -> httpx.Response | JSONResponse:
    assert _client is not None

    retry_enabled = RETRY_ON_GATEWAY_ERRORS and method.upper() in RETRYABLE_METHODS
    max_attempts = RETRY_MAX_ATTEMPTS if retry_enabled else 1
    last_error_status = 502
    last_error_msg = "upstream unavailable"

    for attempt in range(1, max_attempts + 1):
        upstream_req = _client.build_request(
            method=method,
            url=url,
            headers=headers,
            content=body if body else None,
        )

        try:
            upstream_resp = await _client.send(upstream_req, stream=True)
        except httpx.TimeoutException as exc:
            last_error_status, last_error_msg = 504, f"Upstream timeout: {exc}"
            log.warning("[UPSTREAM] timeout (попытка %d/%d): %s", attempt, max_attempts, exc)
        except httpx.RequestError as exc:
            last_error_status, last_error_msg = 502, f"Upstream request error: {exc}"
            log.warning("[UPSTREAM] request error (попытка %d/%d): %s", attempt, max_attempts, exc)
        else:
            if retry_enabled and upstream_resp.status_code in RETRYABLE_STATUS_CODES and attempt < max_attempts:
                status = upstream_resp.status_code
                await upstream_resp.aclose()
                delay = RETRY_BACKOFF_BASE * attempt
                log.warning(
                    "[UPSTREAM] %s от сервера (попытка %d/%d) — повтор через %.1fс",
                    status, attempt, max_attempts, delay,
                )
                await asyncio.sleep(delay)
                continue
            if attempt > 1:
                log.info("[UPSTREAM] успех со %d-й попытки, статус %d", attempt, upstream_resp.status_code)
            return upstream_resp

        if attempt < max_attempts:
            delay = RETRY_BACKOFF_BASE * attempt
            await asyncio.sleep(delay)

    return JSONResponse(
        status_code=last_error_status,
        content={
            "message": last_error_msg,
            "status": last_error_status,
            "proxy": "agentrouter_proxy",
            "attempts": max_attempts,
        },
    )


@app.post("/v1/messages")
async def messages_bridge(request: Request) -> Response:
    """
    Мост Anthropic /v1/messages -> OpenAI /v1/chat/completions (если BRIDGE_ENABLED=true)
    ИЛИ
    Прямой прокси для Anthropic /v1/messages с WAF-байпасом (если BRIDGE_ENABLED=false)
    """
    import json as _json

    body_bytes = await request.body()
    try:
        anth_body = _json.loads(body_bytes)
    except _json.JSONDecodeError:
        return await proxy("v1/messages", request)

    # --- WAF Bypass: Кодируем английские 'c' -> русские 'с' перед отправкой ---
    if "system" in anth_body:
        if isinstance(anth_body["system"], str):
            anth_body["system"] = anth_body["system"].replace('c', 'с')
        elif isinstance(anth_body["system"], list):
            for p in anth_body["system"]:
                if p.get("type") == "text" and isinstance(p.get("text"), str):
                    p["text"] = p["text"].replace('c', 'с')

    for m in anth_body.get("messages", []):
        content = m.get("content")
        if isinstance(content, str):
            m["content"] = content.replace('c', 'с')
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    part["text"] = part["text"].replace('c', 'с')

    original_model = anth_body.get("model", "claude-opus-4-8")
    is_streaming = anth_body.get("stream", False)

    # 1. Если включен МОСТ на OpenAI (gpt-5.5 / glm-5.2)
    if BRIDGE_ENABLED and _BRIDGE_MODULE_OK:
        log.info("[BRIDGE] %s -> %s (stream=%s)", original_model, BRIDGE_TARGET_MODEL, is_streaming)
        oai_body = anthropic_to_openai(anth_body, target_model=BRIDGE_TARGET_MODEL)
        oai_bytes = _json.dumps(oai_body).encode()

        headers = _build_upstream_headers(request)
        headers["Content-Type"] = "application/json"
        for h in ("anthropic-version", "anthropic-beta", "x-api-key"):
            headers.pop(h, None)

        url = f"{UPSTREAM_BASE}/v1/chat/completions"
        result = await _open_upstream_stream("POST", url, headers, oai_bytes)
        if isinstance(result, JSONResponse):
            return result
        upstream_resp = result

        if is_streaming:
            bridge = StreamingBridge(original_model=original_model)
            async def anthropic_sse_stream():
                try:
                    async for chunk in upstream_resp.aiter_bytes():
                        for event in bridge.feed(chunk):
                            yield event
                    for event in bridge.finalize():
                        yield event
                except Exception as exc:
                    log.warning("[BRIDGE] stream error: %s", exc)
                finally:
                    try:
                        await upstream_resp.aclose()
                    except Exception:
                        pass
            return StreamingResponse(
                anthropic_sse_stream(),
                status_code=200,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )
        else:
            raw = await upstream_resp.aread()
            await upstream_resp.aclose()
            try:
                raw_text = raw.decode("utf-8", errors="replace").replace("с", "c")
                oai_resp = _json.loads(raw_text)
                anth_resp = openai_to_anthropic_response(oai_resp, original_model)
                return JSONResponse(anth_resp, status_code=200)
            except Exception as exc:
                log.warning("[BRIDGE] response parse error: %s", exc)
                return Response(content=raw, status_code=upstream_resp.status_code, media_type="application/json")

    # 2. Если мост выключен -> Прямое Anthropic проксирование + WAF Bypass
    else:
        log.info("[PROXY] Direct Anthropic routing for %s (stream=%s)", original_model, is_streaming)
        anth_bytes = _json.dumps(anth_body).encode("utf-8")

        headers = _build_upstream_headers(request)
        url = f"{UPSTREAM_BASE}/v1/messages?beta=true"

        result = await _open_upstream_stream("POST", url, headers, anth_bytes)
        if isinstance(result, JSONResponse):
            return result
        upstream_resp = result

        if is_streaming:
            async def waf_decode_stream():
                decoder = codecs.getincrementaldecoder("utf-8")()
                try:
                    async for chunk in upstream_resp.aiter_bytes():
                        text = decoder.decode(chunk)
                        if text:
                            # Декодируем WAF: заменяем русскую 'с' обратно на английскую 'c'
                            yield text.replace("с", "c").encode("utf-8")
                    text = decoder.decode(b"", final=True)
                    if text:
                        yield text.replace("с", "c").encode("utf-8")
                except Exception as exc:
                    log.warning("[PROXY] stream error: %s", exc)
                finally:
                    try:
                        await upstream_resp.aclose()
                    except Exception:
                        pass

            return StreamingResponse(
                waf_decode_stream(),
                status_code=upstream_resp.status_code,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )
        else:
            raw = await upstream_resp.aread()
            await upstream_resp.aclose()
            try:
                # Декодируем WAF: заменяем русскую 'с' обратно на английскую 'c'
                raw_text = raw.decode("utf-8", errors="replace").replace("с", "c")
                return Response(content=raw_text.encode("utf-8"), status_code=upstream_resp.status_code, media_type="application/json")
            except Exception as exc:
                log.warning("[PROXY] parse error: %s", exc)
                return Response(content=raw, status_code=upstream_resp.status_code, media_type="application/json")


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
)
async def proxy(full_path: str, request: Request) -> Response:
    assert _client is not None

    query = request.url.query
    url = f"{UPSTREAM_BASE}/{full_path}"
    if query:
        url = f"{url}?{query}"

    method = request.method
    headers = _build_upstream_headers(request)
    body = await request.body()

    result = await _open_upstream_stream(method, url, headers, body)
    if isinstance(result, JSONResponse):
        return result
    upstream_resp = result

    resp_headers = _filter_response_headers(upstream_resp.headers)
    media_type = upstream_resp.headers.get("content-type")

    async def body_iterator():
        try:
            async for chunk in upstream_resp.aiter_raw():
                if chunk:
                    yield chunk
        except httpx.StreamClosed:
            log.debug("[STREAM] closed")
        except httpx.RequestError as exc:
            log.warning("[STREAM] upstream error: %s", exc)
        except (BrokenPipeError, ConnectionResetError, ConnectionError):
            log.debug("[STREAM] client disconnected")
        except Exception as exc:
            log.warning("[STREAM] error: %s", exc)
        finally:
            try:
                await upstream_resp.aclose()
            except Exception:
                pass

    return StreamingResponse(
        body_iterator(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        media_type=media_type,
    )


@app.get("/__proxy_health")
async def health() -> dict:
    return {"status": "ok", "upstream": UPSTREAM_BASE}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=LISTEN_HOST,
        port=LISTEN_PORT,
        log_level=os.environ.get("AGENTROUTER_LOGLEVEL", "info").lower(),
        access_log=False,
        timeout_keep_alive=75,
    )
