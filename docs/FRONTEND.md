# Frontend Architecture

## Stack

- React 18
- TypeScript
- Vite
- FastAPI static hosting for `frontend/dist`

## Page ownership

The SPA now owns all four operator-facing pages:

- `/ui/login`
- `/setup`
- `/ui/chat`
- `/admin`

FastAPI still owns authentication, session cookies, and the underlying service APIs.

## Running locally

Build the frontend first:

```sh
cd frontend
npm install
npm run build
```

Then start the backend:

```sh
cd ..
py scripts/run_local.py --env-file .env.local.mock
```

Or use a real account inventory:

```sh
py scripts/run_local.py --env-file .env
```

## Key UI APIs

- `GET /ui/api/me`
- `POST /ui/api/login`
- `POST /ui/api/logout`
- `GET /ui/api/setup/status`
- `GET /ui/api/bootstrap`
- `GET /ui/api/sessions/{id}`
- `GET /ui/api/sessions/{id}/history`
- `POST /ui/api/sessions`
- `POST /ui/api/messages`
- `POST /ui/api/messages:stream`
- `GET /ui/api/admin/overview`
- `POST /ui/api/admin/accounts/{id}/actions/{action}`

## Streaming model

The chat UI uses a small client-side state machine:

- `accepted`
- `status`
- `chunk`
- `done`
- `error`

Streaming chunks are buffered in a ref and flushed once per animation frame. The UI renders plain
text while streaming and only converts the final assistant turn to markdown after `done`.

## Admin model

The admin page is async-first:

- overview refresh uses JSON
- runtime actions use JSON
- the page patches local account state instead of reloading

## E2E

Playwright E2E tests live in [`frontend/e2e`](../frontend/e2e). Run them with:

```sh
cd frontend
npm run test:e2e
```

These tests boot the backend with [`config/e2e.mock.env`](../config/e2e.mock.env) and validate:

- login
- user/admin boundary
- streamed chat flow
- async admin action flow
