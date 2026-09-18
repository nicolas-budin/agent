import asyncio
import logging

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

logger = logging.getLogger(__name__)

# Un unique ClaudeSDKClient partagé par tous les visiteurs, créé
# paresseusement au premier message (pas au démarrage du serveur) pour ne
# pas payer le coût de spawn du CLI avant qu'il ne serve à quelque chose.
_client: ClaudeSDKClient | None = None
_lock = asyncio.Lock()


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


def get_lock() -> asyncio.Lock:
    return _lock


async def get_or_create_client() -> ClaudeSDKClient:
    # Suppose que l'appelant tient déjà get_lock() (c'est le cas de
    # web_app.chat(), qui l'acquiert pour tout le tour) : asyncio.Lock
    # n'étant pas réentrant, le reprendre ici causerait un deadlock au tout
    # premier message.
    global _client
    if _client is None:
        _client = ClaudeSDKClient(options=build_agent_options())
        await _client.connect()
        logger.info("Client Claude connecté")
    return _client


async def disconnect_client() -> None:
    global _client
    if _client is not None:
        await _client.disconnect()
        logger.info("Client Claude déconnecté")
        _client = None
