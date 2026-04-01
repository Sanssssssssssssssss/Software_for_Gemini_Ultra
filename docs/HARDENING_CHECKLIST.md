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
- Confirm AGPL and internal legal obligations with your organization before broad rollout.
