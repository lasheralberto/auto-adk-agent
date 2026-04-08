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
    activity_analysis_skill,
    daily_summary_skill,
    performance_insight_skill,
    wiki_builder_skill,
    embedding_skill,
    query_skill,
)
from agent.tools.pipeline import (
    run_ingestion_pipeline,
    run_activity_analysis_pipeline,
    run_daily_summary_pipeline,
    run_performance_insight_pipeline,
    run_wiki_builder_pipeline,
    run_embedding_pipeline,
    run_query_pipeline,
    run_daily_orchestration_pipeline,
)


def _build_plan_react_kwargs() -> dict[str, object]:
    if PlanReActPlanner is None:
        return {}

    try:
        return {"planner": PlanReActPlanner()}
    except Exception:  # noqa: BLE001
        return {}


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
        "You are the orchestrator for a 4-layer Strava architecture.\n\n"
        "Layers:\n"
        "1) Data Layer: strava_ingestion_agent\n"
        "2) Processing Layer: activity_analysis_agent, daily_summary_agent, performance_insight_agent\n"
        "3) Knowledge Layer: wiki_builder_agent\n"
        "4) Indexing/Query Layer: embedding_agent, query_agent\n\n"
        "Available tools/agents:\n"
        "- intent_router\n"
        "- plan_react_planner\n"
        "- daily_pipeline_agent\n"
        "- strava_ingestion_agent\n"
        "- activity_analysis_agent\n"
        "- daily_summary_agent\n"
        "- performance_insight_agent\n"
        "- wiki_builder_agent\n"
        "- embedding_agent\n"
        "- query_agent\n"
        "- answer_agent\n\n"
        "Routing rules:\n"
        "- For user questions about training data, always delegate to query_agent first.\n"
        "- For sync/rebuild/reindex requests, use daily_pipeline_agent or stage-specific pipeline agents.\n"
        "- Do not call Strava API agents for normal Q&A; use indexed knowledge context.\n"
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

    intent_router = LlmAgent(
        name="intent_router",
        model=selected_model,
        instruction=intent_router_skill.instructions,
    )

    strava_ingestion_agent = AgentTool(agent=LlmAgent(
        name="strava_ingestion_agent",
        model=selected_model,
        instruction=strava_ingestion_skill.instructions,
        tools=[run_ingestion_pipeline],
    ))

    activity_analysis_agent = AgentTool(agent=LlmAgent(
        name="activity_analysis_agent",
        model=selected_model,
        instruction=activity_analysis_skill.instructions,
        tools=[run_activity_analysis_pipeline],
    ))

    daily_summary_agent = AgentTool(agent=LlmAgent(
        name="daily_summary_agent",
        model=selected_model,
        instruction=daily_summary_skill.instructions,
        tools=[run_daily_summary_pipeline],
    ))

    performance_insight_agent = AgentTool(agent=LlmAgent(
        name="performance_insight_agent",
        model=selected_model,
        instruction=performance_insight_skill.instructions,
        tools=[run_performance_insight_pipeline],
    ))

    wiki_builder_agent = AgentTool(agent=LlmAgent(
        name="wiki_builder_agent",
        model=selected_model,
        instruction=wiki_builder_skill.instructions,
        tools=[run_wiki_builder_pipeline],
    ))

    embedding_agent = AgentTool(agent=LlmAgent(
        name="embedding_agent",
        model=selected_model,
        instruction=embedding_skill.instructions,
        tools=[run_embedding_pipeline],
    ))

    query_agent = AgentTool(agent=LlmAgent(
        name="query_agent",
        model=selected_model,
        instruction=query_skill.instructions,
        tools=[run_query_pipeline],
    ))

    daily_pipeline_agent = AgentTool(agent=LlmAgent(
        name="daily_pipeline_agent",
        model=selected_model,
        instruction=(
            "Execute the daily multi-layer pipeline in this strict order: "
            "ingestion, activity analysis, daily summary, performance insight, wiki builder, embedding."
        ),
        tools=[run_daily_orchestration_pipeline],
    ))

    # ─── Answer Agent ────────────────────────────────────────────────────────────
    answer_agent = LlmAgent(
        name="answer_agent",
        model=selected_model,
        instruction=answer_agent_skill.instructions
    )

    # ─── Orchestrator ─────────────────────────────────────────────────────────────
    orchestrator_tools = [
        AgentTool(agent=intent_router),
    ]

    if normalized_planner_mode in {"always", "full_only"}:
        orchestrator_tools.append(_create_plan_react_planner_agent(selected_model))

    orchestrator_tools.extend(
        [
            daily_pipeline_agent,
            strava_ingestion_agent,
            activity_analysis_agent,
            daily_summary_agent,
            performance_insight_agent,
            wiki_builder_agent,
            embedding_agent,
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