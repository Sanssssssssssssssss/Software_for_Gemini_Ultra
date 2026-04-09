from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import uvicorn


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
DEFAULT_ENV_FILE = REPO_ROOT / ".env"
MOCK_ENV_FILE = REPO_ROOT / ".env.local.mock"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Gemini Internal Service locally.")
    parser.add_argument("--env-file", default="", help="Path to an env file to load before startup.")
    parser.add_argument("--mock", action="store_true", help="Load .env.local.mock for offline local startup.")
    parser.add_argument("--host", default="", help="Override host from the env file.")
    parser.add_argument("--port", type=int, default=0, help="Override port from the env file.")
    parser.add_argument("--reload", action="store_true", help="Force uvicorn reload mode on.")
    parser.add_argument(
        "--skip-cookie-sync",
        action="store_true",
        help="Do not auto-refresh Gemini cookies from configured persistent browser profiles before startup.",
    )
    return parser.parse_args()


def load_env_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Environment file not found: {path}")

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()


def main() -> None:
    args = parse_args()
    env_path = Path(args.env_file) if args.env_file else (MOCK_ENV_FILE if args.mock else DEFAULT_ENV_FILE)
    load_env_file(env_path)
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))
    os.environ["PYTHONPATH"] = (
        f"{SRC_DIR}{os.pathsep}{os.environ['PYTHONPATH']}" if os.environ.get("PYTHONPATH") else str(SRC_DIR)
    )
    from gemini_service.core.bootstrap import evaluate_bootstrap_status
    from gemini_service.core.config import get_settings
    from gemini_service.services.account_pool import AccountPool
    from gemini_service.services.account_recovery_service import AccountRecoveryService

    get_settings.cache_clear()
    settings = get_settings()

    if not args.skip_cookie_sync and settings.cookie_autosync_enabled:
        print("Checking persistent browser profiles for fresh Gemini cookies...")
        async def _recover_accounts():
            pool = AccountPool(settings)
            await pool.start()
            try:
                recovery = AccountRecoveryService(settings=settings, pool=pool)
                return await recovery.recover_accounts_for_startup()
            finally:
                await pool.close()

        results = asyncio.run(_recover_accounts())
        for result in results:
            print(f"[account-recovery:{result.status}] {result.account_id}: {result.detail}")
            if result.provider_status or result.runtime_state:
                print(
                    f"  Provider={result.provider_status or '-'} Runtime={result.runtime_state or '-'}"
                    + (f" Cookies={result.cookie_count}" if result.cookie_count is not None else "")
                )
            if result.action:
                print(f"  Action: {result.action}")

    bootstrap = evaluate_bootstrap_status(settings)
    failed_checks = [check for check in bootstrap.checks if check.status == "fail"]
    warned_checks = [check for check in bootstrap.checks if check.status == "warn"]
    if failed_checks or warned_checks:
        print("Startup diagnostics:")
        for check in failed_checks + warned_checks:
            prefix = "[FAIL]" if check.status == "fail" else "[WARN]"
            print(f"{prefix} {check.name}: {check.detail}")
            if check.action:
                print(f"  Action: {check.action}")

    host = args.host or os.environ.get("GEMINI_SERVICE_HOST", "127.0.0.1")
    port = args.port or int(os.environ.get("GEMINI_SERVICE_PORT", "8000"))
    env_name = os.environ.get("GEMINI_SERVICE_ENV", "development")
    reload_enabled = args.reload or env_name == "development"

    print(f"Loaded environment from {env_path}")
    print(f"Starting Gemini Internal Service on http://{host}:{port}")

    uvicorn.run(
        "gemini_service.main:app",
        host=host,
        port=port,
        reload=reload_enabled,
        factory=False,
    )


if __name__ == "__main__":
    main()
