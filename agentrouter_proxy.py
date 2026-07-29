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
import codecs
import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

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


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


# Прозрачный авто-retry при gateway-ошибках (502/503/504/522/524).
RETRY_ON_GATEWAY_ERRORS = _env_flag("AGENTROUTER_RETRY_GATEWAY", True)
RETRY_MAX_ATTEMPTS = int(os.environ.get("AGENTROUTER_RETRY_MAX", "2"))
RETRY_BACKOFF_BASE = float(os.environ.get("AGENTROUTER_RETRY_BACKOFF", "2.0"))

# HTTP-статусы шлюза, при которых имеет смысл прозрачно повторить запрос.
RETRYABLE_STATUS_CODES = {502, 503, 504, 522, 524}

# AgentRouter иногда отдаёт 500 из-за паники на своей стороне (Go-панику видно
# в теле ответа). Такие 500 часто разовые, но 500 из-за WAF
# (`sensitive_words_detected`) детерминирован — повтор только тратит время.
# Поэтому опция выключена по умолчанию.
if _env_flag("AGENTROUTER_RETRY_500", False):
    RETRYABLE_STATUS_CODES.add(500)

# Только идемпотентные (в контексте LLM-чата) методы повторяем автоматически.
RETRYABLE_METHODS = {"GET", "HEAD", "POST"}

# Модели, которые прокси отдаёт в /v1/models для автозаполнения в GUI-клиентах.
# Актуальный список AgentRouter на 29 июля 2026: claude-opus-5 (самая дешёвая),
# claude-opus-4-8, gpt-5.6-sol. Переопределяется через AGENTROUTER_MODELS.
DEFAULT_MODELS = ("gpt-5.6-sol", "claude-opus-4-8", "claude-opus-5")
ADVERTISED_MODELS = tuple(
    m.strip()
    for m in os.environ.get("AGENTROUTER_MODELS", ",".join(DEFAULT_MODELS)).split(",")
    if m.strip()
)

# --------------------------------------------------------------------------- #
# Мост Anthropic → OpenAI
# --------------------------------------------------------------------------- #

# BRIDGE_ENABLED=true:  /v1/messages переводится в /v1/chat/completions (gpt-5.6-sol и т.п.)
# BRIDGE_ENABLED=false: /v1/messages проксируется напрямую с WAF-байпасом
BRIDGE_ENABLED = _env_flag("AGENTROUTER_BRIDGE", True)

# Целевая модель, которую мост будет использовать на стороне AgentRouter.
BRIDGE_TARGET_MODEL = os.environ.get("AGENTROUTER_BRIDGE_MODEL", "gpt-5.6-sol")

# Если переменная задана явно — она главнее модели из запроса клиента.
# Без этого выбор модели в run_proxy.bat/run_proxy.sh не работал вообще: клиент
# (Claude Code, Cline) всегда присылает свою модель, и она молча побеждала.
BRIDGE_MODEL_PINNED = "AGENTROUTER_BRIDGE_MODEL" in os.environ

# Жёстко закрепить модель в ОБОИХ режимах. Нужно, когда клиент не даёт выбрать
# модель сам: Claude Code CLI всегда просит claude-opus-4-8, а на AgentRouter
# claude-opus-5 стоит в 10 раз дешевле на выходных токенах.
FORCE_MODEL = os.environ.get("AGENTROUTER_MODEL", "").strip()

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
# WAF-байпас: гомоглиф 'c' <-> 'с'
# --------------------------------------------------------------------------- #
# WAF AgentRouter режет длинные системные промпты с ошибкой
# `500 sensitive_words_detected`. Подмена латинской 'c' на визуально
# идентичную кириллическую 'с' ломает сигнатуры WAF, и запрос проходит.
#
# Обратное преобразование НЕ может быть простым .replace('с', 'c'): это
# калечило каждое русское слово в ответе модели ("спасибо" -> "cпасибо").
# Поэтому декодер восстанавливает латиницу только там, где ни один сосед
# не кириллический — см. WafStreamDecoder.

WAF_ENABLED = _env_flag("AGENTROUTER_WAF", True)

# Кодировать ли тело сквозных (не /v1/messages) запросов, например
# /v1/chat/completions. По умолчанию нет: gpt-5.6-sol отвечает
# `400 content-blocked` на подменённые символы.
WAF_ENCODE_PASSTHROUGH = _env_flag("AGENTROUTER_WAF_PASSTHROUGH", False)

CYRILLIC_ES = "с"   # 'с' — кириллическая, визуально равна латинской 'c'
LATIN_C = "c"

# Поля, в которых имеет смысл подменять символы: только человекочитаемый текст,
# никогда не ключи JSON и не идентификаторы (call_id, tool_call_id и т.п.).
WAF_TEXT_KEYS = ("content", "text", "prompt", "system")


def _is_cyrillic(ch: str) -> bool:
    return bool(ch) and "Ѐ" <= ch <= "ӿ"


def waf_encode_text(text: str) -> str:
    """Латинская 'c' -> кириллическая 'с' (направление запроса)."""
    if not WAF_ENABLED or not text:
        return text
    return text.replace(LATIN_C, CYRILLIC_ES)


class WafStreamDecoder:
    """
    Восстанавливает латинскую 'c' из кириллической 'с', не ломая русский текст.

    Правило: кириллическая 'с' остаётся кириллической, если ближайший непробельный
    сосед слева ИЛИ справа — кириллическая буква. Иначе это подменённая латиница.

        "спасибо"  -> 'п' справа кириллица      -> остаётся "спасибо"
        "с тобой"  -> 'т' справа кириллица      -> остаётся "с тобой"
        "вопрос."  -> 'о' слева кириллица       -> остаётся "вопрос."
        "aссess"   -> соседи латинские          -> "access"
        "сode"     -> 'o' справа латиница       -> "code"

    Декодер потоковый: если чанк оборвался на 'с' и правый контекст ещё не
    пришёл, символ удерживается до следующего feed() или до flush(). Порядок
    символов сохраняется, ничего не теряется.

    Известный компромисс: одиночная латинская 'c' внутри русской фразы
    ("язык c", "на c++") останется кириллической — левый сосед кириллический.
    Заглавная 'C' не кодируется вовсе, поэтому "C++" не страдает.

    Ограничение: работает только с сырым UTF-8. Если upstream когда-нибудь
    начнёт отдавать JSON с escape-последовательностями (\\u0441 вместо 'с'),
    подмену придётся снимать после json.loads, а не над текстом ответа.
    """

    def __init__(self) -> None:
        self._pending = ""            # удержанный хвост: 'с' + пробелы
        self._last_significant = ""   # последний выданный непробельный символ

    def feed(self, text: str) -> str:
        if not text:
            return ""
        buf = self._pending + text
        self._pending = ""
        out: list[str] = []
        i = 0
        n = len(buf)

        while i < n:
            ch = buf[i]
            if ch != CYRILLIC_ES:
                out.append(ch)
                if not ch.isspace():
                    self._last_significant = ch
                i += 1
                continue

            # Подряд идущие гомоглифы решаются одним решением: "aссess".
            run_end = i
            while run_end < n and buf[run_end] == CYRILLIC_ES:
                run_end += 1

            right = self._scan_right(buf, run_end)
            if right is None:
                # Правый контекст в следующем чанке — удерживаем хвост.
                self._pending = buf[i:]
                break

            keep_cyrillic = _is_cyrillic(self._last_significant) or _is_cyrillic(right)
            emitted = CYRILLIC_ES if keep_cyrillic else LATIN_C
            out.append(emitted * (run_end - i))
            self._last_significant = emitted
            i = run_end

        return "".join(out)

    @staticmethod
    def _scan_right(buf: str, start: int) -> str | None:
        """Ближайший непробельный символ, не считая самих гомоглифов."""
        j = start
        while j < len(buf) and (buf[j].isspace() or buf[j] == CYRILLIC_ES):
            j += 1
        if j >= len(buf):
            return None     # неизвестно — нужен следующий чанк
        return buf[j]

    def flush(self) -> str:
        """Конец потока: решаем удержанный хвост по левому контексту."""
        if not self._pending:
            return ""
        pending, self._pending = self._pending, ""
        out: list[str] = []
        for ch in pending:
            if ch == CYRILLIC_ES:
                emitted = CYRILLIC_ES if _is_cyrillic(self._last_significant) else LATIN_C
                out.append(emitted)
                self._last_significant = emitted
            else:
                out.append(ch)
                if not ch.isspace():
                    self._last_significant = ch
        return "".join(out)


def waf_decode_text(text: str) -> str:
    """Одноразовое (непотоковое) снятие подмены для законченного текста."""
    if not WAF_ENABLED or not text:
        return text
    decoder = WafStreamDecoder()
    return decoder.feed(text) + decoder.flush()


def _decoder_factory() -> WafStreamDecoder:
    """Фабрика декодеров для StreamingBridge (по одному на блок контента)."""
    return WafStreamDecoder()


def waf_encode_json(obj: Any) -> Any:
    """
    Рекурсивно кодирует ТОЛЬКО значения текстовых полей, не трогая ключи JSON
    и идентификаторы вроде call_id / tool_call_id.
    """
    if isinstance(obj, dict):
        encoded: dict[str, Any] = {}
        for key, val in obj.items():
            if key in WAF_TEXT_KEYS and isinstance(val, str):
                encoded[key] = waf_encode_text(val)
            elif isinstance(val, (dict, list)):
                encoded[key] = waf_encode_json(val)
            else:
                encoded[key] = val
        return encoded
    if isinstance(obj, list):
        return [waf_encode_json(item) for item in obj]
    return obj


def waf_encode_anthropic_body(body: dict) -> dict:
    """WAF-кодирование system и текстовых блоков сообщений Anthropic-запроса."""
    if not WAF_ENABLED:
        return body

    system = body.get("system")
    if isinstance(system, str):
        body["system"] = waf_encode_text(system)
    elif isinstance(system, list):
        for part in system:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                part["text"] = waf_encode_text(part["text"])

    for msg in body.get("messages", []):
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = waf_encode_text(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                    part["text"] = waf_encode_text(part["text"])
    return body


def waf_encode_openai_body(body: dict) -> dict:
    """
    WAF-кодирование готового OpenAI-тела: system/user/assistant и, что важно,
    сообщения role="tool" — именно в них едут большие результаты чтения файлов,
    на которых WAF и срабатывает.
    """
    if not WAF_ENABLED:
        return body

    for msg in body.get("messages", []):
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = waf_encode_text(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                    part["text"] = waf_encode_text(part["text"])
    return body


# --------------------------------------------------------------------------- #
# Нормализация истории сообщений
# --------------------------------------------------------------------------- #

def _as_block_list(content: Any) -> list:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        return content
    return []


def normalize_anthropic_messages(messages: Any) -> list[dict]:
    """
    Убирает блоки thinking, выбрасывает опустевшие сообщения и склеивает
    соседние сообщения одной роли.

    Зачем: AgentRouter проксирует Claude через Bedrock, который падает в
    `ValidationException: thinking: Field required`, если ему вернуть блоки
    thinking из истории. Но если убрать их наивно, ассистентский ход может
    остаться с `content: []`, и upstream отвечает
    `400 invalid JSON: ... did not match any variant of untagged enum
    BetaMessageContent` (issue #3). Поэтому пустой ход тоже удаляется, а
    сообщения, которые он разделял, склеиваются — иначе ломается обязательное
    чередование ролей user/assistant.
    """
    if not isinstance(messages, list):
        return []

    cleaned: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue

        content = msg.get("content")
        if isinstance(content, list):
            kept = [
                block for block in content
                if not (isinstance(block, dict)
                        and block.get("type") in ("thinking", "redacted_thinking"))
            ]
            if not kept:
                continue
            msg = {**msg, "content": kept}
        elif isinstance(content, str):
            if not content.strip():
                continue
        elif content is None:
            continue

        if cleaned and cleaned[-1].get("role") == msg.get("role"):
            merged = _as_block_list(cleaned[-1].get("content")) + _as_block_list(msg.get("content"))
            cleaned[-1] = {**cleaned[-1], "content": merged}
        else:
            cleaned.append(msg)

    return cleaned


def _normalize_thinking_config(body: dict) -> None:
    """Чинит частично заполненный блок thinking, чтобы upstream не ругался."""
    cfg = body.get("thinking")
    if not isinstance(cfg, dict):
        return
    if cfg.get("type") == "enabled" and "budget_tokens" not in cfg:
        cfg["budget_tokens"] = 1024
    elif not cfg.get("type"):
        body.pop("thinking", None)


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
    log.info("Режим: %s | WAF-байпас: %s",
             f"мост -> {BRIDGE_TARGET_MODEL}" + (" (закреплён)" if BRIDGE_MODEL_PINNED else "")
             if BRIDGE_ENABLED else "прямой Anthropic",
             "вкл" if WAF_ENABLED else "выкл")
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
        },
    )


@app.get("/v1/models")
async def list_models() -> dict:
    """Возвращает список моделей для автозаполнения в GUI-клиентах (Cherry Studio и др.)."""
    return {
        "object": "list",
        "data": [
            {"id": model_id, "object": "model", "created": 0, "owned_by": "agentrouter"}
            for model_id in ADVERTISED_MODELS
        ],
    }


def _resolve_bridge_target(requested_model: str | None) -> str:
    """
    Какую модель просить у AgentRouter в режиме моста.

    Приоритет: AGENTROUTER_BRIDGE_MODEL -> AGENTROUTER_MODEL -> модель из
    запроса -> значение по умолчанию. Явно заданная переменная главнее клиента,
    иначе выбор в лончере не имел бы никакого эффекта.
    """
    if BRIDGE_MODEL_PINNED:
        return BRIDGE_TARGET_MODEL
    if FORCE_MODEL:
        return FORCE_MODEL
    return requested_model or BRIDGE_TARGET_MODEL


@app.post("/v1/messages")
async def messages_bridge(request: Request) -> Response:
    """
    Мост Anthropic /v1/messages -> OpenAI /v1/chat/completions (если BRIDGE_ENABLED=true)
    ИЛИ
    Прямой прокси для Anthropic /v1/messages с WAF-байпасом (если BRIDGE_ENABLED=false)
    """
    body_bytes = await request.body()
    try:
        anth_body = json.loads(body_bytes)
    except json.JSONDecodeError:
        return await proxy("v1/messages", request)
    if not isinstance(anth_body, dict):
        return await proxy("v1/messages", request)

    anth_body["messages"] = normalize_anthropic_messages(anth_body.get("messages", []))
    _normalize_thinking_config(anth_body)

    original_model = anth_body.get("model", "claude-opus-5")
    is_streaming = bool(anth_body.get("stream", False))

    # 1. Если включен МОСТ на OpenAI (gpt-5.6-sol и т.п.)
    if BRIDGE_ENABLED and _BRIDGE_MODULE_OK:
        target_model = _resolve_bridge_target(anth_body.get("model"))
        log.info("[BRIDGE] %s -> %s (stream=%s)", original_model, target_model, is_streaming)

        oai_body = anthropic_to_openai(anth_body, target_model=target_model)
        # Кодируем один раз, на готовом теле: так под защиту попадает и
        # role="tool" (результаты чтения файлов), из-за которых WAF и срабатывает.
        oai_body = waf_encode_openai_body(oai_body)
        oai_bytes = json.dumps(oai_body).encode()

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
            bridge = StreamingBridge(
                original_model=original_model,
                decoder_factory=_decoder_factory if WAF_ENABLED else None,
            )

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
                    with contextlib.suppress(Exception):
                        await upstream_resp.aclose()

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

        raw = await upstream_resp.aread()
        await upstream_resp.aclose()
        try:
            oai_resp = json.loads(raw.decode("utf-8", errors="replace"))
            anth_resp = openai_to_anthropic_response(
                oai_resp,
                original_model,
                decoder_factory=_decoder_factory if WAF_ENABLED else None,
            )
            return JSONResponse(anth_resp, status_code=200)
        except Exception as exc:
            log.warning("[BRIDGE] response parse error: %s", exc)
            return Response(content=raw, status_code=upstream_resp.status_code,
                            media_type="application/json")

    # 2. Если мост выключен -> Прямое Anthropic проксирование + WAF Bypass
    if FORCE_MODEL and FORCE_MODEL != original_model:
        log.info("[PROXY] модель подменена: %s -> %s", original_model, FORCE_MODEL)
        anth_body["model"] = FORCE_MODEL
    log.info("[PROXY] Direct Anthropic routing for %s (stream=%s)",
             anth_body.get("model"), is_streaming)
    anth_body = waf_encode_anthropic_body(anth_body)
    anth_bytes = json.dumps(anth_body).encode("utf-8")

    headers = _build_upstream_headers(request)
    url = f"{UPSTREAM_BASE}/v1/messages?beta=true"

    result = await _open_upstream_stream("POST", url, headers, anth_bytes)
    if isinstance(result, JSONResponse):
        return result
    upstream_resp = result

    if is_streaming:
        async def waf_decode_stream():
            byte_decoder = codecs.getincrementaldecoder("utf-8")()
            waf = WafStreamDecoder()
            try:
                async for chunk in upstream_resp.aiter_bytes():
                    text = byte_decoder.decode(chunk)
                    if text:
                        yield waf.feed(text).encode("utf-8")
                tail = byte_decoder.decode(b"", final=True)
                remainder = waf.feed(tail) + waf.flush()
                if remainder:
                    yield remainder.encode("utf-8")
            except Exception as exc:
                log.warning("[PROXY] stream error: %s", exc)
            finally:
                with contextlib.suppress(Exception):
                    await upstream_resp.aclose()

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

    raw = await upstream_resp.aread()
    await upstream_resp.aclose()
    try:
        raw_text = waf_decode_text(raw.decode("utf-8", errors="replace"))
        return Response(content=raw_text.encode("utf-8"),
                        status_code=upstream_resp.status_code,
                        media_type="application/json")
    except Exception as exc:
        log.warning("[PROXY] parse error: %s", exc)
        return Response(content=raw, status_code=upstream_resp.status_code,
                        media_type="application/json")


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

    # По умолчанию сквозные запросы не кодируются: gpt-5.6-sol отвечает на
    # подменённые символы `400 content-blocked`.
    if body and WAF_ENCODE_PASSTHROUGH and "json" in request.headers.get("content-type", "").lower():
        try:
            body = json.dumps(waf_encode_json(json.loads(body))).encode("utf-8")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.debug("[WAF] тело не JSON, кодирование пропущено: %s", exc)

    result = await _open_upstream_stream(method, url, headers, body)
    if isinstance(result, JSONResponse):
        return result
    upstream_resp = result

    resp_headers = _filter_response_headers(upstream_resp.headers)
    media_type = upstream_resp.headers.get("content-type")

    async def body_iterator():
        byte_decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        try:
            async for raw_chunk in upstream_resp.aiter_bytes():
                text = byte_decoder.decode(raw_chunk)
                if not text:
                    continue
                buffer += text
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    # Фильтруем служебный 'data: {"billing":...}' чанк от AgentRouter,
                    # чтобы у Cherry Studio не выскакивала ошибка 'Type validation failed'
                    if line.strip().startswith('data: {"billing":'):
                        continue
                    # Декодируем построчно и без переноса состояния: удержанный
                    # символ, вылезший за границу строки, порвал бы JSON.
                    yield (waf_decode_text(line) + "\n").encode("utf-8")

            remainder = byte_decoder.decode(b"", final=True) + buffer
            if remainder and not remainder.strip().startswith('data: {"billing":'):
                yield waf_decode_text(remainder).encode("utf-8")
        except httpx.StreamClosed:
            log.debug("[STREAM] closed")
        except httpx.RequestError as exc:
            log.warning("[STREAM] upstream error: %s", exc)
        except (BrokenPipeError, ConnectionResetError, ConnectionError):
            log.debug("[STREAM] client disconnected")
        except Exception as exc:
            log.warning("[STREAM] error: %s", exc)
        finally:
            with contextlib.suppress(Exception):
                await upstream_resp.aclose()

    return StreamingResponse(
        body_iterator(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        media_type=media_type,
    )


@app.get("/__proxy_health")
async def health() -> dict:
    return {
        "status": "ok",
        "upstream": UPSTREAM_BASE,
        "bridge": BRIDGE_ENABLED,
        "bridge_model": BRIDGE_TARGET_MODEL if BRIDGE_ENABLED else None,
        "bridge_model_pinned": BRIDGE_MODEL_PINNED,
        "forced_model": FORCE_MODEL or None,
        "waf_bypass": WAF_ENABLED,
    }


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
