A demo chat app that talks to Claude through the Claude Agent SDK (claude_agent_sdk, not the raw anthropic API SDK) — it authenticates via the bundled Claude Code CLI using the developer's Claude.ai Pro/Max login, not a separate ANTHROPIC_API_KEY. Backend is FastAPI (Python), frontend is React (Vite), and it includes a RAG tool backed by a local Qdrant instance.

