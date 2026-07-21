from __future__ import annotations
import asyncio
import contextlib
import logging
import os
import sys
import argparse
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

# Setup argument parser
parser = argparse.ArgumentParser(description="AgentRouter Dual-Purpose Proxy (IDE + SillyTavern)")
parser.add_argument("--host", default="127.0.0.1", help="Host to listen on")
parser.add_argument("--port", type=int, default=8318, help="Port to listen on (e.g., 8318 or 5000)")
parser.add_argument("--upstream", default="https://agentrouter.org", help="Upstream API base")
args, unknown = parser.parse_known_args()

UPSTREAM_BASE = args.upstream.rstrip("/")
LISTEN_HOST = args.host
LISTEN_PORT = args.port

# WAF Bypass client headers
MASK_USER_AGENT = os.environ.get("AGENTROUTER_UA", "codex_cli_rs/0.101.0")
MASK_ORIGINATOR = os.environ.get("AGENTROUTER_ORIGINATOR", "codex_cli_rs")

HTTPX_TIMEOUT = httpx.Timeout(connect=30.0, write=60.0, read=None, pool=None)

# Forbidden headers that trigger WAF detection or break responses
DROP_REQUEST_HEADERS = {
    "host", "content-length", "transfer-encoding", "user-agent",
    "accept-encoding", "originator", "connection", "proxy-connection", "keep-alive",
    "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "accept-language"
}

DROP_RESPONSE_HEADERS = {
    "content-length", "transfer-encoding", "connection", "proxy-connection", "keep-alive"
}

# Silent logger to keep console clean for our pretty banner
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
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
    print("=" * 60)
    print("🚀  AgentRouter Proxy Server (FastAPI Async) is active!")
    print(f"📡  Listening on: http://{LISTEN_HOST}:{LISTEN_PORT}")
    print(f"🔗  Forwarding to: {UPSTREAM_BASE}")
    print("-" * 60)
    print("💡  IDE Setup (Cline / Roo Code / Cursor):")
    print(f"    - Base URL: http://{LISTEN_HOST}:{LISTEN_PORT}")
    print("💡  SillyTavern Setup (Custom Endpoint):")
    print(f"    - Base URL: http://{LISTEN_HOST}:{LISTEN_PORT}/v1")
    print("=" * 60)
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
    headers["Accept-Encoding"] = "identity"  # Disable compression to keep SSE stream fluid
    return headers

@app.options("/{full_path:path}")
async def preflight(full_path: str) -> Response:
    return Response(status_code=204)

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
    
    try:
        upstream_resp = await _client.send(upstream_req, stream=True)
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return JSONResponse(status_code=502, content={"error": "Upstream connection failed", "details": str(e)})

    resp_headers = {k: v for k, v in upstream_resp.headers.items() if k.lower() not in DROP_RESPONSE_HEADERS}
    
    async def body_iterator():
        try:
            async for chunk in upstream_resp.aiter_raw():
                if chunk:
                    yield chunk
        except Exception as e:
            pass
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
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT, log_level="warning", access_log=False)
