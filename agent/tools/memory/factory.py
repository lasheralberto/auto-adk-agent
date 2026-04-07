import os
from collections.abc import Mapping
from typing import Any

from agent.tools.memory.interface import IMemoryProvider
from agent.tools.vectors.providers import ProviderFactory
from agent.config.envars import get_secret, get_setting


_provider_instance: IMemoryProvider | None = None


def _read_setting(settings: Any, key: str, default: Any = None) -> Any:
    if settings is None:
        return default

    if isinstance(settings, Mapping):
        return settings.get(key, default)

    return getattr(settings, key, default)


def build_memory_provider(
    settings: Any = None,
    provider_name: str | None = None,
    reset: bool = False,
) -> IMemoryProvider:
    global _provider_instance

    if reset:
        _provider_instance = None

    if _provider_instance is not None:
        return _provider_instance

    selected = (
        provider_name
        or _read_setting(settings, "MEMORY_PROVIDER")
        or get_setting("MEMORY_PROVIDER")
        or "inmemory"
    )
    selected = str(selected).strip().lower()

    try:
        if selected in {"inmemory", "in-memory", "memory", "default"}:
            _provider_instance = ProviderFactory.get_provider("inmemory", {})
            return _provider_instance

        if selected in {"openai", "vector", "vector_store"}:
            cfg = {
                "api_key": _read_setting(settings, "OPENAI_API_KEY") or get_secret("OPENAI_API_KEY"),
                "vector_store_id": _read_setting(settings, "VECTOR_STORE_ID") or get_setting("VECTOR_STORE_ID"),
                "expires_after_days": _read_setting(settings, "MEMORY_EXPIRY_DAYS"),
            }
            _provider_instance = ProviderFactory.get_provider("openai", cfg)
            return _provider_instance

        if selected == "redis":
            cfg = {
                "url": _read_setting(settings, "REDIS_URL") or get_setting("REDIS_URL")
            }
            _provider_instance = ProviderFactory.get_provider("redis", cfg)
            return _provider_instance

        if selected == "sqlite":
            cfg = {
                "db_path": _read_setting(settings, "SQLITE_MEMORY_DB_PATH")
                or get_setting("SQLITE_MEMORY_DB_PATH")
                or ":memory:",
            }
            _provider_instance = ProviderFactory.get_provider("sqlite", cfg)
            return _provider_instance

        raise ValueError(f"Unsupported memory provider: {selected}")
    except Exception:
        _provider_instance = ProviderFactory.get_provider("inmemory", {})
        return _provider_instance
