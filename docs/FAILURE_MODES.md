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

## File privacy and shared-account retention

Attachment-bearing turns can surface sensitive internal files through shared Gemini web accounts. V1 mitigates this by defaulting file-bearing requests to `temporary=true` unless the caller explicitly opts out, but operators still need a retention policy and storage cleanup window.

## Large-file latency and context pressure

PDF and PPTX uploads increase request latency, provider preparation time, and the odds of timeouts or cooldowns. Keep file count and size limits conservative until you have real-account load data.

## Half-complete upload flows

The service can store a file locally and still fail before the provider accepts the message. This is expected; orphan cleanup and TTL expiry are the compensating controls for abandoned uploads.

## Asset lifecycle drift

If files are deleted from local storage outside the service, metadata may remain while the file content is gone. Asset download paths now guard against non-available assets, but operators should avoid manual filesystem tampering.
