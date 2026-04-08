from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import firebase_admin
    from firebase_admin import firestore as firebase_firestore
except Exception:  # noqa: BLE001
    firebase_admin = None
    firebase_firestore = None

try:
    from google.cloud import storage as gcs_storage
except Exception:  # noqa: BLE001
    gcs_storage = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


class ArtifactStore:
    def __init__(self) -> None:
        self._bucket_name = (
            os.environ.get("GCS_KNOWLEDGE_BUCKET")
            or os.environ.get("STRAVA_KNOWLEDGE_BUCKET")
            or ""
        ).strip()
        self._local_root = Path(
            os.environ.get("LOCAL_KNOWLEDGE_ROOT", ".knowledge_data")
        ).resolve()
        self._local_root.mkdir(parents=True, exist_ok=True)

        self._client: Any | None = None
        self._bucket: Any | None = None

        if self._bucket_name and gcs_storage is not None:
            try:
                self._client = gcs_storage.Client()
                self._bucket = self._client.bucket(self._bucket_name)
            except Exception:  # noqa: BLE001
                self._client = None
                self._bucket = None

    @property
    def mode(self) -> str:
        return "gcs" if self._bucket is not None else "local"

    def _normalize_path(self, relative_path: str) -> str:
        return str(relative_path).strip().replace("\\", "/").lstrip("/")

    def _local_path(self, relative_path: str) -> Path:
        return self._local_root / Path(self._normalize_path(relative_path))

    def write_text(self, relative_path: str, content: str) -> str:
        path = self._normalize_path(relative_path)

        if self._bucket is not None:
            blob = self._bucket.blob(path)
            blob.upload_from_string(content, content_type="text/plain; charset=utf-8")
            return f"gs://{self._bucket_name}/{path}"

        full_path = self._local_path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        return str(full_path)

    def write_json(self, relative_path: str, payload: Any) -> str:
        path = self._normalize_path(relative_path)

        if self._bucket is not None:
            blob = self._bucket.blob(path)
            blob.upload_from_string(
                json.dumps(payload, ensure_ascii=False, indent=2),
                content_type="application/json; charset=utf-8",
            )
            return f"gs://{self._bucket_name}/{path}"

        full_path = self._local_path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(full_path)

    def read_text(self, relative_path: str) -> str | None:
        path = self._normalize_path(relative_path)

        if self._bucket is not None:
            blob = self._bucket.blob(path)
            if not blob.exists():
                return None
            return blob.download_as_text(encoding="utf-8")

        full_path = self._local_path(path)
        if not full_path.exists():
            return None
        return full_path.read_text(encoding="utf-8")

    def read_json(self, relative_path: str) -> Any | None:
        text = self.read_text(relative_path)
        if text is None:
            return None

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def exists(self, relative_path: str) -> bool:
        path = self._normalize_path(relative_path)

        if self._bucket is not None:
            blob = self._bucket.blob(path)
            return bool(blob.exists())

        return self._local_path(path).exists()

    def list_paths(self, prefix: str, suffix: str = "") -> list[str]:
        normalized_prefix = self._normalize_path(prefix)
        normalized_suffix = suffix.strip()
        paths: list[str] = []

        if self._bucket is not None:
            for blob in self._client.list_blobs(self._bucket_name, prefix=normalized_prefix):
                name = str(blob.name)
                if normalized_suffix and not name.endswith(normalized_suffix):
                    continue
                paths.append(name)
            return sorted(paths)

        root_prefix = self._local_path(normalized_prefix)
        if not root_prefix.exists():
            return []

        if root_prefix.is_file():
            candidate = root_prefix.relative_to(self._local_root).as_posix()
            if not normalized_suffix or candidate.endswith(normalized_suffix):
                return [candidate]
            return []

        for full_path in root_prefix.rglob("*"):
            if not full_path.is_file():
                continue
            candidate = full_path.relative_to(self._local_root).as_posix()
            if normalized_suffix and not candidate.endswith(normalized_suffix):
                continue
            paths.append(candidate)

        return sorted(paths)


class AthleteStateStore:
    def __init__(self) -> None:
        self._athletes_collection = os.environ.get("FIRESTORE_ATHLETES_COLLECTION", "athletes")
        self._runs_collection = os.environ.get("FIRESTORE_PIPELINE_RUNS_COLLECTION", "pipeline_runs")
        self._state_path = (
            Path(os.environ.get("LOCAL_KNOWLEDGE_ROOT", ".knowledge_data"))
            .resolve()
            .joinpath("state", "athlete_state.json")
        )
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

        self._client: Any | None = None
        use_firestore = os.environ.get("USE_FIRESTORE_STATE", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        if use_firestore and firebase_firestore is not None and firebase_admin is not None:
            try:
                if not firebase_admin._apps:
                    firebase_admin.initialize_app()
                self._client = firebase_firestore.client()
            except Exception:  # noqa: BLE001
                self._client = None

    @property
    def mode(self) -> str:
        return "firestore" if self._client is not None else "local"

    def _load_local_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"athletes": {}, "pipeline_runs": {}}

        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return {"athletes": {}, "pipeline_runs": {}}
            payload.setdefault("athletes", {})
            payload.setdefault("pipeline_runs", {})
            return payload
        except (json.JSONDecodeError, OSError):
            return {"athletes": {}, "pipeline_runs": {}}

    def _save_local_state(self, payload: dict[str, Any]) -> None:
        self._state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _to_plain(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool, list, dict)):
            return value
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "isoformat"):
            try:
                return value.isoformat()
            except Exception:  # noqa: BLE001
                return str(value)
        return str(value)

    def upsert_tokens(self, athlete_id: int, token_payload: dict[str, Any]) -> None:
        athlete_key = str(athlete_id)
        now_iso = utc_now_iso()

        profile = token_payload.get("athlete")
        if not isinstance(profile, dict):
            profile = {}

        update_payload = {
            "athlete_id": athlete_id,
            "access_token": token_payload.get("access_token"),
            "refresh_token": token_payload.get("refresh_token"),
            "expires_at": token_payload.get("expires_at"),
            "scope": token_payload.get("scope"),
            "profile": profile,
            "token_updated_at": now_iso,
        }

        if self._client is not None:
            self._client.collection(self._athletes_collection).document(athlete_key).set(
                update_payload,
                merge=True,
            )
            return

        state = self._load_local_state()
        athletes = state.setdefault("athletes", {})
        current = athletes.get(athlete_key, {})
        if not isinstance(current, dict):
            current = {}
        current.update(update_payload)
        athletes[athlete_key] = current
        self._save_local_state(state)

    def get_athlete(self, athlete_id: int) -> dict[str, Any] | None:
        athlete_key = str(athlete_id)

        if self._client is not None:
            doc = self._client.collection(self._athletes_collection).document(athlete_key).get()
            if not doc.exists:
                return None
            payload = doc.to_dict() or {}
            if not isinstance(payload, dict):
                return None
            payload["athlete_id"] = _to_int(payload.get("athlete_id"), athlete_id)
            return {key: self._to_plain(value) for key, value in payload.items()}

        athletes = self._load_local_state().get("athletes", {})
        payload = athletes.get(athlete_key)
        if not isinstance(payload, dict):
            return None
        normalized = {key: self._to_plain(value) for key, value in payload.items()}
        normalized["athlete_id"] = _to_int(normalized.get("athlete_id"), athlete_id)
        return normalized

    def list_athletes_with_tokens(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []

        if self._client is not None:
            docs = self._client.collection(self._athletes_collection).stream()
            for doc in docs:
                payload = doc.to_dict() or {}
                if not isinstance(payload, dict):
                    continue
                access_token = payload.get("access_token")
                if not isinstance(access_token, str) or not access_token.strip():
                    continue
                payload = {key: self._to_plain(value) for key, value in payload.items()}
                payload["athlete_id"] = _to_int(payload.get("athlete_id"), _to_int(doc.id))
                records.append(payload)
            return records

        athletes = self._load_local_state().get("athletes", {})
        for key, payload in athletes.items():
            if not isinstance(payload, dict):
                continue
            access_token = payload.get("access_token")
            if not isinstance(access_token, str) or not access_token.strip():
                continue
            normalized = {field: self._to_plain(value) for field, value in payload.items()}
            normalized["athlete_id"] = _to_int(normalized.get("athlete_id"), _to_int(key))
            records.append(normalized)

        return records

    def update_sync_state(
        self,
        athlete_id: int,
        *,
        last_sync_epoch: int,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        athlete_key = str(athlete_id)
        payload = {
            "last_sync_epoch": int(last_sync_epoch),
            "last_sync_status": status,
            "last_sync_details": details or {},
            "last_sync_at": utc_now_iso(),
        }

        if self._client is not None:
            self._client.collection(self._athletes_collection).document(athlete_key).set(payload, merge=True)
            return

        state = self._load_local_state()
        athletes = state.setdefault("athletes", {})
        current = athletes.get(athlete_key, {})
        if not isinstance(current, dict):
            current = {}
        current.update(payload)
        current.setdefault("athlete_id", athlete_id)
        athletes[athlete_key] = current
        self._save_local_state(state)

    def get_last_sync_epoch(self, athlete_id: int) -> int | None:
        payload = self.get_athlete(athlete_id)
        if not isinstance(payload, dict):
            return None
        raw_value = payload.get("last_sync_epoch")
        if raw_value is None or raw_value == "":
            return None
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return None

    def set_last_indexed_date(self, athlete_id: int, date_value: str) -> None:
        athlete_key = str(athlete_id)
        payload = {
            "last_indexed_date": str(date_value),
            "last_indexed_at": utc_now_iso(),
        }

        if self._client is not None:
            self._client.collection(self._athletes_collection).document(athlete_key).set(payload, merge=True)
            return

        state = self._load_local_state()
        athletes = state.setdefault("athletes", {})
        current = athletes.get(athlete_key, {})
        if not isinstance(current, dict):
            current = {}
        current.update(payload)
        current.setdefault("athlete_id", athlete_id)
        athletes[athlete_key] = current
        self._save_local_state(state)

    def get_last_indexed_date(self, athlete_id: int) -> str | None:
        payload = self.get_athlete(athlete_id)
        if not isinstance(payload, dict):
            return None
        raw_value = payload.get("last_indexed_date")
        if isinstance(raw_value, str) and raw_value.strip():
            return raw_value.strip()
        return None

    def record_pipeline_run(self, run_id: str, payload: dict[str, Any]) -> None:
        normalized_payload = {
            key: self._to_plain(value)
            for key, value in payload.items()
        }
        normalized_payload.setdefault("created_at", utc_now_iso())

        if self._client is not None:
            self._client.collection(self._runs_collection).document(str(run_id)).set(normalized_payload, merge=True)
            return

        state = self._load_local_state()
        runs = state.setdefault("pipeline_runs", {})
        runs[str(run_id)] = normalized_payload
        self._save_local_state(state)
