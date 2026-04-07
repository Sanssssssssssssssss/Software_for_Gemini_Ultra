from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from gemini_service.core.bootstrap import evaluate_bootstrap_status
from gemini_service.core.config import get_settings


def _http_get(url: str, token: str | None = None) -> dict:
    request = Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local setup checks for Gemini Internal Service.")
    parser.add_argument("--base-url", help="Optional running service base URL, for example http://127.0.0.1:8000")
    parser.add_argument("--token", help="Optional API bearer token for protected endpoints.")
    args = parser.parse_args()

    get_settings.cache_clear()
    settings = get_settings()
    status = evaluate_bootstrap_status(settings)

    print("本地配置检查：")
    for check in status.checks:
        prefix = {"pass": "[PASS]", "warn": "[WARN]", "fail": "[FAIL]"}[check.status]
        print(f"{prefix} {check.name}: {check.detail}")
        if check.action:
            print(f"       建议：{check.action}")

    if args.base_url:
        print("\n在线服务检查：")
        base_url = args.base_url.rstrip("/")
        for path in ("/healthz", "/readyz", "/setup/status"):
            try:
                payload = _http_get(base_url + path)
            except HTTPError as exc:
                print(f"[FAIL] {path}: HTTP {exc.code}")
            except URLError as exc:
                print(f"[FAIL] {path}: {exc.reason}")
            else:
                print(f"[PASS] {path}: {json.dumps(payload, ensure_ascii=False)}")

        if args.token:
            try:
                payload = _http_get(base_url + "/v1/accounts", token=args.token)
            except HTTPError as exc:
                print(f"[FAIL] /v1/accounts: HTTP {exc.code}")
            except URLError as exc:
                print(f"[FAIL] /v1/accounts: {exc.reason}")
            else:
                print(f"[PASS] /v1/accounts: {json.dumps(payload, ensure_ascii=False)}")

    if status.setup_complete:
        print("\n结论：结构化配置已经基本齐全，可以进入服务启动和账号可用性验证。")
    else:
        print("\n结论：当前还不适合直接交给用户使用，请先补齐失败项。")


if __name__ == "__main__":
    main()
