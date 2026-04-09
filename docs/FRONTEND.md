# Frontend Architecture

## Stack

- React 18
- TypeScript
- Vite
- FastAPI static hosting for `frontend/dist`

## Page ownership

The SPA owns all four operator-facing routes:

- `/ui/login`
- `/setup`
- `/ui/chat`
- `/admin`

FastAPI still owns authentication, session cookies, authorization boundaries, and the underlying service APIs.

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
py scripts/run_local.py --env-file .env
```

For the mock E2E environment:

```sh
py scripts/run_local.py --env-file config/e2e.mock.env --host 127.0.0.1 --port 8011
```

## Current chat architecture

The chat workspace no longer uses a page-global streaming singleton. The main ownership is:

- `frontend/src/chat/useChatWorkspace.ts`
  - workspace bootstrap
  - per-session drafts
  - per-session uploads
  - per-session send and streaming activity
  - per-session runtime refs for abort controller, stream buffer, and rAF flush
- `frontend/src/components/SessionRail.tsx`
  - session navigation
  - lightweight session status presentation
- `frontend/src/components/chat/ConversationPane.tsx`
  - active conversation shell
- `frontend/src/components/chat/MessageViewport.tsx`
  - message list viewport and jump-to-latest affordance
- `frontend/src/components/chat/ComposerDock.tsx`
  - input, send, stream toggle, temporary toggle
- `frontend/src/components/chat/UploadTray.tsx`
  - staged attachment UI

## Streaming model

The SSE event contract is unchanged:

- `accepted`
- `status`
- `chunk`
- `done`
- `error`

The important behavioral change is the ownership model:

- each session keeps its own `isSending`
- each session keeps its own `isStreaming`
- each session keeps its own abort controller
- each session keeps its own stream buffer
- each session keeps its own rAF flush loop

This means:

- session A can keep streaming while session B is created
- session A can keep streaming while session B is opened and browsed
- session B can send independently if the backend accepts concurrent work

## Upload model

Uploads remain UI-first and keep the existing API contract:

- `POST /ui/api/uploads`
- staged assets stay local to the active session draft
- attachment staging does not leak across sessions
- image / PDF / PPTX V1 entry points remain intact

## Admin model

The admin route is still async-first:

- overview refresh uses JSON
- runtime actions use JSON
- account state is patched locally before refresh
- the page does not navigate away after account actions

The UI has been reorganized into:

- summary strip
- runtime account control plane
- recent sessions panel
- state mix panel
- recent assets panel

## Visual system

The shared tokens now follow the opencode-inspired direction:

- warm near-black background
- off-white primary text
- monospace-first UI
- flat borders instead of glassmorphism
- 4px to 8px radius scale
- shared spacing and action primitives across Login, Setup, Chat, and Admin

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

## E2E

Playwright E2E tests live in [`frontend/e2e`](../frontend/e2e). Run them with:

```sh
cd frontend
npm run test:e2e
```

Targeted flows covered by the current suite include:

- login
- user/admin boundary
- async admin action
- responsive laptop viewport smoke
- creating and sending in a new session while another session is still pending
