import anyio
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query


async def main():
    options = ClaudeAgentOptions(
        system_prompt="Réponds de façon brève et factuelle.",
        allowed_tools=["WebSearch"],  # active la recherche web pour une info à jour
        max_turns=1,
    )

    async for message in query(prompt="Qui est le président des USA ?", options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)


if __name__ == "__main__":
    anyio.run(main)