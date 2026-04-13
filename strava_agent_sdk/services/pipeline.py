from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent.tools.pipeline.storage_backend import AthleteStateStore, utc_now_iso
from agent.tools.pipeline.wiki_vector_index import backfill_athlete as _backfill_wiki_index
from agent.tools.pipeline.workflow import (
    research_wiki_pipeline,
    run_daily_pipeline,
    run_strava_ingestion,
)

from strava_agent_sdk.errors import ValidationError

logger = logging.getLogger(__name__)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_athlete_ids_csv(
    *,
    athlete_id: int | None = None,
    athlete_ids: list[int] | None = None,
    athlete_ids_csv: str = "",
) -> str:
    if athlete_ids:
        normalized = [str(int(value)) for value in athlete_ids if int(value) > 0]
        if normalized:
            return ",".join(normalized)

    if isinstance(athlete_ids_csv, str) and athlete_ids_csv.strip():
        return athlete_ids_csv.strip()

    if athlete_id is not None and int(athlete_id) > 0:
        return str(int(athlete_id))

    return ""


class PipelineService:
    async def run_daily_pipeline(
        self,
        *,
        athlete_id: int | None = None,
        athlete_ids: list[int] | None = None,
        athlete_ids_csv: str = "",
        target_date: str = "",
        latest_limit: int = 10,
    ) -> dict[str, Any]:
        normalized_csv = _parse_athlete_ids_csv(
            athlete_id=athlete_id,
            athlete_ids=athlete_ids,
            athlete_ids_csv=athlete_ids_csv,
        )
        return await asyncio.to_thread(
            run_daily_pipeline,
            athlete_ids_csv=normalized_csv,
            target_date=(target_date or "").strip(),
            latest_limit=max(1, min(int(latest_limit), 200)),
        )

    async def run_research_wiki(
        self,
        *,
        athlete_id: int | None = None,
        athlete_ids: list[int] | None = None,
        athlete_ids_csv: str = "",
        daily_run_id: str = "",
        max_activities: int = 100,
    ) -> dict[str, Any]:
        normalized_csv = _parse_athlete_ids_csv(
            athlete_id=athlete_id,
            athlete_ids=athlete_ids,
            athlete_ids_csv=athlete_ids_csv,
        )
        return await asyncio.to_thread(
            research_wiki_pipeline,
            athlete_ids_csv=normalized_csv,
            daily_run_id=(daily_run_id or "").strip(),
            max_activities=max(1, min(int(max_activities), 500)),
        )

    async def run_stage(
        self,
        *,
        stage: str,
        athlete_id: int | None = None,
        athlete_ids: list[int] | None = None,
        athlete_ids_csv: str = "",
        latest_limit: int = 10,
        max_activities: int = 100,
        daily_run_id: str = "",
    ) -> dict[str, Any]:
        normalized_stage = (stage or "").strip().lower()
        normalized_csv = _parse_athlete_ids_csv(
            athlete_id=athlete_id,
            athlete_ids=athlete_ids,
            athlete_ids_csv=athlete_ids_csv,
        )

        if normalized_stage == "ingestion":
            return await asyncio.to_thread(
                run_strava_ingestion,
                athlete_ids_csv=normalized_csv,
                latest_limit=max(1, min(int(latest_limit), 200)),
            )

        if normalized_stage == "research_wiki":
            return await asyncio.to_thread(
                research_wiki_pipeline,
                athlete_ids_csv=normalized_csv,
                daily_run_id=(daily_run_id or "").strip(),
                max_activities=max(1, min(int(max_activities), 500)),
            )

        raise ValidationError("Unsupported stage. Use 'ingestion' or 'research_wiki'.")

    async def run_index_wiki(
        self,
        *,
        athlete_id: int | None = None,
        athlete_ids: list[int] | None = None,
        athlete_ids_csv: str = "",
    ) -> dict[str, Any]:
        normalized_csv = _parse_athlete_ids_csv(
            athlete_id=athlete_id,
            athlete_ids=athlete_ids,
            athlete_ids_csv=athlete_ids_csv,
        )

        def _run() -> dict[str, Any]:
            state_store = AthleteStateStore()

            target_ids: list[int] = []
            if normalized_csv:
                for raw in normalized_csv.split(","):
                    parsed_id = _to_int(raw, 0)
                    if parsed_id > 0:
                        target_ids.append(parsed_id)
            else:
                for record in state_store.list_athletes_with_tokens():
                    parsed_id = _to_int(record.get("athlete_id"), 0)
                    if parsed_id > 0:
                        target_ids.append(parsed_id)

            target_ids = sorted(set(target_ids))

            started_at = utc_now_iso()
            results: list[dict[str, Any]] = []
            total_pages = 0
            total_indexed = 0
            failures = 0

            for resolved_athlete_id in target_ids:
                try:
                    result = _backfill_wiki_index(resolved_athlete_id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("index-wiki failed for athlete %s", resolved_athlete_id)
                    result = {
                        "athlete_id": resolved_athlete_id,
                        "status": "failed",
                        "error": str(exc),
                    }

                total_pages += _to_int(result.get("pages_found"), 0)
                total_indexed += _to_int(result.get("pages_indexed"), 0)
                if result.get("status") == "failed":
                    failures += 1
                results.append(result)

            status = "failed" if failures and failures == len(target_ids) else (
                "partial_failure" if failures else "success"
            )

            return {
                "stage": "index_wiki",
                "status": status,
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "athletes_processed": len(target_ids),
                "total_pages_found": total_pages,
                "total_pages_indexed": total_indexed,
                "results": results,
            }

        return await asyncio.to_thread(_run)
