# Frontend — Vanilla HTML / CSS / JS

This directory contains the **dependency-free** Document Intelligence Console.

## Why vanilla?

The platform is designed to run in **fully air-gapped Kubernetes clusters**.
A vanilla static frontend means:

- **Zero npm / yarn / Node.js requirement** for deployment.
- **Zero external CDNs / Google Fonts / web sockets** — works behind the strictest egress rules.
- **One container** — FastAPI serves both `/api/*` and the static UI at `/`.

## Layout

```
frontend/
├── dist/                 # the production static frontend (THIS is what gets served)
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── legacy-react/         # archived React/CRA app (kept for reference only — not served)
├── package.json          # thin wrapper so the dev supervisor can `yarn start`
└── README.md             # this file
```

## How it's served

| Mode                       | Who serves the UI                                     |
|----------------------------|-------------------------------------------------------|
| Production / Kubernetes    | **FastAPI** itself (`StaticFiles` mount in `main.py`) — single container, port `8001`. |
| Dev preview / local        | `python3 -m http.server` on port `3000` (via `yarn start` shim or directly). API calls are reverse-proxied or use same-origin relative URLs. |

The vanilla JS uses **same-origin relative URLs** (`fetch('/api/v1/...')`) — there is **no** `REACT_APP_BACKEND_URL` to configure. Just deploy the static files anywhere that proxies `/api/*` to the FastAPI backend, or let FastAPI serve everything.

## Local development

### Option A — single container (recommended)
```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8001
# open http://localhost:8001
```

### Option B — separate static server
```bash
# Terminal 1
cd backend && uvicorn main:app --reload --port 8001

# Terminal 2
cd frontend && python3 -m http.server 3000 --directory dist
# open http://localhost:3000
# (set up your reverse-proxy so /api/* goes to :8001, or just use Option A)
```

## Editing the UI

Open `dist/index.html`, `dist/styles.css`, `dist/app.js`. No build step, no transpiler — refresh the browser. That's the whole story.
