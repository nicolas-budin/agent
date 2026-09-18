import asyncio
import logging
from collections import defaultdict

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

logger = logging.getLogger(__name__)

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
        # couvre que les outils intégrés (WebSearch...).
        tools=["WebSearch"],
        allowed_tools=["WebSearch"],
        strict_mcp_config=True,
        setting_sources=[],
    )


def get_user_lock(user_id: int) -> asyncio.Lock:
    return _locks[user_id]


async def get_or_create_client(user_id: int) -> ClaudeSDKClient:
    # Suppose que l'appelant tient déjà get_user_lock(user_id) (c'est le cas
    # de web_app.chat(), qui l'acquiert pour tout le tour) : asyncio.Lock
    # n'étant pas réentrant, le reprendre ici causerait un deadlock au
    # premier message de chaque utilisateur.
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
