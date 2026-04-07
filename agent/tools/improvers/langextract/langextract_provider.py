from __future__ import annotations
from typing import Any, Dict, List, Optional
import langextract as lx
from agent.config.envars import get_secret, get_setting



DEFAULT_PROMPT = """
You are a universal relation extraction agent.
Extract all meaningful entities and relationships using exact spans.
"""


class LangExtractProvider:
    """Provider wrapper for the `langextract` library.

    Usage:
        p = LangExtractProvider(api_key="...", model_id="gpt-4o-mini")
        result = p.extract("Your text here")

    The constructor can accept a default `model_id`, `api_key`, `prompt_description`
    and `examples` (list of lx.data.ExampleData objects).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_id: Optional[str] = None,
        prompt_description: Optional[str] = None,
        examples: Optional[List[Any]] = None,
    ) -> None:
        self._api_key = api_key or get_secret("OPENAI_API_KEY")
        self._model_id = model_id or get_setting("LANGEXTRACT_MODEL_ID")
        self._prompt = prompt_description or DEFAULT_PROMPT
        self._examples = examples or []

    def _ensure_available(self) -> None:
        if lx is None:
            raise ImportError(
                "langextract is not installed. Install with `pip install langextract`."
            )

    def extract(
        self,
        text: str,
        model_id: Optional[str] = None,
        api_key: Optional[str] = None,
        prompt_description: Optional[str] = None,
        examples: Optional[List[Any]] = None,
    ) -> Any:
        """Run extraction over `text` and return the raw langextract result.

        Parameters `model_id` and `api_key` default to the values passed
        to the constructor or environment variables.
        """
        self._ensure_available()

        mid = model_id or self._model_id
        key = api_key or self._api_key
        prompt = prompt_description or self._prompt
        ex = examples if examples is not None else self._examples

        if not mid:
            raise ValueError("model_id is required (constructor or argument)")
        if not key:
            raise ValueError("api_key is required (constructor or argument)")

        # Call langextract
        return lx.extract(
            text_or_documents=text,
            prompt_description=prompt,
            examples=ex,
            model_id=mid,
            api_key=key,
        )

    # Convenience alias to align with other provider interfaces
    def search(self, query: str, model_id: Optional[str] = None, api_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """Run extraction on `query` and return a list of extraction dicts when possible."""
        res = self.extract(text=query, model_id=model_id, api_key=api_key)

        # try to normalize into list of dicts if result provides `extractions` or similar
        if hasattr(res, "extractions"):
            return list(getattr(res, "extractions"))

        if isinstance(res, (list, tuple)):
            return list(res)

        # fallback: return the raw result wrapped
        return [res]
