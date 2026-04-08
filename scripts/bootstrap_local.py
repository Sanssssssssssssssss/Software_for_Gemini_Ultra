from __future__ import annotations

import argparse
import secrets
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
ENV_TARGET = REPO_ROOT / ".env"
MOCK_ENV_TARGET = REPO_ROOT / ".env.local.mock"
ACCOUNTS_EXAMPLE = REPO_ROOT / "config" / "accounts.example.json"
ACCOUNTS_TARGET = REPO_ROOT / "config" / "accounts.json"
MOCK_ACCOUNTS_TARGET = REPO_ROOT / "config" / "accounts.mock.json"
DATA_DIR = REPO_ROOT / "data"
ASSET_DIR = DATA_DIR / "assets"


def _render_env_template(profile: str) -> str:
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    replacements = {
        "change-me-ui-password": secrets.token_urlsafe(18),
        "change-me-session-secret": secrets.token_urlsafe(32),
        "change-me": secrets.token_urlsafe(24),
    }
    for source, target in replacements.items():
        content = content.replace(source, target, 1)

    if profile == "mock":
        content = content.replace("GEMINI_SERVICE_ENV=development", "GEMINI_SERVICE_ENV=local-mock")
        content = content.replace("GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=config/accounts.json", "GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=config/accounts.mock.json")
        content = content.replace("GEMINI_SERVICE_DATABASE_URL=sqlite+aiosqlite:///./data/gemini_service.db", "GEMINI_SERVICE_DATABASE_URL=sqlite+aiosqlite:///./data/gemini_service.mock.db")
        content = content.replace("GEMINI_SERVICE_UI_USER_USERNAME=", "GEMINI_SERVICE_UI_USER_USERNAME=user")
        content = content.replace("GEMINI_SERVICE_UI_USER_PASSWORD=", f"GEMINI_SERVICE_UI_USER_PASSWORD={secrets.token_urlsafe(14)}")
    return content


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap local config for Gemini Internal Service.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing .env and accounts.json.")
    parser.add_argument(
        "--profile",
        choices=["real", "mock"],
        default="real",
        help="Create config for real Gemini cookies or offline mock-provider startup.",
    )
    parser.add_argument(
        "--env-target",
        default="",
        help="Override the target env file path. Defaults to .env for real and .env.local.mock for mock.",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    skipped: list[str] = []
    env_target = Path(args.env_target) if args.env_target else (ENV_TARGET if args.profile == "real" else MOCK_ENV_TARGET)
    accounts_target = ACCOUNTS_TARGET if args.profile == "real" else MOCK_ACCOUNTS_TARGET

    if args.force or not env_target.exists():
        env_target.write_text(_render_env_template(args.profile), encoding="utf-8")
        created.append(str(env_target.relative_to(REPO_ROOT)))
    else:
        skipped.append(str(env_target.relative_to(REPO_ROOT)))

    if args.profile == "real":
        if args.force or not accounts_target.exists():
            accounts_target.write_text(ACCOUNTS_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
            created.append(str(accounts_target.relative_to(REPO_ROOT)))
        else:
            skipped.append(str(accounts_target.relative_to(REPO_ROOT)))

    print("Local bootstrap completed.")
    if created:
        print("Created:")
        for item in created:
            print(f"  - {item}")
    if skipped:
        print("Skipped because the file already exists:")
        for item in skipped:
            print(f"  - {item}")

    print("\nNext steps:")
    if args.profile == "real":
        print("1. Edit config/accounts.json and replace the placeholder Gemini cookies.")
        print("2. Run `python scripts/doctor.py --env-file .env` to validate local configuration.")
        print("3. Start the service with `python scripts/run_local.py --env-file .env`.")
    else:
        print("1. Start the offline mock service with `python scripts/run_local.py --mock`.")
        print("2. Open `http://127.0.0.1:8000/ui/login` using the admin credentials from .env.local.mock.")
        print("3. Run `python scripts/validate_service.py` for a full offline verification pass.")


if __name__ == "__main__":
    main()
