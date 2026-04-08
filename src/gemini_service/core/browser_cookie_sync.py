from __future__ import annotations

import json
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

COOKIE_NAMES = ("__Secure-1PSID", "__Secure-1PSIDTS")
DEFAULT_BROWSER_PATHS = {
    "chrome": [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ],
    "edge": [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ],
}


@dataclass(slots=True)
class CookieSyncResult:
    account_id: str
    status: str
    detail: str
    updated: bool = False
    profile_dir: str | None = None
    browser: str | None = None


def load_inventory(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_inventory(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_browser_path(browser: str, explicit_path: str | None = None) -> Path | None:
    if explicit_path:
        candidate = Path(explicit_path).expanduser().resolve()
        return candidate if candidate.exists() else None

    for candidate in DEFAULT_BROWSER_PATHS.get(browser.lower(), []):
        if candidate.exists():
            return candidate
    return None


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_for_cdp(port: int, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    url = f"http://127.0.0.1:{port}/json/version"
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2):
                return
        except URLError:
            time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for browser CDP endpoint on port {port}.")


def _extract_cookies_via_cdp(port: int) -> tuple[str, str] | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for browser cookie sync. Install it in the active environment first."
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        try:
            found: dict[str, str] = {}
            for context in browser.contexts:
                for cookie in context.cookies():
                    if cookie.get("name") in COOKIE_NAMES and cookie.get("domain", "").endswith("google.com"):
                        value = cookie.get("value", "")
                        if value:
                            found[cookie["name"]] = value
            if all(name in found and found[name] for name in COOKIE_NAMES):
                return found["__Secure-1PSID"], found["__Secure-1PSIDTS"]
            return None
        finally:
            browser.close()


def sync_cookies_from_profile(
    *,
    browser: str,
    profile_dir: Path,
    browser_path: Path,
    start_url: str,
    timeout_seconds: int,
    headless: bool,
) -> tuple[str, str]:
    profile_dir.mkdir(parents=True, exist_ok=True)
    port = _find_free_port()
    args = [
        str(browser_path),
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        args.append("--headless=new")
        args.append("about:blank")
    else:
        args.extend(["--new-window", start_url])

    process = subprocess.Popen(args)
    try:
        _wait_for_cdp(port, timeout_seconds=min(timeout_seconds, 20))
        cookies = _extract_cookies_via_cdp(port)
        if cookies is None:
            raise RuntimeError(
                f"Required Gemini cookies were not found in browser profile {profile_dir}."
            )
        return cookies
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def sync_inventory_from_browser_profiles(
    *,
    accounts_path: Path,
    timeout_seconds: int,
    start_url: str,
    default_browser: str = "chrome",
    headless: bool = True,
    only_account_id: str | None = None,
) -> list[CookieSyncResult]:
    payload = load_inventory(accounts_path)
    accounts = payload.get("accounts", [])
    results: list[CookieSyncResult] = []
    dirty = False

    for account in accounts:
        account_id = account.get("account_id", "")
        if only_account_id and account_id != only_account_id:
            continue

        profile_dir_value = account.get("cookie_source_profile_dir")
        if not profile_dir_value:
            results.append(
                CookieSyncResult(
                    account_id=account_id,
                    status="skipped",
                    detail="No cookie_source_profile_dir configured.",
                )
            )
            continue

        browser = str(account.get("cookie_source_browser") or default_browser).lower()
        browser_path = resolve_browser_path(browser, account.get("cookie_source_browser_path"))
        if browser_path is None:
            results.append(
                CookieSyncResult(
                    account_id=account_id,
                    status="error",
                    detail=f"Browser executable for {browser} was not found.",
                    profile_dir=profile_dir_value,
                    browser=browser,
                )
            )
            continue

        profile_dir = Path(profile_dir_value).expanduser()
        if not profile_dir.is_absolute():
            profile_dir = (accounts_path.parent.parent / profile_dir).resolve()
        try:
            secure_1psid, secure_1psidts = sync_cookies_from_profile(
                browser=browser,
                profile_dir=profile_dir,
                browser_path=browser_path,
                start_url=start_url,
                timeout_seconds=timeout_seconds,
                headless=headless,
            )
        except Exception as exc:
            results.append(
                CookieSyncResult(
                    account_id=account_id,
                    status="error",
                    detail=str(exc),
                    profile_dir=str(profile_dir),
                    browser=browser,
                )
            )
            continue

        if (
            account.get("secure_1psid") != secure_1psid
            or account.get("secure_1psidts") != secure_1psidts
        ):
            account["secure_1psid"] = secure_1psid
            account["secure_1psidts"] = secure_1psidts
            dirty = True
            updated = True
        else:
            updated = False

        results.append(
            CookieSyncResult(
                account_id=account_id,
                status="ok",
                detail="Browser profile cookies synced successfully.",
                updated=updated,
                profile_dir=str(profile_dir),
                browser=browser,
            )
        )

    if dirty:
        save_inventory(accounts_path, payload)

    return results
