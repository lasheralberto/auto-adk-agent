from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

try:
    from google import genai as _genai
    from google.genai import types as _genai_types
except Exception:  # noqa: BLE001
    _genai = None
    _genai_types = None


_DEFAULT_WORKER_MODEL = os.environ.get("RESEARCH_WIKI_WORKER_MODEL", "gemini-2.5-flash").strip()
_DEFAULT_CRITIC_MODEL = os.environ.get("RESEARCH_WIKI_CRITIC_MODEL", "gemini-2.5-flash").strip()
_MAX_EVAL_ITERATIONS = max(1, min(int(os.environ.get("RESEARCH_WIKI_MAX_EVAL_ITERATIONS", "2")), 5))


class FollowUpTask(BaseModel):
    task: str = Field(description="Targeted follow up task to improve the research output.")


class ResearchFeedback(BaseModel):
    grade: Literal["pass", "fail"] = Field(
        description="Evaluation result. pass if findings are sufficient, fail otherwise."
    )
    comment: str = Field(description="Detailed QA feedback about missing depth or quality.")
    follow_up_tasks: list[FollowUpTask] = Field(
        default_factory=list,
        description="Specific follow up tasks to close quality gaps.",
    )


class ResearchAgentResult(BaseModel):
    athlete_id: int
    target_date: str
    window_days: int
    plan: str
    sections: str
    findings: str
    evaluations: list[dict[str, Any]]
    final_report: str
    created_at: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    text = (raw_text or "").strip()
    if not text:
        return None

    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced_match:
        text = fenced_match.group(1).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last <= first:
        return None

    candidate = text[first:last + 1]
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        return None

    return None


def _gen_text(
    client: Any,
    *,
    model: str,
    system_instruction: str,
    payload: str,
) -> str:
    response = client.models.generate_content(
        model=model,
        config=_genai_types.GenerateContentConfig(system_instruction=system_instruction),
        contents=payload,
    )
    return (response.text or "").strip()


def _compact_record(record: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "_id",
        "id",
        "name",
        "sport_type",
        "distance",
        "moving_time",
        "total_elevation_gain",
        "average_speed",
        "average_heartrate",
        "max_heartrate",
        "average_watts",
        "kilojoules",
        "start_date",
        "start_date_local",
        "summary",
    ]
    compact: dict[str, Any] = {}
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        compact[key] = value
    return compact


def _build_dataset_digest(
    *,
    athlete_id: int,
    target_date: str,
    window_days: int,
    target_records: list[dict[str, Any]],
    historical_records: list[dict[str, Any]],
    metrics: dict[str, Any] | None,
    athlete_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = athlete_profile if isinstance(athlete_profile, dict) else {}
    digest = {
        "athlete_id": athlete_id,
        "target_date": target_date,
        "window_days": window_days,
        "metrics": metrics or {},
        "athlete_profile": {
            "id": profile.get("id") or athlete_id,
            "firstname": profile.get("firstname"),
            "lastname": profile.get("lastname"),
            "sex": profile.get("sex"),
            "city": profile.get("city"),
            "country": profile.get("country"),
        },
        "target_records": [_compact_record(item) for item in target_records[:200]],
        "historical_records": [_compact_record(item) for item in historical_records[:400]],
    }
    return digest


def _generate_plan(client: Any, *, model: str, digest: dict[str, Any]) -> str:
    instruction = (
        "You are a deep research planner for endurance training analytics. "
        "Create exactly 5 action oriented goals as a markdown bullet list. "
        "Then append any implied deliverables. "
        "Prefix goals with [RESEARCH] and implied outputs with [DELIVERABLE][IMPLIED]. "
        "Use only the provided Strava dataset. Do not mention web search or external sources. "
        "Write in Spanish."
    )
    payload = json.dumps(digest, ensure_ascii=False)
    return _gen_text(client, model=model, system_instruction=instruction, payload=payload)


def _generate_sections(client: Any, *, model: str, plan: str, digest: dict[str, Any]) -> str:
    instruction = (
        "You are a report architect. Build a markdown outline with 4 to 6 sections for a training deep-research report. "
        "The outline must cover rendimiento, datos de entrenamiento, mejoras, consejos, and riesgos. "
        "Do not include a references section. Write in Spanish."
    )
    payload = json.dumps(
        {
            "plan": plan,
            "dataset_summary": {
                "athlete_id": digest.get("athlete_id"),
                "target_date": digest.get("target_date"),
                "window_days": digest.get("window_days"),
                "metrics": digest.get("metrics"),
                "target_records_count": len(digest.get("target_records") or []),
                "historical_records_count": len(digest.get("historical_records") or []),
            },
        },
        ensure_ascii=False,
    )
    return _gen_text(client, model=model, system_instruction=instruction, payload=payload)


def _generate_findings(
    client: Any,
    *,
    model: str,
    plan: str,
    sections: str,
    digest: dict[str, Any],
) -> str:
    instruction = (
        "You are a research analyst. Execute every [RESEARCH] item first, then complete [DELIVERABLE] items. "
        "Use only the provided dataset. "
        "No internet, no external data, no Pinecone retrieval. "
        "Support claims using activity IDs when possible. "
        "Output markdown findings ready for QA. Write in Spanish."
    )
    payload = json.dumps(
        {
            "plan": plan,
            "sections": sections,
            "dataset": digest,
        },
        ensure_ascii=False,
    )
    return _gen_text(client, model=model, system_instruction=instruction, payload=payload)


def _evaluate_findings(
    client: Any,
    *,
    model: str,
    plan: str,
    sections: str,
    findings: str,
) -> ResearchFeedback:
    instruction = (
        "You are a strict quality evaluator for training research. "
        "Evaluate completeness, depth, actionability, and consistency. "
        "Return a single JSON object with fields: grade, comment, follow_up_tasks. "
        "grade must be pass or fail. follow_up_tasks is a list of objects with key task. "
        "If pass, follow_up_tasks should be empty."
    )
    payload = json.dumps(
        {
            "plan": plan,
            "sections": sections,
            "findings": findings,
        },
        ensure_ascii=False,
    )
    raw = _gen_text(client, model=model, system_instruction=instruction, payload=payload)
    parsed = _extract_json_object(raw)
    if isinstance(parsed, dict):
        try:
            return ResearchFeedback.model_validate(parsed)
        except Exception:  # noqa: BLE001
            pass

    # Fallback to pass if evaluator output is malformed.
    return ResearchFeedback(grade="pass", comment="Evaluator output parse fallback.", follow_up_tasks=[])


def _refine_findings(
    client: Any,
    *,
    model: str,
    findings: str,
    feedback: ResearchFeedback,
    digest: dict[str, Any],
) -> str:
    instruction = (
        "You are a refinement analyst. Improve the findings to address every follow up task exactly. "
        "Keep factual grounding on the provided dataset only. "
        "Do not introduce external assumptions. Write in Spanish."
    )
    payload = json.dumps(
        {
            "current_findings": findings,
            "feedback": feedback.model_dump(mode="json"),
            "dataset": digest,
        },
        ensure_ascii=False,
    )
    return _gen_text(client, model=model, system_instruction=instruction, payload=payload)


def _compose_final_report(
    client: Any,
    *,
    model: str,
    plan: str,
    sections: str,
    findings: str,
    digest: dict[str, Any],
) -> str:
    instruction = (
        "You are a senior endurance coach and technical writer. "
        "Compose a final polished markdown report using the provided outline and findings. "
        "Must include clear recommendations and practical next steps. "
        "Mention that conclusions are based on internal Strava data for the selected window. "
        "No references section. Write in Spanish."
    )
    payload = json.dumps(
        {
            "plan": plan,
            "sections": sections,
            "findings": findings,
            "dataset_summary": {
                "athlete_id": digest.get("athlete_id"),
                "target_date": digest.get("target_date"),
                "window_days": digest.get("window_days"),
                "metrics": digest.get("metrics"),
            },
        },
        ensure_ascii=False,
    )
    return _gen_text(client, model=model, system_instruction=instruction, payload=payload)


def run_deep_research_wiki_agent(
    *,
    athlete_id: int,
    target_date: str,
    window_days: int,
    target_records: list[dict[str, Any]],
    historical_records: list[dict[str, Any]],
    metrics: dict[str, Any] | None = None,
    athlete_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if _genai is None or _genai_types is None:
        raise RuntimeError("google-genai package is not installed")

    client = _genai.Client()
    digest = _build_dataset_digest(
        athlete_id=athlete_id,
        target_date=target_date,
        window_days=max(2, int(window_days)),
        target_records=target_records,
        historical_records=historical_records,
        metrics=metrics,
        athlete_profile=athlete_profile,
    )

    plan = _generate_plan(client, model=_DEFAULT_WORKER_MODEL, digest=digest)
    sections = _generate_sections(client, model=_DEFAULT_WORKER_MODEL, plan=plan, digest=digest)
    findings = _generate_findings(
        client,
        model=_DEFAULT_WORKER_MODEL,
        plan=plan,
        sections=sections,
        digest=digest,
    )

    evaluations: list[dict[str, Any]] = []
    for _ in range(_MAX_EVAL_ITERATIONS):
        feedback = _evaluate_findings(
            client,
            model=_DEFAULT_CRITIC_MODEL,
            plan=plan,
            sections=sections,
            findings=findings,
        )
        evaluations.append(feedback.model_dump(mode="json"))
        if feedback.grade == "pass":
            break
        findings = _refine_findings(
            client,
            model=_DEFAULT_WORKER_MODEL,
            findings=findings,
            feedback=feedback,
            digest=digest,
        )

    final_report = _compose_final_report(
        client,
        model=_DEFAULT_CRITIC_MODEL,
        plan=plan,
        sections=sections,
        findings=findings,
        digest=digest,
    )

    result = ResearchAgentResult(
        athlete_id=athlete_id,
        target_date=target_date,
        window_days=max(2, int(window_days)),
        plan=plan,
        sections=sections,
        findings=findings,
        evaluations=evaluations,
        final_report=final_report,
        created_at=_utc_now_iso(),
    )
    return result.model_dump(mode="json")
