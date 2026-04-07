from __future__ import annotations


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
