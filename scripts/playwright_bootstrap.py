from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from gemini_service.core.browser_cookie_sync import resolve_browser_path, sync_inventory_from_browser_profiles


DEFAULT_ACCOUNTS_PATH = REPO_ROOT / "config" / "accounts.json"
DEFAULT_PROFILE_DIR = REPO_ROOT / "data" / "chrome-manual-profile"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open a real browser profile for Gemini login, then export cookies into config/accounts.json."
    )
    parser.add_argument("--account-id", default="primary-ultra-1", help="Account ID in config/accounts.json to update.")
    parser.add_argument("--accounts-path", default=str(DEFAULT_ACCOUNTS_PATH), help="Path to local account inventory JSON.")
    parser.add_argument("--browser", choices=("chrome", "edge"), default="chrome", help="Browser to launch.")
    parser.add_argument("--browser-path", default="", help="Optional explicit path to chrome.exe or msedge.exe.")
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR), help="Persistent browser user data directory.")
    parser.add_argument("--start-url", default="https://gemini.google.com/app", help="Page to open for login/bootstrap.")
    parser.add_argument("--wait-timeout-seconds", type=int, default=900, help="How long to wait for Gemini cookies.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    accounts_path = Path(args.accounts_path).resolve()
    profile_dir = Path(args.profile_dir).resolve()
    browser_path = resolve_browser_path(args.browser, args.browser_path or None)

    if browser_path is None:
        raise SystemExit(f"{args.browser} executable was not found. Pass --browser-path explicitly.")
    if not accounts_path.exists():
        raise SystemExit(f"Account inventory not found: {accounts_path}")

    payload = json.loads(accounts_path.read_text(encoding="utf-8"))
    target = None
    for account in payload.get("accounts", []):
        if account.get("account_id") == args.account_id:
            account["cookie_source_browser"] = args.browser
            account["cookie_source_browser_path"] = str(browser_path)
            account["cookie_source_profile_dir"] = str(profile_dir)
            target = account
            break
    if target is None:
        raise SystemExit(f"Account {args.account_id!r} was not found in {accounts_path}.")
    accounts_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    profile_dir.mkdir(parents=True, exist_ok=True)

    import subprocess

    process = subprocess.Popen(
        [
            str(browser_path),
            f"--user-data-dir={profile_dir}",
            "--new-window",
            args.start_url,
        ]
    )
    print(f"Using persistent {args.browser} profile: {profile_dir}")
    print(f"Opening Gemini login page: {args.start_url}")
    print("Please complete login in the opened browser window, confirm Gemini is usable, then close that window.")

    deadline = time.time() + args.wait_timeout_seconds
    while process.poll() is None and time.time() < deadline:
        time.sleep(1)

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        print("Timed out waiting for the browser window to close.")
        return 1

    results = sync_inventory_from_browser_profiles(
        accounts_path=accounts_path,
        timeout_seconds=20,
        start_url=args.start_url,
        default_browser=args.browser,
        headless=True,
        only_account_id=args.account_id,
    )
    result = results[0] if results else None
    if result is None or result.status != "ok":
        detail = result.detail if result else "No cookie sync result was produced."
        print(f"Cookie sync failed: {detail}")
        return 1

    print(
        json.dumps(
            {
                "account_id": args.account_id,
                "accounts_path": str(accounts_path),
                "profile_dir": str(profile_dir),
                "browser": args.browser,
                "updated": result.updated,
                "detail": result.detail,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
