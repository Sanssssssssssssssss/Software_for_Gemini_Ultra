from __future__ import annotations

import argparse
import secrets
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
ENV_TARGET = REPO_ROOT / ".env"
ACCOUNTS_EXAMPLE = REPO_ROOT / "config" / "accounts.example.json"
ACCOUNTS_TARGET = REPO_ROOT / "config" / "accounts.json"
DATA_DIR = REPO_ROOT / "data"


def _render_env_template() -> str:
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    replacements = {
        "change-me-ui-password": secrets.token_urlsafe(18),
        "change-me-session-secret": secrets.token_urlsafe(32),
        "change-me": secrets.token_urlsafe(24),
    }
    for source, target in replacements.items():
        content = content.replace(source, target, 1)
    return content


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap local config for Gemini Internal Service.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing .env and accounts.json.")
    args = parser.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    created: list[str] = []
    skipped: list[str] = []

    if args.force or not ENV_TARGET.exists():
        ENV_TARGET.write_text(_render_env_template(), encoding="utf-8")
        created.append(str(ENV_TARGET.relative_to(REPO_ROOT)))
    else:
        skipped.append(str(ENV_TARGET.relative_to(REPO_ROOT)))

    if args.force or not ACCOUNTS_TARGET.exists():
        ACCOUNTS_TARGET.write_text(ACCOUNTS_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        created.append(str(ACCOUNTS_TARGET.relative_to(REPO_ROOT)))
    else:
        skipped.append(str(ACCOUNTS_TARGET.relative_to(REPO_ROOT)))

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
    print("1. Edit config/accounts.json and replace the placeholder Gemini cookies.")
    print("2. Run `py scripts/doctor.py` to validate local configuration.")
    print("3. Start the service with `py -m uvicorn gemini_service.main:app --host 0.0.0.0 --port 8000`.")


if __name__ == "__main__":
    main()
