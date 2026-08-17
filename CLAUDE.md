# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A demo chat app that talks to Claude through the **Claude Agent SDK** (`claude_agent_sdk`, not the raw `anthropic` API SDK) — it authenticates via the bundled Claude Code CLI using the developer's Claude.ai Pro/Max login, not a separate `ANTHROPIC_API_KEY`. Backend is FastAPI (Python), frontend is React (Vite), and it includes a RAG tool backed by a local Qdrant instance. The app has its own separate, unrelated user-account layer (email + password) so each signed-in user gets an isolated Claude conversation — see "Multi-user auth" below; don't confuse it with the Claude.ai login above.

## Commands

### Backend (Python)

```bash
source venv/bin/activate

# No requirements.txt — install the direct dependencies manually if rebuilding the venv:
pip install claude-agent-sdk fastapi "uvicorn[standard]" sse-starlette "qdrant-client[fastembed]" bcrypt pytest pytest-asyncio

# Run the server (serves API + built frontend from frontend/dist)
uvicorn web_app:app --reload --port 8123
# .vscode/launch.json has a matching debugpy config ("Python: web_app (uvicorn debug)")
# for launching the same command under the VSCode debugger

# Run tests (mocked, no live server/services needed)
pytest -v
# Single test file / test:
pytest tests/test_web_app.py -v
pytest tests/test_web_app.py::test_chat_streams_text_and_done_events -v

# Manual browser end-to-end test (requires the app AND Qdrant actually running —
# not picked up by `pytest`, see the file's docstring)
pip install playwright && playwright install chromium
python3 tests/playwright_e2e.py           # headless
python3 tests/playwright_e2e.py --headed  # watch the browser act

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
npm test          # vitest (jsdom) — parseEvent, App and LoginForm tests
npm run preview   # serve the built frontend/dist locally, without the FastAPI backend
```

**Two-server dev workflow**: run `uvicorn` (backend, :8123) and `npm run dev` (frontend, :5173) in parallel terminals. For a single-server setup, run `npm run build` then serve everything through `uvicorn` alone — the backend mounts `frontend/dist` directly.

### External dependency: Qdrant

RAG requires a Qdrant instance reachable at `http://localhost:6333` (run separately via Docker, not managed by this repo). `agent.py` pings it at import time (client construction does a version-compatibility check), so **both running the server and running the test suite require Qdrant to be up**, even though the tests mock the actual queries.

⚠️ The Qdrant instance used in dev has other, unrelated collections (`company_docs`, `star_charts`) — this project owns only the `claude_demo_docs` collection. Don't touch the others.

## Architecture

### Two conversation "engines" living side by side

- `claude_demo.py` and `claude_sdk_client.py` are standalone scripts, not wired into the web app — kept as minimal reference examples of the SDK's one-shot `query()` vs. multi-turn `ClaudeSDKClient`.
- `web_app.py` is the real app and is the only thing that matters for the running product.
- `hello_world.py` is an unrelated leftover practice script.

### Multi-user auth (`db.py` + `auth.py`)

Each user has an account (email + bcrypt-hashed password) in a local SQLite file (`users.db`, gitignored, created by `db.init_db()` at startup). `db.py` has no ORM — short-lived `sqlite3` connections per call. `db.DB_PATH` is read fresh on every call (never cached at import time) specifically so tests can monkeypatch it to a temp file (see `tests/conftest.py`'s autouse `_isolated_db` fixture).

Sessions are an **opaque random token in an httponly cookie**, mapped server-side to a user id in an in-memory `dict` (`auth._sessions`) — deliberately not a JWT and not Starlette's `SessionMiddleware`/`itsdangerous`: the app already needs per-user server-side state for the Claude client itself (see below), so a server-side session store is no extra complexity, and there's no cross-site/stateless requirement that would justify a signed cookie. This means sessions don't survive a server restart (acceptable for a local demo — `uvicorn --reload` already resets Claude connections on every restart too).

Routes (`auth.router`, included in `web_app.app` before the `StaticFiles` mount): `POST /api/register`, `POST /api/login`, `POST /api/logout`, `GET /api/me`. `auth.get_current_user` is a FastAPI dependency (`Depends(...)`) that reads the session cookie and 401s if missing/invalid — used directly by `/api/me` and by `/api/chat` in `web_app.py`. Tests override it via `app.dependency_overrides[auth.get_current_user]` rather than fabricating real cookies (see `tests/test_web_app.py`); `tests/test_auth.py` exercises the real cookie round-trip instead, relying on `TestClient`'s per-instance cookiejar.

### Per-user Claude client isolation (`agent.py`)

Unlike a typical single-tenant demo, **each authenticated user gets their own `ClaudeSDKClient`**, not a shared one. `agent.py` owns:

- `build_agent_options()` — a pure function building the hardened `ClaudeAgentOptions` (see "Tool sandboxing footgun" below), reused for every user's client. This is what `test_build_agent_options_is_hardened` asserts on directly — there's no `lifespan`-time client construction to intercept anymore.
- `get_or_create_client(user_id)` — a `dict[int, ClaudeSDKClient]` registry, created **lazily on a user's first chat message** (not at login/registration, to avoid paying CLI-subprocess-spawn latency on the login request), guarded by a per-user `asyncio.Lock` (double-checked locking) to avoid duplicate connections on concurrent first messages.
- That same per-user lock also wraps the whole `query()`/`receive_response()` turn in `web_app.chat()`, not just client creation — two tabs open for the *same* user can't interleave two turns on one `ClaudeSDKClient` connection.
- `disconnect_all_clients()` — called from `web_app.py`'s `lifespan` shutdown, disconnects every per-user client.
- `current_sources_var: ContextVar[list[str] | None]` — replaces what used to be a single module-level list. Set to `[]` at the top of every `/api/chat` request; the `search_docs` MCP tool handler (whose signature is fixed by the SDK's `@tool` decorator, so it can't take a "current user" parameter) reads it via `.get()` and mutates the list in place. Each request runs in its own asyncio/anyio task/context, so concurrent requests — even from different users — never share a value, with no extra locking needed for this specific piece of state.

### Backend request flow (`web_app.py`)

- `lifespan` is now thin: `db.init_db()` at startup, `agent.disconnect_all_clients()` at shutdown. All Claude-client wiring lives in `agent.py` (see above).
- `POST /api/chat` requires `Depends(auth.get_current_user)` (401 if not authenticated), then calls `agent.get_or_create_client(user.id)`, then `client.query(message)` / streams `client.receive_response()` back to the browser as **Server-Sent Events** via `sse-starlette`'s `EventSourceResponse`. Event types sent: `text` (one per `TextBlock`), `sources` (JSON list of doc paths, emitted before `done` whenever `search_docs` returned results — rendered by the frontend as "📄 Sources : ..."), `done` (cost/duration from the final `ResultMessage`), `error`.
- `StaticFiles(directory=FRONTEND_DIST, html=True)` is mounted at `/` **last**, after `/api/chat` and `auth.router`'s routes — mount order matters here: an earlier mount at `/` would shadow the API routes.

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

### Frontend auth flow (`frontend/src/App.jsx` + `LoginForm.jsx`)

`App.jsx` calls `GET /api/me` in a `useEffect` on mount to check for an existing session cookie, and renders `LoginForm` (not the chat UI) until a user is known. `LoginForm.jsx` is a single controlled-input component toggling between login/register mode, POSTing to `/api/login` or `/api/register`. Neither needs an explicit `credentials` option on `fetch` — frontend and backend are always same-origin (Vite's dev proxy or single-server prod), so the session cookie round-trips automatically. There's no router: two "screens" (login vs. chat) are handled with plain conditional rendering in `App.jsx`, matching the rest of the app's no-extra-dependencies style.

### RAG (`index_docs.py` + `search_docs` tool in `agent.py`)

- `index_docs.py` is a standalone, manually-run script (not called by the web app) that chunks project files (`.py .jsx .md .css .html`, paragraph-based, ~800 chars/chunk) and upserts them into Qdrant collection `claude_demo_docs`, wiping and recreating the collection each run.
- Embeddings are computed **client-side** via FastEmbed (`qdrant-client[fastembed]`), model `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-dim, cosine) — no external embeddings API/key involved.
- Retrieval is exposed to Claude as a custom **in-process MCP tool** (`@tool` + `create_sdk_mcp_server`, registered under server name `docs`), not a hardcoded RAG pipeline that always injects context. Claude decides autonomously — based purely on the tool's `description` string — whether to call `search_docs`, call the also-enabled `WebSearch`, or answer directly. The MCP tool naming convention required in `allowed_tools` is `mcp__<server_name>__<tool_name>` (here: `mcp__docs__search_docs`).

### Tests (`tests/`)

Tests mock both external dependencies rather than hitting them:
- `tests/conftest.py` has an autouse `_isolated_db` fixture: monkeypatches `db.DB_PATH` to a `tmp_path` file and calls `db.init_db()`, so every test gets a fresh, disposable SQLite file — no test touches the real `users.db`.
- `/api/chat` tests (`tests/test_web_app.py`) use `app.dependency_overrides[auth.get_current_user] = lambda: FAKE_USER` (via the `authenticated` fixture) instead of fabricating cookies, and monkeypatch `agent.get_or_create_client` with an async function returning a fake client implementing `query()`/`receive_response()` (constructed from the real `claude_agent_sdk` dataclasses — `AssistantMessage`, `TextBlock`, `ResultMessage` — since the route code does `isinstance()` checks against them).
- `agent.qdrant.query_points` is monkeypatched directly for `search_docs` tests; `agent.current_sources_var` (a `ContextVar`) is set/read directly rather than mutating a module global.
- `tests/test_auth.py` exercises the real register → login → `/api/me` → logout cookie round-trip using `TestClient`'s per-instance cookiejar (no `dependency_overrides` there — that's specifically what's being tested).
- `test_build_agent_options_is_hardened` and `test_get_or_create_client_isolates_users` (`tests/test_web_app.py`) are the regression guards for, respectively, the tool-sandboxing hardening and the "each user gets their own client" requirement — the latter monkeypatches `agent.ClaudeSDKClient` itself (plus resets `agent._clients`/`agent._locks`) to assert two different user ids produce two distinct client instances while the same id doesn't reconnect.
- `TestClient(web_app.app)` is used **without** the `with ... as` context-manager form throughout, since `lifespan` no longer does anything that needs avoiding (no eager Claude-client connection at startup) — real cookie/session behavior in `test_auth.py` doesn't require it either.
- `pytest.ini` sets `pythonpath = .` (so root-level modules import cleanly from `tests/`) and `asyncio_mode = auto` (so `async def test_...` needs no `@pytest.mark.asyncio`).
- `tests/test_index_docs.py` unit-tests `chunk_text()` directly (empty/whitespace input, paragraph merging, max-char splitting, whitespace stripping) — pure logic, no mocking and no Qdrant/CLI dependency needed.

`tests/playwright_e2e.py` is a separate, **unmocked** browser test (real uvicorn + real Qdrant + real Claude Code CLI) — deliberately not named `test_*.py` so `pytest` never auto-collects it; run it directly (see Commands above).
