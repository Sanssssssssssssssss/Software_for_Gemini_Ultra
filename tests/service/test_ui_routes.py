from __future__ import annotations

from pathlib import Path


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


def test_ui_admin_overview_and_json_action(client_factory):
    accounts_path = Path(__file__).resolve().parents[2] / "config" / "accounts.mock.json"
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


def test_ui_session_json_endpoint(client_factory):
    accounts_path = Path(__file__).resolve().parents[2] / "config" / "accounts.mock.json"
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
