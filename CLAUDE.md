# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A demo chat app that talks to Claude through the **Claude Agent SDK** (`claude_agent_sdk`, not the raw `anthropic` API SDK) — it authenticates via the bundled Claude Code CLI using the developer's Claude.ai Pro/Max login, not a separate `ANTHROPIC_API_KEY`. Backend is FastAPI (Python), frontend is React (Vite). There's no user-account layer: it's single-tenant, one shared Claude conversation for every visitor.

## Commands

### Backend (Python)

```bash
source venv/bin/activate

# No requirements.txt — install the direct dependencies manually if rebuilding the venv:
pip install claude-agent-sdk fastapi "uvicorn[standard]" sse-starlette pytest pytest-asyncio

# Run the server (serves API + built frontend from frontend/dist)
uvicorn web_app:app --reload --port 8123
# .vscode/launch.json has a matching debugpy config ("Python: web_app (uvicorn debug)")
# for launching the same command under the VSCode debugger

# Run tests (mocked, no live server/services needed)
pytest -v
# Single test file / test:
pytest tests/test_web_app.py -v
pytest tests/test_web_app.py::test_chat_streams_text_and_done_events -v

# Manual browser end-to-end test (requires the app actually running —
# not picked up by `pytest`, see the file's docstring)
pip install playwright && playwright install chromium
python3 tests/playwright_e2e.py           # headless
python3 tests/playwright_e2e.py --headed  # watch the browser act
```

### Frontend (Node/React/Vite)

```bash
cd frontend
npm install
npm run dev      # dev server on :5173, proxies /api/* to :8123 (see vite.config.js)
npm run build    # outputs to frontend/dist/, served by FastAPI in prod
npm run lint      # oxlint
npm test          # vitest (jsdom) — parseEvent and App tests
npm run preview   # serve the built frontend/dist locally, without the FastAPI backend
```

**Two-server dev workflow**: run `uvicorn` (backend, :8123) and `npm run dev` (frontend, :5173) in parallel terminals. For a single-server setup, run `npm run build` then serve everything through `uvicorn` alone — the backend mounts `frontend/dist` directly.

## Architecture

### Two conversation "engines" living side by side

- `claude_demo.py` and `claude_sdk_client.py` are standalone scripts, not wired into the web app — kept as minimal reference examples of the SDK's one-shot `query()` vs. multi-turn `ClaudeSDKClient`.
- `web_app.py` is the real app and is the only thing that matters for the running product.
- `hello_world.py` is an unrelated leftover practice script.

### Claude client (`agent.py`)

Single-tenant: **one shared `ClaudeSDKClient` for every visitor**, no accounts, no sessions. `agent.py` owns:

- `build_agent_options()` — a pure function building the hardened `ClaudeAgentOptions` (see "Tool sandboxing footgun" below). This is what `test_build_agent_options_is_hardened` asserts on directly.
- `get_or_create_client()` — a module-level `_client` singleton, created **lazily on the first chat message** (not at server startup, to avoid paying CLI-subprocess-spawn latency before it's needed). It does **not** take `get_lock()` itself — it assumes the caller already holds it, since `asyncio.Lock` isn't reentrant and re-acquiring it here would deadlock on the very first message (this was an actual bug, fixed by removing the inner `async with`).
- `web_app.chat()` acquires that lock around the whole `query()`/`receive_response()` turn — including the call to `get_or_create_client()` — so both connection creation and turn ordering are serialized: two concurrent requests can't interleave two turns on the one `ClaudeSDKClient` connection, and can't double-connect on the first message either.
- `disconnect_client()` — called from `web_app.py`'s `lifespan` shutdown.

### Backend request flow (`web_app.py`)

- `lifespan` is thin: just `agent.disconnect_client()` at shutdown. All Claude-client wiring lives in `agent.py` (see above).
- `POST /api/chat` calls `agent.get_or_create_client()` under `agent.get_lock()`, then `client.query(message)` / streams `client.receive_response()` back to the browser as **Server-Sent Events** via `sse-starlette`'s `EventSourceResponse`. Event types sent: `text` (one per `TextBlock`), `done` (cost/duration from the final `ResultMessage`), `error`.
- `StaticFiles(directory=FRONTEND_DIST, html=True)` is mounted at `/` **last**, after `/api/chat` — mount order matters here: an earlier mount at `/` would shadow the API route.

### ⚠️ Tool sandboxing footgun: `allowed_tools` alone does NOT restrict tools

`ClaudeAgentOptions.allowed_tools` only **pre-approves** those tools (skips the confirmation prompt) — it does **not** replace or restrict the underlying tool set. Verified by tracing `claude_agent_sdk`'s CLI-arg construction (`_internal/transport/subprocess_cli.py`) and confirmed empirically: with only `allowed_tools=["WebSearch", "mcp__docs__search_docs"]` set (as this file had for a while), the bundled CLI actually executed a real `Bash` command when asked to (verified by matching real wall-clock output), despite `Bash` never being listed anywhere — because the CLI's own default tool set (Bash, Read, Write, Edit, Agent, plus any user-level `~/.claude/` plugins/MCP servers) stays fully active unless separately restricted.

Real restriction requires **all** of:
- `tools=["WebSearch"]` — the actual base set of *built-in* tools (Bash/Read/Write/Edit/... are excluded by omission; note MCP-provided tools like `mcp__docs__search_docs` are *not* gated by this field — they come from `mcp_servers` + `allowed_tools` instead, so don't list them here too)
- `strict_mcp_config=True` — ignore MCP servers configured outside this process (user/project `~/.claude/` config)
- `setting_sources=[]` — ignore user/project/local settings files entirely

Without the fix, a request that can't actually reach a tool doesn't reliably get refused either — in testing, the CLI sometimes fabricated a plausible-looking success (a fake `date` timestamp, a "file created" confirmation for a file that was never written) instead of saying it lacked the tool. After applying the fix, the same prompts got honest refusals. **Never trust a self-reported tool list** (asking Claude "what tools do you have" is unreliable — it can echo tool names mentioned in its own system-prompt instructional text, whether or not those tools are actually wired up) — verify with an action that has an independently checkable result (e.g. compare a requested `date` output against the real wall clock, or check the filesystem after a claimed `Write`). This hardening now lives in `agent.build_agent_options()` rather than inline in `web_app.py`'s `lifespan`, but the requirement is unchanged.

### Frontend SSE parsing (`frontend/src/App.jsx`)

The browser can't use the native `EventSource` API because it only supports GET, and this needs POST — so `App.jsx` manually reads `fetch()`'s `ReadableStream` and parses SSE framing by hand. **Known gotcha already fixed here**: `sse-starlette` terminates lines with `\r\n`, not `\n` — the parser normalizes `\r\n` → `\n` before splitting on blank lines. If SSE parsing ever silently stops working after touching this code, check that normalization first.

`parseEvent` is exported from `App.jsx` specifically so `parseEvent.test.js` can unit-test the SSE line-parsing logic in isolation. `App.test.jsx` mocks `global.fetch` with a fake `Response` whose `body.getReader()` replays raw `\r\n`-terminated SSE text (optionally split across two `read()` calls, to exercise the `bufferRef` reassembly path) — this is the same shape `sse-starlette` actually produces, not a simplified stand-in.

### Tests (`tests/`)

Tests mock the external Claude client rather than hitting it:
- `/api/chat` tests (`tests/test_web_app.py`) monkeypatch `agent.get_or_create_client` with an async function returning a fake client implementing `query()`/`receive_response()` (constructed from the real `claude_agent_sdk` dataclasses — `AssistantMessage`, `TextBlock`, `ResultMessage` — since the route code does `isinstance()` checks against them).
- `test_build_agent_options_is_hardened` is the regression guard for the tool-sandboxing hardening; `test_get_or_create_client_reuses_the_same_client` and `test_chat_connects_without_deadlocking` monkeypatch `agent.ClaudeSDKClient` itself (plus resets `agent._client` to `None`) to assert the singleton is only connected once, and that `web_app.chat()` doesn't deadlock on the first message (`asyncio.Lock` isn't reentrant, and `get_or_create_client()` used to re-acquire a lock the caller already held).
- `TestClient(web_app.app)` is used **without** the `with ... as` context-manager form throughout, since `lifespan` no longer does anything that needs avoiding (no eager Claude-client connection at startup).
- `pytest.ini` sets `pythonpath = .` (so root-level modules import cleanly from `tests/`) and `asyncio_mode = auto` (so `async def test_...` needs no `@pytest.mark.asyncio`).

`tests/playwright_e2e.py` is a separate, **unmocked** browser test (real uvicorn + real Claude Code CLI) — deliberately not named `test_*.py` so `pytest` never auto-collects it; run it directly (see Commands above).
