# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A demo chat app that talks to Claude through the **Claude Agent SDK** (`claude_agent_sdk`, not the raw `anthropic` API SDK) — it authenticates via the bundled Claude Code CLI using the developer's Claude.ai Pro/Max login, not a separate `ANTHROPIC_API_KEY`. Backend is FastAPI (Python), frontend is React (Vite), and it includes a RAG tool backed by a local Qdrant instance.

## Commands

### Backend (Python)

```bash
source venv/bin/activate

# No requirements.txt — install the direct dependencies manually if rebuilding the venv:
pip install claude-agent-sdk fastapi "uvicorn[standard]" sse-starlette "qdrant-client[fastembed]" pytest pytest-asyncio

# Run the server (serves API + built frontend from frontend/dist)
uvicorn web_app:app --reload --port 8123

# Run tests
pytest -v
# Single test file / test:
pytest tests/test_web_app.py -v
pytest tests/test_web_app.py::test_chat_streams_text_and_done_events -v

# Rebuild the RAG index (after editing project files you want searchable)
python3 index_docs.py
```

### Frontend (Node/React/Vite)

```bash
cd frontend
npm install
npm run dev      # dev server on :5173, proxies /api/* to :8123 (see vite.config.js)
npm run build    # outputs to frontend/dist/, served by FastAPI in prod
npm run lint      # oxlint
```

**Two-server dev workflow**: run `uvicorn` (backend, :8123) and `npm run dev` (frontend, :5173) in parallel terminals. For a single-server setup, run `npm run build` then serve everything through `uvicorn` alone — the backend mounts `frontend/dist` directly.

### External dependency: Qdrant

RAG requires a Qdrant instance reachable at `http://localhost:6333` (run separately via Docker, not managed by this repo). `web_app.py` pings it at import time (client construction does a version-compatibility check), so **both running the server and running the test suite require Qdrant to be up**, even though the tests mock the actual queries.

⚠️ The Qdrant instance used in dev has other, unrelated collections (`company_docs`, `star_charts`) — this project owns only the `claude_demo_docs` collection. Don't touch the others.

## Architecture

### Two conversation "engines" living side by side

- `claude_demo.py` and `claude_sdk_client.py` are standalone scripts, not wired into the web app — kept as minimal reference examples of the SDK's one-shot `query()` vs. multi-turn `ClaudeSDKClient`.
- `web_app.py` is the real app and is the only thing that matters for the running product.
- `hello_world.py` is an unrelated leftover practice script.

### Backend request flow (`web_app.py`)

- A single `ClaudeSDKClient` is created once in FastAPI's `lifespan` and kept connected for the server's entire lifetime — this is a **mono-user demo**: all browser tabs share one Claude conversation/session, there is no per-user session isolation.
- `POST /api/chat` calls `client.query(message)` then streams `client.receive_response()` back to the browser as **Server-Sent Events** via `sse-starlette`'s `EventSourceResponse`. Event types sent: `text` (one per `TextBlock`), `done` (cost/duration from the final `ResultMessage`), `error`.
- `StaticFiles(directory=FRONTEND_DIST, html=True)` is mounted at `/` **last**, after the `/api/chat` route — mount order matters here: an earlier mount at `/` would shadow the API route.

### Frontend SSE parsing (`frontend/src/App.jsx`)

The browser can't use the native `EventSource` API because it only supports GET, and this needs POST — so `App.jsx` manually reads `fetch()`'s `ReadableStream` and parses SSE framing by hand. **Known gotcha already fixed here**: `sse-starlette` terminates lines with `\r\n`, not `\n` — the parser normalizes `\r\n` → `\n` before splitting on blank lines. If SSE parsing ever silently stops working after touching this code, check that normalization first.

### RAG (`index_docs.py` + `search_docs` tool in `web_app.py`)

- `index_docs.py` is a standalone, manually-run script (not called by the web app) that chunks project files (`.py .jsx .md .css .html`, paragraph-based, ~800 chars/chunk) and upserts them into Qdrant collection `claude_demo_docs`, wiping and recreating the collection each run.
- Embeddings are computed **client-side** via FastEmbed (`qdrant-client[fastembed]`), model `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-dim, cosine) — no external embeddings API/key involved.
- Retrieval is exposed to Claude as a custom **in-process MCP tool** (`@tool` + `create_sdk_mcp_server`, registered under server name `docs`), not a hardcoded RAG pipeline that always injects context. Claude decides autonomously — based purely on the tool's `description` string — whether to call `search_docs`, call the also-enabled `WebSearch`, or answer directly. The MCP tool naming convention required in `allowed_tools` is `mcp__<server_name>__<tool_name>` (here: `mcp__docs__search_docs`).

### Tests (`tests/`)

Tests mock both external dependencies rather than hitting them:
- `web_app.client` (the `ClaudeSDKClient`) is monkeypatched with a fake object implementing async `query()`/`receive_response()`, constructed from the real `claude_agent_sdk` dataclasses (`AssistantMessage`, `TextBlock`, `ResultMessage`) since the route code does `isinstance()` checks against them.
- `web_app.qdrant.query_points` is monkeypatched directly for `search_docs` tests.
- `TestClient(web_app.app)` is used **without** the `with ... as` context-manager form specifically to avoid triggering FastAPI's `lifespan` (which would spawn a real Claude Code CLI subprocess).
- `pytest.ini` sets `pythonpath = .` (so root-level modules import cleanly from `tests/`) and `asyncio_mode = auto` (so `async def test_...` needs no `@pytest.mark.asyncio`).
