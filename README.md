# Руководство по интеграции Claude Code и VS Code с AgentRouter

Это подробное руководство объясняет, как настроить и использовать неограниченные лимиты и флагманские модели (Claude Opus 4.8, GPT-5.5) через API-шлюз **AgentRouter** в консольном интерфейсе **Claude Code CLI** и расширениях **VS Code** (Claude Code, Kilo Code, Cline и др.).

---

## 1. Зачем нужен локальный прокси?
На серверах AgentRouter работает сетевой экран (WAF), который сбрасывает запросы от сторонних программ (выдавая ошибку `unauthorized client`). Чтобы обойти защиту и предотвратить обрывы длинного контекста (ошибки `BrokenPipeError` / `malformed response` на контексте 200k+ токенов), все запросы пропускаются через **локальный асинхронный прокси-сервер на FastAPI**. 

Прокси маскирует запросы под разрешенного клиента (`codex_cli_rs`) и осуществляет потоковую передачу данных (Server-Sent Events) в реальном времени.

---

## Шаг 1. Получение API-ключа
1. Перейдите на сайт [agentrouter.org](https://agentrouter.org/register?aff=KM29).
2. Авторизуйтесь под своим аккаунтом **GitHub** (кнопка Sign In).
3. В боковом меню перейдите в раздел **Tokens** (или **Ключи**).
4. Нажмите **Add Token** (Добавить токен):
   * Задайте имя токена (например, `vs-code-claude`).
   * В поле **Quota** установите **Unlimited** (или укажите лимит баланса).
5. Нажмите **Submit** и скопируйте созданный ключ (начинается на `sk-...`).

---

## Шаг 2. Установка зависимостей и сохранение прокси
Для запуска прокси-сервера потребуется Python 3 и три библиотеки: `fastapi`, `uvicorn` и `httpx`.

1. **Установите библиотеки в терминале:**
   ```bash
   pip install fastapi uvicorn httpx
   ```
2. **Создайте файл `agentrouter_proxy.py` и вставьте в него следующий код:**

```python
#!/usr/bin/env python3
"""
Локальный асинхронный прокси для agentrouter.org.
Стек: FastAPI + httpx (async) + uvicorn.
"""
from __future__ import annotations
import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

UPSTREAM_BASE = os.environ.get("AGENTROUTER_UPSTREAM", "https://agentrouter.org/register?aff=KM29").rstrip("/")
LISTEN_HOST = os.environ.get("AGENTROUTER_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("AGENTROUTER_PORT", "8318"))

# Заголовки маскировки под разрешённого клиента для обхода WAF.
MASK_USER_AGENT = os.environ.get("AGENTROUTER_UA", "codex_cli_rs/0.101.0")
MASK_ORIGINATOR = os.environ.get("AGENTROUTER_ORIGINATOR", "codex_cli_rs")

RETRY_ON_GATEWAY_ERRORS = os.environ.get("AGENTROUTER_RETRY_GATEWAY", "true").lower() in ("true", "1", "yes")
RETRY_MAX_ATTEMPTS = int(os.environ.get("AGENTROUTER_RETRY_MAX", "2"))
RETRY_BACKOFF_BASE = float(os.environ.get("AGENTROUTER_RETRY_BACKOFF", "2.0"))
RETRYABLE_STATUS_CODES = {502, 503, 504, 522, 524}
RETRYABLE_METHODS = {"GET", "HEAD", "POST"}

HTTPX_TIMEOUT = httpx.Timeout(
    connect=float(os.environ.get("AGENTROUTER_CONNECT_TIMEOUT", "30")),
    write=float(os.environ.get("AGENTROUTER_WRITE_TIMEOUT", "60")),
    read=None,  # Без таймаута на чтение потока для тяжелых рассуждений LLM
    pool=None,
)

DROP_REQUEST_HEADERS = {
    "host", "content-length", "transfer-encoding", "user-agent",
    "accept-encoding", "originator", "connection", "proxy-connection", "keep-alive"
}
DROP_RESPONSE_HEADERS = {
    "content-length", "transfer-encoding", "connection", "proxy-connection", "keep-alive"
}

logging.basicConfig(
    level=os.environ.get("AGENTROUTER_LOGLEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("agentrouter-proxy")

_client: httpx.AsyncClient | None = None

@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
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

app = FastAPI(title="AgentRouter Local Proxy", docs_url=None, redoc_url=None, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

def _build_upstream_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, val in request.headers.items():
        if key.lower() in DROP_REQUEST_HEADERS:
            continue
        headers[key] = val
    headers["User-Agent"] = MASK_USER_AGENT
    headers["Originator"] = MASK_ORIGINATOR
    headers["Accept-Encoding"] = "identity"  # Запрещаем сжатие
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
            continue
        out[key] = val
    return out

@app.options("/{full_path:path}")
async def preflight(full_path: str) -> Response:
    return Response(status_code=204)

async def _open_upstream_stream(method: str, url: str, headers: dict[str, str], body: bytes) -> httpx.Response | JSONResponse:
    assert _client is not None
    retry_enabled = RETRY_ON_GATEWAY_ERRORS and method.upper() in RETRYABLE_METHODS
    max_attempts = RETRY_MAX_ATTEMPTS if retry_enabled else 1
    last_error_status = 502
    last_error_msg = "upstream unavailable"

    for attempt in range(1, max_attempts + 1):
        upstream_req = _client.build_request(
            method=method, url=url, headers=headers, content=body if body else None
        )
        try:
            upstream_resp = await _client.send(upstream_req, stream=True)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            last_error_status, last_error_msg = 504 if isinstance(exc, httpx.TimeoutException) else 502, str(exc)
            log.warning("[UPSTREAM] Ошибка (попытка %d/%d): %s", attempt, max_attempts, exc)
        else:
            if retry_enabled and upstream_resp.status_code in RETRYABLE_STATUS_CODES and attempt < max_attempts:
                status = upstream_resp.status_code
                await upstream_resp.aclose()
                delay = RETRY_BACKOFF_BASE * attempt
                log.warning("[UPSTREAM] %s от сервера (попытка %d/%d) — повтор через %.1fс", status, attempt, max_attempts, delay)
                await asyncio.sleep(delay)
                continue
            return upstream_resp

        if attempt < max_attempts:
            await asyncio.sleep(RETRY_BACKOFF_BASE * attempt)

    return JSONResponse(
        status_code=last_error_status,
        content={"message": last_error_msg, "status": last_error_status, "proxy": "agentrouter_proxy", "attempts": max_attempts}
    )

@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
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
            log.debug("[STREAM] закрыт")
        except httpx.RequestError as exc:
            log.warning("[STREAM] ошибка посреди потока: %s", exc)
        except (BrokenPipeError, ConnectionResetError, ConnectionError):
            log.debug("[STREAM] клиент отключился")
        except Exception as exc:
            log.warning("[STREAM] непредвиденная ошибка: %s", exc)
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT, log_level="info", access_log=False, timeout_keep_alive=75)
```

---

## Шаг 3. Запуск прокси-сервера
Запустите прокси в отдельном терминале, чтобы он постоянно работал в фоне:
```bash
python agentrouter_proxy.py
```
*Для Windows можно создать текстовый файл на Рабочем столе с расширением `.bat` (например, `StartProxy.bat`), вписать туда:*
```cmd
@echo off
title AgentRouter Proxy
python "C:\путь_к_файлу\agentrouter_proxy.py"
pause
```
*И запускать прокси двойным кликом.*

---

## Шаг 4. Настройка окружения (Переменные)

Поскольку VS Code запускается операционной системой, он должен унаследовать переменные окружения глобально.

### Вариант А: Настройка под Windows (PowerShell)
Запустите PowerShell и выполните следующие команды (подставьте ваш API-ключ):
```powershell
# Запись настроек в реестр пользователя Windows
[System.Environment]::SetEnvironmentVariable('ANTHROPIC_BASE_URL', 'http://127.0.0.1:8318', 'User')
[System.Environment]::SetEnvironmentVariable('ANTHROPIC_API_KEY', 'твой_ключ_sk_с_сайта', 'User')
[System.Environment]::SetEnvironmentVariable('ANTHROPIC_MODEL', 'claude-opus-4-8', 'User')
```
*После выполнения команд обязательно **полностью перезапустите VS Code**.*

### Вариант Б: Настройка под macOS / Linux / WSL (Ubuntu)
Пропишите переменные в файл профиля командной строки:
```bash
echo 'export ANTHROPIC_API_KEY="твой_ключ_sk_с_сайта"' >> ~/.bashrc
echo 'export ANTHROPIC_BASE_URL="http://127.0.0.1:8318"' >> ~/.bashrc
echo 'export ANTHROPIC_MODEL="claude-opus-4-8"' >> ~/.bashrc
source ~/.bashrc
```
*Для применения изменений в VS Code запустите редактор прямо из этого терминала командой `code .`.*

---

## Шаг 5. Обход бага «Not logged in · Please run /login»
В Claude Code CLI есть частый баг: при запуске в оффлайн-режиме или с кастомным адресом он может выдать ошибку `Not logged in`. Чтобы её заглушить, создайте фейковый файл авторизации:

* **Для Windows (PowerShell):**
  ```powershell
  New-Item -ItemType Directory -Force -Path "$HOME\.claude"
  Set-Content -Path "$HOME\.claude\.credentials.json" -Value '{"hasCompletedOnboarding": true}'
  ```
* **Для macOS / Linux (Terminal):**
  ```bash
  mkdir -p ~/.claude && echo '{"hasCompletedOnboarding": true}' > ~/.claude/.credentials.json && chmod 600 ~/.claude/.credentials.json
  ```

---

## Шаг 6. Проверка работы
1. Запустите прокси.
2. Откройте VS Code.
3. Откройте вкладку расширения Claude Code (или запустите CLI командой `claude` в интегрированном терминале).
4. Задайте любой вопрос. В логах прокси-сервера отобразится перенаправление запроса:
   `HTTP Request: POST https://agentrouter.org/register?aff=KM29/v1/chat/completions "HTTP/1.1 200 OK"`
   Ответ начнет быстро выводиться в режиме стриминга.
