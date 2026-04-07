import asyncio
import sys
from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from agent.runner import run_agent
from agent.config.config import (
    get_llm_provider,
    code_programmer_skill,
    answer_agent_skill,
    orchestrator_skill,
    generic_scripts_skill,
    script_generator_skill,
    memory_agent_skill,
    intent_router_skill,
<<<<<<< HEAD
    strava_agent_skill,
=======
    sd_agent_skill,
    fi_agent_skill,
    sap_technical_skill,
    cloudification_skill,
>>>>>>> e72f17c02742dfffd00f7332afb8aea5928227a8
)
from agent.tools.sandbox import (
    generate_script,
    run_in_sandbox_gcp,
    execute_inline_script,
    execute_project_script,
    list_project_scripts,
)
from agent.tools.mcp.sap_cloudification_tools import build_cloudification_agent
from agent.tools.memory import retrieve_memory_context, save_interaction_memory
from agent.tools.strava import (
    create_activity,
    create_upload,
    complete_strava_oauth,
    explore_segments,
    export_route_gpx,
    export_route_tcx,
    get_activity_by_id,
    get_activity_comments,
    get_activity_kudoers,
    get_activity_laps,
    get_activity_streams,
    get_activity_zones,
    get_athlete_stats,
    get_club_activities,
    get_club_admins,
    get_club_by_id,
    get_club_members,
    get_gear_by_id,
    get_logged_in_athlete,
    get_logged_in_athlete_zones,
    parse_strava_redirect_url,
    refresh_strava_access_token,
    get_route_by_id,
    get_route_streams,
    get_segment_by_id,
    get_segment_effort_by_id,
    get_segment_effort_streams,
    get_segment_streams,
    get_upload_by_id,
    list_athlete_routes,
    list_logged_in_athlete_activities,
    list_logged_in_athlete_clubs,
    list_segment_efforts,
    list_starred_segments,
    set_segment_starred,
    start_strava_oauth,
    train_strava_rl_model,
    update_activity_by_id,
    update_logged_in_athlete_weight,
)


def build_orchestrator(llm_provider: str | None = None, model_name: str | None = None) -> LlmAgent:
    selected_model = get_llm_provider(llm_provider=llm_provider, model_name=model_name)

    intent_router = LlmAgent(
        name="intent_router",
        model=selected_model,
        instruction=intent_router_skill.instructions,
    )

    # ─── Code Programmer Agent ───────────────────────────────────────────────────
    script_executor_agent = LlmAgent(
        name="generic_scripts_agent",
        model=selected_model,
        instruction=generic_scripts_skill.instructions,
        tools=[list_project_scripts, execute_project_script, execute_inline_script],
    )
    script_executor = AgentTool(agent=script_executor_agent)

    script_generator = AgentTool(agent=LlmAgent(
        name="script_generator_agent",
        model=selected_model,
        instruction=script_generator_skill.instructions,
        tools=[list_project_scripts, generate_script, execute_inline_script, script_executor],
    ))

    code_programmer = LlmAgent(
        name="code_programmer",
        model=selected_model,
        instruction=code_programmer_skill.instructions,
        tools=[
            run_in_sandbox_gcp,
            list_project_scripts,
            execute_project_script,
            execute_inline_script,
            script_executor,
            script_generator,
        ],
    )

    strava_agent = LlmAgent(
        name="strava_agent",
        model=selected_model,
        instruction=strava_agent_skill.instructions,
        tools=[
            start_strava_oauth,
            parse_strava_redirect_url,
            complete_strava_oauth,
            refresh_strava_access_token,
            get_logged_in_athlete,
            update_logged_in_athlete_weight,
            get_logged_in_athlete_zones,
            get_athlete_stats,
            list_logged_in_athlete_activities,
            get_activity_by_id,
            create_activity,
            update_activity_by_id,
            get_activity_laps,
            get_activity_zones,
            get_activity_comments,
            get_activity_kudoers,
            get_activity_streams,
            get_segment_by_id,
            list_starred_segments,
            set_segment_starred,
            list_segment_efforts,
            explore_segments,
            get_segment_effort_by_id,
            get_segment_effort_streams,
            get_segment_streams,
            get_club_by_id,
            get_club_members,
            get_club_admins,
            get_club_activities,
            list_logged_in_athlete_clubs,
            get_gear_by_id,
            get_route_by_id,
            list_athlete_routes,
            export_route_gpx,
            export_route_tcx,
            get_route_streams,
            create_upload,
            get_upload_by_id,
            train_strava_rl_model,
        ],
    )

    

    cloudification_agent = build_cloudification_agent(model=selected_model, skill=cloudification_skill)

    # ─── Answer Agent ────────────────────────────────────────────────────────────
    answer_agent = LlmAgent(
        name="answer_agent",
        model=selected_model,
        instruction=answer_agent_skill.instructions
    )

    memory_agent = LlmAgent(
        name="memory_agent",
        model=selected_model,
        instruction=memory_agent_skill.instructions,
        tools=[retrieve_memory_context, save_interaction_memory],
    )



    # ─── Orchestrator ─────────────────────────────────────────────────────────────
    return LlmAgent(
    name="orchestrator",
    model=selected_model,
    instruction=orchestrator_skill.instructions,
    tools=[
        AgentTool(agent=intent_router),
        AgentTool(agent=strava_agent),
        AgentTool(agent=code_programmer),
<<<<<<< HEAD
=======
        AgentTool(agent=sd_agent),          # ← directo al orchestrator
        AgentTool(agent=fi_agent),          # ← directo al orchestrator
        AgentTool(agent=sap_technical_agent), # ← directo al orchestrator
        AgentTool(agent=cloudification_agent), # ← directo al orchestrator
>>>>>>> e72f17c02742dfffd00f7332afb8aea5928227a8
        AgentTool(agent=answer_agent),      # solo para respuestas generales
        
    ],
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