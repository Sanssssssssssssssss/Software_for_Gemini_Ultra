# Frontend Performance Notes

## Budget

The frontend is tuned around a few strict rules:

- streaming text updates: at most one React commit per animation frame
- no session-rail full refresh after every send
- no markdown parsing during streaming
- no full-page admin refresh after runtime actions
- motion relies primarily on opacity and transform

## Current implementation

### Chat streaming

- SSE events are parsed in `frontend/src/lib/streaming.ts`
- text deltas are accumulated in a ref buffer
- `requestAnimationFrame` performs batched UI flushes
- the assistant bubble renders plain text during streaming
- markdown rendering happens only after `done`

### Thinking indicator

- frame cadence: `125ms`
- label cadence: `1800ms`
- reduced-motion users receive a static indicator

### Scroll behavior

- auto-follow stays enabled only while the user is near the bottom
- once the user scrolls away, the UI stops forcing the viewport down
- a “Jump to latest” control restores follow mode

## Validation workflow

Use this sequence when validating frontend performance locally:

```sh
cd frontend
npm run build
cd ..
py scripts/run_local.py --env-file .env.local.mock --host 127.0.0.1 --port 8010
```

Then in the browser:

1. Open `/ui/chat`
2. Send a long prompt that produces multiple streamed chunks
3. Record a Chrome or Edge Performance trace
4. Confirm there is no layout thrash on every chunk
5. Confirm React commits stay coarse-grained instead of per-chunk

## What to inspect

- main thread flame chart during streaming
- long tasks over 50ms
- layout / style recalculation frequency
- React commit count while chunks arrive
- input responsiveness while a stream is active

## Expected healthy signals

- the message bubble updates smoothly without rail flicker
- the composer remains clickable during stream output
- admin actions update in place without navigation flashes
- reduced-motion mode still keeps the UI usable and readable

## Current limits

- markdown highlighting is intentionally deferred; code highlighting is not yet lazy-loaded
- metrics for frontend render timings are not yet shipped to the backend
- E2E validates flow correctness, not FPS directly
