# Failure Modes

## Upstream web contract drift

Google may change RPC ids, response shapes, headers, or anti-abuse behavior. Expect breakage without warning.

## Cookie invalidation

Accounts can fail independently when cookies expire or re-authentication is required.

## Partial account instability

One account may be healthy while another is throttled, blocked, or region-restricted. The service is designed to isolate those failures in account state.

## Long-lived conversation pressure

Sticky sessions keep context on one account. If that account degrades, the session may become unavailable until explicit failover rules are introduced.

## Burst traffic

If burst load exceeds configured concurrency, latency rises and more accounts may enter cooldown.

## Mixed stream and non-stream traffic

Streaming requests occupy account capacity longer than short non-stream calls. Use load testing to tune ratios and per-account concurrency.

## Validation blind spots

Mock-provider validation proves the service shell, scheduler, batch worker, and auth boundaries, but it cannot prove real Gemini cookie health, anti-abuse posture, or upstream web compatibility. Treat `scripts/validate_service.py` and real-account smoke tests as complementary gates.
