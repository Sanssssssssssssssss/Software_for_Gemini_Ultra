# Disclaimer

## Non-official integration

This project depends on reverse-engineered access to the Gemini web
application rather than an official Google API. It may stop working without
notice when Google changes frontend behavior, cookies, RPC contracts, or
abuse-detection mechanisms.

## Operational expectations

- Account cookies can expire, be revoked, or require manual re-login.
- Different accounts may expose different model availability and rate limits.
- Regional restrictions, temporary blocks, and account-level feature changes are
  normal failure modes and must be treated as expected runtime conditions.
- This repository aims to fail explicitly and observably rather than pretend the
  upstream dependency is stable.

## Deployment boundary

- Intended for internal team use inside a controlled network.
- Not suitable for public multi-tenant exposure without additional security,
  legal review, and abuse controls.
- Operators are responsible for protecting cookies, API tokens, and audit logs.

## Legal and compliance note

Because this repository derives from an AGPL-3.0 codebase, operators should
review their obligations before deploying it as a network service. This file is
not legal advice.
