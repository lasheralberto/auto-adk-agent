from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from strava_agent_sdk.errors import ExternalServiceError, NotFoundError, ValidationError

if TYPE_CHECKING:
    from google.cloud import secretmanager

logger = logging.getLogger(__name__)


class SecretsService:
    """Manage GCP Secret Manager secrets from the SDK.

    Lazily instantiates the GCP client so importing the SDK does not require
    credentials. Reads fall back to os.environ when a secret is missing in
    Secret Manager and ``env_fallback=True`` (default), which keeps local
    development working without extra setup.
    """

    def __init__(
        self,
        project_id: str | None = None,
        *,
        client: "secretmanager.SecretManagerServiceClient | None" = None,
        credentials_path: str | None = None,
        env_fallback: bool = True,
    ) -> None:
        self._project_id = (project_id or os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip() or None
        self._client = client
        self._credentials_path = (credentials_path or "").strip() or None
        self._env_fallback = env_fallback
        self._cache: dict[tuple[str, str], str] = {}
        self._auth_checked = False
        self._auth_info: dict[str, str | bool] | None = None

    @property
    def project_id(self) -> str:
        if not self._project_id:
            raise ValidationError(
                "Secret Manager project_id is not configured. "
                "Pass project_id=... or set GOOGLE_CLOUD_PROJECT."
            )
        return self._project_id

    def _get_client(self) -> "secretmanager.SecretManagerServiceClient":
        if self._client is not None:
            return self._client
        try:
            from google.cloud import secretmanager
        except ImportError as exc:
            raise ExternalServiceError(
                "google-cloud-secret-manager is not installed. "
                "Add it to your dependencies to use SecretsService."
            ) from exc

        try:
            if self._credentials_path:
                self._client = secretmanager.SecretManagerServiceClient.from_service_account_file(
                    self._credentials_path
                )
            else:
                self._client = secretmanager.SecretManagerServiceClient()
        except FileNotFoundError as exc:
            raise ValidationError(
                f"Service account key not found at '{self._credentials_path}'."
            ) from exc
        except Exception as exc:
            raise ExternalServiceError(
                "Could not initialize Secret Manager client. "
                "Make sure Application Default Credentials are configured "
                "(run `gcloud auth application-default login`, set "
                "GOOGLE_APPLICATION_CREDENTIALS, or pass credentials_path=...). "
                f"Underlying error: {exc}"
            ) from exc
        return self._client

    def _ensure_auth(self) -> None:
        """Verify GCP auth lazily on first use and cache the result.

        Subsequent calls are no-ops. If verification fails, the flag is not
        set so the next call will retry after the user fixes credentials.
        """
        if self._auth_checked:
            return
        self.check_auth()

    def check_auth(self) -> dict[str, str | bool]:
        """Verify GCP authentication and return info about the caller identity.

        Performs a lightweight ``list_secrets`` call to confirm the credentials
        actually work against the target project. Returns a dict with
        ``authenticated``, ``identity``, ``credential_type`` and ``project``.
        Raises ``ExternalServiceError`` with an actionable message on failure.
        """
        try:
            import google.auth
        except ImportError as exc:
            raise ExternalServiceError("google-auth is not installed.") from exc

        credentials = None
        detected_project: str | None = None
        if self._credentials_path:
            try:
                from google.oauth2 import service_account

                credentials = service_account.Credentials.from_service_account_file(
                    self._credentials_path
                )
                import json

                with open(self._credentials_path, encoding="utf-8") as fh:
                    detected_project = json.load(fh).get("project_id")
            except FileNotFoundError as exc:
                raise ValidationError(
                    f"Service account key not found at '{self._credentials_path}'."
                ) from exc
            except Exception as exc:
                raise ExternalServiceError(
                    f"Could not load service account from '{self._credentials_path}': {exc}"
                ) from exc
        else:
            try:
                credentials, detected_project = google.auth.default()
            except Exception as exc:
                raise ExternalServiceError(
                    "No Application Default Credentials found. Run "
                    "`gcloud auth application-default login`, set "
                    "GOOGLE_APPLICATION_CREDENTIALS to a service account key, "
                    "or pass credentials_path=... when constructing SecretsService. "
                    f"Underlying error: {exc}"
                ) from exc

        identity = getattr(credentials, "service_account_email", None) or "user-credentials"
        credential_type = type(credentials).__name__
        project = self._project_id or detected_project or ""

        try:
            client = self._get_client()
            list(client.list_secrets(request={"parent": f"projects/{project}"}, timeout=10))
        except Exception as exc:
            raise ExternalServiceError(
                f"Authenticated as '{identity}' but could not access project "
                f"'{project}'. Check that the identity has "
                f"'roles/secretmanager.viewer' or higher on the project. "
                f"Underlying error: {exc}"
            ) from exc

        info: dict[str, str | bool] = {
            "authenticated": True,
            "identity": identity,
            "credential_type": credential_type,
            "project": project,
        }
        self._auth_checked = True
        self._auth_info = info
        return info

    def get_secret(
        self,
        name: str,
        *,
        version: str = "latest",
        use_cache: bool = True,
        default: str | None = None,
    ) -> str:
        if not name or not name.strip():
            raise ValidationError("Secret name is required.")
        name = name.strip()

        cache_key = (name, version)
        if use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        self._ensure_auth()
        try:
            client = self._get_client()
            resource = f"projects/{self.project_id}/secrets/{name}/versions/{version}"
            response = client.access_secret_version(request={"name": resource})
            value = response.payload.data.decode("utf-8")
            self._cache[cache_key] = value
            return value
        except ExternalServiceError:
            raise
        except Exception as exc:
            from google.api_core import exceptions as gcp_exceptions

            if isinstance(exc, gcp_exceptions.NotFound):
                fallback = os.environ.get(name) if self._env_fallback else None
                if fallback is not None:
                    return fallback
                if default is not None:
                    return default
                raise NotFoundError(f"Secret '{name}' not found in Secret Manager.") from exc

            fallback = os.environ.get(name) if self._env_fallback else None
            if fallback is not None:
                logger.warning("Secret Manager unavailable for %s, falling back to env: %s", name, exc)
                return fallback
            if default is not None:
                return default
            raise ExternalServiceError(f"Failed to read secret '{name}': {exc}") from exc

    def set_secret(self, name: str, value: str) -> str:
        if not name or not name.strip():
            raise ValidationError("Secret name is required.")
        if value is None:
            raise ValidationError("Secret value cannot be None.")
        name = name.strip()

        self._ensure_auth()
        client = self._get_client()
        parent = f"projects/{self.project_id}"
        secret_path = f"{parent}/secrets/{name}"

        try:
            from google.api_core import exceptions as gcp_exceptions

            try:
                client.get_secret(request={"name": secret_path})
            except gcp_exceptions.NotFound:
                client.create_secret(
                    request={
                        "parent": parent,
                        "secret_id": name,
                        "secret": {"replication": {"automatic": {}}},
                    }
                )

            version = client.add_secret_version(
                request={
                    "parent": secret_path,
                    "payload": {"data": value.encode("utf-8")},
                }
            )
            self._cache.pop((name, "latest"), None)
            return version.name
        except (NotFoundError, ValidationError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(f"Failed to write secret '{name}': {exc}") from exc

    def delete_secret(self, name: str) -> None:
        if not name or not name.strip():
            raise ValidationError("Secret name is required.")
        name = name.strip()

        self._ensure_auth()
        try:
            client = self._get_client()
            client.delete_secret(request={"name": f"projects/{self.project_id}/secrets/{name}"})
            self._cache = {key: val for key, val in self._cache.items() if key[0] != name}
        except Exception as exc:
            from google.api_core import exceptions as gcp_exceptions

            if isinstance(exc, gcp_exceptions.NotFound):
                raise NotFoundError(f"Secret '{name}' not found in Secret Manager.") from exc
            raise ExternalServiceError(f"Failed to delete secret '{name}': {exc}") from exc

    def list_secrets(self) -> list[str]:
        self._ensure_auth()
        try:
            client = self._get_client()
            parent = f"projects/{self.project_id}"
            return [secret.name.rsplit("/", 1)[-1] for secret in client.list_secrets(request={"parent": parent})]
        except Exception as exc:
            raise ExternalServiceError(f"Failed to list secrets: {exc}") from exc

    def grant_access(
        self,
        name: str,
        member: str,
        *,
        role: str = "roles/secretmanager.secretAccessor",
    ) -> None:
        """Add an IAM binding granting ``member`` the given role on a secret.

        ``member`` must be a fully-qualified IAM principal, e.g.
        ``serviceAccount:123-compute@developer.gserviceaccount.com`` or
        ``user:alice@example.com``. If the member already has the role this
        method is a no-op.
        """
        if not name or not name.strip():
            raise ValidationError("Secret name is required.")
        if not member or not member.strip():
            raise ValidationError("IAM member is required.")
        name = name.strip()
        member = member.strip()

        self._ensure_auth()
        try:
            client = self._get_client()
            resource = f"projects/{self.project_id}/secrets/{name}"
            policy = client.get_iam_policy(request={"resource": resource})

            binding = next((b for b in policy.bindings if b.role == role), None)
            if binding is None:
                binding = policy.bindings.add()
                binding.role = role
            if member in binding.members:
                return
            binding.members.append(member)
            client.set_iam_policy(request={"resource": resource, "policy": policy})
        except Exception as exc:
            from google.api_core import exceptions as gcp_exceptions

            if isinstance(exc, gcp_exceptions.NotFound):
                raise NotFoundError(f"Secret '{name}' not found in Secret Manager.") from exc
            raise ExternalServiceError(f"Failed to set IAM on secret '{name}': {exc}") from exc

    def create_secrets_from_env(
        self,
        env_path: str,
        *,
        names: list[str] | None = None,
        only_update: bool = False,
        grant_access_to: str | list[str] | None = None,
        role: str = "roles/secretmanager.secretAccessor",
    ) -> dict[str, str]:
        """Read a .env file and upload selected keys to Secret Manager.

        Args:
            env_path: path to the .env file to read.
            names: keys to upload; ``None`` means every key found in the file.
            only_update: if True, skip keys whose secret does not exist yet.
            grant_access_to: one or more IAM members (e.g.
                ``"serviceAccount:123-compute@developer.gserviceaccount.com"``)
                that should be granted ``role`` on every uploaded secret.
            role: IAM role to grant when ``grant_access_to`` is set. Defaults
                to ``roles/secretmanager.secretAccessor``.

        Returns a mapping ``{secret_name: version_resource_name}`` for each
        secret that was created or updated.
        """
        if not env_path or not str(env_path).strip():
            raise ValidationError("env_path is required.")

        try:
            with open(env_path, encoding="utf-8") as fh:
                env_values: dict[str, str] = {}
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    env_values[key.strip()] = value.strip().strip('"').strip("'")
        except OSError as exc:
            raise ValidationError(f"Could not read env file '{env_path}': {exc}") from exc

        selected = list(names) if names else list(env_values.keys())
        self._ensure_auth()
        client = self._get_client()
        from google.api_core import exceptions as gcp_exceptions

        if grant_access_to is None:
            members: list[str] = []
        elif isinstance(grant_access_to, str):
            members = [grant_access_to]
        else:
            members = list(grant_access_to)

        results: dict[str, str] = {}
        for name in selected:
            value = env_values.get(name)
            if not value:
                logger.warning("Secret %s not found in %s — skipped", name, env_path)
                continue

            secret_path = f"projects/{self.project_id}/secrets/{name}"
            try:
                client.get_secret(request={"name": secret_path})
                exists = True
            except gcp_exceptions.NotFound:
                exists = False

            if not exists and only_update:
                logger.warning("Secret %s does not exist — run without only_update to create it", name)
                continue

            results[name] = self.set_secret(name, value)

            for member in members:
                self.grant_access(name, member, role=role)

        return results

    def load_into_env(self, names: list[str], *, overwrite: bool = False) -> dict[str, str]:
        loaded: dict[str, str] = {}
        for name in names:
            if not overwrite and os.environ.get(name):
                loaded[name] = os.environ[name]
                continue
            try:
                value = self.get_secret(name)
            except NotFoundError:
                continue
            os.environ[name] = value
            loaded[name] = value
        return loaded

    def clear_cache(self) -> None:
        self._cache.clear()
