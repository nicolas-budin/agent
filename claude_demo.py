import logging

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    options = ClaudeAgentOptions(
        system_prompt="Réponds de façon brève et factuelle.",
        allowed_tools=["WebSearch"],  # active la recherche web pour une info à jour
        max_turns=1,
    )

    prompt = "Qui est le président des USA ?"
    logger.info("Envoi de la requête : %s", prompt)

    async for message in query(prompt=prompt, options=options):
        logger.debug("Message reçu : %s", type(message).__name__)
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    logger.info("Réponse reçue (%d caractères)", len(block.text))
                    print(block.text)
        elif isinstance(message, ResultMessage):
            logger.info(
                "Coût : %.6f USD | Durée : %d ms (API : %d ms) | Tours : %d | Erreur : %s",
                message.total_cost_usd or 0.0,
                message.duration_ms,
                message.duration_api_ms,
                message.num_turns,
                message.is_error,
            )
            logger.info("Usage détaillé : %s", message.usage)

    logger.info("Terminé.")





if __name__ == "__main__":
    anyio.run(main)