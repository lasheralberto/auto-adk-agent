"""Índice vectorial de páginas de la wiki en Pinecone.

Cada página de la wiki (``wiki/{athlete_id}/{slug}.md``) se indexa como un
único vector usando el embedding model hospedado por Pinecone
``llama-text-embed-v2`` (vía ``pc.inference.embed``). Los vectores se
guardan en Pinecone bajo un namespace por atleta para aislar datos.

Uso típico:

- ``index_page(athlete_id, slug, content)`` tras escribir una página.
- ``retrieve_relevant_pages(athlete_id, query, top_k=3)`` al responder al
  usuario en el chat agent, para recuperar solo las páginas relevantes.

Si Pinecone no está configurado (falta ``PINECONE_API_KEY`` o el SDK no
está disponible), las operaciones de indexado son no-op y la recuperación
devuelve ``None`` para que el caller haga fallback al contenido completo.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

try:
    from pinecone import Pinecone, ServerlessSpec
except Exception:  # noqa: BLE001
    Pinecone = None  # type: ignore[assignment]
    ServerlessSpec = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

_EMBEDDING_MODEL_DEFAULT = "llama-text-embed-v2"
_EMBEDDING_DIMENSION_DEFAULT = 1024
_INDEX_NAME_DEFAULT = "strava-wiki"
_CLOUD_DEFAULT = "aws"
_REGION_DEFAULT = "us-east-1"
_MAX_CHARS_PER_EMBEDDING = 30000


_client_lock = threading.Lock()
_client: Any | None = None
_index: Any | None = None
_index_name: str | None = None


def _embedding_model() -> str:
    return (os.environ.get("WIKI_EMBEDDING_MODEL") or _EMBEDDING_MODEL_DEFAULT).strip()


def _embedding_dimension() -> int:
    raw = os.environ.get("WIKI_EMBEDDING_DIMENSION", "").strip()
    try:
        return int(raw) if raw else _EMBEDDING_DIMENSION_DEFAULT
    except ValueError:
        return _EMBEDDING_DIMENSION_DEFAULT


def _namespace_for(athlete_id: int) -> str:
    return f"ath_{int(athlete_id)}"


def _get_client() -> Any | None:
    """Devuelve el cliente Pinecone inicializado, o ``None`` si no está configurado."""
    global _client

    if Pinecone is None:
        return None

    api_key = (os.environ.get("PINECONE_API_KEY") or "").strip()
    if not api_key:
        return None

    with _client_lock:
        if _client is None:
            try:
                _client = Pinecone(api_key=api_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Pinecone client init failed: %s", exc)
                _client = None
                return None
        return _client


def _get_index() -> Any | None:
    """Devuelve el handle del índice de Pinecone, creándolo si hace falta."""
    global _index, _index_name

    client = _get_client()
    if client is None:
        return None

    desired_name = (os.environ.get("PINECONE_INDEX") or _INDEX_NAME_DEFAULT).strip()

    with _client_lock:
        if _index is not None and _index_name == desired_name:
            return _index

        try:
            existing = {i["name"] for i in client.list_indexes()}
            if desired_name not in existing:
                cloud = (os.environ.get("PINECONE_CLOUD") or _CLOUD_DEFAULT).strip()
                region = (os.environ.get("PINECONE_REGION") or _REGION_DEFAULT).strip()
                client.create_index(
                    name=desired_name,
                    dimension=_embedding_dimension(),
                    metric="cosine",
                    spec=ServerlessSpec(cloud=cloud, region=region),
                )
            _index = client.Index(desired_name)
            _index_name = desired_name
            return _index
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pinecone index init failed: %s", exc)
            _index = None
            _index_name = None
            return None


def _embed_detailed(
    text: str, input_type: str = "passage"
) -> tuple[list[float] | None, str | None]:
    """Calcula el embedding vía Pinecone Inference.

    ``input_type`` debe ser ``"passage"`` al indexar y ``"query"`` al
    recuperar (así lo recomienda ``llama-text-embed-v2``). Devuelve
    ``(vector, error_message)``; si ``vector`` es ``None``,
    ``error_message`` explica por qué.
    """
    if not text or not text.strip():
        return None, "empty_text"

    client = _get_client()
    if client is None:
        return None, "pinecone_client_unavailable"

    trimmed = text[:_MAX_CHARS_PER_EMBEDDING]
    model = _embedding_model()

    try:
        response = client.inference.embed(
            model=model,
            inputs=[trimmed],
            parameters={"input_type": input_type, "truncate": "END"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("embedding call failed (model=%s): %s", model, exc)
        return None, f"{type(exc).__name__}: {exc}"

    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response.get("data")
    if not data:
        return None, "empty_embedding_response"

    first = data[0]
    values = getattr(first, "values", None)
    if values is None and isinstance(first, dict):
        values = first.get("values")

    if not isinstance(values, list) or not values:
        return None, "malformed_embedding"
    try:
        return [float(v) for v in values], None
    except (TypeError, ValueError) as exc:
        return None, f"embedding_cast_failed: {exc}"


def _embed(text: str, input_type: str = "passage") -> list[float] | None:
    vector, _err = _embed_detailed(text, input_type)
    return vector


def _vector_id(athlete_id: int, slug: str) -> str:
    return f"{int(athlete_id)}:{slug}"


def index_page(athlete_id: int, slug: str, content: str) -> bool:
    """Indexa (o re-indexa) una página de la wiki en Pinecone.

    Devuelve ``True`` si el upsert se realizó, ``False`` si Pinecone no está
    configurado o hubo error. El caller no debe fallar si esto devuelve
    ``False``: la wiki sigue guardada en GCS/local.
    """
    index = _get_index()
    if index is None:
        return False

    embedding = _embed(content, input_type="passage")
    if embedding is None:
        return False

    vector_id = _vector_id(athlete_id, slug)
    metadata = {
        "athlete_id": int(athlete_id),
        "slug": slug,
        "preview": content[:500],
    }

    try:
        index.upsert(
            vectors=[{"id": vector_id, "values": embedding, "metadata": metadata}],
            namespace=_namespace_for(athlete_id),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pinecone upsert failed for %s: %s", vector_id, exc)
        return False


def index_pages(athlete_id: int, pages: dict[str, str]) -> int:
    """Indexa varias páginas en batch. Devuelve cuántas se subieron."""
    index = _get_index()
    if index is None:
        return 0

    vectors: list[dict[str, Any]] = []
    for slug, content in pages.items():
        if not content or not content.strip():
            continue
        embedding = _embed(content, input_type="passage")
        if embedding is None:
            continue
        vectors.append(
            {
                "id": _vector_id(athlete_id, slug),
                "values": embedding,
                "metadata": {
                    "athlete_id": int(athlete_id),
                    "slug": slug,
                    "preview": content[:500],
                },
            }
        )

    if not vectors:
        return 0

    try:
        index.upsert(vectors=vectors, namespace=_namespace_for(athlete_id))
        return len(vectors)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pinecone batch upsert failed: %s", exc)
        return 0


def retrieve_relevant_slugs(
    athlete_id: int,
    query: str,
    top_k: int = 3,
) -> list[str] | None:
    """Devuelve los slugs de las top_k páginas más relevantes para la query.

    - ``None``: Pinecone no configurado o error → el caller debe hacer
      fallback al contenido completo.
    - ``[]``: Pinecone operativo pero no hay matches para el atleta.
    - ``[slug, ...]``: slugs ordenados por relevancia.
    """
    index = _get_index()
    if index is None:
        return None

    embedding = _embed(query, input_type="query")
    if embedding is None:
        return None

    try:
        response = index.query(
            vector=embedding,
            top_k=max(1, int(top_k)),
            namespace=_namespace_for(athlete_id),
            include_metadata=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pinecone query failed: %s", exc)
        return None

    matches = response.get("matches") if isinstance(response, dict) else getattr(response, "matches", None)
    if not matches:
        return []

    slugs: list[str] = []
    for match in matches:
        metadata = match.get("metadata") if isinstance(match, dict) else getattr(match, "metadata", None)
        if not isinstance(metadata, dict):
            continue
        slug = metadata.get("slug")
        if isinstance(slug, str) and slug:
            slugs.append(slug)
    return slugs


def delete_athlete_index(athlete_id: int) -> bool:
    """Borra todos los vectores del atleta (útil al resetear su wiki)."""
    index = _get_index()
    if index is None:
        return False
    try:
        index.delete(delete_all=True, namespace=_namespace_for(athlete_id))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pinecone delete_all failed for athlete %s: %s", athlete_id, exc)
        return False


def backfill_athlete(athlete_id: int) -> dict[str, Any]:
    """Re-indexa en Pinecone todas las páginas actuales de la wiki del atleta.

    Lee los ``.md`` existentes bajo ``wiki/{athlete_id}/`` desde
    ``ArtifactStore`` (GCS o local), calcula el embedding de cada una y
    las sube en un único upsert. Excluye páginas que empiezan por ``_``
    (como ``_index`` o ``_log``) ya que no se recuperan en el chat.

    Devuelve un dict detallado con el motivo de cada fallo para que el
    endpoint pueda devolverlo al cliente en vez de un ``status: failed``
    opaco.
    """
    from .storage_backend import ArtifactStore

    result: dict[str, Any] = {
        "athlete_id": int(athlete_id),
        "pages_found": 0,
        "pages_indexed": 0,
        "status": "skipped",
        "embedding_model": _embedding_model(),
        "embedding_dimension": _embedding_dimension(),
        "errors": [],
        "failed_slugs": [],
    }

    if Pinecone is None:
        result["status"] = "failed"
        result["errors"].append("pinecone_sdk_not_installed")
        return result
    if not (os.environ.get("PINECONE_API_KEY") or "").strip():
        result["status"] = "failed"
        result["errors"].append("PINECONE_API_KEY_missing")
        return result

    index = _get_index()
    if index is None:
        result["status"] = "failed"
        result["errors"].append("pinecone_index_init_failed_check_logs")
        return result

    store = ArtifactStore()
    prefix = f"wiki/{int(athlete_id)}/"
    pages: dict[str, str] = {}
    for path in store.list_paths(prefix=prefix, suffix=".md"):
        slug = str(path).split("/")[-1].removesuffix(".md")
        if not slug or slug.startswith("_"):
            continue
        content = store.read_text(path)
        if content and content.strip():
            pages[slug] = content

    result["pages_found"] = len(pages)
    if not pages:
        result["status"] = "skipped"
        return result

    vectors: list[dict[str, Any]] = []
    for slug, content in pages.items():
        embedding, err = _embed_detailed(content, input_type="passage")
        if embedding is None:
            result["failed_slugs"].append(slug)
            result["errors"].append(f"{slug}: embedding_failed ({err})")
            continue
        vectors.append(
            {
                "id": _vector_id(athlete_id, slug),
                "values": embedding,
                "metadata": {
                    "athlete_id": int(athlete_id),
                    "slug": slug,
                    "preview": content[:500],
                },
            }
        )

    if not vectors:
        result["status"] = "failed"
        if not result["errors"]:
            result["errors"].append("no_vectors_generated")
        return result

    try:
        index.upsert(vectors=vectors, namespace=_namespace_for(athlete_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pinecone upsert failed for athlete %s", athlete_id)
        result["status"] = "failed"
        result["errors"].append(f"pinecone_upsert_failed: {type(exc).__name__}: {exc}")
        return result

    result["pages_indexed"] = len(vectors)
    if result["failed_slugs"]:
        result["status"] = "partial_failure"
    else:
        result["status"] = "ok"
    return result
