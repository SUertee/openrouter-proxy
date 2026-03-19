from __future__ import annotations

import json
import os
from typing import Iterator

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from env_loader import load_env_file

load_env_file()

app = FastAPI(title="Tokyo LLM Proxy", version="0.1.0")


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env: {name}")
    return value


def _load_settings() -> dict:
    return {
        "proxy_token": _require_env("PROXY_TOKEN"),
        "upstream_base_url": _require_env("UPSTREAM_BASE_URL").rstrip("/"),
        "upstream_api_key": _require_env("UPSTREAM_API_KEY"),
        "timeout_sec": float(os.getenv("UPSTREAM_TIMEOUT_SEC", "120")),
        "http_referer": os.getenv("UPSTREAM_HTTP_REFERER", "").strip(),
        "x_title": os.getenv("UPSTREAM_X_TITLE", "").strip(),
    }


def _forward_stream(resp: requests.Response) -> Iterator[bytes]:
    try:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                yield chunk
    finally:
        resp.close()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.api_route("/proxy/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(
    path: str,
    request: Request,
    x_proxy_token: str | None = Header(default=None),
):
    settings = _load_settings()

    if x_proxy_token != settings["proxy_token"]:
        raise HTTPException(status_code=401, detail="unauthorized")

    upstream_url = f"{settings['upstream_base_url']}/{path.lstrip('/')}"

    inbound_headers = dict(request.headers)
    outbound_headers = {
        "Authorization": f"Bearer {settings['upstream_api_key']}",
        "Content-Type": inbound_headers.get("content-type", "application/json"),
        "Accept": inbound_headers.get("accept", "application/json"),
    }
    if settings["http_referer"]:
        outbound_headers["HTTP-Referer"] = settings["http_referer"]
    if settings["x_title"]:
        outbound_headers["X-Title"] = settings["x_title"]

    try:
        body = await request.body()
        raw_body = body if body else None
        method = request.method.upper()
        params = dict(request.query_params)

        stream_enabled = False
        if raw_body:
            try:
                payload = json.loads(raw_body)
                stream_enabled = bool(payload.get("stream", False))
            except json.JSONDecodeError:
                stream_enabled = False

        resp = requests.request(
            method=method,
            url=upstream_url,
            params=params,
            data=raw_body,
            headers=outbound_headers,
            timeout=settings["timeout_sec"],
            stream=stream_enabled,
        )

        if stream_enabled:
            media_type = resp.headers.get("content-type", "text/event-stream")
            passthrough_headers = {}
            if request.headers.get("accept-encoding"):
                passthrough_headers["Content-Encoding"] = resp.headers.get("content-encoding", "")
            return StreamingResponse(
                _forward_stream(resp),
                status_code=resp.status_code,
                media_type=media_type,
                headers=passthrough_headers,
            )

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            return JSONResponse(status_code=resp.status_code, content=resp.json())

        return JSONResponse(status_code=resp.status_code, content={"raw": resp.text})
    except requests.Timeout as exc:
        raise HTTPException(status_code=504, detail=f"upstream timeout: {exc}") from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"upstream request failed: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("tokyo_llm_proxy:app", host="0.0.0.0", port=8787, reload=False)
