import os

from dotenv import load_dotenv


def load_environment_from_sources() -> None:
    """Load environment variables from `.env`."""
    load_dotenv()


def get_setting(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def get_secret(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)
