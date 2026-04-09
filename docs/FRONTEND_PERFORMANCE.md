# Frontend Performance Notes

## Budget

The frontend is tuned around a few strict rules:

- session-level streaming state instead of a page-global lock
- streamed text updates batched by `requestAnimationFrame`
- no markdown parsing during streaming
- no full-page admin refresh after runtime actions
- no layout-property animation dependencies

## Current implementation

### Chat streaming

- SSE events are parsed in `frontend/src/lib/streaming.ts`
- session runtime state is owned by `frontend/src/chat/useChatWorkspace.ts`
- text deltas are buffered per session
- each active stream flushes at most once per animation frame
- the assistant bubble renders plain text while streaming
- markdown rendering happens only after `done`

### Workspace responsiveness

- send/cancel state is scoped to the active session
- a pending stream in session A does not disable send in session B
- the session rail and chat shell stay navigable during streaming
- uploads remain draft-local instead of page-global

### Thinking indicator

- reduced-motion users receive a static indicator
- the cadence was slowed down to reduce unnecessary churn from the indicator itself

## Validation commands

Build:

```sh
cd frontend
npm run build
```

Targeted E2E:

```sh
cd frontend
npx playwright test e2e/auth.spec.ts e2e/admin.spec.ts e2e/chat.spec.ts --workers=1
```

Trace summary:

```sh
cd frontend
npm run perf:trace
```

This writes:

- `output/perf/chat-stream-trace.json`
- `output/perf/chat-stream-summary.json`

## Latest local trace

Local run date: April 8, 2026

Environment:

- backend at `http://127.0.0.1:8011`
- controlled slow streaming via browser-side delayed SSE response
- desktop viewport `1366x900`

Observed summary from `output/perf/chat-stream-summary.json`:

- `secondSendEnabledDuringPending: true`
- `longTaskCount: 0`
- `maxLongTaskMs: 0`
- `layoutEventCount: 33`
- `maxLayoutMs: 14.06`
- `styleEventCount: 92`
- `maxStyleMs: 0.91`

Interpretation:

- the controlled slow-stream scenario did not produce any >50ms renderer tasks
- layout work remained well below the 50ms long-task threshold
- session B remained sendable while session A was still pending

## What to inspect

- long tasks over 50ms
- layout duration spikes during streaming
- style recalculation spikes during streaming
- whether send remains enabled in a second session while the first is still pending
- whether the rail remains visually stable while the active conversation updates

## React profiler note

The backend-hosted production bundle does not expose React DevTools timing hooks in this environment, so `window.__REACT_DEVTOOLS_GLOBAL_HOOK__` was `false` during the latest local run. If deeper React commit profiling is needed, use a profiling-capable development build and React DevTools on top of the same backend APIs.

## Current limits

- this repo now has a repeatable trace summary script, but it is still a lightweight regression tool rather than a full CI performance lab
- frontend render timing metrics are not yet shipped back to backend telemetry
- the trace summary is strongest for regression detection and session-concurrency verification, not for absolute FPS certification
