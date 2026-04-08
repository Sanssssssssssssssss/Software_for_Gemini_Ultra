from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

COOKIE_NAMES = ("__Secure-1PSID", "__Secure-1PSIDTS")
WINDOWS_BROWSER_PATHS = {
    "chrome": [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ],
    "edge": [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ],
}
MACOS_BROWSER_PATHS = {
    "chrome": [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")],
    "edge": [Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")],
}
LINUX_BROWSER_PATHS = {
    "chrome": [
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/snap/bin/chromium"),
    ],
    "edge": [
        Path("/usr/bin/microsoft-edge"),
        Path("/usr/bin/microsoft-edge-stable"),
    ],
}
PATH_CANDIDATES = {
    "chrome": ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"),
    "edge": ("microsoft-edge", "microsoft-edge-stable", "msedge"),
}


@dataclass(slots=True)
class CookieSyncResult:
    account_id: str
    status: str
    detail: str
    updated: bool = False
    profile_dir: str | None = None
    browser: str | None = None
    code: str = ""
    action: str | None = None


def load_inventory(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_inventory(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_browser_paths(browser: str) -> list[Path]:
    browser = browser.lower()
    if sys.platform == "win32":
        candidates = WINDOWS_BROWSER_PATHS.get(browser, [])
    elif sys.platform == "darwin":
        candidates = MACOS_BROWSER_PATHS.get(browser, [])
    else:
        candidates = LINUX_BROWSER_PATHS.get(browser, [])
    resolved = [candidate for candidate in candidates if candidate.exists()]
    for name in PATH_CANDIDATES.get(browser, ()):
        which = shutil.which(name)
        if which:
            path = Path(which)
            if path not in resolved:
                resolved.append(path)
    return resolved


def resolve_browser_path(browser: str, explicit_path: str | None = None) -> Path | None:
    if explicit_path:
        candidate = Path(explicit_path).expanduser().resolve()
        return candidate if candidate.exists() else None

    candidates = default_browser_paths(browser)
    return candidates[0] if candidates else None


def normalize_profile_dir(accounts_path: Path, profile_dir_value: str) -> Path:
    profile_dir = Path(profile_dir_value).expanduser()
    if not profile_dir.is_absolute():
        profile_dir = (accounts_path.parent.parent / profile_dir).resolve()
    return profile_dir


def validate_profile_dir(profile_dir: Path) -> tuple[bool, str, str | None]:
    if not profile_dir.exists():
        return (
            False,
            "cookie_profile_missing",
            "Create or log into the configured browser profile first, or disable cookie autosync for this account.",
        )
    if not profile_dir.is_dir():
        return (
            False,
            "cookie_profile_invalid",
            "Point cookie_source_profile_dir at a browser user-data directory.",
        )
    if not os.access(profile_dir, os.R_OK):
        return (
            False,
            "cookie_profile_unreadable",
            "Grant the service user read access to the configured browser profile directory.",
        )
    if not os.access(profile_dir, os.W_OK):
        return (
            False,
            "cookie_profile_unwritable",
            "Grant the service user write access to the configured browser profile directory or use a dedicated persistent profile.",
        )
    return True, "", None


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
    raise TimeoutError("Timed out waiting for the browser debugging endpoint.")


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


def _looks_like_cookie_pair(secure_1psid: str, secure_1psidts: str) -> bool:
    return (
        bool(secure_1psid)
        and bool(secure_1psidts)
        and len(secure_1psid) > 20
        and secure_1psidts.startswith("sidts-")
    )


def sync_cookies_from_profile(
    *,
    browser: str,
    profile_dir: Path,
    browser_path: Path,
    start_url: str,
    timeout_seconds: int,
    headless: bool,
) -> tuple[str, str]:
    valid, _, action = validate_profile_dir(profile_dir)
    if not valid:
        raise FileNotFoundError(action or "The configured browser profile is not ready.")

    port = _find_free_port()
    args = [
        str(browser_path),
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        args.extend(["--headless=new", "about:blank"])
    else:
        args.extend(["--new-window", start_url])

    process = subprocess.Popen(args)
    try:
        _wait_for_cdp(port, timeout_seconds=min(timeout_seconds, 20))
        cookies = _extract_cookies_via_cdp(port)
        if cookies is None:
            raise RuntimeError(
                "Required Gemini cookies were not found in the configured browser profile."
            )
        if not _looks_like_cookie_pair(*cookies):
            raise RuntimeError(
                "The browser profile returned cookie values that do not look like Gemini web session cookies."
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
                    code="cookie_profile_unconfigured",
                    detail="No persistent browser profile is configured for cookie autosync.",
                    action="Set cookie_source_profile_dir for the account or start with --skip-cookie-sync.",
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
                    code="browser_not_found",
                    detail=f"Could not find a {browser} executable on this machine.",
                    profile_dir=profile_dir_value,
                    browser=browser,
                    action="Install the browser, set cookie_source_browser_path, or start with --skip-cookie-sync.",
                )
            )
            continue

        profile_dir = normalize_profile_dir(accounts_path, profile_dir_value)
        valid_profile, profile_code, profile_action = validate_profile_dir(profile_dir)
        if not valid_profile:
            results.append(
                CookieSyncResult(
                    account_id=account_id,
                    status="error",
                    code=profile_code,
                    detail="The configured browser profile directory is missing or unusable.",
                    profile_dir=str(profile_dir),
                    browser=browser,
                    action=profile_action,
                )
            )
            continue

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
                    code="cookie_sync_failed",
                    detail=str(exc),
                    profile_dir=str(profile_dir),
                    browser=browser,
                    action="Log into Gemini in that browser profile, then retry startup or pass --skip-cookie-sync.",
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
                code="cookie_sync_ok",
                detail="Browser profile cookies synced successfully.",
                updated=updated,
                profile_dir=str(profile_dir),
                browser=browser,
            )
        )

    if dirty:
        save_inventory(accounts_path, payload)

    return results
