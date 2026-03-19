from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Iterator

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from env_loader import load_env_file

load_env_file()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("tokyo-llm-proxy")

app = FastAPI(title="Tokyo LLM Proxy", version="0.1.0")
_MODELS_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}


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
        "models_cache_ttl_sec": int(os.getenv("MODELS_CACHE_TTL_SEC", "600")),
        "model_limit_per_family": int(os.getenv("MODEL_LIMIT_PER_FAMILY", "2")),
        "model_families": [
            item.strip().lower()
            for item in os.getenv("MODEL_FAMILIES", "gemini,claude,openai,llama").split(",")
            if item.strip()
        ],
    }


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    value = authorization.strip()
    if not value:
        return None
    if value.lower().startswith("bearer "):
        return value[7:].strip() or None
    return None


def _forward_stream(resp: requests.Response) -> Iterator[bytes]:
    try:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                yield chunk
    finally:
        resp.close()


def _is_models_path(path: str) -> bool:
    normalized = path.strip().lstrip("/").lower()
    return normalized in {"models", "v1/models", "api/v1/models"}


def _model_family(model_id: str) -> str | None:
    mid = (model_id or "").lower()
    if mid.startswith("google/gemini"):
        return "gemini"
    if mid.startswith("anthropic/claude"):
        return "claude"
    if mid.startswith("openai/"):
        return "openai"
    if mid.startswith("meta-llama/") or "llama" in mid:
        return "llama"
    return None


def _model_created(item: dict[str, Any]) -> float:
    value = item.get("created")
    if value is None:
        value = item.get("created_at")
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _filter_models(payload: dict[str, Any], families: list[str], limit_per_family: int) -> dict[str, Any]:
    data = payload.get("data", [])
    if not isinstance(data, list):
        return payload

    allowed = set(families)
    if not allowed:
        allowed = {"gemini", "claude", "openai", "llama"}

    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in allowed}
    for item in data:
        if not isinstance(item, dict):
            continue
        fam = _model_family(str(item.get("id", "")))
        if fam and fam in allowed:
            buckets[fam].append(item)

    out: list[dict[str, Any]] = []
    for fam in families:
        if fam not in buckets:
            continue
        models = sorted(buckets[fam], key=_model_created, reverse=True)
        out.extend(models[: max(limit_per_family, 1)])

    result = dict(payload)
    result["data"] = out
    return result


def _cache_get_models(now: float) -> dict[str, Any] | None:
    payload = _MODELS_CACHE.get("payload")
    expires_at = float(_MODELS_CACHE.get("expires_at", 0.0))
    if payload is not None and now < expires_at:
        return payload
    return None


def _cache_set_models(payload: dict[str, Any], now: float, ttl_sec: int) -> None:
    _MODELS_CACHE["payload"] = payload
    _MODELS_CACHE["expires_at"] = now + max(ttl_sec, 0)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.api_route("/proxy/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(
    path: str,
    request: Request,
    x_proxy_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    settings = _load_settings()
    request_id = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    token = x_proxy_token or _extract_bearer_token(authorization)
    if token != settings["proxy_token"]:
        logger.warning("proxy_unauthorized request_id=%s path=%s", request_id, path)
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
        model = ""
        if raw_body:
            try:
                payload = json.loads(raw_body)
                stream_enabled = bool(payload.get("stream", False))
                model = str(payload.get("model", ""))
            except json.JSONDecodeError:
                stream_enabled = False

        logger.info(
            "proxy_request request_id=%s method=%s path=%s model=%s stream=%s",
            request_id,
            method,
            path,
            model or "-",
            stream_enabled,
        )

        if method == "GET" and _is_models_path(path):
            now = time.time()
            cached_payload = _cache_get_models(now)
            if cached_payload is not None:
                logger.info(
                    "models_cache_hit request_id=%s families=%s limit=%s count=%s",
                    request_id,
                    ",".join(settings["model_families"]),
                    settings["model_limit_per_family"],
                    len(cached_payload.get("data", [])),
                )
                return JSONResponse(status_code=200, content=cached_payload)

            logger.info("models_cache_miss request_id=%s path=%s", request_id, path)
            resp = requests.get(
                upstream_url,
                params=params,
                headers=outbound_headers,
                timeout=settings["timeout_sec"],
            )
            content_type = resp.headers.get("content-type", "")
            if "application/json" not in content_type:
                return JSONResponse(status_code=resp.status_code, content={"raw": resp.text})

            payload = resp.json()
            filtered = _filter_models(
                payload=payload,
                families=settings["model_families"],
                limit_per_family=settings["model_limit_per_family"],
            )
            _cache_set_models(filtered, now, settings["models_cache_ttl_sec"])
            logger.info(
                "models_cache_set request_id=%s ttl_sec=%s count=%s",
                request_id,
                settings["models_cache_ttl_sec"],
                len(filtered.get("data", [])),
            )
            return JSONResponse(status_code=resp.status_code, content=filtered)

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
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "proxy_stream_open request_id=%s status=%s model=%s elapsed_ms=%.1f",
                request_id,
                resp.status_code,
                model or "-",
                elapsed_ms,
            )
            return StreamingResponse(
                _forward_stream(resp),
                status_code=resp.status_code,
                media_type=media_type,
                headers=passthrough_headers,
            )

        content_type = resp.headers.get("content-type", "")
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "proxy_response request_id=%s status=%s model=%s elapsed_ms=%.1f",
            request_id,
            resp.status_code,
            model or "-",
            elapsed_ms,
        )
        if "application/json" in content_type:
            return JSONResponse(status_code=resp.status_code, content=resp.json())

        return JSONResponse(status_code=resp.status_code, content={"raw": resp.text})
    except requests.Timeout as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.error(
            "proxy_timeout request_id=%s path=%s elapsed_ms=%.1f error=%s",
            request_id,
            path,
            elapsed_ms,
            exc,
        )
        raise HTTPException(status_code=504, detail=f"upstream timeout: {exc}") from exc
    except requests.RequestException as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.error(
            "proxy_upstream_error request_id=%s path=%s elapsed_ms=%.1f error=%s",
            request_id,
            path,
            elapsed_ms,
            exc,
        )
        raise HTTPException(status_code=502, detail=f"upstream request failed: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("tokyo_llm_proxy:app", host="0.0.0.0", port=8787, reload=False)
