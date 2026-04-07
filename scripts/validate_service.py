from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
DEFAULT_ACCOUNTS = REPO_ROOT / "config" / "accounts.mock.json"
ADMIN_TOKEN = "validator-admin-token"
USER_TOKEN = "validator-user-token"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Closed-loop validation for Gemini Internal Service.")
    parser.add_argument("--accounts-config", default=str(DEFAULT_ACCOUNTS))
    parser.add_argument("--workers-matrix", default="1,2,5,10,20")
    parser.add_argument("--duration-seconds", type=int, default=2)
    parser.add_argument("--output", default="")
    return parser.parse_args()


def find_free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_service(base_url: str, timeout_seconds: float = 20.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/healthz", timeout=2.0)
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.3)
    raise RuntimeError("Service did not become healthy within the timeout window.")


def run_load_matrix(base_url: str, workers_matrix: list[int], duration_seconds: int) -> list[dict]:
    results: list[dict] = []
    for workers in workers_matrix:
        command = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "load_test.py"),
            "--base-url",
            base_url,
            "--token",
            ADMIN_TOKEN,
            "--workers",
            str(workers),
            "--duration-seconds",
            str(duration_seconds),
            "--stream-ratio",
            "0.4",
            "--session-mode",
            "mixed",
            "--new-session-ratio",
            "0.35",
        ]
        completed = subprocess.run(
            command,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
            timeout=max(60, duration_seconds * 20),
        )
        results.append(json.loads(completed.stdout))
    return results


def _tail_text(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:]


def main() -> int:
    args = parse_args()
    workers_matrix = [int(item) for item in args.workers_matrix.split(",") if item.strip()]

    with tempfile.TemporaryDirectory(prefix="gemini-service-validate-") as temp_dir:
        temp_path = Path(temp_dir)
        database_path = temp_path / "validation.db"
        stdout_log = temp_path / "service.stdout.log"
        stderr_log = temp_path / "service.stderr.log"
        port = find_free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(SRC_DIR),
                "GEMINI_SERVICE_ENV": "validation",
                "GEMINI_SERVICE_REQUIRE_AUTH": "true",
                "GEMINI_SERVICE_API_TOKENS": ",".join(
                    [
                        f"validator-admin|{ADMIN_TOKEN}|admin",
                        f"validator-user|{USER_TOKEN}|user",
                    ]
                ),
                "GEMINI_SERVICE_DATABASE_URL": f"sqlite+aiosqlite:///{database_path.as_posix()}",
                "GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH": str(Path(args.accounts_config).resolve()),
                "GEMINI_SERVICE_UI_USERNAME": "validator-admin",
                "GEMINI_SERVICE_UI_PASSWORD": "validator-admin-pass",
                "GEMINI_SERVICE_UI_USER_USERNAME": "validator-user",
                "GEMINI_SERVICE_UI_USER_PASSWORD": "validator-user-pass",
                "GEMINI_SERVICE_UI_SESSION_SECRET": "validation-session-secret-1234567890",
            }
        )

        with stdout_log.open("w", encoding="utf-8") as stdout_handle, stderr_log.open(
            "w", encoding="utf-8"
        ) as stderr_handle:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "gemini_service.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=str(REPO_ROOT),
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
            )

            try:
                try:
                    wait_for_service(base_url)
                except Exception as exc:  # pragma: no cover - best effort diagnostics
                    raise RuntimeError(
                        "Service failed to start.\n"
                        f"stdout tail:\n{_tail_text(stdout_log)}\n\n"
                        f"stderr tail:\n{_tail_text(stderr_log)}"
                    ) from exc

                summary: dict[str, object] = {"base_url": base_url}
                headers_admin = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
                headers_user = {"Authorization": f"Bearer {USER_TOKEN}"}

                with httpx.Client(base_url=base_url, timeout=30.0) as client:
                    health = client.get("/healthz")
                    ready = client.get("/readyz")
                    accounts = client.get("/v1/accounts", headers=headers_admin)
                    user_session = client.post("/v1/sessions", headers=headers_user, json={"routing_policy": "sticky"})
                    forbidden_pin = client.post(
                        "/v1/sessions",
                        headers=headers_user,
                        json={"routing_policy": "sticky", "account_id": "mock-ready-1"},
                    )
                    admin_session = client.post(
                        "/v1/sessions",
                        headers=headers_admin,
                        json={
                            "routing_policy": "sticky",
                            "account_id": "mock-ready-1",
                            "allow_failover": True,
                        },
                    )
                    admin_message = client.post(
                        "/v1/messages",
                        headers=headers_admin,
                        json={
                            "session_id": admin_session.json()["session_id"],
                            "message": "phase5 validation ping",
                            "stream": False,
                        },
                    )
                    failover_session = client.post(
                        "/v1/sessions",
                        headers=headers_admin,
                        json={
                            "routing_policy": "sticky",
                            "account_id": "mock-flaky-1",
                            "allow_failover": True,
                        },
                    )
                    admin_mark_reauth = client.post(
                        "/v1/admin/accounts/mock-flaky-1/actions/mark-reauth-required",
                        headers=headers_admin,
                    )
                    failover_message = client.post(
                        "/v1/messages",
                        headers=headers_admin,
                        json={
                            "session_id": failover_session.json()["session_id"],
                            "message": "trigger failover validation",
                            "stream": False,
                        },
                    )
                    batch = client.post(
                        "/v1/batches",
                        headers=headers_user,
                        json={
                            "items": [
                                {"external_id": "ok-1", "prompt": "hello"},
                                {"external_id": "fail-1", "prompt": "please fail"},
                            ]
                        },
                    )

                    batch_result = batch.json()
                    for _ in range(40):
                        polled = client.get(f"/v1/batches/{batch_result['batch_id']}", headers=headers_user)
                        batch_result = polled.json()
                        if batch_result["status"] in {"completed", "partial", "failed"}:
                            break
                        time.sleep(0.1)

                    admin_disable = client.post(
                        "/v1/admin/accounts/mock-flaky-1/actions/disable-runtime",
                        headers=headers_admin,
                    )
                    admin_enable = client.post(
                        "/v1/admin/accounts/mock-flaky-1/actions/enable-runtime",
                        headers=headers_admin,
                    )
                    metrics = client.get("/metrics")

                load_matrix = run_load_matrix(base_url, workers_matrix, args.duration_seconds)

                summary["healthz"] = {"status_code": health.status_code, "payload": health.json()}
                summary["readyz"] = {"status_code": ready.status_code, "payload": ready.json()}
                summary["accounts"] = accounts.json()
                summary["user_session_status"] = user_session.status_code
                summary["user_pinned_forbidden_status"] = forbidden_pin.status_code
                summary["admin_message"] = admin_message.json()
                summary["failover_validation"] = {
                    "mark_reauth_status": admin_mark_reauth.status_code,
                    "response": failover_message.json(),
                }
                summary["batch"] = batch_result
                summary["admin_actions"] = {
                    "disable_status": admin_disable.status_code,
                    "enable_status": admin_enable.status_code,
                }
                summary["metrics_present"] = {
                    "provider_calls": "gemini_service_provider_calls_total" in metrics.text,
                    "batch_status": "gemini_service_batch_status" in metrics.text,
                    "admin_actions": "gemini_service_admin_actions_total" in metrics.text,
                    "service_errors": "gemini_service_service_errors_total" in metrics.text,
                    "session_failovers": "gemini_service_session_failovers_total" in metrics.text,
                }
                summary["load_matrix"] = load_matrix

                output = json.dumps(summary, ensure_ascii=False, indent=2)
                if args.output:
                    output_path = Path(args.output)
                    output_path.write_text(output, encoding="utf-8")
                print(output)
                return 0
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
