from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from gemini_service.core.config import Settings
from gemini_service.schemas.accounts import load_account_inventory, save_account_inventory
from gemini_service.services.account_pool import AccountPool
from gemini_service.services.account_recovery_service import AccountRecoveryService
from gemini_service.services.persistent_browser_manager import PersistentBrowserManager


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap or reauthenticate a managed Gemini browser profile without closing the browser window."
    )
    parser.add_argument("--account-id", default="primary-ultra-1", help="Account ID in config/accounts.json to update.")
    parser.add_argument("--accounts-path", default=str(REPO_ROOT / "config" / "accounts.json"))
    parser.add_argument("--browser", choices=("chrome", "edge"), default="chrome")
    parser.add_argument("--browser-path", default="")
    parser.add_argument("--profile-dir", default="")
    parser.add_argument("--start-url", default="https://gemini.google.com/app")
    parser.add_argument("--wait-timeout-seconds", type=int, default=900)
    parser.add_argument("--keep-open", action="store_true", default=True)
    parser.add_argument("--focus-existing", action="store_true", default=True)
    parser.add_argument("--sync-now", action="store_true")
    parser.add_argument("--stop-after-sync", action="store_true", default=False)
    return parser.parse_args()


async def _main() -> int:
    args = parse_args()
    accounts_path = Path(args.accounts_path).resolve()
    inventory, _, _ = load_account_inventory(accounts_path, save_clean=True)
    account = next((item for item in inventory.accounts if item.account_id == args.account_id), None)
    if account is None:
        raise SystemExit(f"Account {args.account_id!r} was not found in {accounts_path}.")

    update = {}
    if args.browser:
        update["cookie_source_browser"] = args.browser
    if args.browser_path:
        update["cookie_source_browser_path"] = args.browser_path
    if args.profile_dir:
        update["cookie_source_profile_dir"] = args.profile_dir
    if update:
        inventory.accounts = [
            item.model_copy(update=update) if item.account_id == args.account_id else item
            for item in inventory.accounts
        ]
        save_account_inventory(accounts_path, inventory.model_dump(mode="json"))
        inventory, _, _ = load_account_inventory(accounts_path, save_clean=True)
        account = next(item for item in inventory.accounts if item.account_id == args.account_id)

    settings = Settings(
        accounts_config_path=str(accounts_path),
        cookie_autosync_enabled=True,
        cookie_autosync_start_url=args.start_url,
        browser_manager_enabled=True,
    )
    pool = AccountPool(settings)
    browser_manager = PersistentBrowserManager(settings)
    await pool.start()
    await browser_manager.start()
    recovery = AccountRecoveryService(settings=settings, pool=pool, browser_manager=browser_manager)
    try:
        if args.focus_existing:
            await browser_manager.focus_session(account)
        else:
            await browser_manager.ensure_session(account)

        if args.sync_now:
            result = await recovery.recover_account(args.account_id, allow_browser_launch=False)
            print(json.dumps(asdict(result), ensure_ascii=False, default=str))
            return 0 if result.status == "completed" else 1

        deadline = time.time() + args.wait_timeout_seconds
        print("Managed browser session is ready. Finish Gemini login in that window; the script will validate automatically.")
        while time.time() < deadline:
            result = await recovery.recover_account(args.account_id, allow_browser_launch=False)
            print(json.dumps(asdict(result), ensure_ascii=False, default=str))
            if result.status == "completed":
                if args.stop_after_sync:
                    await browser_manager.stop_session(args.account_id)
                return 0
            await asyncio.sleep(settings.browser_recovery_interval_seconds)
        print("Timed out waiting for provider validation to reach AVAILABLE.")
        return 1
    finally:
        if args.stop_after_sync and not args.keep_open:
            await browser_manager.stop_session(args.account_id)
        await browser_manager.close()
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
