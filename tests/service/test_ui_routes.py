from __future__ import annotations

from pathlib import Path
import sys

from gemini_service.core.browser_cookie_sync import BrowserLoginSession


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


def test_ui_admin_reauth_job_flow(client_factory, monkeypatch, isolated_service_env):
    accounts_path = _copy_mock_accounts(isolated_service_env)

    class _DummyProcess:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    def _fake_launch_browser_login_session(**kwargs):
        return BrowserLoginSession(
            browser=kwargs["browser"],
            browser_path=Path(kwargs["browser_path"]),
            profile_dir=Path(kwargs["profile_dir"]),
            port=9222,
            process=_DummyProcess(),
            start_url=kwargs["start_url"],
        )

    monkeypatch.setattr(
        "gemini_service.services.admin_console_service.launch_browser_login_session",
        _fake_launch_browser_login_session,
    )
    monkeypatch.setattr(
        "gemini_service.services.admin_console_service.collect_cookies_from_browser_session",
        lambda session, timeout_seconds: ("cookie-value", "sidts-cookie-value", 12),
    )
    monkeypatch.setattr(
        "gemini_service.core.browser_cookie_sync.terminate_browser_login_session",
        lambda session: None,
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
        assert started.json()["status"] == "awaiting_login"

        completed = client.post(f"/ui/api/admin/reauth-jobs/{started.json()['job_id']}/complete")
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"


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
