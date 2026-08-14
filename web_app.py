import json
import logging
from contextlib import asynccontextmanager

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)
from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Conversation partagée pour cette démo mono-utilisateur : un seul
# ClaudeSDKClient reste connecté pendant toute la vie du serveur.
client: ClaudeSDKClient | None = None


@asynccontextmanager
async def lifespan(app: Starlette):
    global client
    options = ClaudeAgentOptions(
        system_prompt="Réponds de façon brève et factuelle.",
        allowed_tools=["WebSearch"],
    )
    client = ClaudeSDKClient(options=options)
    await client.connect()
    logger.info("Client Claude connecté.")
    try:
        yield
    finally:
        await client.disconnect()
        logger.info("Client Claude déconnecté.")


INDEX_HTML = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Claude SDK Client</title>
<style>
  :root { color-scheme: light dark; }
  body {
    font-family: -apple-system, system-ui, sans-serif;
    max-width: 720px;
    margin: 2rem auto;
    padding: 0 1rem;
  }
  h1 { font-size: 1.2rem; }
  #chat {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    min-height: 50vh;
    margin-bottom: 1rem;
  }
  .msg {
    padding: 0.6rem 0.9rem;
    border-radius: 0.6rem;
    max-width: 80%;
    white-space: pre-wrap;
  }
  .user { align-self: flex-end; background: #2563eb; color: white; }
  .assistant { align-self: flex-start; background: rgba(127,127,127,0.15); }
  .meta { align-self: flex-start; font-size: 0.75rem; opacity: 0.6; }
  form { display: flex; gap: 0.5rem; }
  input {
    flex: 1;
    padding: 0.6rem;
    border-radius: 0.5rem;
    border: 1px solid rgba(127,127,127,0.4);
    font-size: 1rem;
  }
  button {
    padding: 0.6rem 1rem;
    border-radius: 0.5rem;
    border: none;
    background: #2563eb;
    color: white;
    font-size: 1rem;
    cursor: pointer;
  }
  button:disabled { opacity: 0.5; cursor: default; }
</style>
</head>
<body>
  <h1>💬 Claude SDK Client — conversation multi-tours</h1>
  <div id="chat"></div>
  <form id="form">
    <input id="input" autocomplete="off" placeholder="Écris ton message..." />
    <button id="send" type="submit">Envoyer</button>
  </form>

<script>
const chat = document.getElementById('chat');
const form = document.getElementById('form');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');

function appendMessage(role, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.textContent = text;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

function appendMeta(text) {
  const div = document.createElement('div');
  div.className = 'meta';
  div.textContent = text;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  sendBtn.disabled = true;
  appendMessage('user', text);

  let assistantDiv = null;

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });
    if (!resp.ok) {
      appendMeta('Erreur : ' + resp.status);
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      // sse-starlette termine ses lignes en \\r\\n : on normalise avant de découper.
      buffer += decoder.decode(value, { stream: true }).replace(/\\r\\n/g, '\\n');
      const parts = buffer.split('\\n\\n');
      buffer = parts.pop();

      for (const part of parts) {
        let event = 'message';
        const dataLines = [];
        for (const line of part.split('\\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
        }
        const data = dataLines.join('\\n');

        if (event === 'text') {
          if (!assistantDiv) assistantDiv = appendMessage('assistant', '');
          assistantDiv.textContent += data;
          chat.scrollTop = chat.scrollHeight;
        } else if (event === 'done') {
          const info = JSON.parse(data);
          appendMeta(`Coût : $${info.cost_usd.toFixed(6)} · ${info.duration_ms} ms`);
        } else if (event === 'error') {
          appendMeta('Erreur : ' + data);
        }
      }
    }
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
});
</script>
</body>
</html>
"""


async def index(request: Request):
    return HTMLResponse(INDEX_HTML)


async def chat(request: Request):
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message vide"}, status_code=400)

    async def event_stream():
        logger.info("Message reçu : %s", message)
        try:
            await client.query(message)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            yield {"event": "text", "data": block.text}
                elif isinstance(msg, ResultMessage):
                    logger.info(
                        "Coût : %.6f USD | Durée : %d ms | Tours : %d",
                        msg.total_cost_usd or 0.0,
                        msg.duration_ms,
                        msg.num_turns,
                    )
                    yield {
                        "event": "done",
                        "data": json.dumps(
                            {
                                "cost_usd": msg.total_cost_usd or 0.0,
                                "duration_ms": msg.duration_ms,
                            }
                        ),
                    }
        except Exception as exc:  # noqa: BLE001 - remonter l'erreur au client web
            logger.exception("Erreur pendant la génération")
            yield {"event": "error", "data": str(exc)}

    return EventSourceResponse(event_stream())


app = Starlette(
    routes=[
        Route("/", index),
        Route("/api/chat", chat, methods=["POST"]),
    ],
    lifespan=lifespan,
)
