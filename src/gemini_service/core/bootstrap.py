from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ..schemas.accounts import AccountInventory
from ..schemas.common import BootstrapCheck, BootstrapStatusResponse

if TYPE_CHECKING:
    from .config import Settings
    from ..services.account_pool import AccountPool


_PLACEHOLDER_MARKERS = {
    "",
    "change-me",
    "change-me-ui-password",
    "change-me-session-secret",
    "replace-me",
    "replace-me-if-required",
}


def _is_placeholder(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    return (
        normalized in _PLACEHOLDER_MARKERS
        or normalized.startswith("replace-")
        or "change-me" in normalized
    )


def evaluate_bootstrap_status(
    settings: "Settings",
    pool: "AccountPool | None" = None,
) -> BootstrapStatusResponse:
    checks: list[BootstrapCheck] = []

    env_path = Path(".env")
    if env_path.exists():
        checks.append(
            BootstrapCheck(
                name="env_file",
                status="pass",
                detail=f"检测到环境文件：{env_path.resolve()}",
            )
        )
    else:
        checks.append(
            BootstrapCheck(
                name="env_file",
                status="fail",
                detail="未找到 .env 文件。",
                action="运行 `py scripts/bootstrap_local.py` 生成本地配置模板。",
            )
        )

    if not settings.require_auth:
        checks.append(
            BootstrapCheck(
                name="api_auth",
                status="warn",
                detail="当前禁用了 API Bearer 认证，适合本地验证，不建议在局域网长期裸奔。",
                action="上线前把 `GEMINI_SERVICE_REQUIRE_AUTH` 改回 true，并配置令牌。",
            )
        )
    elif not settings.api_token_values:
        checks.append(
            BootstrapCheck(
                name="api_auth",
                status="fail",
                detail="已启用 API 认证，但没有配置 Bearer Token。",
                action="在 .env 中设置 `GEMINI_SERVICE_API_TOKENS`。",
            )
        )
    elif any(_is_placeholder(token) for token in settings.api_token_values):
        checks.append(
            BootstrapCheck(
                name="api_auth",
                status="fail",
                detail="API Bearer Token 仍是占位值。",
                action="把 `GEMINI_SERVICE_API_TOKENS` 改成随机高强度值。",
            )
        )
    else:
        checks.append(
            BootstrapCheck(
                name="api_auth",
                status="pass",
                detail=f"已配置 {len(settings.api_token_values)} 个 API Bearer Token。",
            )
        )

    if _is_placeholder(settings.ui_password):
        checks.append(
            BootstrapCheck(
                name="ui_password",
                status="fail",
                detail="UI 登录密码仍是默认占位值。",
                action="修改 `GEMINI_SERVICE_UI_PASSWORD`，避免局域网内被直接猜中。",
            )
        )
    else:
        checks.append(
            BootstrapCheck(
                name="ui_password",
                status="pass",
                detail="UI 登录密码已设置为非默认值。",
            )
        )

    if _is_placeholder(settings.ui_session_secret):
        checks.append(
            BootstrapCheck(
                name="ui_session_secret",
                status="fail",
                detail="UI session 签名密钥仍是默认占位值。",
                action="修改 `GEMINI_SERVICE_UI_SESSION_SECRET`，避免 session 被伪造。",
            )
        )
    else:
        checks.append(
            BootstrapCheck(
                name="ui_session_secret",
                status="pass",
                detail="UI session 签名密钥已设置为非默认值。",
            )
        )

    accounts_path = Path(settings.accounts_config_path)
    if not accounts_path.exists():
        checks.append(
            BootstrapCheck(
                name="accounts_file",
                status="fail",
                detail=f"未找到账号清单文件：{accounts_path}",
                action="复制 `config/accounts.example.json` 到目标路径，并填入真实 cookies。",
            )
        )
    else:
        try:
            inventory = AccountInventory.model_validate(
                json.loads(accounts_path.read_text(encoding="utf-8"))
            )
        except Exception as exc:
            checks.append(
                BootstrapCheck(
                    name="accounts_file",
                    status="fail",
                    detail=f"账号清单文件无法解析：{exc}",
                    action="修正 JSON 结构，确保包含 `accounts` 数组。",
                )
            )
        else:
            if not inventory.accounts:
                checks.append(
                    BootstrapCheck(
                        name="accounts_file",
                        status="fail",
                        detail="账号清单文件存在，但 `accounts` 为空。",
                        action="至少配置一个可用的 Gemini Web 账号。",
                    )
                )
            else:
                placeholder_accounts = [
                    account.account_id
                    for account in inventory.accounts
                    if _is_placeholder(account.secure_1psid)
                    or _is_placeholder(account.secure_1psidts)
                ]
                if placeholder_accounts:
                    checks.append(
                        BootstrapCheck(
                            name="accounts_credentials",
                            status="fail",
                            detail="以下账号仍是占位 cookie：" + ", ".join(placeholder_accounts),
                            action="把 `secure_1psid` 和 `secure_1psidts` 替换成真实值。",
                        )
                    )
                else:
                    checks.append(
                        BootstrapCheck(
                            name="accounts_credentials",
                            status="pass",
                            detail=f"已发现 {len(inventory.accounts)} 个账号配置，且 cookie 不是占位值。",
                        )
                    )

    if pool is not None:
        ready_accounts = pool.ready_account_count
        total_accounts = pool.inventory_count
        if total_accounts == 0:
            checks.append(
                BootstrapCheck(
                    name="runtime_readiness",
                    status="warn",
                    detail="服务已启动，但当前没有已加载账号。",
                    action="补齐账号清单后重启服务，再检查 `/readyz`。",
                )
            )
        elif ready_accounts < settings.min_ready_accounts:
            checks.append(
                BootstrapCheck(
                    name="runtime_readiness",
                    status="warn",
                    detail=(
                        f"当前就绪账号 {ready_accounts}/{total_accounts}，"
                        f"低于最小要求 {settings.min_ready_accounts}。"
                    ),
                    action="检查 cookies 是否过期，或等待冷却结束后再试。",
                )
            )
        else:
            checks.append(
                BootstrapCheck(
                    name="runtime_readiness",
                    status="pass",
                    detail=f"当前就绪账号 {ready_accounts}/{total_accounts}，满足启动要求。",
                )
            )

    next_steps = [check.action for check in checks if check.status == "fail" and check.action]
    if not next_steps and any(check.status == "warn" for check in checks):
        next_steps = [check.action for check in checks if check.status == "warn" and check.action]

    return BootstrapStatusResponse(
        status="ready" if not any(check.status == "fail" for check in checks) else "needs_setup",
        setup_complete=not any(check.status == "fail" for check in checks),
        checks=checks,
        next_steps=next_steps,
        docs={
            "health": "/healthz",
            "readiness": "/readyz",
            "openapi": "/docs",
            "admin": "/admin",
        },
    )
