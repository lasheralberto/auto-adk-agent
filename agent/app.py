import asyncio
import sys
from google.adk.agents import LlmAgent

from agent.builder import AgentDefinitionBuilder
from agent.runner import run_agent


_definition_builder = AgentDefinitionBuilder()


def build_orchestrator(
    model_name: str | None = None,
    planner_mode: str | None = None,
    athlete_id: str | int | None = None,
) -> LlmAgent:
    return _definition_builder.build_orchestrator(
        athlete_id=athlete_id,
        model_name=model_name,
        planner_mode=planner_mode,
    )

async def main():
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        question = input("Pregunta> ").strip()

    if not question:
        print("No se proporciono ninguna pregunta.")
        return

    orchestrator = build_orchestrator()
    result = await run_agent(question, orchestrator)
    print(result.get("response", ""))

if __name__ == "__main__":
    asyncio.run(main())
