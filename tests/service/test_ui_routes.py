from __future__ import annotations

import time
from pathlib import Path
import sys

from gemini_service.core.browser_cookie_sync import BrowserLoginSession
from gemini_service.services.account_recovery_service import AccountRecoveryResult


def _copy_mock_accounts(isolated_service_env) -> Path:
    source = Path(__file__).resolve().parents[2] / "config" / "accounts.mock.json"
    target = Path(isolated_service_env["accounts_path"])
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_ui_protected_routes_redirect_to_login(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
    ) as client:
        response = client.get("/ui/chat", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/ui/login"


def test_ui_login_and_admin_page(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_UI_USERNAME="admin",
        GEMINI_SERVICE_UI_PASSWORD="secret-pass",
    ) as client:
        login = client.post(
            "/ui/login",
            data={"username": "admin", "password": "secret-pass"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/ui/chat"

        admin = client.get("/admin")
        assert admin.status_code == 200
        assert "Account Pool and Session Overview" in admin.text


def test_standard_ui_user_cannot_access_admin_page(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_UI_USERNAME="admin",
        GEMINI_SERVICE_UI_PASSWORD="secret-pass",
        GEMINI_SERVICE_UI_USER_USERNAME="analyst",
        GEMINI_SERVICE_UI_USER_PASSWORD="user-pass",
    ) as client:
        login = client.post(
            "/ui/login",
            data={"username": "analyst", "password": "user-pass"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/ui/chat"

        admin = client.get("/admin")
        assert admin.status_code == 403

        chat = client.get("/ui/chat")
        assert chat.status_code == 200
        assert "Automatic account routing" in chat.text


def test_ui_json_login_flow_and_me_endpoint(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_UI_USERNAME="admin",
        GEMINI_SERVICE_UI_PASSWORD="secret-pass",
    ) as client:
        me_before = client.get("/ui/api/me")
        assert me_before.status_code == 200
        assert me_before.json()["authenticated"] is False

        login = client.post(
            "/ui/api/login",
            json={"username": "admin", "password": "secret-pass"},
        )
        assert login.status_code == 200
        assert login.json()["authenticated"] is True
        assert login.json()["role"] == "admin"

        me_after = client.get("/ui/api/me")
        assert me_after.status_code == 200
        assert me_after.json()["authenticated"] is True
        assert me_after.json()["is_admin"] is True

        logout = client.post("/ui/api/logout")
        assert logout.status_code == 200
        assert logout.json()["ok"] is True

        me_final = client.get("/ui/api/me")
        assert me_final.status_code == 200
        assert me_final.json()["authenticated"] is False


def test_ui_session_cookie_is_not_secure_in_local_mock(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_ENV="local-mock",
        GEMINI_SERVICE_UI_USERNAME="admin",
        GEMINI_SERVICE_UI_PASSWORD="secret-pass",
    ) as client:
        login = client.post(
            "/ui/api/login",
            json={"username": "admin", "password": "secret-pass"},
        )
        assert login.status_code == 200
        assert "Secure" not in login.headers.get("set-cookie", "")


def test_ui_admin_overview_and_json_action(client_factory, isolated_service_env):
    accounts_path = _copy_mock_accounts(isolated_service_env)
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=str(accounts_path),
        GEMINI_SERVICE_UI_USERNAME="admin",
        GEMINI_SERVICE_UI_PASSWORD="secret-pass",
    ) as client:
        login = client.post(
            "/ui/api/login",
            json={"username": "admin", "password": "secret-pass"},
        )
        assert login.status_code == 200

        overview = client.get("/ui/api/admin/overview")
        assert overview.status_code == 200
        payload = overview.json()
        assert payload["accounts"]
        assert payload["telemetry"]["account_total"] >= 1

        account_id = payload["accounts"][0]["account_id"]
        action = client.post(f"/ui/api/admin/accounts/{account_id}/actions/refresh")
        assert action.status_code == 200
        assert action.json()["account_id"] == account_id
        assert action.json()["action"] == "refresh"


def test_ui_session_json_endpoint(client_factory, isolated_service_env):
    accounts_path = _copy_mock_accounts(isolated_service_env)
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=str(accounts_path),
        GEMINI_SERVICE_UI_USERNAME="admin",
        GEMINI_SERVICE_UI_PASSWORD="secret-pass",
    ) as client:
        login = client.post(
            "/ui/api/login",
            json={"username": "admin", "password": "secret-pass"},
        )
        assert login.status_code == 200

        created = client.post("/ui/api/sessions", json={"routing_policy": "sticky"})
        assert created.status_code == 200
        session_id = created.json()["session_id"]

        fetched = client.get(f"/ui/api/sessions/{session_id}")
        assert fetched.status_code == 200
        assert fetched.json()["session_id"] == session_id


def test_ui_session_can_be_renamed_and_deleted(client_factory, isolated_service_env):
    accounts_path = _copy_mock_accounts(isolated_service_env)
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=str(accounts_path),
        GEMINI_SERVICE_UI_USERNAME="admin",
        GEMINI_SERVICE_UI_PASSWORD="secret-pass",
    ) as client:
        login = client.post(
            "/ui/api/login",
            json={"username": "admin", "password": "secret-pass"},
        )
        assert login.status_code == 200

        created = client.post("/ui/api/sessions", json={"routing_policy": "sticky"})
        assert created.status_code == 200
        session_id = created.json()["session_id"]

        renamed = client.patch(f"/ui/api/sessions/{session_id}", json={"title": "新的会话标题"})
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "新的会话标题"

        deleted = client.delete(f"/ui/api/sessions/{session_id}")
        assert deleted.status_code == 200
        assert deleted.json()["ok"] is True

        fetched = client.get(f"/ui/api/sessions/{session_id}")
        assert fetched.status_code == 404


def test_ui_admin_dashboard_upsert_account_and_export(client_factory, isolated_service_env):
    accounts_path = _copy_mock_accounts(isolated_service_env)
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=str(accounts_path),
        GEMINI_SERVICE_UI_USERNAME="admin",
        GEMINI_SERVICE_UI_PASSWORD="secret-pass",
    ) as client:
        login = client.post(
            "/ui/api/login",
            json={"username": "admin", "password": "secret-pass"},
        )
        assert login.status_code == 200

        dashboard = client.get("/ui/api/admin/dashboard")
        assert dashboard.status_code == 200
        assert "inventory_accounts" in dashboard.json()

        account = client.post(
            "/ui/api/admin/accounts",
            json={
                "account_id": "ops-account-2",
                "enabled": True,
                "provider_backend": "mock",
                "cookie_source_browser": "chrome",
                "cookie_source_browser_path": str(Path(sys.executable)),
                "cookie_source_profile_dir": "data/chrome-ops-account-2",
                "max_concurrency": 2,
                "cooldown_seconds": 60,
                "request_timeout_seconds": 120,
                "verify_ssl": True,
                "tags": ["ops", "shared"],
            },
        )
        assert account.status_code == 200
        assert account.json()["account_id"] == "ops-account-2"

        created = client.post("/ui/api/sessions", json={"routing_policy": "sticky"})
        assert created.status_code == 200
        session_id = created.json()["session_id"]

        exported = client.get(f"/ui/api/admin/sessions/{session_id}/export?format=json")
        assert exported.status_code == 200
        assert exported.headers["content-disposition"].startswith("attachment; filename=")
        assert '"session_id"' in exported.text


def test_ui_admin_account_can_be_deleted(client_factory, isolated_service_env):
    accounts_path = _copy_mock_accounts(isolated_service_env)
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=str(accounts_path),
        GEMINI_SERVICE_UI_USERNAME="admin",
        GEMINI_SERVICE_UI_PASSWORD="secret-pass",
    ) as client:
        login = client.post(
            "/ui/api/login",
            json={"username": "admin", "password": "secret-pass"},
        )
        assert login.status_code == 200

        created = client.post(
            "/ui/api/admin/accounts",
            json={
                "account_id": "ops-delete-1",
                "enabled": True,
                "provider_backend": "mock",
                "cookie_source_browser": "chrome",
                "cookie_source_browser_path": str(Path(sys.executable)),
                "cookie_source_profile_dir": "data/chrome-ops-delete-1",
                "max_concurrency": 1,
                "cooldown_seconds": 60,
                "request_timeout_seconds": 120,
                "verify_ssl": True,
                "tags": ["ops"],
            },
        )
        assert created.status_code == 200

        deleted = client.delete("/ui/api/admin/accounts/ops-delete-1")
        assert deleted.status_code == 200
        assert deleted.json()["ok"] is True

        dashboard = client.get("/ui/api/admin/dashboard")
        assert dashboard.status_code == 200
        account_ids = [item["account_id"] for item in dashboard.json()["inventory_accounts"]]
        assert "ops-delete-1" not in account_ids


def test_ui_admin_reauth_job_flow(client_factory, monkeypatch, isolated_service_env):
    accounts_path = _copy_mock_accounts(isolated_service_env)

    class _DummyProcess:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    async def _fake_recover_account(self, account_id, *, allow_browser_launch, browser_session=None, browser=None):
        if browser_session is None:
            return AccountRecoveryResult(
                account_id=account_id,
                status="awaiting_login",
                code="interactive_login_required",
                detail="Browser opened. Finish Gemini login in that browser.",
                browser="chrome",
                profile_dir="data/chrome-ops-reauth",
                launched=True,
                recovery_source="browser_launch",
                action="Finish Gemini login in the launched browser.",
                session=BrowserLoginSession(
                    browser="chrome",
                    browser_path=Path(sys.executable),
                    profile_dir=Path("data/chrome-ops-reauth"),
                    port=9222,
                    process=_DummyProcess(),
                    start_url="https://gemini.google.com/app",
                ),
            )
        return AccountRecoveryResult(
            account_id=account_id,
            status="completed",
            code="recovery_completed",
            detail="Cookies synced and Gemini is routable again.",
            browser="chrome",
            profile_dir="data/chrome-ops-reauth",
            recovery_source="browser_session",
            cookie_count=12,
            provider_status="AVAILABLE",
            runtime_state="ready",
        )

    monkeypatch.setattr(
        "gemini_service.services.account_recovery_service.AccountRecoveryService.recover_account",
        _fake_recover_account,
    )

    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=str(accounts_path),
        GEMINI_SERVICE_UI_USERNAME="admin",
        GEMINI_SERVICE_UI_PASSWORD="secret-pass",
    ) as client:
        login = client.post(
            "/ui/api/login",
            json={"username": "admin", "password": "secret-pass"},
        )
        assert login.status_code == 200

        save = client.post(
            "/ui/api/admin/accounts",
            json={
                "account_id": "ops-account-reauth",
                "enabled": True,
                "provider_backend": "mock",
                "cookie_source_browser": "chrome",
                "cookie_source_browser_path": str(Path(sys.executable)),
                "cookie_source_profile_dir": "data/chrome-ops-reauth",
                "max_concurrency": 1,
                "cooldown_seconds": 60,
                "request_timeout_seconds": 120,
                "verify_ssl": True,
                "tags": ["ops"],
            },
        )
        assert save.status_code == 200

        started = client.post("/ui/api/admin/accounts/ops-account-reauth/reauth")
        assert started.status_code == 200
        assert started.json()["status"] in {"queued", "validating_provider"}

        completed = client.post(f"/ui/api/admin/reauth-jobs/{started.json()['job_id']}/complete")
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"
        assert completed.json()["is_terminal"] is True


def test_ui_admin_reauth_job_can_complete_from_existing_profile(client_factory, monkeypatch, isolated_service_env):
    accounts_path = _copy_mock_accounts(isolated_service_env)

    async def _fake_recover_account(self, account_id, *, allow_browser_launch, browser_session=None, browser=None):
        return AccountRecoveryResult(
            account_id=account_id,
            status="completed",
            code="recovery_completed",
            detail="Existing browser profile cookies were synced successfully.",
            updated=True,
            browser="chrome",
            profile_dir="data/chrome-acc-1",
            recovery_source="profile_sync",
            cookie_count=12,
            provider_status="AVAILABLE",
            runtime_state="ready",
        )

    monkeypatch.setattr(
        "gemini_service.services.account_recovery_service.AccountRecoveryService.recover_account",
        _fake_recover_account,
    )

    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=str(accounts_path),
        GEMINI_SERVICE_UI_USERNAME="admin",
        GEMINI_SERVICE_UI_PASSWORD="secret-pass",
    ) as client:
        login = client.post(
            "/ui/api/login",
            json={"username": "admin", "password": "secret-pass"},
        )
        assert login.status_code == 200

        started = client.post("/ui/api/admin/accounts/mock-ready-1/reauth")
        assert started.status_code == 200
        assert started.json()["status"] in {"queued", "validating_provider"}

        time.sleep(0.1)
        jobs = client.get("/ui/api/admin/reauth-jobs")
        assert jobs.status_code == 200
        assert jobs.json()["items"][0]["status"] == "completed"
        assert jobs.json()["items"][0]["launched"] is False
        assert jobs.json()["items"][0]["monitoring"] is False


def test_ui_admin_reauth_job_auto_completes_after_browser_login(client_factory, monkeypatch, isolated_service_env):
    accounts_path = _copy_mock_accounts(isolated_service_env)

    class _DummyProcess:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    cookie_attempts = {"count": 0}

    async def _fake_recover_account(self, account_id, *, allow_browser_launch, browser_session=None, browser=None):
        if browser_session is None:
            return AccountRecoveryResult(
                account_id=account_id,
                status="awaiting_login",
                code="interactive_login_required",
                detail="Browser opened. Finish Gemini login in that browser.",
                browser="chrome",
                profile_dir="data/chrome-ops-auto-reauth",
                launched=True,
                recovery_source="browser_launch",
                action="Finish Gemini login in the launched browser.",
                session=BrowserLoginSession(
                    browser="chrome",
                    browser_path=Path(sys.executable),
                    profile_dir=Path("data/chrome-ops-auto-reauth"),
                    port=9222,
                    process=_DummyProcess(),
                    start_url="https://gemini.google.com/app",
                ),
            )
        cookie_attempts["count"] += 1
        if cookie_attempts["count"] == 1:
            return AccountRecoveryResult(
                account_id=account_id,
                status="awaiting_login",
                code="gemini_not_logged_in",
                detail="Waiting for a valid Gemini session in the launched browser.",
                browser="chrome",
                profile_dir="data/chrome-ops-auto-reauth",
                launched=True,
                recovery_source="browser_session",
                action="Open gemini.google.com/app in the launched browser and make sure chat works there.",
                session=browser_session,
            )
        return AccountRecoveryResult(
            account_id=account_id,
            status="completed",
            code="recovery_completed",
            detail="Cookies synced and Gemini is routable again.",
            browser="chrome",
            profile_dir="data/chrome-ops-auto-reauth",
            recovery_source="browser_session",
            cookie_count=8,
            provider_status="AVAILABLE",
            runtime_state="ready",
        )

    monkeypatch.setattr(
        "gemini_service.services.account_recovery_service.AccountRecoveryService.recover_account",
        _fake_recover_account,
    )

    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=str(accounts_path),
        GEMINI_SERVICE_UI_USERNAME="admin",
        GEMINI_SERVICE_UI_PASSWORD="secret-pass",
        GEMINI_SERVICE_ADMIN_REAUTH_POLL_INTERVAL_SECONDS="0.05",
        GEMINI_SERVICE_ADMIN_REAUTH_TIMEOUT_SECONDS="5",
    ) as client:
        login = client.post(
            "/ui/api/login",
            json={"username": "admin", "password": "secret-pass"},
        )
        assert login.status_code == 200

        save = client.post(
            "/ui/api/admin/accounts",
            json={
                "account_id": "ops-account-auto-reauth",
                "enabled": True,
                "provider_backend": "mock",
                "cookie_source_browser": "chrome",
                "cookie_source_browser_path": str(Path(sys.executable)),
                "cookie_source_profile_dir": "data/chrome-ops-auto-reauth",
                "max_concurrency": 1,
                "cooldown_seconds": 60,
                "request_timeout_seconds": 120,
                "verify_ssl": True,
                "tags": ["ops"],
            },
        )
        assert save.status_code == 200

        started = client.post("/ui/api/admin/accounts/ops-account-auto-reauth/reauth")
        assert started.status_code == 200
        assert started.json()["status"] in {"queued", "validating_provider"}

        time.sleep(0.25)
        jobs = client.get("/ui/api/admin/reauth-jobs")
        assert jobs.status_code == 200
        assert jobs.json()["items"][0]["status"] == "completed"


def test_standard_ui_user_cannot_access_admin_dashboard(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_UI_USERNAME="admin",
        GEMINI_SERVICE_UI_PASSWORD="secret-pass",
        GEMINI_SERVICE_UI_USER_USERNAME="analyst",
        GEMINI_SERVICE_UI_USER_PASSWORD="user-pass",
    ) as client:
        login = client.post(
            "/ui/api/login",
            json={"username": "analyst", "password": "user-pass"},
        )
        assert login.status_code == 200

        dashboard = client.get("/ui/api/admin/dashboard")
        assert dashboard.status_code == 403
