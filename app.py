import asyncio
import json
import os
import tempfile
import threading
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import requests
from flask import Flask, Response, jsonify, request, stream_with_context
from flask_cors import CORS
from werkzeug.utils import secure_filename
from openai import OpenAI

from agent.app import build_orchestrator
from agent.runner import run_agent, run_agent_streaming
from agent.tools.vectors import vector_store
from agent.service.stream_utils import _rag_stream_generator

app = Flask(__name__)

_DEFAULT_ALLOWED_ORIGINS = [
    "https://strava-adk-agent-frontend.vercel.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
_configured_origins = [
    origin.strip()
    for origin in (os.environ.get("CORS_ALLOWED_ORIGINS") or "").split(",")
    if origin.strip()
]
allowed_origins = _configured_origins or _DEFAULT_ALLOWED_ORIGINS
CORS(app, origins=allowed_origins, supports_credentials=True)

# OAuth state cache (short-lived) for CSRF protection in UI auth flow.
_STRAVA_STATE_TTL_SECONDS = 600
_strava_oauth_state_cache: dict[str, float] = {}
_strava_oauth_lock = threading.Lock()

_STRAVA_API_BASE_URL = "https://www.strava.com/api/v3"
_DEFAULT_WEEKLY_DAYS = 7
_MAX_WEEKLY_DAYS = 31
_WEEKLY_ACTIVITY_PER_PAGE = 100
_WEEKLY_ACTIVITY_MAX_PAGES = 10
_DEFAULT_ZONE_SAMPLE_LIMIT = 8
_MAX_ZONE_SAMPLE_LIMIT = 20
_DEFAULT_CYCLING_SPORT_TYPES = {
    "Ride",
    "MountainBikeRide",
    "GravelRide",
    "VirtualRide",
    "EBikeRide",
    "EMountainBikeRide",
    "Velomobile",
    "Handcycle",
}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return value != 0
    return default


def _extract_http_error_details(exc: requests.HTTPError) -> str:
    response = exc.response
    if response is None:
        return str(exc)

    try:
        payload = response.json()
        if isinstance(payload, dict):
            if payload.get("message"):
                return str(payload["message"])
            if payload.get("errors"):
                return json.dumps(payload["errors"], ensure_ascii=False)
            return json.dumps(payload, ensure_ascii=False)
    except ValueError:
        pass

    return response.text or str(exc)


def _extract_strava_access_token(payload: dict[str, Any]) -> str | None:
    candidates = [
        payload.get("strava_access_token"),
        payload.get("access_token"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:].strip()
        if bearer_token:
            return bearer_token

    return None


def _resolve_week_window(days: int, end_date_raw: str | None) -> tuple[date, date, int, int]:
    if end_date_raw:
        end_date = date.fromisoformat(end_date_raw)
    else:
        end_date = datetime.now(timezone.utc).date()

    start_date = end_date - timedelta(days=days - 1)
    start_epoch = int(datetime.combine(start_date, dt_time.min, tzinfo=timezone.utc).timestamp())
    end_epoch_exclusive = int(
        datetime.combine(end_date + timedelta(days=1), dt_time.min, tzinfo=timezone.utc).timestamp()
    )
    return start_date, end_date, start_epoch, end_epoch_exclusive


def _strava_api_request(
    method: str,
    path: str,
    access_token: str,
    *,
    params: dict[str, Any] | None = None,
) -> Any:
    response = requests.request(
        method=method,
        url=f"{_STRAVA_API_BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params={key: value for key, value in (params or {}).items() if value is not None},
        timeout=30,
    )
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    if "application/json" in content_type:
        return response.json()
    return response.text


def _fetch_activities_for_window(
    access_token: str,
    *,
    after_epoch: int,
    before_epoch: int,
    per_page: int = _WEEKLY_ACTIVITY_PER_PAGE,
    max_pages: int = _WEEKLY_ACTIVITY_MAX_PAGES,
) -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []

    for page in range(1, max_pages + 1):
        payload = _strava_api_request(
            "GET",
            "/athlete/activities",
            access_token,
            params={
                "after": after_epoch,
                "before": before_epoch,
                "page": page,
                "per_page": per_page,
            },
        )

        if not isinstance(payload, list) or not payload:
            break

        page_activities = [item for item in payload if isinstance(item, dict)]
        activities.extend(page_activities)

        if len(payload) < per_page:
            break

    return activities


def _filter_cycling_activities(
    activities: list[dict[str, Any]],
    sport_types: set[str],
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for activity in activities:
        sport_type = str(activity.get("sport_type") or activity.get("type") or "").strip()
        if sport_type in sport_types:
            filtered.append(activity)
    return filtered


def _build_delta_payload(current: float, previous: float, decimals: int = 2) -> dict[str, float | None]:
    delta = current - previous
    delta_pct = None
    if previous > 0:
        delta_pct = round((delta / previous) * 100, 1)

    return {
        "current": round(current, decimals),
        "previous": round(previous, decimals),
        "delta": round(delta, decimals),
        "delta_pct": delta_pct,
    }


def _estimate_tss(activities: list[dict[str, Any]], ftp: int | None) -> float | None:
    if ftp is None or ftp <= 0:
        return None

    tss = 0.0
    has_supported_activity = False
    for activity in activities:
        moving_time = _to_int(activity.get("moving_time"), 0)
        weighted_power = _to_optional_float(activity.get("weighted_average_watts"))
        if moving_time <= 0 or weighted_power is None or weighted_power <= 0:
            continue

        has_supported_activity = True
        intensity_factor = weighted_power / float(ftp)
        tss += (moving_time / 3600.0) * (intensity_factor ** 2) * 100.0

    if not has_supported_activity:
        return None

    return round(tss, 1)


def _aggregate_weekly_metrics(activities: list[dict[str, Any]]) -> dict[str, Any]:
    total_distance_m = 0.0
    total_moving_time_s = 0
    total_elapsed_time_s = 0
    total_elevation_gain_m = 0.0
    total_kilojoules = 0.0
    total_suffer_score = 0.0
    total_pr_count = 0
    total_achievement_count = 0

    power_weighted_sum = 0.0
    power_weighted_time_s = 0
    weighted_power_sum = 0.0
    weighted_power_time_s = 0
    heartrate_weighted_sum = 0.0
    heartrate_weighted_time_s = 0
    cadence_weighted_sum = 0.0
    cadence_weighted_time_s = 0

    max_heartrate_bpm = 0.0
    max_watts = 0
    trainer_count = 0
    commute_count = 0

    activities_with_power = 0
    activities_with_device_watts = 0
    activities_with_heartrate = 0
    activities_with_cadence = 0

    daily_rollup: dict[str, dict[str, Any]] = {}
    normalized_activities: list[dict[str, Any]] = []
    longest_ride: dict[str, Any] | None = None

    for activity in activities:
        activity_id = _to_int(activity.get("id"), 0)
        name = str(activity.get("name") or "Actividad")
        sport_type = str(activity.get("sport_type") or activity.get("type") or "Ride")
        start_date_local = str(activity.get("start_date_local") or activity.get("start_date") or "")
        activity_day = start_date_local.split("T", 1)[0] if "T" in start_date_local else start_date_local

        distance_m = _to_float(activity.get("distance"), 0.0)
        moving_time_s = _to_int(activity.get("moving_time"), 0)
        elapsed_time_s = _to_int(activity.get("elapsed_time"), 0)
        elevation_gain_m = _to_float(activity.get("total_elevation_gain"), 0.0)
        kilojoules = _to_float(activity.get("kilojoules"), 0.0)
        suffer_score = _to_float(activity.get("suffer_score"), 0.0)
        pr_count = _to_int(activity.get("pr_count"), 0)
        achievement_count = _to_int(activity.get("achievement_count"), 0)

        avg_speed_mps = _to_optional_float(activity.get("average_speed"))
        avg_power_w = _to_optional_float(activity.get("average_watts"))
        weighted_power_w = _to_optional_float(activity.get("weighted_average_watts"))
        avg_heartrate_bpm = _to_optional_float(activity.get("average_heartrate"))
        max_heartrate = _to_optional_float(activity.get("max_heartrate"))
        avg_cadence_rpm = _to_optional_float(activity.get("average_cadence"))
        max_watts_activity = _to_optional_int(activity.get("max_watts"))

        trainer = bool(activity.get("trainer"))
        commute = bool(activity.get("commute"))
        has_heartrate = bool(activity.get("has_heartrate")) or avg_heartrate_bpm is not None
        device_watts = bool(activity.get("device_watts"))

        total_distance_m += distance_m
        total_moving_time_s += moving_time_s
        total_elapsed_time_s += elapsed_time_s
        total_elevation_gain_m += elevation_gain_m
        total_kilojoules += kilojoules
        total_suffer_score += suffer_score
        total_pr_count += pr_count
        total_achievement_count += achievement_count

        if trainer:
            trainer_count += 1
        if commute:
            commute_count += 1

        if avg_power_w is not None and moving_time_s > 0:
            activities_with_power += 1
            power_weighted_sum += avg_power_w * moving_time_s
            power_weighted_time_s += moving_time_s

        if weighted_power_w is not None and moving_time_s > 0:
            weighted_power_sum += weighted_power_w * moving_time_s
            weighted_power_time_s += moving_time_s

        if device_watts:
            activities_with_device_watts += 1

        if has_heartrate:
            activities_with_heartrate += 1
            if avg_heartrate_bpm is not None and moving_time_s > 0:
                heartrate_weighted_sum += avg_heartrate_bpm * moving_time_s
                heartrate_weighted_time_s += moving_time_s

        if avg_cadence_rpm is not None:
            activities_with_cadence += 1
            if moving_time_s > 0:
                cadence_weighted_sum += avg_cadence_rpm * moving_time_s
                cadence_weighted_time_s += moving_time_s

        if max_heartrate is not None:
            max_heartrate_bpm = max(max_heartrate_bpm, max_heartrate)

        if max_watts_activity is not None:
            max_watts = max(max_watts, max_watts_activity)

        if longest_ride is None or distance_m > _to_float(longest_ride.get("distance_m"), 0.0):
            longest_ride = {
                "id": activity_id,
                "name": name,
                "distance_m": round(distance_m, 1),
                "distance_km": round(distance_m / 1000.0, 2),
                "start_date_local": start_date_local,
            }

        if activity_day:
            day_entry = daily_rollup.setdefault(
                activity_day,
                {
                    "date": activity_day,
                    "activities": 0,
                    "distance_m": 0.0,
                    "moving_time_s": 0,
                    "elapsed_time_s": 0,
                    "elevation_gain_m": 0.0,
                    "kilojoules": 0.0,
                },
            )
            day_entry["activities"] += 1
            day_entry["distance_m"] += distance_m
            day_entry["moving_time_s"] += moving_time_s
            day_entry["elapsed_time_s"] += elapsed_time_s
            day_entry["elevation_gain_m"] += elevation_gain_m
            day_entry["kilojoules"] += kilojoules

        normalized_activities.append(
            {
                "id": activity_id,
                "name": name,
                "sport_type": sport_type,
                "start_date_local": start_date_local,
                "distance_m": round(distance_m, 1),
                "distance_km": round(distance_m / 1000.0, 2),
                "moving_time_s": moving_time_s,
                "moving_time_h": round(moving_time_s / 3600.0, 2),
                "elapsed_time_s": elapsed_time_s,
                "elevation_gain_m": round(elevation_gain_m, 1),
                "avg_speed_kmh": round(avg_speed_mps * 3.6, 2) if avg_speed_mps is not None else None,
                "avg_power_w": round(avg_power_w, 1) if avg_power_w is not None else None,
                "weighted_power_w": round(weighted_power_w, 1) if weighted_power_w is not None else None,
                "avg_heartrate_bpm": round(avg_heartrate_bpm, 1) if avg_heartrate_bpm is not None else None,
                "max_heartrate_bpm": round(max_heartrate, 1) if max_heartrate is not None else None,
                "avg_cadence_rpm": round(avg_cadence_rpm, 1) if avg_cadence_rpm is not None else None,
                "kilojoules": round(kilojoules, 1),
                "suffer_score": round(suffer_score, 1),
                "pr_count": pr_count,
                "achievement_count": achievement_count,
                "trainer": trainer,
                "commute": commute,
                "has_heartrate": has_heartrate,
                "device_watts": device_watts,
            }
        )

    activity_count = len(activities)
    avg_speed_mps = (total_distance_m / total_moving_time_s) if total_moving_time_s > 0 else 0.0
    avg_power_w = (power_weighted_sum / power_weighted_time_s) if power_weighted_time_s > 0 else 0.0
    weighted_avg_power_w = (
        (weighted_power_sum / weighted_power_time_s)
        if weighted_power_time_s > 0
        else 0.0
    )
    avg_heartrate_bpm = (
        (heartrate_weighted_sum / heartrate_weighted_time_s)
        if heartrate_weighted_time_s > 0
        else 0.0
    )
    avg_cadence_rpm = (
        (cadence_weighted_sum / cadence_weighted_time_s)
        if cadence_weighted_time_s > 0
        else 0.0
    )

    sorted_daily = [
        {
            "date": day["date"],
            "activities": day["activities"],
            "distance_m": round(day["distance_m"], 1),
            "distance_km": round(day["distance_m"] / 1000.0, 2),
            "moving_time_s": day["moving_time_s"],
            "moving_time_h": round(day["moving_time_s"] / 3600.0, 2),
            "elapsed_time_s": day["elapsed_time_s"],
            "elevation_gain_m": round(day["elevation_gain_m"], 1),
            "kilojoules": round(day["kilojoules"], 1),
        }
        for day in sorted(daily_rollup.values(), key=lambda item: item["date"])
    ]

    sorted_activities = sorted(
        normalized_activities,
        key=lambda activity: activity.get("start_date_local") or "",
        reverse=True,
    )

    summary = {
        "total_activities": activity_count,
        "active_days": len(sorted_daily),
        "total_distance_m": round(total_distance_m, 1),
        "total_distance_km": round(total_distance_m / 1000.0, 2),
        "total_moving_time_s": total_moving_time_s,
        "total_moving_time_h": round(total_moving_time_s / 3600.0, 2),
        "total_elapsed_time_s": total_elapsed_time_s,
        "total_elapsed_time_h": round(total_elapsed_time_s / 3600.0, 2),
        "total_elevation_gain_m": round(total_elevation_gain_m, 1),
        "total_kilojoules": round(total_kilojoules, 1),
        "total_suffer_score": round(total_suffer_score, 1),
        "total_pr_count": total_pr_count,
        "total_achievement_count": total_achievement_count,
        "avg_speed_mps": round(avg_speed_mps, 3),
        "avg_speed_kmh": round(avg_speed_mps * 3.6, 2),
        "avg_power_w": round(avg_power_w, 1) if avg_power_w > 0 else None,
        "weighted_avg_power_w": round(weighted_avg_power_w, 1) if weighted_avg_power_w > 0 else None,
        "avg_heartrate_bpm": round(avg_heartrate_bpm, 1) if avg_heartrate_bpm > 0 else None,
        "max_heartrate_bpm": round(max_heartrate_bpm, 1) if max_heartrate_bpm > 0 else None,
        "avg_cadence_rpm": round(avg_cadence_rpm, 1) if avg_cadence_rpm > 0 else None,
        "max_watts": max_watts if max_watts > 0 else None,
        "trainer_ratio": round((trainer_count / activity_count) * 100.0, 1) if activity_count > 0 else 0.0,
        "commute_ratio": round((commute_count / activity_count) * 100.0, 1) if activity_count > 0 else 0.0,
        "power_data_coverage_pct": (
            round((activities_with_power / activity_count) * 100.0, 1)
            if activity_count > 0
            else 0.0
        ),
        "heartrate_data_coverage_pct": (
            round((activities_with_heartrate / activity_count) * 100.0, 1)
            if activity_count > 0
            else 0.0
        ),
        "cadence_data_coverage_pct": (
            round((activities_with_cadence / activity_count) * 100.0, 1)
            if activity_count > 0
            else 0.0
        ),
        "device_watts_coverage_pct": (
            round((activities_with_device_watts / activity_count) * 100.0, 1)
            if activity_count > 0
            else 0.0
        ),
        "longest_ride": longest_ride,
    }

    return {
        "summary": summary,
        "daily": sorted_daily,
        "activities": sorted_activities,
    }


def _aggregate_activity_zones(
    access_token: str,
    activities: list[dict[str, Any]],
    *,
    sample_limit: int,
    total_moving_time_s: int,
) -> dict[str, Any]:
    if not activities:
        return {
            "available": False,
            "reason": "No hay actividades de ciclismo para calcular zonas.",
            "sampled_activities": 0,
            "distribution": {},
        }

    candidates = sorted(
        activities,
        key=lambda activity: _to_int(activity.get("moving_time"), 0),
        reverse=True,
    )[:sample_limit]

    zone_totals: dict[str, dict[tuple[int, int], int]] = {
        "power": {},
        "heartrate": {},
    }
    sampled = 0

    for activity in candidates:
        activity_id = _to_int(activity.get("id"), 0)
        if activity_id <= 0:
            continue

        try:
            zone_payload = _strava_api_request(
                "GET",
                f"/activities/{activity_id}/zones",
                access_token,
            )
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 401:
                raise
            if status_code == 403:
                return {
                    "available": False,
                    "reason": "Strava no habilito zonas por actividad para este atleta (Summit o scope insuficiente).",
                    "sampled_activities": sampled,
                    "distribution": {},
                }
            continue

        if not isinstance(zone_payload, list):
            continue

        sampled += 1
        for zone in zone_payload:
            if not isinstance(zone, dict):
                continue
            zone_type = str(zone.get("type") or "").strip().lower()
            if zone_type not in zone_totals:
                continue

            for bucket in zone.get("distribution_buckets", []):
                if not isinstance(bucket, dict):
                    continue
                bucket_min = _to_int(bucket.get("min"), 0)
                bucket_max = _to_int(bucket.get("max"), -1)
                seconds = _to_int(bucket.get("time"), 0)
                if seconds <= 0:
                    continue

                key = (bucket_min, bucket_max)
                zone_totals[zone_type][key] = zone_totals[zone_type].get(key, 0) + seconds

    if sampled == 0:
        return {
            "available": False,
            "reason": "No se pudieron calcular zonas con los datos disponibles.",
            "sampled_activities": 0,
            "distribution": {},
        }

    distribution: dict[str, list[dict[str, Any]]] = {}
    for zone_type, buckets in zone_totals.items():
        sorted_buckets = sorted(buckets.items(), key=lambda item: item[0][0])
        distribution[zone_type] = [
            {
                "min": zone_range[0],
                "max": zone_range[1],
                "time_s": seconds,
                "pct_of_week_moving_time": (
                    round((seconds / total_moving_time_s) * 100.0, 1)
                    if total_moving_time_s > 0
                    else 0.0
                ),
            }
            for zone_range, seconds in sorted_buckets
        ]

    return {
        "available": True,
        "sampled_activities": sampled,
        "distribution": distribution,
    }


def _prune_expired_oauth_states() -> None:
    now = time.time()
    with _strava_oauth_lock:
        expired_states = [
            state
            for state, created_at in _strava_oauth_state_cache.items()
            if now - created_at > _STRAVA_STATE_TTL_SECONDS
        ]
        for state in expired_states:
            _strava_oauth_state_cache.pop(state, None)


def _build_strava_auth_url(client_id: int, redirect_uri: str, scope: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "approval_prompt": "force",
        }
    )
    return f"https://www.strava.com/oauth/authorize?{query}"


def _get_strava_client_id() -> int:
    raw_client_id = (os.environ.get("STRAVA_CLIENT_ID") or "").strip()
    if not raw_client_id:
        raise ValueError("STRAVA_CLIENT_ID is not configured in environment")
    try:
        return int(raw_client_id)
    except ValueError as exc:
        raise ValueError("STRAVA_CLIENT_ID must be a valid integer") from exc


def _get_strava_client_secret() -> str:
    client_secret = (os.environ.get("STRAVA_CLIENT_SECRET") or "").strip()
    if not client_secret:
        raise ValueError("STRAVA_CLIENT_SECRET is not configured in environment")
    return client_secret


def _exchange_strava_code(client_id: int, client_secret: str, code: str, redirect_uri: str) -> dict:
    response = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _refresh_strava_token(client_id: int, client_secret: str, refresh_token: str) -> dict:
    response = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


@app.get("/health")
def health() -> tuple[dict, int]:
    return {"status": "ok"}, 200

# ================================
# VECTOR STORES MANAGEMENT
# ================================

@app.get("/vector_stores")
def list_vector_stores():
    """List available vector stores in the OpenAI account."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return jsonify({"error": "OPENAI_API_KEY is not configured in environment"}), 500

    client = OpenAI(api_key=api_key)

    def _as_plain_dict(value):
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "to_dict"):
            return value.to_dict()
        try:
            return dict(value)
        except Exception:
            return {
                "in_progress": getattr(value, "in_progress", None),
                "completed": getattr(value, "completed", None),
                "failed": getattr(value, "failed", None),
                "cancelled": getattr(value, "cancelled", None),
                "total": getattr(value, "total", None),
            }

    try:
        vector_stores = client.vector_stores.list()
        data_list = []
        for vs in vector_stores.data:
            data_list.append({
                "id": vs.id,
                "name": getattr(vs, "name", "Unnamed Vector Store"),
                "status": getattr(vs, "status", "unknown"),
                "created_at": getattr(vs, "created_at", None),
                "file_counts": _as_plain_dict(getattr(vs, "file_counts", {}))
            })
        return jsonify({"data": data_list})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/vector_stores")
def create_vector_store():
    """Create a new vector store."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "New Vector Store")
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    try:
        vs = client.vector_stores.create(name=name)
        return jsonify({
            "id": vs.id,
            "name": vs.name,
            "status": vs.status,
            "created_at": vs.created_at
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.delete("/vector_stores")
def delete_vector_store():
    """Delete a vector store."""
    vs_id = request.args.get("vector_store_id")
    if not vs_id:
        return jsonify({"error": "Missing vector_store_id parameter"}), 400

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    try:
        deleted_vs = client.vector_stores.delete(vector_store_id=vs_id)
        return jsonify({
            "id": deleted_vs.id,
            "deleted": deleted_vs.deleted
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================================
# END VECTOR STORES MANAGEMENT
# ================================

@app.post("/add_to_vs")
def add_to_vs():
    """Accepts multipart/form-data with a file field named 'file'. Optional form field
    'vector_store_id' can override the env VAR VECTOR_STORE_ID. Returns JSON status.
    """
    if "file" not in request.files:
        return {"error": "No file part in request (expected field 'file')."}, 400

    f = request.files.get("file")
    if f.filename == "":
        return {"error": "Empty filename."}, 400

    vs_id = request.form.get("vector_store_id") or os.environ.get("VECTOR_STORE_ID")

    # Save to a temporary file
    tmp = None
    try:
        filename = secure_filename(f.filename)
        suffix = os.path.splitext(filename)[1]
        print(f"Saving uploaded file to temp file with suffix {suffix}")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        f.save(tmp.name)
        tmp.close()

        result = vector_store.attach(tmp.name, vector_store_id=vs_id)
        return jsonify({"status": "ok", "file": filename, "vector_store_id": vs_id, "result": str(result)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            if tmp is not None:
                os.unlink(tmp.name)
        except Exception:
            pass


def _parse_chat_request(
    data: dict,
) -> tuple[
    str | None,
    str | None,
    str | None,
    bool,
    str | None,
    str | None,
    int | None,
    str,
    str,
]:
    question = data.get("message") or data.get("question")
    model = data.get("model")
    llm_param = (data.get("llm_provider") or data.get("llm") or os.environ.get("LLM_PROVIDER") or os.environ.get("LLM") or "")
    stream_param = data.get("stream", False)
    strava_access_token = data.get("strava_access_token")
    strava_athlete_id = data.get("strava_athlete_id")
    response_format = data.get("response_format")
    planner_mode = data.get("planner_mode")

    if isinstance(stream_param, str):
        stream = stream_param.lower() in ("true", "1", "yes")
    else:
        stream = bool(stream_param)

    if isinstance(llm_param, str) and "/" in llm_param:
        parsed_provider, model_from_llm = llm_param.split("/", 1)
        llm_provider = parsed_provider
        if model_from_llm and not model:
            model = model_from_llm.strip()
    else:
        llm_provider = llm_param

    model_name = model.strip() if isinstance(model, str) and model.strip() else None
    vector_store_id = data.get("vector_store_id")
    athlete_id: int | None = None
    if strava_athlete_id is not None:
        try:
            athlete_id = int(strava_athlete_id)
        except (TypeError, ValueError):
            athlete_id = None

    normalized_access_token = (
        strava_access_token.strip()
        if isinstance(strava_access_token, str) and strava_access_token.strip()
        else None
    )

    if isinstance(response_format, str) and response_format.strip():
        normalized_response_format = response_format.strip().lower()
    else:
        normalized_response_format = "plan_react_v1"

    if normalized_response_format not in {"plan_react_v1", "structured", "plain"}:
        normalized_response_format = "plan_react_v1"

    if isinstance(planner_mode, str) and planner_mode.strip():
        normalized_planner_mode = planner_mode.strip().lower()
    else:
        normalized_planner_mode = "full_only"

    if normalized_planner_mode not in {"off", "full_only", "always"}:
        normalized_planner_mode = "full_only"

    return (
        question,
        llm_provider,
        model_name,
        stream,
        vector_store_id,
        normalized_access_token,
        athlete_id,
        normalized_response_format,
        normalized_planner_mode,
    )


def _normalize_chat_result(result: object) -> dict[str, object]:
    if isinstance(result, dict):
        response_text = result.get("response")
        if response_text is None:
            response_text = result.get("message")
        if response_text is None:
            response_text = result.get("answer")

        tool_calls = result.get("tool_calls")
        if not isinstance(tool_calls, list):
            tool_calls = []

        normalized_result: dict[str, object] = {
            "response": str(response_text) if response_text is not None else "",
            "tool_calls": tool_calls,
        }

        structured = result.get("structured")
        if isinstance(structured, dict):
            normalized_result["structured"] = structured

        api_version = result.get("api_version")
        if isinstance(api_version, str) and api_version.strip():
            normalized_result["api_version"] = api_version.strip()

        return normalized_result

    return {
        "response": str(result),
        "tool_calls": [],
    }


@app.post("/chat")
@app.post("/ask")
def chat_agent() -> Response | tuple[dict, int]:
    data = request.get_json(silent=True) or {}
    (
        question,
        llm_provider,
        model_name_to_use,
        stream,
        vs_id,
        strava_access_token,
        strava_athlete_id,
        response_format,
        planner_mode,
    ) = _parse_chat_request(data)

    print(
        "Received chat request with question: "
        f"{question}, llm_provider: {llm_provider}, model: {model_name_to_use}, stream: {stream}"
    )

    if not isinstance(question, str) or not question.strip():
        return {"error": "Field 'message' or 'question' must be a non-empty string."}, 400

    if not isinstance(llm_provider, str) or not llm_provider.strip():
        return {"error": "Field 'llm_provider' must be a non-empty string (e.g. 'openai/gpt-4o')."}, 400

    try:
        rag_info = vector_store.search_vs(question.strip(), vector_store_id=vs_id)
        rag_context = rag_info.get("context", "")
        rag_filenames = rag_info.get("filenames", [])
        
        augmented_question = question.strip()

        if strava_access_token:
            athlete_hint = (
                f"ID del atleta autenticado: {strava_athlete_id}.\n"
                if strava_athlete_id is not None
                else ""
            )
            auth_context = (
                "Contexto de autenticacion Strava para esta sesion (no pedir OAuth al usuario):\n"
                "- access_token disponible y vigente para esta consulta.\n"
                f"- access_token: {strava_access_token}\n"
                f"{athlete_hint}"
                "- Usa este token cuando llames herramientas Strava que requieran access_token.\n"
                "- No reveles ni repitas el token en la respuesta final al usuario.\n\n"
            )
            augmented_question = f"{auth_context}{augmented_question}"
        
        if rag_context:
            augmented_question = (
                f"Contexto del Vector Store con información relevante para responder:\n"
                f"### COMIENZO DEL CONTEXTO ###\n"
                f"{rag_context}\n"
                f"### FIN DEL CONTEXTO ###\n\n"
                f"Pregunta del usuario: {question.strip()}\n"
                f"Responde basándote en el contexto anterior. Si no está la información en el contexto o en tu base de conocimientos general sobre Strava y entrenamiento, indícalo educadamente."
            )

        orchestrator = build_orchestrator(
            llm_provider=llm_provider.strip().lower(),
            model_name=model_name_to_use,
            planner_mode=planner_mode,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if stream:
        run_streaming_with_format = lambda current_question, current_agent: run_agent_streaming(
            current_question,
            current_agent,
            response_format=response_format,
        )
        return Response(
            stream_with_context(
                _rag_stream_generator(
                    rag_filenames,
                    augmented_question,
                    orchestrator,
                    run_streaming_with_format,
                )
            ),
            content_type="text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
            },
        )

    result = _normalize_chat_result(
        asyncio.run(
            run_agent(
                augmented_question,
                orchestrator,
                response_format=response_format,
            )
        )
    )

    if rag_filenames and isinstance(result, dict):
        result["rag_files"] = rag_filenames

    return jsonify(result)


@app.get("/auth/strava/start")
def start_strava_auth() -> tuple[dict, int]:
    """Genera la URL de autorizacion de Strava para flujo OAuth en frontend."""
    redirect_uri = (request.args.get("redirect_uri") or "").strip()
    scope = (request.args.get("scope") or "read,activity:read_all,profile:read_all").strip()

    if not redirect_uri:
        return {"error": "Query param 'redirect_uri' is required."}, 400

    try:
        client_id = _get_strava_client_id()
    except ValueError as exc:
        return {"error": str(exc)}, 500

    _prune_expired_oauth_states()
    state = os.urandom(24).hex()
    with _strava_oauth_lock:
        _strava_oauth_state_cache[state] = time.time()

    auth_url = _build_strava_auth_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
    )
    return {
        "auth_url": auth_url,
        "state": state,
        "scope": scope,
        "redirect_uri": redirect_uri,
    }, 200


@app.post("/auth/strava/exchange")
def exchange_strava_auth_code() -> tuple[dict, int]:
    """Intercambia code por tokens de Strava validando state anti-CSRF."""
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    state = (data.get("state") or "").strip()
    redirect_uri = (data.get("redirect_uri") or "").strip()

    if not code:
        return {"error": "Field 'code' is required."}, 400
    if not state:
        return {"error": "Field 'state' is required."}, 400
    if not redirect_uri:
        return {"error": "Field 'redirect_uri' is required."}, 400

    _prune_expired_oauth_states()
    with _strava_oauth_lock:
        state_created_at = _strava_oauth_state_cache.pop(state, None)

    if state_created_at is None:
        return {"error": "Invalid or expired OAuth state. Please login again."}, 400

    try:
        client_id = _get_strava_client_id()
        client_secret = _get_strava_client_secret()
        token_data = _exchange_strava_code(
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=redirect_uri,
        )
    except requests.HTTPError as exc:
        error_payload = ""
        try:
            error_payload = exc.response.text
        except Exception:
            pass
        return {
            "error": "Strava token exchange failed.",
            "details": error_payload or str(exc),
        }, 400
    except ValueError as exc:
        return {"error": str(exc)}, 500
    except Exception as exc:
        return {"error": str(exc)}, 500

    return {
        "token_type": token_data.get("token_type"),
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": token_data.get("expires_at"),
        "expires_in": token_data.get("expires_in"),
        "scope": token_data.get("scope"),
        "athlete": token_data.get("athlete") or {},
    }, 200


@app.post("/auth/strava/refresh")
def refresh_strava_auth_token() -> tuple[dict, int]:
    """Refresca access token de Strava usando refresh_token rotativo."""
    data = request.get_json(silent=True) or {}
    refresh_token = (data.get("refresh_token") or "").strip()

    if not refresh_token:
        return {"error": "Field 'refresh_token' is required."}, 400

    try:
        client_id = _get_strava_client_id()
        client_secret = _get_strava_client_secret()
        token_data = _refresh_strava_token(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )
    except requests.HTTPError as exc:
        error_payload = ""
        try:
            error_payload = exc.response.text
        except Exception:
            pass
        return {
            "error": "Strava token refresh failed.",
            "details": error_payload or str(exc),
        }, 400
    except ValueError as exc:
        return {"error": str(exc)}, 500
    except Exception as exc:
        return {"error": str(exc)}, 500

    return {
        "token_type": token_data.get("token_type"),
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": token_data.get("expires_at"),
        "expires_in": token_data.get("expires_in"),
        "scope": token_data.get("scope"),
        "athlete": token_data.get("athlete") or {},
    }, 200


@app.post("/strava/weekly-summary")
def get_strava_weekly_summary() -> tuple[dict, int]:
    """Construye un resumen semanal de KPIs de ciclismo para el dashboard inicial."""
    data = request.get_json(silent=True) or {}
    access_token = _extract_strava_access_token(data)
    if not access_token:
        return {
            "error": "Falta strava_access_token en el body o Authorization: Bearer en headers."
        }, 400

    days = _to_int(data.get("days"), _DEFAULT_WEEKLY_DAYS)
    days = max(1, min(days, _MAX_WEEKLY_DAYS))

    end_date_raw = data.get("end_date")
    if end_date_raw is not None and not isinstance(end_date_raw, str):
        return {"error": "Field 'end_date' must be a string in YYYY-MM-DD format."}, 400

    try:
        start_date, end_date, start_epoch, end_epoch_exclusive = _resolve_week_window(
            days=days,
            end_date_raw=end_date_raw.strip() if isinstance(end_date_raw, str) else None,
        )
    except ValueError:
        return {"error": "Field 'end_date' must use YYYY-MM-DD format."}, 400

    requested_sport_types = data.get("sport_types")
    if isinstance(requested_sport_types, list):
        normalized_sport_types = {
            str(sport_type).strip()
            for sport_type in requested_sport_types
            if str(sport_type).strip()
        }
        sport_types = normalized_sport_types or set(_DEFAULT_CYCLING_SPORT_TYPES)
    else:
        sport_types = set(_DEFAULT_CYCLING_SPORT_TYPES)

    include_activity_zones = _coerce_bool(data.get("include_activity_zones"), default=True)
    zone_sample_limit = _to_int(data.get("zone_sample_limit"), _DEFAULT_ZONE_SAMPLE_LIMIT)
    zone_sample_limit = max(1, min(zone_sample_limit, _MAX_ZONE_SAMPLE_LIMIT))

    athlete_id_hint = _to_optional_int(data.get("strava_athlete_id"))

    try:
        athlete_profile = _strava_api_request("GET", "/athlete", access_token)
        if not isinstance(athlete_profile, dict):
            athlete_profile = {}

        athlete_id = athlete_id_hint or _to_optional_int(athlete_profile.get("id"))

        week_activities = _fetch_activities_for_window(
            access_token,
            after_epoch=start_epoch,
            before_epoch=end_epoch_exclusive,
        )
        week_cycling_activities = _filter_cycling_activities(week_activities, sport_types)
        week_metrics = _aggregate_weekly_metrics(week_cycling_activities)

        previous_start_date = start_date - timedelta(days=days)
        previous_start_epoch = int(
            datetime.combine(previous_start_date, dt_time.min, tzinfo=timezone.utc).timestamp()
        )
        previous_activities = _fetch_activities_for_window(
            access_token,
            after_epoch=previous_start_epoch,
            before_epoch=start_epoch,
        )
        previous_cycling_activities = _filter_cycling_activities(previous_activities, sport_types)
        previous_metrics = _aggregate_weekly_metrics(previous_cycling_activities)

        week_summary = week_metrics["summary"]
        previous_summary = previous_metrics["summary"]

        ftp = _to_optional_int(athlete_profile.get("ftp"))
        weighted_avg_power_w = _to_optional_float(week_summary.get("weighted_avg_power_w"))
        estimated_if = None
        if ftp is not None and ftp > 0 and weighted_avg_power_w is not None and weighted_avg_power_w > 0:
            estimated_if = round(weighted_avg_power_w / float(ftp), 3)

        zones_payload: dict[str, Any]
        if include_activity_zones:
            zones_payload = _aggregate_activity_zones(
                access_token,
                week_cycling_activities,
                sample_limit=zone_sample_limit,
                total_moving_time_s=_to_int(week_summary.get("total_moving_time_s"), 0),
            )
        else:
            zones_payload = {
                "available": False,
                "reason": "Calculo de zonas desactivado por include_activity_zones=false.",
                "sampled_activities": 0,
                "distribution": {},
            }

        benchmark_payload: dict[str, Any] | None = None
        if athlete_id is not None:
            try:
                athlete_stats = _strava_api_request("GET", f"/athletes/{athlete_id}/stats", access_token)
                if isinstance(athlete_stats, dict):
                    benchmark_payload = {
                        "available": True,
                        "biggest_ride_distance": athlete_stats.get("biggest_ride_distance"),
                        "biggest_climb_elevation_gain": athlete_stats.get("biggest_climb_elevation_gain"),
                        "recent_ride_totals": athlete_stats.get("recent_ride_totals"),
                        "ytd_ride_totals": athlete_stats.get("ytd_ride_totals"),
                        "all_ride_totals": athlete_stats.get("all_ride_totals"),
                    }
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code == 401:
                    raise
                benchmark_payload = {
                    "available": False,
                    "reason": "No fue posible recuperar /athletes/{id}/stats con el token actual.",
                }

        trends_payload = {
            "activities": _build_delta_payload(
                float(_to_int(week_summary.get("total_activities"), 0)),
                float(_to_int(previous_summary.get("total_activities"), 0)),
                decimals=0,
            ),
            "distance_km": _build_delta_payload(
                _to_float(week_summary.get("total_distance_km"), 0.0),
                _to_float(previous_summary.get("total_distance_km"), 0.0),
                decimals=2,
            ),
            "moving_time_h": _build_delta_payload(
                _to_float(week_summary.get("total_moving_time_h"), 0.0),
                _to_float(previous_summary.get("total_moving_time_h"), 0.0),
                decimals=2,
            ),
            "elevation_gain_m": _build_delta_payload(
                _to_float(week_summary.get("total_elevation_gain_m"), 0.0),
                _to_float(previous_summary.get("total_elevation_gain_m"), 0.0),
                decimals=1,
            ),
            "kilojoules": _build_delta_payload(
                _to_float(week_summary.get("total_kilojoules"), 0.0),
                _to_float(previous_summary.get("total_kilojoules"), 0.0),
                decimals=1,
            ),
        }

        return {
            "week": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": days,
                "after_epoch": start_epoch,
                "before_epoch": end_epoch_exclusive,
                "previous_start_date": previous_start_date.isoformat(),
                "previous_end_date": (start_date - timedelta(days=1)).isoformat(),
            },
            "filters": {
                "sport_types": sorted(sport_types),
                "include_activity_zones": include_activity_zones,
                "zone_sample_limit": zone_sample_limit,
            },
            "athlete": {
                "id": athlete_profile.get("id"),
                "firstname": athlete_profile.get("firstname"),
                "lastname": athlete_profile.get("lastname"),
                "measurement_preference": athlete_profile.get("measurement_preference"),
                "ftp": ftp,
                "weight": athlete_profile.get("weight"),
            },
            "summary": week_summary,
            "intensity": {
                "estimated_if": estimated_if,
                "estimated_tss": _estimate_tss(week_cycling_activities, ftp),
            },
            "trends": trends_payload,
            "daily": week_metrics["daily"],
            "activities": week_metrics["activities"],
            "zones": zones_payload,
            "benchmarks": benchmark_payload,
        }, 200
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        details = _extract_http_error_details(exc)
        if status_code in {401, 403}:
            return {
                "error": "Strava rechazo la consulta con el token actual.",
                "details": details,
            }, status_code
        return {
            "error": "Fallo consultando la API de Strava para resumen semanal.",
            "details": details,
        }, 502
    except requests.RequestException as exc:
        return {
            "error": "No fue posible comunicarse con Strava.",
            "details": str(exc),
        }, 502
    except Exception as exc:
        return {
            "error": "Error inesperado construyendo resumen semanal.",
            "details": str(exc),
        }, 500


@app.get("/search_vs")
def search_vs_endpoint(query: str = "all files"):
    """
    Endpoint para listar y buscar archivos en el Vector Store.
    Retorna la estructura de datos compatible con el popover 'Files Context'.
    """
   
    # Realiza la búsqueda para obtener los resultados reales (score, file_id, etc)
    # Por defecto, la función search_vs retorna context y filenames.
    # Necesitamos modificarla o llamar directamente a la lógica de OpenAI para más detalle si se requiere.
    # De momento retornamos los metadatos de búsqueda.
    
    # En un caso real, esto llamaría a vector_stores.search y retornaría el objeto 'data' crudo
    # pero siguiendo el flujo actual del backend:
 
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    vs_id = request.args.get("vector_store_id") or os.environ.get("VECTOR_STORE_ID")
    
    try:
        # 1. Listamos todos los archivos asociados al Vector Store
        vs_files = client.vector_stores.files.list(
            vector_store_id=vs_id
        )
        
        data_list = []
        # 2. Para cada archivo listado, recuperamos sus detalles específicos (como el filename que no viene en list)
        for vs_file in vs_files.data:
            file_details = client.files.retrieve(vs_file.id)
            data_list.append({
                "file_id": vs_file.id,
                "filename": getattr(file_details, "filename", getattr(file_details, "name", None)),
                "status": vs_file.status,
                "created_at": vs_file.created_at,
                "usage_bytes": vs_file.usage_bytes
            })
        return jsonify({"data": data_list})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/vectorize")
async def vectorize_text():
    """
    Endpoint asíncrono para vectorizar un texto usando LangExtract y un provider de vectores.
    Cuerpo esperado (JSON):
    {
      "text": "Texto a procesar",
      "provider": "pinecone" (opcional, default pinecone),
      "model_id": "gpt-4o-mini" (opcional),
      "api_key": "sk-..." (opcional)
    }
    """
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    provider = data.get("provider", "pinecone")
    model_id = data.get("model_id", "gpt-4o-mini")
    api_key = data.get("api_key")

    if not text or not isinstance(text, str):
        return jsonify({"error": "Field 'text' is required and must be a string."}), 400

    try:
        # Ejecutamos la extracción y vectorización en un hilo aparte para no bloquear el loop asíncrono
        # si la librería langextract o el provider no son nativamente asíncronos.
        loop = asyncio.get_event_loop()
        extraction = await loop.run_in_executor(
            None, 
            vector_store.extract_and_vectorize, 
            text, 
            provider, 
            model_id, 
            api_key
        )
        
        return jsonify({
            "status": "ok",
            "provider": provider,
            "extraction": extraction
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/get_vs_file_details")
def get_vs_file_details():
    """
    Endpoint para obtener los detalles de un archivo específico del Vector Store
    utilizando client.vector_stores.files.retrieve.
    """
    file_id = request.args.get("file_id")
    if not file_id:
        return {"error": "Missing file_id parameter"}, 400

    from openai import OpenAI
    import os
    
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    vs_id = request.args.get("vector_store_id") or os.environ.get("VECTOR_STORE_ID")
    
    try:
        # Recupera los detalles específicos del archivo
        file_details = client.vector_stores.files.retrieve(
            vector_store_id=vs_id,
            file_id=file_id
        )
        
        # Retornamos los detalles serializables
        return {
            "id": getattr(file_details, "id", file_id),
            "object": getattr(file_details, "object", "vector_store.file"),
            "created_at": getattr(file_details, "created_at", None),
            "vector_store_id": getattr(file_details, "vector_store_id", vs_id),
            "status": getattr(file_details, "status", "unknown"),
            "last_error": getattr(file_details, "last_error", None),
            # Algunos campos pueden estar anidados o ser objetos de la SDK
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/get_vs_file_content")
def get_vs_file_content():
    """
    Endpoint para descargar el contenido de un archivo del Vector Store.
    Usa client.vector_stores.files.content para obtener el texto.
    """
    file_id = request.args.get("file_id")
    if not file_id:
        return {"error": "Missing file_id parameter"}, 400

    from openai import OpenAI
    import os
    
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    vs_id = request.args.get("vector_store_id") or os.environ.get("VECTOR_STORE_ID")
    
    try:
        # Recupera el contenido del archivo procesado en el Vector Store
        # Note: .content returns a generator/iterator of pages
        content_response = client.vector_stores.files.content(
            vector_store_id=vs_id,
            file_id=file_id
        )
        
        full_text = ""
        # Iteramos sobre las páginas de contenido (vector_store.file.content)
        for page in content_response:
            if hasattr(page, "text") and page.text:
                full_text += page.text
            elif isinstance(page, dict) and "text" in page:
                 full_text += page["text"]
        
        return {
            "file_id": file_id,
            "content": full_text
        }
    except Exception as e:
        return {"error": str(e)}, 500

@app.delete("/delete_vs_file")
def delete_vs_file():
    """
    Endpoint para eliminar un archivo del Vector Store.
    """
    file_id = request.args.get("file_id")
    if not file_id:
        return {"error": "Missing file_id parameter"}, 400

    from openai import OpenAI
    import os
    
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    vs_id = request.args.get("vector_store_id") or os.environ.get("VECTOR_STORE_ID")
    
    try:
        deleted_file = client.vector_stores.files.delete(
            vector_store_id=vs_id,
            file_id=file_id
        )
        return jsonify({
            "status": "deleted",
            "file_id": file_id,
            "details": str(deleted_file)
        })
    except Exception as e:
        return {"error": str(e)}, 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    debug = os.environ.get("FLASK_DEBUG", "").strip() in {"1", "true", "True"}
    app.run(host="0.0.0.0", port=port, debug=debug)