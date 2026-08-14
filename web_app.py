import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Conversation partagée pour cette démo mono-utilisateur : un seul
# ClaudeSDKClient reste connecté pendant toute la vie du serveur.
client: ClaudeSDKClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
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


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/chat")
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
