from __future__ import annotations

import argparse
import json
import os
import sys

import requests
from env_loader import load_env_file

load_env_file()


def get_key_info(api_key: str, timeout: float) -> dict:
    url = "https://openrouter.ai/api/v1/key"
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("data", {})


def main() -> None:
    parser = argparse.ArgumentParser(description="Check OpenRouter API key usage")
    parser.add_argument("--api-key", default=os.getenv("OPENROUTER_API_KEY", ""))
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--json", action="store_true", help="Print raw JSON")
    args = parser.parse_args()

    api_key = (args.api_key or "").strip()
    if not api_key:
        print("Missing api key. Set OPENROUTER_API_KEY or pass --api-key", file=sys.stderr)
        raise SystemExit(2)

    try:
        data = get_key_info(api_key, args.timeout)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        body = exc.response.text[:500] if exc.response is not None else str(exc)
        print(f"OpenRouter request failed: HTTP {status} body={body}", file=sys.stderr)
        raise SystemExit(1)
    except requests.RequestException as exc:
        print(f"OpenRouter request failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    label = data.get("label")
    limit = data.get("limit")
    usage = data.get("usage")
    usage_daily = data.get("usage_daily")
    usage_weekly = data.get("usage_weekly")
    usage_monthly = data.get("usage_monthly")
    limit_remaining = data.get("limit_remaining")
    limit_reset = data.get("limit_reset")
    expires_at = data.get("expires_at")

    print("OpenRouter Key Usage")
    print(f"- label: {label}")
    print(f"- limit: {limit}")
    print(f"- usage_total: {usage}")
    print(f"- usage_daily: {usage_daily}")
    print(f"- usage_weekly: {usage_weekly}")
    print(f"- usage_monthly: {usage_monthly}")
    print(f"- limit_remaining: {limit_remaining}")
    print(f"- limit_reset: {limit_reset}")
    print(f"- expires_at: {expires_at}")


if __name__ == "__main__":
    main()
