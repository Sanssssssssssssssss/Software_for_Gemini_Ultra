from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict


@dataclass(slots=True)
class TelemetrySnapshot:
    total_requests: int
    total_errors: int


class TelemetryService:
    def __init__(self) -> None:
        self.http_requests_total: dict[tuple[str, str, str], int] = defaultdict(int)
        self.http_request_duration_sum: dict[tuple[str, str], float] = defaultdict(float)
        self.http_request_duration_count: dict[tuple[str, str], int] = defaultdict(int)
        self.active_requests = 0
        self.account_ready = 0
        self.account_total = 0
        self.chat_sessions = 0
        self.chat_messages = 0
        self.total_requests = 0
        self.total_errors = 0

    def request_started(self) -> None:
        self.active_requests += 1

    def request_finished(self, method: str, path: str, status: int, duration: float) -> None:
        self.active_requests = max(0, self.active_requests - 1)
        self.http_requests_total[(method, path, str(status))] += 1
        self.http_request_duration_sum[(method, path)] += duration
        self.http_request_duration_count[(method, path)] += 1
        self.total_requests += 1
        if status >= 500:
            self.total_errors += 1

    def update_runtime(self, ready_accounts: int, total_accounts: int, sessions: int, messages: int) -> None:
        self.account_ready = ready_accounts
        self.account_total = total_accounts
        self.chat_sessions = sessions
        self.chat_messages = messages

    def render_prometheus(self) -> bytes:
        lines = [
            "# HELP gemini_service_http_requests_total HTTP requests handled by the Gemini internal service.",
            "# TYPE gemini_service_http_requests_total counter",
        ]
        for (method, path, status), value in sorted(self.http_requests_total.items()):
            lines.append(
                f'gemini_service_http_requests_total{{method="{method}",path="{path}",status="{status}"}} {value}'
            )

        lines.extend(
            [
                "# HELP gemini_service_http_request_duration_seconds_sum Total request latency in seconds.",
                "# TYPE gemini_service_http_request_duration_seconds_sum counter",
            ]
        )
        for (method, path), value in sorted(self.http_request_duration_sum.items()):
            lines.append(
                f'gemini_service_http_request_duration_seconds_sum{{method="{method}",path="{path}"}} {value}'
            )

        lines.extend(
            [
                "# HELP gemini_service_http_request_duration_seconds_count Number of latency samples.",
                "# TYPE gemini_service_http_request_duration_seconds_count counter",
            ]
        )
        for (method, path), value in sorted(self.http_request_duration_count.items()):
            lines.append(
                f'gemini_service_http_request_duration_seconds_count{{method="{method}",path="{path}"}} {value}'
            )

        lines.extend(
            [
                "# HELP gemini_service_http_requests_in_flight Current in-flight HTTP requests.",
                "# TYPE gemini_service_http_requests_in_flight gauge",
                f"gemini_service_http_requests_in_flight {self.active_requests}",
                "# HELP gemini_service_accounts_ready Number of accounts currently ready to accept requests.",
                "# TYPE gemini_service_accounts_ready gauge",
                f"gemini_service_accounts_ready {self.account_ready}",
                "# HELP gemini_service_accounts_total Number of accounts loaded into the service.",
                "# TYPE gemini_service_accounts_total gauge",
                f"gemini_service_accounts_total {self.account_total}",
                "# HELP gemini_service_chat_sessions_total Number of persisted chat sessions.",
                "# TYPE gemini_service_chat_sessions_total gauge",
                f"gemini_service_chat_sessions_total {self.chat_sessions}",
                "# HELP gemini_service_chat_messages_total Number of persisted chat messages.",
                "# TYPE gemini_service_chat_messages_total gauge",
                f"gemini_service_chat_messages_total {self.chat_messages}",
            ]
        )
        return ("\n".join(lines) + "\n").encode("utf-8")

    def snapshot(self) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            total_requests=self.total_requests,
            total_errors=self.total_errors,
        )
