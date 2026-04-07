from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE_DIR = REPO_ROOT / "data" / "playwright-edge-profile"
DEFAULT_ACCOUNTS_PATH = REPO_ROOT / "config" / "accounts.json"
DEFAULT_EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
COOKIE_NAMES = ("__Secure-1PSID", "__Secure-1PSIDTS")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch a persistent Edge profile, log into Gemini, and export cookies for local service use."
    )
    parser.add_argument("--account-id", default="primary-ultra-1", help="Account ID in config/accounts.json to update.")
    parser.add_argument("--accounts-path", default=str(DEFAULT_ACCOUNTS_PATH), help="Path to local account inventory JSON.")
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR), help="Persistent Edge user data directory.")
    parser.add_argument("--edge-path", default=str(DEFAULT_EDGE_PATH), help="Path to msedge.exe.")
    parser.add_argument("--start-url", default="https://gemini.google.com/app", help="Page to open for login/bootstrap.")
    parser.add_argument("--wait-timeout-seconds", type=int, default=900, help="How long to wait for Gemini cookies.")
    parser.add_argument("--skip-write", action="store_true", help="Do not update config/accounts.json; only print a summary.")
    return parser.parse_args()


def load_inventory(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_inventory(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def upsert_account_cookies(path: Path, account_id: str, secure_1psid: str, secure_1psidts: str) -> None:
    payload = load_inventory(path)
    accounts = payload.get("accounts", [])
    for account in accounts:
        if account.get("account_id") == account_id:
            account["secure_1psid"] = secure_1psid
            account["secure_1psidts"] = secure_1psidts
            save_inventory(path, payload)
            return
    raise SystemExit(f"Account {account_id!r} was not found in {path}.")


def extract_required_cookies(cookies: list[dict]) -> tuple[str, str] | None:
    by_name = {cookie["name"]: cookie["value"] for cookie in cookies if cookie.get("domain", "").endswith("google.com")}
    if all(name in by_name and by_name[name] for name in COOKIE_NAMES):
        return by_name["__Secure-1PSID"], by_name["__Secure-1PSIDTS"]
    return None


def main() -> int:
    args = parse_args()
    accounts_path = Path(args.accounts_path).resolve()
    profile_dir = Path(args.profile_dir).resolve()
    edge_path = Path(args.edge_path)

    if not edge_path.exists():
        raise SystemExit(f"Edge executable not found: {edge_path}")
    if not accounts_path.exists():
        raise SystemExit(f"Account inventory not found: {accounts_path}")

    profile_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + args.wait_timeout_seconds

    print(f"Using persistent Edge profile: {profile_dir}")
    print(f"Opening Gemini login/bootstrap page in Edge: {args.start_url}")
    print("Please complete Google login and wait until the Gemini page is usable.")
    print("The script will detect __Secure-1PSID and __Secure-1PSIDTS automatically.")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            executable_path=str(edge_path),
            headless=False,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(args.start_url, wait_until="domcontentloaded", timeout=60000)

            cookies: tuple[str, str] | None = None
            while time.time() < deadline:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=2000)
                except PlaywrightTimeoutError:
                    pass
                current = extract_required_cookies(context.cookies())
                if current is not None:
                    cookies = current
                    break
                time.sleep(2)

            if cookies is None:
                print("Timed out waiting for Gemini cookies in the persistent browser session.")
                print("Keep the profile directory and rerun this command after completing login.")
                return 1

            secure_1psid, secure_1psidts = cookies
            if not args.skip_write:
                upsert_account_cookies(accounts_path, args.account_id, secure_1psid, secure_1psidts)
                print(f"Updated {accounts_path} for account {args.account_id}.")
            print(
                json.dumps(
                    {
                        "account_id": args.account_id,
                        "accounts_path": str(accounts_path),
                        "profile_dir": str(profile_dir),
                        "has_secure_1psid": bool(secure_1psid),
                        "has_secure_1psidts": bool(secure_1psidts),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
