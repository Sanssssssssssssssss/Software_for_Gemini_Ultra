# Hardening Checklist

- Replace every placeholder secret in `.env`.
- Rotate `GEMINI_SERVICE_API_TOKENS` before production use.
- Replace the default UI password and session secret.
- Store `.env` and `config/accounts.json` outside source control.
- Restrict LAN ingress to trusted subnets only.
- Place the service behind an internal reverse proxy if TLS termination is required.
- Monitor `/metrics` and `/admin` during rollout.
- Keep account `max_concurrency` conservative until load testing establishes safe limits.
- Re-run `scripts/smoke_test.py` after every cookie rotation.
- Re-run `scripts/load_test.py` after concurrency or routing changes.
- Mount multimodal asset storage on a controlled filesystem path with backup and capacity monitoring.
- Review `GEMINI_SERVICE_ASSET_TTL_HOURS` and `GEMINI_SERVICE_ASSET_ORPHAN_GRACE_HOURS` before enabling file uploads for users.
- Validate that asset downloads require the owning UI/API subject or an admin token.
- Keep unsupported file types blocked at the service layer; do not trust filename extensions alone.
- Add malware scanning or downstream DLP controls before broad enterprise rollout of shared-account file workflows.
- Confirm AGPL and internal legal obligations with your organization before broad rollout.
