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
    create_sdk_mcp_server,
    tool,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from qdrant_client import QdrantClient, models
from sse_starlette.sse import EventSourceResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Build React : `cd frontend && npm run build`
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

# RAG : voir index_docs.py pour l'indexation de la collection Qdrant.
QDRANT_COLLECTION = "claude_demo_docs"
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
qdrant = QdrantClient(url="http://localhost:6333")

# Sources utilisées par search_docs pour le tour de conversation en cours.
# Démo mono-utilisateur (un seul ClaudeSDKClient partagé) : vidée au début de
# chaque requête /api/chat, remplie par search_docs, lue à la fin du stream.
current_sources: list[str] = []


@tool(
    "search_docs",
    "Recherche par similarité sémantique dans les fichiers de ce projet "
    "(code source, README, config). À utiliser pour toute question sur le "
    "contenu, l'architecture ou le fonctionnement de ce projet.",
    {"query": str},
)
async def search_docs(args: dict) -> dict:

    logger.info("RAG args : %s", args["query"])

    results = qdrant.query_points(
        collection_name=QDRANT_COLLECTION,
        query=models.Document(text=args["query"], model=EMBEDDING_MODEL),
        limit=4,
    )
    if not results.points:
        return {"content": [{"type": "text", "text": "Aucun résultat trouvé."}]}

    for p in results.points:
        source = p.payload["source"]
        if source not in current_sources:
            current_sources.append(source)

    text = "\n\n---\n\n".join(
        f"[{p.payload['source']}]\n{p.payload['text']}" for p in results.points
    )

    logger.info("RAG text : %s", text)

    return {"content": [{"type": "text", "text": text}]}


docs_server = create_sdk_mcp_server(name="docs", tools=[search_docs])

# Conversation partagée pour cette démo mono-utilisateur : un seul
# ClaudeSDKClient reste connecté pendant toute la vie du serveur.
client: ClaudeSDKClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    options = ClaudeAgentOptions(
        system_prompt="Réponds de façon brève et factuelle.",
        # `allowed_tools` seul ne fait que pré-approuver ces outils sans
        # invalider les autres — sans `tools`, le CLI démarre quand même avec
        # le jeu d'outils complet (Bash, Read, Write, Edit, Agent...) et, sans
        # `strict_mcp_config`/`setting_sources=[]`, charge aussi les MCP et
        # settings définis au niveau utilisateur (~/.claude/). `tools` ne
        # couvre que les outils intégrés (WebSearch...) — les outils MCP
        # (mcp__docs__search_docs) sont gérés séparément via `mcp_servers` +
        # `allowed_tools`, donc pas besoin de les lister ici aussi.
        tools=["WebSearch"],
        allowed_tools=["WebSearch", "mcp__docs__search_docs"],
        mcp_servers={"docs": docs_server},
        strict_mcp_config=True,
        setting_sources=[],
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


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message vide"}, status_code=400)

    async def event_stream():
        logger.info("Message reçu : %s", message)
        current_sources.clear()
        try:
            await client.query(message)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            # logger.info("Assistant : %s", block.text)
                            yield {"event": "text", "data": block.text}
                elif isinstance(msg, ResultMessage):
                    logger.info(
                        "Coût : %.6f USD | Durée : %d ms | Tours : %d",
                        msg.total_cost_usd or 0.0,
                        msg.duration_ms,
                        msg.num_turns,
                    )
                    if current_sources:
                        logger.info("Sources RAG utilisées : %s", current_sources)
                        yield {"event": "sources", "data": json.dumps(current_sources)}
                    yield {
                        "event": "done",
                        "data": json.dumps(
                            {
                                "cost_usd": msg.total_cost_usd or 0.0,
                                "duration_ms": msg.duration_ms,
                            }
                        ),
                    }
                # else:
                    # logger.info("Message : %s", msg)
        except Exception as exc:  # noqa: BLE001 - remonter l'erreur au client web
            logger.exception("Erreur pendant la génération")
            yield {"event": "error", "data": str(exc)}

    return EventSourceResponse(event_stream())


# Monté en dernier : sert le build React (index.html + assets),
# sans masquer la route /api/chat déclarée au-dessus.
app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")