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

# Настройки шлюза (подставьте свой реферал, если нужно)
UPSTREAM_BASE = os.environ.get("AGENTROUTER_UPSTREAM", "https://agentrouter.org").rstrip("/")
LISTEN_HOST = os.environ.get("AGENTROUTER_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("AGENTROUTER_PORT", "8318"))

# Маскировка клиента для обхода WAF защиты AgentRouter
MASK_USER_AGENT = os.environ.get("AGENTROUTER_UA", "codex_cli_rs/0.101.0")
MASK_ORIGINATOR = os.environ.get("AGENTROUTER_ORIGINATOR", "codex_cli_rs")

HTTPX_TIMEOUT = httpx.Timeout(connect=30.0, write=60.0, read=None, pool=None)

# Заголовки, которые мы вырезаем перед отправкой, чтобы не спалиться
DROP_REQUEST_HEADERS = {
    "host", "content-length", "transfer-encoding", "user-agent",
    "accept-encoding", "originator", "connection", "proxy-connection", "keep-alive"
}

DROP_RESPONSE_HEADERS = {
    "content-length", "transfer-encoding", "connection", "proxy-connection", "keep-alive"
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
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
    log.info("Proxy running on http://%s:%s -> %s", LISTEN_HOST, LISTEN_PORT, UPSTREAM_BASE)
    try:
        yield
    finally:
        if _client is not None:
            await _client.aclose()
            _client = None

app = FastAPI(title="AgentRouter Proxy", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

def _build_upstream_headers(request: Request) -> dict[str, str]:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_REQUEST_HEADERS}
    headers["User-Agent"] = MASK_USER_AGENT
    headers["Originator"] = MASK_ORIGINATOR
    headers["Accept-Encoding"] = "identity"  # Избегаем сжатия для чистого SSE
    return headers

@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def proxy(full_path: str, request: Request) -> Response:
    query = request.url.query
    url = f"{UPSTREAM_BASE}/{full_path}"
    if query:
        url = f"{url}?{query}"

    headers = _build_upstream_headers(request)
    body = await request.body()

    assert _client is not None
    upstream_req = _client.build_request(request.method, url, headers=headers, content=body if body else None)
    upstream_resp = await _client.send(upstream_req, stream=True)

    resp_headers = {k: v for k, v in upstream_resp.headers.items() if k.lower() not in DROP_RESPONSE_HEADERS}
    
    async def body_iterator():
        try:
            async for chunk in upstream_resp.aiter_raw():
                if chunk:
                    yield chunk
        finally:
            await upstream_resp.aclose()

    return StreamingResponse(
        body_iterator(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        media_type=upstream_resp.headers.get("content-type")
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT, log_level="info", access_log=False)
