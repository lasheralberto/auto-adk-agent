from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from flask import Flask, Response, jsonify, request, stream_with_context

from agent.service.stream_utils import _stream_generator
from agent.tools.pipeline.storage_backend import AthleteStateStore, utc_now_iso
from strava_agent_sdk import StravaAgentClient
from strava_agent_sdk.errors import ConflictError, ExternalServiceError, NotFoundError, SDKError, ValidationError

logger = logging.getLogger(__name__)


def _normalize_route(route_str: str) -> str:
    normalized = (route_str or "").strip().lower()
    if not normalized:
        return ""
    if normalized in {"*", "all"}:
        return normalized
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if len(normalized) > 1 and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return normalized


def _rule_matches_filter(rule: str, route_filter: str | None) -> bool:
    if route_filter is None:
        return True

    normalized_filter = _normalize_route(route_filter)
    if normalized_filter in {"", "*", "all"}:
        return True

    return _normalize_route(rule) == normalized_filter


def _make_filtered_decorator(
    base_decorator: Callable[..., Callable[[Any], Any]],
    route_filter: str | None,
) -> Callable[..., Callable[[Any], Any]]:
    def _decorator(rule: str, *args: Any, **kwargs: Any) -> Callable[[Any], Any]:
        if _rule_matches_filter(rule, route_filter):
            return base_decorator(rule, *args, **kwargs)

        def _passthrough(func: Any) -> Any:
            return func

        return _passthrough

    return _decorator


def register_route(app: Flask, sdk_client: StravaAgentClient, route_str: str) -> None:
    if any(_rule_matches_filter(rule.rule, route_str) for rule in app.url_map.iter_rules()):
        return

    previous_rules = len(app.url_map._rules)
    register_routes(app, sdk_client, route_filter=route_str)
    if len(app.url_map._rules) == previous_rules:
        raise ValueError(f"No route matched '{route_str}'.")


def register_routes(
    app: Flask,
    sdk_client: StravaAgentClient,
    route_filter: str | None = None,
) -> None:
    app_get = _make_filtered_decorator(app.get, route_filter)
    app_post = _make_filtered_decorator(app.post, route_filter)
    app_put = _make_filtered_decorator(app.put, route_filter)
    app_delete = _make_filtered_decorator(app.delete, route_filter)
    app_route = _make_filtered_decorator(app.route, route_filter)

    @app_get("/health")
    def health() -> tuple[dict[str, Any], int]:
        return {
            "status": "ok",
            "architecture": "layered-pipeline-v2",
        }, 200

    @app_get("/athletes")
    def list_athletes() -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized."}, 401

        try:
            return asyncio.run(sdk_client.list_athletes()), 200
        except SDKError as exc:
            return {"error": str(exc)}, 500
        except Exception as exc:  # noqa: BLE001
            return {"error": "Failed to list athletes.", "details": str(exc)}, 500

    @app_post("/pipeline/query")
    def query_layer_endpoint() -> tuple[dict[str, Any], int]:
        data = request.get_json(silent=True) or {}
        question = (data.get("question") or data.get("message") or "").strip()
        athlete_id = sdk_client.to_optional_int(data.get("athlete_id") or data.get("strava_athlete_id"))
        top_k = max(1, min(sdk_client.to_int(data.get("top_k"), 5), 20))
        target_date = data.get("target_date") if isinstance(data.get("target_date"), str) else ""

        if not question:
            return {"error": "Field 'question' or 'message' is required."}, 400
        if athlete_id is None or athlete_id <= 0:
            return {"error": "Field 'athlete_id' or 'strava_athlete_id' is required."}, 400

        try:
            payload = asyncio.run(sdk_client.query_wiki(
                question=question,
                athlete_id=athlete_id,
                top_k=top_k,
                target_date=target_date,
            ))
            return payload, 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            return {"error": "Query layer execution failed.", "details": str(exc)}, 500

    @app_post("/chat")
    @app_post("/ask")
    def chat_agent() -> Response | tuple[dict[str, Any], int]:
        data = request.get_json(silent=True) or {}
        (
            question,
            model_name_to_use,
            stream,
            top_k,
            query_target_date,
            strava_access_token,
            strava_athlete_id,
            response_format,
            planner_mode,
        ) = sdk_client.parse_chat_request(
            data=data,
            authorization_header=request.headers.get("Authorization", ""),
        )

        if not isinstance(question, str) or not question.strip():
            return {"error": "Field 'message' or 'question' must be a non-empty string."}, 400

        if strava_athlete_id is None or strava_athlete_id <= 0:
            return {
                "error": "Field 'strava_athlete_id' or 'athlete_id' is required for multi-athlete retrieval."
            }, 400

        if stream:
            run_streaming_with_sdk = lambda current_question, _agent: sdk_client.chat_stream(
                question=current_question,
                athlete_id=int(strava_athlete_id),
                model_name=model_name_to_use,
                top_k=top_k,
                target_date=query_target_date,
                access_token=strava_access_token,
                response_format=response_format,
                planner_mode=planner_mode,
            )
            return Response(
                stream_with_context(
                    _stream_generator(question.strip(), None, run_streaming_with_sdk)
                ),
                content_type="text/event-stream",
                headers={
                    "X-Accel-Buffering": "no",
                    "Cache-Control": "no-cache, no-transform",
                    "Connection": "keep-alive",
                },
            )

        try:
            result = asyncio.run(sdk_client.chat(
                question=question.strip(),
                athlete_id=int(strava_athlete_id),
                model_name=model_name_to_use,
                top_k=top_k,
                target_date=query_target_date,
                access_token=strava_access_token,
                response_format=response_format,
                planner_mode=planner_mode,
            ))
            return jsonify(result.to_payload())
        except ValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        except ExternalServiceError as exc:
            return jsonify({"error": str(exc)}), 400
        except SDKError as exc:
            return jsonify({"error": str(exc)}), 500
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": "Chat execution failed.", "details": str(exc)}), 500

    @app_get("/auth/strava/start")
    def start_strava_auth() -> tuple[dict[str, Any], int]:
        redirect_uri = (request.args.get("redirect_uri") or "").strip()
        requested_scope = (request.args.get("scope") or "").strip()
        try:
            payload = asyncio.run(sdk_client.start_strava_oauth(
                redirect_uri=redirect_uri,
                scope=requested_scope,
            ))
            return payload, 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except SDKError as exc:
            return {"error": str(exc)}, 500
        except Exception as exc:  # noqa: BLE001
            return {"error": "Strava OAuth start failed.", "details": str(exc)}, 500

    @app_post("/auth/strava/exchange")
    def exchange_strava_auth_code() -> tuple[dict[str, Any], int]:
        data = request.get_json(silent=True) or {}
        try:
            payload = asyncio.run(sdk_client.exchange_strava_code(
                code=(data.get("code") or "").strip(),
                state=(data.get("state") or "").strip(),
                redirect_uri=(data.get("redirect_uri") or "").strip(),
                scope=(data.get("scope") or "").strip(),
            ))
            return payload, 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except ExternalServiceError as exc:
            return {"error": "Strava token exchange failed.", "details": str(exc)}, 400
        except SDKError as exc:
            return {"error": str(exc)}, 500
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}, 500

    @app_post("/auth/strava/refresh")
    def refresh_strava_auth_token() -> tuple[dict[str, Any], int]:
        data = request.get_json(silent=True) or {}
        try:
            athlete_id = sdk_client.to_optional_int(data.get("strava_athlete_id") or data.get("athlete_id"))
            payload = asyncio.run(sdk_client.refresh_strava_token(
                refresh_token=(data.get("refresh_token") or "").strip(),
                athlete_id=athlete_id,
            ))
            return payload, 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except ExternalServiceError as exc:
            return {"error": "Strava token refresh failed.", "details": str(exc)}, 400
        except SDKError as exc:
            return {"error": str(exc)}, 500
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}, 500

    @app_post("/internal/pipeline/daily")
    @app_post("/pipeline/daily")
    def run_daily_pipeline_endpoint() -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized internal pipeline trigger."}, 401

        data = request.get_json(silent=True) or {}
        athlete_ids_csv = sdk_client.parse_athlete_ids_csv(data)
        target_date = data.get("target_date") if isinstance(data.get("target_date"), str) else ""

        try:
            report = asyncio.run(sdk_client.run_daily_pipeline(
                athlete_ids_csv=athlete_ids_csv,
                target_date=target_date,
            ))

            run_id = str(report.get("run_id") or "").strip()
            queued_at = utc_now_iso()

            dispatch_payload = {
                "athlete_id": sdk_client.to_optional_int(data.get("athlete_id")),
                "athlete_ids_csv": athlete_ids_csv,
                "daily_run_id": run_id,
            }

            report_steps = report.setdefault("steps", {})
            if not isinstance(report_steps, dict):
                report_steps = {}
                report["steps"] = report_steps
            report_steps.pop("research_wiki_dispatch", None)

            report["status"] = "running"
            report["updated_at"] = queued_at
            report.pop("finished_at", None)

            try:
                dispatch_result = sdk_client.dispatch_research_wiki_async(dispatch_payload)
                report_steps["research_wiki"] = {
                    "stage": "research_wiki",
                    "status": "queued",
                    "queued_at": queued_at,
                    "endpoint": str(dispatch_result.get("endpoint") or ""),
                }
            except Exception as exc:  # noqa: BLE001
                report_steps["research_wiki"] = {
                    "stage": "research_wiki",
                    "status": "failed",
                    "endpoint": f"{sdk_client.internal_pipeline_base_url()}/internal/pipeline/research-wiki",
                    "error": str(exc),
                    "finished_at": utc_now_iso(),
                }
                report["status"] = "partial_failure"
                report["finished_at"] = utc_now_iso()
                report["updated_at"] = report["finished_at"]

            report_steps.pop("index_wiki_dispatch", None)
            try:
                index_dispatch_result = sdk_client.dispatch_index_wiki_async(dispatch_payload)
                report_steps["index_wiki"] = {
                    "stage": "index_wiki",
                    "status": "queued",
                    "queued_at": queued_at,
                    "endpoint": str(index_dispatch_result.get("endpoint") or ""),
                }
            except Exception as exc:  # noqa: BLE001
                report_steps["index_wiki"] = {
                    "stage": "index_wiki",
                    "status": "failed",
                    "endpoint": f"{sdk_client.internal_pipeline_base_url()}/internal/pipeline/index-wiki",
                    "error": str(exc),
                    "finished_at": utc_now_iso(),
                }
                if report.get("status") != "partial_failure":
                    report["status"] = "partial_failure"
                    report["finished_at"] = utc_now_iso()
                    report["updated_at"] = report["finished_at"]

            if run_id:
                AthleteStateStore().record_pipeline_run(run_id, report)

            return report, 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            return {"error": "Daily pipeline execution failed.", "details": str(exc)}, 500

    @app_post("/internal/pipeline/research-wiki")
    @app_post("/pipeline/research-wiki")
    def run_research_wiki_endpoint() -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized internal pipeline trigger."}, 401

        data = request.get_json(silent=True) or {}
        athlete_ids_csv = sdk_client.parse_athlete_ids_csv(data)
        daily_run_id = str(data.get("daily_run_id") or "").strip()
        max_activities = max(1, min(sdk_client.to_int(data.get("max_activities"), 100), 500))

        try:
            report = asyncio.run(sdk_client.run_research_wiki(
                athlete_ids_csv=athlete_ids_csv,
                daily_run_id=daily_run_id,
                max_activities=max_activities,
            ))
            return report, 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            return {"error": "Research wiki pipeline execution failed.", "details": str(exc)}, 500

    @app_post("/internal/pipeline/index-wiki")
    @app_post("/pipeline/index-wiki")
    def run_index_wiki_endpoint() -> tuple[dict[str, Any], int]:
        """Re-index wiki pages in Pinecone.

        Accepts ``athlete_id``, ``athlete_ids`` (list) or ``athlete_ids_csv``.
        If none is specified, it processes all athletes with tokens.
        """
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized internal pipeline trigger."}, 401

        data = request.get_json(silent=True) or {}
        try:
            athlete_ids_csv = sdk_client.parse_athlete_ids_csv(data)
            report = asyncio.run(sdk_client.run_index_wiki(athlete_ids_csv=athlete_ids_csv))
            return report, 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            return {"error": "Index wiki pipeline execution failed.", "details": str(exc)}, 500

    @app_get("/pipeline/run/<run_id>")
    def get_pipeline_run_endpoint(run_id: str) -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized."}, 401

        try:
            return asyncio.run(sdk_client.get_pipeline_run(run_id=run_id.strip())), 200
        except NotFoundError:
            return {"error": "run_not_found", "run_id": run_id}, 404
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            return {"error": "Failed to get pipeline run.", "details": str(exc)}, 500

    @app_get("/pipeline/runs")
    def list_pipeline_runs_endpoint() -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized."}, 401

        limit = max(1, min(sdk_client.to_int(request.args.get("limit"), 20), 100))
        stage = (request.args.get("stage") or "").strip()

        try:
            payload = asyncio.run(sdk_client.list_pipeline_runs(limit=limit, stage=stage))
            return payload, 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            return {"error": "Failed to list pipeline runs.", "details": str(exc)}, 500

    @app_post("/internal/pipeline/stage")
    @app_post("/pipeline/stage")
    def run_pipeline_stage_endpoint() -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized internal pipeline trigger."}, 401

        data = request.get_json(silent=True) or {}
        stage = (data.get("stage") or "").strip().lower()
        if not stage:
            return {"error": "Field 'stage' is required."}, 400

        try:
            report = asyncio.run(sdk_client.run_pipeline_stage(
                stage=stage,
                athlete_ids_csv=sdk_client.parse_athlete_ids_csv(data),
                latest_limit=max(1, min(sdk_client.to_int(data.get("latest_limit"), 10), 200)),
                max_activities=max(1, min(sdk_client.to_int(data.get("max_activities"), 100), 500)),
                daily_run_id=str(data.get("daily_run_id") or "").strip(),
            ))
            return report, 200
        except ValidationError as exc:
            if "Unsupported stage" in str(exc):
                return {
                    "error": "Unsupported stage.",
                    "supported_stages": [
                        "ingestion",
                        "research_wiki",
                    ],
                }, 400
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            return {"error": "Pipeline stage execution failed.", "details": str(exc)}, 500

    @app_get("/pipeline/activities-runs")
    def list_activities_runs_endpoint() -> tuple[dict[str, Any], int]:
        """List Strava activity indexing runs for an athlete."""
        athlete_id = sdk_client.to_optional_int(request.args.get("athlete_id"))
        if athlete_id is None or athlete_id <= 0:
            return {"error": "Query param 'athlete_id' is required."}, 400

        limit = max(1, min(sdk_client.to_int(request.args.get("limit"), 20), 100))

        try:
            return asyncio.run(sdk_client.list_activity_runs(athlete_id=athlete_id, limit=limit)), 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            logger.exception("list_activity_runs failed for athlete %s", athlete_id)
            return {"error": "Failed to list activity runs.", "details": str(exc)}, 500

    @app_get("/pipeline/indexed-activities")
    def list_indexed_activities_endpoint() -> tuple[dict[str, Any], int]:
        """List only indexed activities for an athlete ordered by date desc."""
        athlete_id = sdk_client.to_optional_int(request.args.get("athlete_id"))
        if athlete_id is None or athlete_id <= 0:
            return {"error": "Query param 'athlete_id' is required."}, 400

        limit = max(1, min(sdk_client.to_int(request.args.get("limit"), 20), 100))

        try:
            return asyncio.run(sdk_client.list_indexed_activities(
                athlete_id=athlete_id,
                limit=limit,
            )), 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            logger.exception("list_indexed_activities failed for athlete %s", athlete_id)
            return {"error": "Failed to list indexed activities.", "details": str(exc)}, 500

    @app_get("/pipeline/indexing-status")
    def get_indexing_status() -> tuple[dict[str, Any], int]:
        athlete_id = sdk_client.to_optional_int(request.args.get("athlete_id"))
        if athlete_id is None or athlete_id <= 0:
            return {"error": "Query param 'athlete_id' is required."}, 400

        try:
            return asyncio.run(sdk_client.get_indexing_status(athlete_id=athlete_id)), 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            return {"error": "Failed to get indexing status.", "details": str(exc)}, 500

    @app_post("/chat/wiki")
    def chat_wiki_agent() -> Response | tuple[dict[str, Any], int]:
        data = request.get_json(silent=True) or {}

        question = (data.get("message") or data.get("question") or "").strip()
        if not question:
            return {"error": "Field 'message' or 'question' must be a non-empty string."}, 400

        athlete_id = sdk_client.to_optional_int(data.get("athlete_id") or data.get("strava_athlete_id"))
        if athlete_id is None or athlete_id <= 0:
            return {"error": "Field 'athlete_id' is required."}, 400

        model_raw = data.get("model")
        model_name = model_raw.strip() if isinstance(model_raw, str) and model_raw.strip() else None

        agent_id_raw = data.get("agent_id")
        agent_id = agent_id_raw.strip() if isinstance(agent_id_raw, str) and agent_id_raw.strip() else None

        stream_param = data.get("stream", False)
        if isinstance(stream_param, str):
            stream = stream_param.lower() in ("true", "1", "yes")
        else:
            stream = bool(stream_param)

        if stream:
            run_streaming_fn = lambda q, _agent: sdk_client.chat_wiki_stream(
                question=q,
                athlete_id=int(athlete_id),
                model_name=model_name,
                agent_id=agent_id,
            )
            return Response(
                stream_with_context(
                    _stream_generator(question, None, run_streaming_fn)
                ),
                content_type="text/event-stream",
                headers={
                    "X-Accel-Buffering": "no",
                    "Cache-Control": "no-cache, no-transform",
                    "Connection": "keep-alive",
                },
            )

        try:
            result = asyncio.run(sdk_client.chat_wiki(
                question=question,
                athlete_id=int(athlete_id),
                model_name=model_name,
                agent_id=agent_id,
            ))
            payload = result.to_payload()
            payload["athlete_id"] = athlete_id
            return jsonify(payload)
        except ValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        except NotFoundError as exc:
            return jsonify({"error": "wiki_not_found", "details": str(exc)}), 404
        except SDKError as exc:
            return jsonify({"error": str(exc)}), 500
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": "Wiki chat agent setup failed.", "details": str(exc)}), 500

    @app_get("/agent-definition-logs/<athlete_id>")
    def get_agent_definition_logs_endpoint(athlete_id: str) -> tuple[dict[str, Any], int]:
        parsed_athlete_id = sdk_client.to_optional_int(athlete_id.strip())
        if parsed_athlete_id is None or parsed_athlete_id <= 0:
            return {"error": "Path param 'athlete_id' is required."}, 400

        page = max(1, sdk_client.to_int(request.args.get("page"), 1))
        page_size = max(1, min(sdk_client.to_int(request.args.get("page_size"), 5), 50))

        include_events_raw = request.args.get("include_events", "false")
        include_events = str(include_events_raw).strip().lower() in {"1", "true", "yes", "on"}

        try:
            payload = asyncio.run(sdk_client.get_agent_chain_logs(
                athlete_id=int(parsed_athlete_id),
                page=page,
                page_size=page_size,
                include_events=include_events,
            ))
            return payload, 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            logger.exception("get_agent_definition_logs failed for athlete %s", parsed_athlete_id)
            return {"error": "Failed to fetch agent definition logs.", "details": str(exc)}, 500

    @app_get("/agents")
    def list_agents_endpoint() -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized."}, 401
        try:
            return asyncio.run(sdk_client.list_agents()), 200
        except Exception as exc:  # noqa: BLE001
            return {"error": "Failed to list agents.", "details": str(exc)}, 500

    @app_get("/agents/<agent_id>")
    def get_agent_endpoint(agent_id: str) -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized."}, 401
        try:
            return asyncio.run(sdk_client.get_agent(agent_id=agent_id.strip())), 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except NotFoundError as exc:
            return {"error": str(exc)}, 404
        except Exception as exc:  # noqa: BLE001
            return {"error": "Failed to get agent.", "details": str(exc)}, 500

    @app_post("/agents")
    def create_agent_endpoint() -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized."}, 401
        data = request.get_json(silent=True) or {}

        agent_id = (data.get("agent_id") or "").strip()
        name = (data.get("name") or "").strip()
        instruction_template = data.get("instruction_template")
        description = (data.get("description") or "").strip()
        updated_by_raw = data.get("updated_by")
        updated_by = updated_by_raw.strip() if isinstance(updated_by_raw, str) and updated_by_raw.strip() else None

        if not agent_id:
            return {"error": "Field 'agent_id' is required."}, 400
        if not name:
            return {"error": "Field 'name' is required."}, 400
        if not isinstance(instruction_template, str) or not instruction_template.strip():
            return {"error": "Field 'instruction_template' must be a non-empty string."}, 400

        try:
            payload = asyncio.run(sdk_client.create_agent(
                agent_id=agent_id,
                name=name,
                description=description,
                instruction_template=instruction_template,
                updated_by=updated_by,
            ))
            return payload, 201
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            return {"error": "Failed to create agent.", "details": str(exc)}, 500

    @app_route("/agents/<agent_id>", methods=["PUT", "POST"])
    def update_agent_endpoint(agent_id: str) -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized."}, 401
        data = request.get_json(silent=True) or {}
        instruction_template = data.get("instruction_template")
        name = data.get("name")
        description = data.get("description")
        updated_by_raw = data.get("updated_by")
        updated_by = updated_by_raw.strip() if isinstance(updated_by_raw, str) and updated_by_raw.strip() else None

        if not isinstance(instruction_template, str) or not instruction_template.strip():
            return {"error": "Field 'instruction_template' must be a non-empty string."}, 400

        try:
            payload = asyncio.run(sdk_client.update_agent(
                agent_id=agent_id.strip(),
                instruction_template=instruction_template,
                name=name.strip() if isinstance(name, str) and name.strip() else None,
                description=description.strip() if isinstance(description, str) else None,
                updated_by=updated_by,
            ))
            return payload, 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except NotFoundError as exc:
            return {"error": str(exc)}, 404
        except Exception as exc:  # noqa: BLE001
            return {"error": "Failed to update agent.", "details": str(exc)}, 500

    @app_delete("/agents/<agent_id>")
    def delete_agent_endpoint(agent_id: str) -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized."}, 401
        try:
            payload = asyncio.run(sdk_client.delete_agent(agent_id=agent_id.strip()))
            return payload, 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            return {"error": "Failed to delete agent.", "details": str(exc)}, 500

    @app_get("/agent-definition/<athlete_id>")
    def get_agent_definition_endpoint(athlete_id: str) -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized."}, 401

        try:
            payload = asyncio.run(sdk_client.get_agent_definition(athlete_id=athlete_id.strip()))
            return payload, 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            return {"error": "Failed to get agent definition.", "details": str(exc)}, 500

    @app_put("/agent-definition/<athlete_id>")
    def put_agent_definition_endpoint(athlete_id: str) -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized."}, 401

        data = request.get_json(silent=True) or {}
        toml_content = data.get("toml_content")
        version = data.get("version")
        updated_by_raw = data.get("updated_by")
        updated_by = updated_by_raw.strip() if isinstance(updated_by_raw, str) and updated_by_raw.strip() else None

        if not isinstance(toml_content, str) or not toml_content.strip():
            return {"error": "Field 'toml_content' must be a non-empty string."}, 400

        parsed_version = sdk_client.to_optional_int(version)
        if parsed_version is None or parsed_version < 0:
            return {"error": "Field 'version' must be an integer >= 0."}, 400

        try:
            payload = asyncio.run(sdk_client.update_agent_definition(
                athlete_id=athlete_id.strip(),
                toml_content=toml_content,
                version=parsed_version,
                updated_by=updated_by,
            ))
            return payload, 200
        except ConflictError as exc:
            return {"error": str(exc)}, 409
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            return {"error": "Failed to update agent definition.", "details": str(exc)}, 500

    @app_delete("/agent-definition/<athlete_id>")
    def delete_agent_definition_endpoint(athlete_id: str) -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized."}, 401

        try:
            payload = asyncio.run(sdk_client.delete_agent_definition(athlete_id=athlete_id.strip()))
            return payload, 200
        except ValidationError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            return {"error": "Failed to delete agent definition.", "details": str(exc)}, 500

    @app_post("/agent-definition/<athlete_id>/validate")
    def validate_agent_definition_endpoint(athlete_id: str) -> tuple[dict[str, Any], int]:
        if not sdk_client.internal_request_authorized(headers=request.headers):
            return {"error": "Unauthorized."}, 401

        if not athlete_id.strip():
            return {"error": "athlete_id is required."}, 400

        data = request.get_json(silent=True) or {}
        toml_content = data.get("toml_content")
        if not isinstance(toml_content, str) or not toml_content.strip():
            return {"error": "Field 'toml_content' must be a non-empty string."}, 400

        try:
            payload = asyncio.run(sdk_client.validate_agent_definition(toml_content=toml_content))
            status_code = 200 if payload.get("valid") else 400
            return payload, status_code
        except ValidationError as exc:
            return {"valid": False, "errors": [str(exc)]}, 400
        except Exception as exc:  # noqa: BLE001
            return {"error": "Failed to validate agent definition.", "details": str(exc)}, 500

    @app_route("/vector_stores", methods=["GET", "POST", "DELETE"])
    @app_post("/add_to_vs")
    @app_post("/strava/weekly-summary")
    @app_get("/search_vs")
    @app_post("/vectorize")
    @app_get("/get_vs_file_details")
    @app_get("/get_vs_file_content")
    @app_delete("/delete_vs_file")
    def deprecated_legacy_routes() -> tuple[dict[str, Any], int]:
        return {
            "error": "Endpoint removed in layered architecture.",
            "use": {
                "chat": ["/chat", "/ask"],
                "query": ["/pipeline/query"],
                "daily_pipeline": ["/pipeline/daily", "/internal/pipeline/daily"],
                "stage_pipeline": ["/pipeline/stage", "/internal/pipeline/stage"],
                "research_wiki_pipeline": ["/pipeline/research-wiki", "/internal/pipeline/research-wiki"],
                "index_wiki_pipeline": ["/pipeline/index-wiki", "/internal/pipeline/index-wiki"],
            },
        }, 410

