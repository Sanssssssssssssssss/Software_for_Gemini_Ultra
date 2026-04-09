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

from gemini_webapi.utils.rotate_1psidts import _get_cookie_cache_dir

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


@dataclass(slots=True)
class BrowserLoginSession:
    browser: str
    browser_path: Path
    profile_dir: Path
    port: int
    process: subprocess.Popen[Any]
    start_url: str


def _is_process_running(process: subprocess.Popen[Any]) -> bool:
    return process.poll() is None


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


def ensure_profile_dir(profile_dir: Path) -> Path:
    profile_dir = profile_dir.expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir


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


def _extract_cookies_via_cdp(port: int) -> tuple[str, str, list[dict[str, Any]]] | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for browser cookie sync. Install it in the active environment first."
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        try:
            cookies: list[dict[str, Any]] = []
            for context in browser.contexts:
                for cookie in context.cookies():
                    domain = str(cookie.get("domain", "")).lstrip(".").lower()
                    if domain == "google.com" or domain.endswith(".google.com") or domain == "google.co.uk" or domain.endswith(".google.co.uk"):
                        cookies.append(cookie)
            return _select_cookie_bundle(cookies)
        finally:
            browser.close()


def _cookie_rank(cookie: dict[str, Any]) -> tuple[int, int]:
    domain = str(cookie.get("domain", "")).lstrip(".").lower()
    if domain == "google.com":
        domain_rank = 0
    elif domain.endswith(".google.com"):
        domain_rank = 1
    elif domain == "google.co.uk":
        domain_rank = 2
    elif domain.endswith(".google.co.uk"):
        domain_rank = 3
    else:
        domain_rank = 9
    path_rank = 0 if cookie.get("path") == "/" else 1
    return (domain_rank, path_rank)


def _select_cookie_bundle(cookies: list[dict[str, Any]]) -> tuple[str, str, list[dict[str, Any]]] | None:
    by_name: dict[str, list[dict[str, Any]]] = {name: [] for name in COOKIE_NAMES}
    for cookie in cookies:
        name = cookie.get("name")
        if name in by_name and cookie.get("value"):
            by_name[name].append(cookie)

    if not all(by_name[name] for name in COOKIE_NAMES):
        return None

    secure_1psid = sorted(by_name["__Secure-1PSID"], key=_cookie_rank)[0]["value"]
    secure_1psidts = sorted(by_name["__Secure-1PSIDTS"], key=_cookie_rank)[0]["value"]
    return secure_1psid, secure_1psidts, cookies


def _write_cookie_cache(secure_1psid: str, cookies: list[dict[str, Any]]) -> int:
    cache_dir = _get_cookie_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f".cached_cookies_{secure_1psid}.json"
    filtered: list[dict[str, Any]] = []
    for cookie in cookies:
        domain = str(cookie.get("domain", "")).lstrip(".").lower()
        if not (domain == "google.com" or domain.endswith(".google.com")):
            continue
        expires = cookie.get("expires")
        if expires and expires < time.time():
            continue
        filtered.append(
            {
                "name": cookie.get("name"),
                "value": cookie.get("value"),
                "domain": cookie.get("domain", ".google.com"),
                "path": cookie.get("path", "/"),
                "expires": expires,
            }
        )
    cache_path.write_text(json.dumps(filtered, ensure_ascii=False), encoding="utf-8")
    return len(filtered)


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
) -> tuple[str, str, int]:
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
        extracted = _extract_cookies_via_cdp(port)
        if extracted is None:
            raise RuntimeError(
                "Required Gemini cookies were not found in the configured browser profile."
            )
        secure_1psid, secure_1psidts, cookies = extracted
        if not _looks_like_cookie_pair(secure_1psid, secure_1psidts):
            raise RuntimeError(
                "The browser profile returned cookie values that do not look like Gemini web session cookies."
            )
        cached_count = _write_cookie_cache(secure_1psid, cookies)
        return secure_1psid, secure_1psidts, cached_count
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def launch_browser_login_session(
    *,
    browser: str,
    profile_dir: Path,
    browser_path: Path,
    start_url: str,
) -> BrowserLoginSession:
    profile_dir = ensure_profile_dir(profile_dir)
    port = _find_free_port()
    args = [
        str(browser_path),
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        start_url,
    ]
    process = subprocess.Popen(args)
    try:
        _wait_for_cdp(port, timeout_seconds=20)
    except Exception as exc:
        if _is_process_running(process):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        raise RuntimeError(
            "The browser was launched but the debugging endpoint did not become ready. "
            "Close any stale browser windows using the same profile and try again."
        ) from exc
    return BrowserLoginSession(
        browser=browser,
        browser_path=browser_path,
        profile_dir=profile_dir,
        port=port,
        process=process,
        start_url=start_url,
    )


def terminate_browser_login_session(session: BrowserLoginSession) -> None:
    if session.process.poll() is not None:
        return
    session.process.terminate()
    try:
        session.process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        session.process.kill()
        session.process.wait(timeout=10)


def collect_cookies_from_browser_session(
    session: BrowserLoginSession,
    *,
    timeout_seconds: int,
) -> tuple[str, str, int]:
    _wait_for_cdp(session.port, timeout_seconds=min(timeout_seconds, 20))
    extracted = _extract_cookies_via_cdp(session.port)
    if extracted is None:
        raise RuntimeError(
            "Required Gemini cookies were not found in the configured browser profile."
        )
    secure_1psid, secure_1psidts, cookies = extracted
    if not _looks_like_cookie_pair(secure_1psid, secure_1psidts):
        raise RuntimeError(
            "The browser profile returned cookie values that do not look like Gemini web session cookies."
        )
    cached_count = _write_cookie_cache(secure_1psid, cookies)
    return secure_1psid, secure_1psidts, cached_count


def sync_single_account_from_inventory(
    *,
    accounts_path: Path,
    account_id: str,
    timeout_seconds: int,
    start_url: str,
    default_browser: str = "chrome",
    headless: bool = True,
) -> CookieSyncResult:
    results = sync_inventory_from_browser_profiles(
        accounts_path=accounts_path,
        timeout_seconds=timeout_seconds,
        start_url=start_url,
        default_browser=default_browser,
        headless=headless,
        only_account_id=account_id,
    )
    if not results:
        return CookieSyncResult(
            account_id=account_id,
            status="error",
            code="account_not_found",
            detail=f"Account {account_id} was not found in the inventory.",
            action="Check the account ID and retry the reauthentication flow.",
        )
    return results[0]


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
            secure_1psid, secure_1psidts, cached_count = sync_cookies_from_profile(
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
                    detail=f"Browser profile cookies synced successfully and refreshed {cached_count} cached google.com cookies.",
                    updated=updated,
                    profile_dir=str(profile_dir),
                    browser=browser,
                )
            )

    if dirty:
        save_inventory(accounts_path, payload)

    return results
