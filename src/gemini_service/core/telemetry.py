from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..schemas.common import AccountSummary


@dataclass(slots=True)
class TelemetrySnapshot:
    total_requests: int
    total_errors: int


class TelemetryService:
    def __init__(self) -> None:
        self.http_requests_total: dict[tuple[str, str, str], int] = defaultdict(int)
        self.http_request_duration_sum: dict[tuple[str, str], float] = defaultdict(float)
        self.http_request_duration_count: dict[tuple[str, str], int] = defaultdict(int)
        self.provider_calls_total: dict[tuple[str, str, str, str], int] = defaultdict(int)
        self.admin_actions_total: dict[tuple[str, str], int] = defaultdict(int)
        self.service_errors_total: dict[str, int] = defaultdict(int)
        self.account_state_counts: dict[str, int] = defaultdict(int)
        self.account_queue_depth: dict[str, int] = defaultdict(int)
        self.account_in_flight: dict[str, int] = defaultdict(int)
        self.batch_status_counts: dict[str, int] = defaultdict(int)
        self.asset_status_counts: dict[str, int] = defaultdict(int)
        self.session_failovers_total = 0
        self.batch_workers_active = 0
        self.asset_cleanup_runs_total = 0
        self.asset_expired_total = 0
        self.asset_deleted_total = 0
        self.active_requests = 0
        self.account_ready = 0
        self.account_total = 0
        self.chat_sessions = 0
        self.chat_messages = 0
        self.chat_batches = 0
        self.chat_assets = 0
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

    def record_provider_call(
        self,
        account_id: str,
        operation: str,
        result: str,
        error_code: str = "",
    ) -> None:
        self.provider_calls_total[(account_id, operation, result, error_code)] += 1

    def record_admin_action(self, action: str, result: str) -> None:
        self.admin_actions_total[(action, result)] += 1

    def record_service_error(self, code: str) -> None:
        self.service_errors_total[code] += 1

    def record_session_failover(self) -> None:
        self.session_failovers_total += 1

    def update_runtime(
        self,
        ready_accounts: int,
        total_accounts: int,
        sessions: int,
        messages: int,
        batches: int,
        assets: int = 0,
    ) -> None:
        self.account_ready = ready_accounts
        self.account_total = total_accounts
        self.chat_sessions = sessions
        self.chat_messages = messages
        self.chat_batches = batches
        self.chat_assets = assets

    def update_account_pool(self, accounts: list[AccountSummary]) -> None:
        self.account_state_counts = defaultdict(int)
        self.account_queue_depth = defaultdict(int)
        self.account_in_flight = defaultdict(int)
        for account in accounts:
            self.account_state_counts[account.state] += 1
            self.account_queue_depth[account.account_id] = account.queue_depth
            self.account_in_flight[account.account_id] = account.active_requests

    def update_batch_runtime(self, batch_status_counts: dict[str, int], active_workers: int) -> None:
        self.batch_status_counts = defaultdict(int, batch_status_counts)
        self.batch_workers_active = active_workers

    def update_asset_runtime(self, asset_status_counts: dict[str, int]) -> None:
        self.asset_status_counts = defaultdict(int, asset_status_counts)

    def record_asset_cleanup(self, *, expired_count: int, deleted_count: int) -> None:
        self.asset_cleanup_runs_total += 1
        self.asset_expired_total += expired_count
        self.asset_deleted_total += deleted_count

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
                "# HELP gemini_service_provider_calls_total Provider calls by account, operation, result, and error code.",
                "# TYPE gemini_service_provider_calls_total counter",
            ]
        )
        for (account_id, operation, result, error_code), value in sorted(self.provider_calls_total.items()):
            lines.append(
                'gemini_service_provider_calls_total'
                f'{{account_id="{account_id}",operation="{operation}",result="{result}",error_code="{error_code}"}} {value}'
            )

        lines.extend(
            [
                "# HELP gemini_service_admin_actions_total Admin actions by action and result.",
                "# TYPE gemini_service_admin_actions_total counter",
            ]
        )
        for (action, result), value in sorted(self.admin_actions_total.items()):
            lines.append(f'gemini_service_admin_actions_total{{action="{action}",result="{result}"}} {value}')

        lines.extend(
            [
                "# HELP gemini_service_service_errors_total Service errors by code.",
                "# TYPE gemini_service_service_errors_total counter",
            ]
        )
        for code, value in sorted(self.service_errors_total.items()):
            lines.append(f'gemini_service_service_errors_total{{code="{code}"}} {value}')

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
                "# HELP gemini_service_chat_batches_total Number of persisted batch jobs.",
                "# TYPE gemini_service_chat_batches_total gauge",
                f"gemini_service_chat_batches_total {self.chat_batches}",
                "# HELP gemini_service_chat_assets_total Number of persisted media assets.",
                "# TYPE gemini_service_chat_assets_total gauge",
                f"gemini_service_chat_assets_total {self.chat_assets}",
                "# HELP gemini_service_account_states Number of accounts in each runtime state.",
                "# TYPE gemini_service_account_states gauge",
            ]
        )
        for state, value in sorted(self.account_state_counts.items()):
            lines.append(f'gemini_service_account_states{{state="{state}"}} {value}')

        lines.extend(
            [
                "# HELP gemini_service_account_queue_depth Current per-account queue depth.",
                "# TYPE gemini_service_account_queue_depth gauge",
            ]
        )
        for account_id, value in sorted(self.account_queue_depth.items()):
            lines.append(f'gemini_service_account_queue_depth{{account_id="{account_id}"}} {value}')

        lines.extend(
            [
                "# HELP gemini_service_account_in_flight Current in-flight requests per account.",
                "# TYPE gemini_service_account_in_flight gauge",
            ]
        )
        for account_id, value in sorted(self.account_in_flight.items()):
            lines.append(f'gemini_service_account_in_flight{{account_id="{account_id}"}} {value}')

        lines.extend(
            [
                "# HELP gemini_service_batch_status Number of batches in each status.",
                "# TYPE gemini_service_batch_status gauge",
            ]
        )
        for status, value in sorted(self.batch_status_counts.items()):
            lines.append(f'gemini_service_batch_status{{status="{status}"}} {value}')

        lines.extend(
            [
                "# HELP gemini_service_asset_status Number of assets in each status.",
                "# TYPE gemini_service_asset_status gauge",
            ]
        )
        for status, value in sorted(self.asset_status_counts.items()):
            lines.append(f'gemini_service_asset_status{{status="{status}"}} {value}')

        lines.extend(
            [
                "# HELP gemini_service_batch_workers_active Number of active in-process batch workers.",
                "# TYPE gemini_service_batch_workers_active gauge",
                f"gemini_service_batch_workers_active {self.batch_workers_active}",
                "# HELP gemini_service_session_failovers_total Number of persisted session failovers.",
                "# TYPE gemini_service_session_failovers_total counter",
                f"gemini_service_session_failovers_total {self.session_failovers_total}",
                "# HELP gemini_service_asset_cleanup_runs_total Number of asset cleanup passes.",
                "# TYPE gemini_service_asset_cleanup_runs_total counter",
                f"gemini_service_asset_cleanup_runs_total {self.asset_cleanup_runs_total}",
                "# HELP gemini_service_asset_expired_total Number of expired assets removed from storage.",
                "# TYPE gemini_service_asset_expired_total counter",
                f"gemini_service_asset_expired_total {self.asset_expired_total}",
                "# HELP gemini_service_asset_deleted_total Number of orphan assets deleted from storage.",
                "# TYPE gemini_service_asset_deleted_total counter",
                f"gemini_service_asset_deleted_total {self.asset_deleted_total}",
            ]
        )

        return ("\n".join(lines) + "\n").encode("utf-8")

    def snapshot(self) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            total_requests=self.total_requests,
            total_errors=self.total_errors,
        )
