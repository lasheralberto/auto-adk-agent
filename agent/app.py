import asyncio
import sys
from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool

try:
    from google.adk.planners import PlanReActPlanner
except Exception:  # noqa: BLE001
    PlanReActPlanner = None

from agent.runner import run_agent
from agent.config.config import (
    get_llm_provider,
    answer_agent_skill,
    intent_router_skill,
    plan_react_planner_skill,
    strava_ingestion_skill,
    query_skill,
)
from agent.tools.pipeline import (
    run_ingestion_pipeline,
    run_query_pipeline,
)


def _build_plan_react_kwargs() -> dict[str, object]:
    if PlanReActPlanner is None:
        return {}

    try:
        return {"planner": PlanReActPlanner()}
    except Exception:  # noqa: BLE001
        return {}


# Genera un plan de pasos (Plan → ReAct) antes de que el orquestador delegue.
# Se activa solo cuando intent_router clasifica la intención como FULL_EXECUTION,
# o siempre si planner_mode="always". Mejora la coherencia en tareas multi-paso.
def _create_plan_react_planner_agent(selected_model: object) -> AgentTool:
    planner_kwargs = _build_plan_react_kwargs()
    try:
        planner_agent = LlmAgent(
            name="plan_react_planner",
            model=selected_model,
            instruction=plan_react_planner_skill.instructions,
            **planner_kwargs,
        )
    except TypeError:
        planner_agent = LlmAgent(
            name="plan_react_planner",
            model=selected_model,
            instruction=plan_react_planner_skill.instructions,
        )

    return AgentTool(agent=planner_agent)


def _build_orchestrator_instruction(normalized_planner_mode: str) -> str:
    planner_directive = (
        "Always run plan_react_planner before delegation."
        if normalized_planner_mode == "always"
        else "Run plan_react_planner only when intent_router outputs FULL_EXECUTION."
    )

    return (
        "You are the orchestrator for a Strava training assistant.\n\n"
        "Available tools/agents:\n"
        "- intent_router\n"
        "- plan_react_planner\n"
        "- strava_ingestion_agent\n"
        "- query_agent\n"
        "- answer_agent\n\n"
        "Routing rules:\n"
        "- For user questions about training data, always delegate to query_agent first.\n"
        "- For sync/ingestion requests, use strava_ingestion_agent.\n"
        "- Pipeline stages (indexing, RAG wiki, daily pipeline) are handled externally via API endpoints — do not attempt to run them.\n"
        "- Do not call strava_ingestion_agent for normal Q&A; use query_agent for indexed knowledge.\n"
        "- Use answer_agent only for generic conversation or final wording.\n\n"
        f"Runtime directive: {planner_directive}"
    )


def build_orchestrator(
    llm_provider: str | None = None,
    model_name: str | None = None,
    planner_mode: str | None = None,
) -> LlmAgent:
    selected_model = get_llm_provider(llm_provider=llm_provider, model_name=model_name)
    normalized_planner_mode = (planner_mode or "full_only").strip().lower()

    # Clasifica la intención del mensaje antes de delegar: decide si se trata de
    # una consulta simple, una solicitud de sincronización o una ejecución completa.
    # El orquestador lo usa como primer paso para elegir la ruta de agentes correcta.
    intent_router = LlmAgent(
        name="intent_router",
        model=selected_model,
        instruction=intent_router_skill.instructions,
    )

    # Extrae actividades de la API de Strava y las almacena en el estado local.
    # Se invoca cuando el usuario solicita sincronizar o actualizar sus datos.
    # Es el punto de entrada de datos antes de que la pipeline de indexación los procese.
    strava_ingestion_agent = AgentTool(agent=LlmAgent(
        name="strava_ingestion_agent",
        model=selected_model,
        instruction=strava_ingestion_skill.instructions,
        tools=[run_ingestion_pipeline],
    ))

    # Realiza búsqueda semántica sobre los datos indexados en Pinecone.
    # Es el agente principal para responder preguntas sobre el historial de entrenamiento.
    # Depende de que la pipeline de indexación se haya ejecutado previamente via endpoint.
    query_agent = AgentTool(agent=LlmAgent(
        name="query_agent",
        model=selected_model,
        instruction=query_skill.instructions,
        tools=[run_query_pipeline],
    ))

    # Redacta la respuesta final al usuario con el contexto recibido de los demás agentes.
    # Solo se usa para conversación genérica o para dar forma al texto de cierre.
    answer_agent = LlmAgent(
        name="answer_agent",
        model=selected_model,
        instruction=answer_agent_skill.instructions
    )

    # Coordina el flujo completo: intent_router → plan_react_planner (opcional) →
    # agente especializado → answer_agent. Es el único agente expuesto al exterior.
    orchestrator_tools = [
        AgentTool(agent=intent_router),
    ]

    if normalized_planner_mode in {"always", "full_only"}:
        orchestrator_tools.append(_create_plan_react_planner_agent(selected_model))

    orchestrator_tools.extend(
        [
            strava_ingestion_agent,
            query_agent,
            AgentTool(agent=answer_agent),
        ]
    )

    orchestrator_instruction = _build_orchestrator_instruction(normalized_planner_mode)

    return LlmAgent(
    name="orchestrator",
    model=selected_model,
    instruction=orchestrator_instruction,
    tools=orchestrator_tools,
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
