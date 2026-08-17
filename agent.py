import asyncio
import logging
from collections import defaultdict
from contextvars import ContextVar

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server, tool
from qdrant_client import QdrantClient, models

logger = logging.getLogger(__name__)

# RAG : voir index_docs.py pour l'indexation de la collection Qdrant.
QDRANT_COLLECTION = "claude_demo_docs"
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
qdrant = QdrantClient(url="http://localhost:6333")

# Sources utilisées par search_docs pour le tour de conversation en cours.
# Par requête (pas par utilisateur) : posée à [] en tête de chaque appel
# /api/chat, remplie par search_docs, lue à la fin du stream. Chaque requête
# tourne dans son propre contexte asyncio/anyio, donc deux requêtes
# concurrentes (même du même utilisateur) ne se marchent jamais dessus.
current_sources_var: ContextVar[list[str] | None] = ContextVar(
    "current_sources", default=None
)


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

    sources = current_sources_var.get()
    for p in results.points:
        source = p.payload["source"]
        if sources is not None and source not in sources:
            sources.append(source)

    text = "\n\n---\n\n".join(
        f"[{p.payload['source']}]\n{p.payload['text']}" for p in results.points
    )

    logger.info("RAG text : %s", text)

    return {"content": [{"type": "text", "text": text}]}


docs_server = create_sdk_mcp_server(name="docs", tools=[search_docs])

# Un ClaudeSDKClient isolé par utilisateur (clé : user_id), créé
# paresseusement au premier message pour ne pas payer le coût de spawn du
# CLI sur la requête de login.
_clients: dict[int, ClaudeSDKClient] = {}
_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def build_agent_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
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


def get_user_lock(user_id: int) -> asyncio.Lock:
    return _locks[user_id]


async def get_or_create_client(user_id: int) -> ClaudeSDKClient:
    if user_id in _clients:
        return _clients[user_id]
    async with get_user_lock(user_id):
        if user_id not in _clients:
            c = ClaudeSDKClient(options=build_agent_options())
            await c.connect()
            _clients[user_id] = c
            logger.info("Client Claude connecté pour user_id=%s", user_id)
    return _clients[user_id]


async def disconnect_all_clients() -> None:
    for user_id, c in list(_clients.items()):
        await c.disconnect()
        logger.info("Client Claude déconnecté pour user_id=%s", user_id)
    _clients.clear()
