import os
from datetime import datetime, timezone
from typing import Any, List, Optional

try:
    from pinecone import Pinecone
except Exception:
    Pinecone = None



class PineconeProvider:
    def __init__(
        self,
        api_key: Optional[str] = None,
        index_name: Optional[str] = None,
        namespace: Optional[str] = None,
        embedding_model: Optional[str] = None,
    ) -> None:
        self._api_key = api_key or os.getenv("PINECONE_API_KEY")
        self._index_name = index_name or os.getenv("PINECONE_INDEX_NAME")
        self._namespace = namespace or os.getenv("PINECONE_NAMESPACE")
        self._embedding_model = embedding_model or os.getenv("PINECONE_EMBEDDING_MODEL") 

    def _client(self) -> Pinecone:
        if Pinecone is None:
            raise ImportError(
                "Pinecone SDK is not installed. Install the package 'pinecone-client' (e.g. add to requirements.txt and pip install)."
            )
        return Pinecone(api_key=self._api_key)

    def _get_index(self):
        pc = self._client()
        return pc, pc.Index(self._index_name)

    def _embed(self, texts: List[str]) -> List[List[float]]:
        pc = self._client()
        response = pc.inference.embed(
            model=self._embedding_model,
            inputs=texts,
            parameters={"input_type": "passage", "truncate": "END"},
        )
        return [item.values for item in response.data]

    def _to_text(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        del ttl
        question_text = self._to_text(key)

        if isinstance(value, dict):
            answer_source = value.get("answer_summary") or value.get("final_answer") or value.get("answer")
            answer_text = self._to_text(answer_source)
        else:
            answer_text = self._to_text(value)

        if not question_text or not answer_text:
            raise ValueError("question and answer are required")

        created_at = datetime.now(timezone.utc).isoformat()
        text = f"Question: {question_text}\nAnswer Summary: {answer_text}\nCreated At: {created_at}"

        pc, index = self._get_index()
        embeddings = self._embed([text])

        metadata = {
            "source": "orchestrator-memory",
            "text": text[:1000],
            "last_memory_at": created_at,
        }

        vectors = [
            {"id": key, "values": embeddings[0], "metadata": metadata}
        ]

        index.upsert(vectors=vectors, namespace=self._namespace)

    def get(self, key: str) -> Optional[Any]:
        pc, index = self._get_index()
        try:
            res = index.fetch(ids=[key], namespace=self._namespace)
        except Exception:
            return None

        # The response shape varies; try common keys
        if not res:
            return None
        if getattr(res, "vectors", None):
            vecs = res.vectors
        else:
            vecs = res.get("vectors") if isinstance(res, dict) else None

        if not vecs:
            return None
        return vecs.get(key) if isinstance(vecs, dict) else vecs

    def delete(self, key: str) -> None:
        pc, index = self._get_index()
        try:
            index.delete(ids=[key], namespace=self._namespace)
        except Exception:
            pass

    def search(self, query: str, top_k: int = 5) -> list[Any]:
        query_text = self._to_text(query)
        if not query_text:
            return []

        safe_top_k = top_k if isinstance(top_k, int) and top_k > 0 else 5
        pc, index = self._get_index()
        embeddings = self._embed([query_text])
        q_emb = embeddings[0]

        try:
            # Try common query shapes
            resp = index.query(queries=[q_emb], top_k=safe_top_k, namespace=self._namespace, include_metadata=True)
        except TypeError:
            resp = index.query(vector=q_emb, top_k=safe_top_k, namespace=self._namespace, include_metadata=True)

        results: list[str] = []
        # parse possible response formats
        matches = getattr(resp, "matches", None) or resp.get("matches") if isinstance(resp, dict) else None
        if not matches:
            matches = getattr(resp, "results", None)

        if matches:
            for m in matches:
                md = getattr(m, "metadata", None) or m.get("metadata") if isinstance(m, dict) else None
                if md:
                    text = md.get("text") if isinstance(md, dict) else None
                    if text:
                        results.append(text)

        return results
