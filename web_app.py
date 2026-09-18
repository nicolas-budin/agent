import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

import agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Build React : `cd frontend && npm run build`
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        await agent.disconnect_client()


app = FastAPI(lifespan=lifespan)


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message vide"}, status_code=400)

    async def event_stream():
        logger.info("Message reçu : %s", message)
        async with agent.get_lock():
            client = await agent.get_or_create_client()
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


# Monté en dernier : sert le build React (index.html + assets),
# sans masquer la route /api/chat déclarée au-dessus.
app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
