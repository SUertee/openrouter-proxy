from __future__ import annotations

import argparse
import json
import os
import statistics
import time

import requests
from env_loader import load_env_file

load_env_file()


def run_one(url: str, headers: dict, payload: dict, timeout_sec: float) -> tuple[float, int, str, dict]:
    start = time.perf_counter()
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout_sec)
    elapsed_ms = (time.perf_counter() - start) * 1000
    usage = {}
    try:
        body_json = resp.json()
        usage = body_json.get("usage", {}) if isinstance(body_json, dict) else {}
    except json.JSONDecodeError:
        usage = {}
    return elapsed_ms, resp.status_code, resp.text[:300], usage


def benchmark(name: str, url: str, headers: dict, payload: dict, count: int, timeout_sec: float) -> None:
    latencies = []
    failures = 0
    prompt_tokens_total = 0
    completion_tokens_total = 0
    total_tokens_total = 0

    for _ in range(count):
        try:
            ms, code, body, usage = run_one(url, headers, payload, timeout_sec)
            if code >= 400:
                failures += 1
                print(f"[{name}] http {code} body={body}")
            else:
                latencies.append(ms)
                prompt_tokens_total += int(usage.get("prompt_tokens", 0) or 0)
                completion_tokens_total += int(usage.get("completion_tokens", 0) or 0)
                total_tokens_total += int(usage.get("total_tokens", 0) or 0)
        except requests.RequestException as exc:
            failures += 1
            print(f"[{name}] request error: {exc}")

    print(f"\\n{name} summary")
    print(f"- total: {count}")
    print(f"- success: {len(latencies)}")
    print(f"- failure: {failures}")
    if latencies:
        print(f"- avg_ms: {statistics.mean(latencies):.2f}")
        print(f"- p95_ms: {statistics.quantiles(latencies, n=20)[18]:.2f}")
        print(f"- min_ms: {min(latencies):.2f}")
        print(f"- max_ms: {max(latencies):.2f}")
        print(f"- prompt_tokens_total: {prompt_tokens_total}")
        print(f"- completion_tokens_total: {completion_tokens_total}")
        print(f"- total_tokens_total: {total_tokens_total}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare direct LLM call vs Tokyo proxy call")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=120)

    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", default="请用一句话介绍你自己")

    parser.add_argument("--direct-url", default="https://openrouter.ai/api/v1/chat/completions")
    parser.add_argument("--direct-api-key", default="")
    parser.add_argument("--proxy-url", default="")
    parser.add_argument("--proxy-token", default="")

    args = parser.parse_args()
    if not args.direct_api_key:
        args.direct_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not args.proxy_token:
        args.proxy_token = os.getenv("PROXY_TOKEN", "").strip()
    if not args.proxy_url:
        proxy_base_url = os.getenv("PROXY_BASE_URL", "").strip()
        if proxy_base_url:
            args.proxy_url = f"{proxy_base_url.rstrip('/')}/proxy/api/v1/chat/completions"
    if not args.direct_api_key or not args.proxy_token or not args.proxy_url:
        raise SystemExit(
            "Missing required values. Use CLI args or set OPENROUTER_API_KEY/PROXY_TOKEN/PROXY_BASE_URL in .env"
        )

    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "stream": False,
        "usage": {"include": True},
    }

    direct_headers = {
        "Authorization": f"Bearer {args.direct_api_key}",
        "Content-Type": "application/json",
    }
    proxy_headers = {
        "X-Proxy-Token": args.proxy_token,
        "Content-Type": "application/json",
    }

    benchmark("direct", args.direct_url, direct_headers, payload, args.count, args.timeout)
    benchmark("proxy", args.proxy_url, proxy_headers, payload, args.count, args.timeout)


if __name__ == "__main__":
    main()
