from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from agent.tools.pipeline.storage_backend import AthleteStateStore

from strava_agent_sdk.errors import NotFoundError, ValidationError


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


class StatusService:
    async def list_athletes(self) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            state_store = AthleteStateStore()
            athletes = state_store.list_athletes_with_tokens()

            normalized: list[dict[str, Any]] = []
            for athlete in athletes:
                profile = athlete.get("profile") if isinstance(athlete.get("profile"), dict) else {}
                normalized.append(
                    {
                        "athlete_id": athlete.get("athlete_id"),
                        "firstname": profile.get("firstname"),
                        "lastname": profile.get("lastname"),
                        "country": profile.get("country"),
                        "last_sync_epoch": athlete.get("last_sync_epoch"),
                        "last_sync_status": athlete.get("last_sync_status"),
                        "last_indexed_date": athlete.get("last_indexed_date"),
                        "token_updated_at": athlete.get("token_updated_at"),
                    }
                )

            return {
                "data": normalized,
                "count": len(normalized),
                "state_mode": state_store.mode,
            }

        return await asyncio.to_thread(_run)

    async def get_pipeline_run(self, run_id: str) -> dict[str, Any]:
        normalized_run_id = (run_id or "").strip()
        if not normalized_run_id:
            raise ValidationError("Field 'run_id' is required.")

        run = await asyncio.to_thread(AthleteStateStore().get_pipeline_run, normalized_run_id)
        if run is None:
            raise NotFoundError(f"run_not_found: {normalized_run_id}")
        return run

    async def list_pipeline_runs(self, *, limit: int = 20, stage: str = "") -> dict[str, Any]:
        resolved_limit = max(1, min(int(limit), 100))
        normalized_stage = (stage or "").strip()

        def _run() -> dict[str, Any]:
            state_store = AthleteStateStore()
            runs = state_store.list_pipeline_runs(limit=100 if normalized_stage else resolved_limit)

            if normalized_stage:
                filtered_runs: list[dict[str, Any]] = []
                for run in runs:
                    if run.get("stage") == normalized_stage:
                        filtered_runs.append(run)
                        continue

                    steps = run.get("steps")
                    if not isinstance(steps, dict):
                        continue

                    matched_step = steps.get(normalized_stage)
                    if not isinstance(matched_step, dict):
                        continue

                    normalized_run = dict(run)
                    step_status = matched_step.get("status")
                    if isinstance(step_status, str) and step_status.strip():
                        normalized_run["status"] = step_status.strip()
                    filtered_runs.append(normalized_run)

                runs = filtered_runs

            runs = runs[:resolved_limit]
            return {
                "runs": runs,
                "count": len(runs),
            }

        return await asyncio.to_thread(_run)

    async def list_activity_runs(self, *, athlete_id: int, limit: int = 20) -> dict[str, Any]:
        if athlete_id <= 0:
            raise ValidationError("Field 'athlete_id' is required.")

        resolved_limit = max(1, min(int(limit), 100))

        def _run() -> dict[str, Any]:
            runs = AthleteStateStore().list_activity_runs(
                athlete_id=athlete_id,
                limit=resolved_limit,
            )
            return {
                "athlete_id": athlete_id,
                "count": len(runs),
                "runs": runs,
            }

        return await asyncio.to_thread(_run)

    async def list_indexed_activities(self, *, athlete_id: int, limit: int = 20) -> dict[str, Any]:
        if athlete_id <= 0:
            raise ValidationError("Field 'athlete_id' is required.")

        resolved_limit = max(1, min(int(limit), 100))

        def _run() -> dict[str, Any]:
            state_store = AthleteStateStore()
            # Fetch all indexed activities for the athlete so the Python-side
            # sort+slice returns the correct newest N, not N arbitrary docs.
            fetch_limit = 500
            success_runs = state_store.list_activity_runs_by_status(
                "success",
                athlete_id=athlete_id,
                limit=fetch_limit,
            )
            partial_runs = state_store.list_activity_runs_by_status(
                "partial_success",
                athlete_id=athlete_id,
                limit=fetch_limit,
            )

            merged: dict[Any, dict[str, Any]] = {}
            for run in (*success_runs, *partial_runs):
                key = run.get("activity_id") or run.get("id")
                if key is None:
                    continue
                merged[key] = run

            def _sort_key(run: dict[str, Any]) -> str:
                return str(
                    run.get("processed_at")
                    or run.get("queued_at")
                    or run.get("status_updated_at")
                    or run.get("start_date")
                    or ""
                )

            runs = sorted(merged.values(), key=_sort_key, reverse=True)[:resolved_limit]
            return {
                "athlete_id": athlete_id,
                "count": len(runs),
                "runs": runs,
            }

        return await asyncio.to_thread(_run)

    async def get_indexing_status(self, *, athlete_id: int) -> dict[str, Any]:
        if athlete_id <= 0:
            raise ValidationError("Field 'athlete_id' is required.")

        def _run() -> dict[str, Any]:
            state_store = AthleteStateStore()
            today = datetime.now(timezone.utc).date().isoformat()
            athlete_doc = state_store.get_athlete(athlete_id) or {}
            last_indexed = athlete_doc.get("last_indexed_date") or state_store.get_last_indexed_date(athlete_id)
            last_sync_status = athlete_doc.get("last_sync_status")

            return {
                "athlete_id": athlete_id,
                "today": today,
                "last_indexed_date": last_indexed,
                "indexed_today": last_indexed == today,
                "last_sync_status": last_sync_status,
            }

        return await asyncio.to_thread(_run)
