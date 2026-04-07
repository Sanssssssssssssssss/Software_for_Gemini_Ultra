from __future__ import annotations

import argparse
import json
import sys

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test for Gemini Internal Service.")
    parser.add_argument("--base-url", required=True, help="Service base URL, for example http://127.0.0.1:8000")
    parser.add_argument("--token", required=True, help="Bearer token configured in GEMINI_SERVICE_API_TOKENS")
    parser.add_argument("--account-id", default=None, help="Optional preferred account id for session creation")
    parser.add_argument("--message", default="Please introduce yourself in one sentence.", help="Smoke test prompt")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    headers = {"Authorization": f"Bearer {args.token}"}

    with httpx.Client(base_url=args.base_url, headers=headers, timeout=60.0) as client:
        health = client.get("/healthz")
        readiness = client.get("/readyz")
        accounts = client.get("/v1/accounts")

        session_payload = {"routing_policy": "sticky"}
        if args.account_id:
            session_payload["account_id"] = args.account_id

        session_response = client.post("/v1/sessions", json=session_payload)
        session_response.raise_for_status()
        session = session_response.json()

        message_response = client.post(
            "/v1/messages",
            json={
                "session_id": session["session_id"],
                "message": args.message,
                "stream": False,
                "idempotency_key": "smoke-test",
            },
        )
        message_response.raise_for_status()
        message = message_response.json()

        history = client.get(f"/v1/sessions/{session['session_id']}/history")
        history.raise_for_status()

    result = {
        "healthz_status": health.status_code,
        "readyz_status": readiness.status_code,
        "accounts_status": accounts.status_code,
        "session_id": session["session_id"],
        "account_id": session["account_id"],
        "message_preview": message["content"][:200],
        "history_items": len(history.json()["items"]),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
