# PageWatch Web Dashboard

React single-page app for [pagewatch](../../README.md), served locally by
`pagewatch serve`. All data lives in plain JSON files under `~/.pagewatch`
(or `PAGEWATCH_HOME`) — the dashboard talks to the same storage the CLI uses,
through a small REST API (`/api/*`).

## Development

```bash
# 1. start the API backend (also serves the last built UI)
pagewatch serve --no-browser          # http://127.0.0.1:8787

# 2. start the Vite dev server with hot reload (proxies /api to :8787)
cd apps/web
npm install
npm run dev                           # http://127.0.0.1:5173
```

## Build

```bash
cd apps/web
npm run build
```

The bundle is emitted to `src/pagewatch/webui/` (stable file names, no
hashes) so it ships inside the Python package — `pip install pagewatch`
users get the dashboard with zero Node.js involved. CI rebuilds the bundle
automatically on any push that touches `apps/web/`.

## Stack

- React 19 + Vite, no other runtime dependencies
- Plain hand-written CSS (`src/styles.css`) — dark, terminal-adjacent theme
- Backend: Python stdlib HTTP server (`src/pagewatch/server.py`), no database
