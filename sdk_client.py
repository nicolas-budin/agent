import logging

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def afficher_reponse(client: ClaudeSDKClient) -> None:
    """Lit et affiche les messages jusqu'à la fin du tour courant."""
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)
        elif isinstance(message, ResultMessage):
            logger.info(
                "Coût : %.6f USD | Durée : %d ms | Tours : %d",
                message.total_cost_usd or 0.0,
                message.duration_ms,
                message.num_turns,
            )


async def main():
    options = ClaudeAgentOptions(
        system_prompt="Réponds de façon brève et factuelle.",
        allowed_tools=["WebSearch"],
    )

    # ClaudeSDKClient garde la conversation ouverte : contrairement à query(),
    # on peut envoyer plusieurs messages successifs qui partagent le même contexte.
    async with ClaudeSDKClient(options=options) as client:
        await client.query("Qui est le président des USA ?")
        await afficher_reponse(client)

        await client.query("Et son prédécesseur, c'était qui ?")
        await afficher_reponse(client)

        await client.query("Résume les deux réponses précédentes en une phrase.")
        await afficher_reponse(client)

    logger.info("Terminé.")


if __name__ == "__main__":
    anyio.run(main)
