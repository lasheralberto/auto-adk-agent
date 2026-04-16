"""Sincroniza `.env` → Secret Manager y redepliega Cloud Run.

Para cada clave del `.env`:
- si el secret existe en GCP, se salta (no se modifica su valor).
- si no existe, se crea con replicación automática y se sube la versión inicial.

Al final lanza `gcloud run services update` montando todas las claves como
`--set-secrets` sobre el servicio de Cloud Run.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

from dotenv import dotenv_values

PROJECT_ID = "strava-chat"
REGION = "europe-west1"
SERVICE = "strava-api"
ENV_FILE = pathlib.Path(__file__).resolve().parent.parent / ".env"

# Service account del runtime de Cloud Run. Necesita poder leer los secrets.
RUNTIME_SERVICE_ACCOUNT = "153952529856-compute@developer.gserviceaccount.com"

# Claves del .env que no deben subir a Secret Manager ni a Cloud Run.
EXCLUDE = {
    "GOOGLE_APPLICATION_CREDENTIALS",  # ruta a un fichero local
    "GOOGLE_CLOUD_PROJECT",            # lo inyecta Cloud Run por defecto
}


def _gcloud() -> str:
    path = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not path:
        sys.exit("gcloud no está en el PATH")
    return path


def _secret_exists(gcloud: str, name: str) -> bool:
    result = subprocess.run(
        [gcloud, "secrets", "describe", name, f"--project={PROJECT_ID}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _secret_latest_value(gcloud: str, name: str) -> str | None:
    """Devuelve el valor de la última versión activa, o None si no se puede leer."""
    result = subprocess.run(
        [
            gcloud, "secrets", "versions", "access", "latest",
            f"--secret={name}",
            f"--project={PROJECT_ID}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _add_secret_version(gcloud: str, name: str, value: str) -> None:
    subprocess.run(
        [
            gcloud, "secrets", "versions", "add", name,
            f"--project={PROJECT_ID}",
            "--data-file=-",
        ],
        input=value,
        text=True,
        check=True,
    )


def _grant_accessor(gcloud: str, name: str) -> None:
    """Concede roles/secretmanager.secretAccessor a la SA del runtime.

    Idempotente: si ya existe el binding, gcloud lo ignora silenciosamente.
    """
    subprocess.run(
        [
            gcloud, "secrets", "add-iam-policy-binding", name,
            f"--project={PROJECT_ID}",
            f"--member=serviceAccount:{RUNTIME_SERVICE_ACCOUNT}",
            "--role=roles/secretmanager.secretAccessor",
        ],
        check=True,
        capture_output=True,
    )


def _create_secret(gcloud: str, name: str, value: str) -> None:
    subprocess.run(
        [
            gcloud, "secrets", "create", name,
            f"--project={PROJECT_ID}",
            "--replication-policy=automatic",
        ],
        check=True,
    )
    _add_secret_version(gcloud, name, value)


def main() -> int:
    if not ENV_FILE.exists():
        sys.exit(f"No existe {ENV_FILE}")

    gcloud = _gcloud()
    raw = dotenv_values(ENV_FILE)

    managed: list[str] = []
    created: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []

    for key, raw_value in raw.items():
        if key in EXCLUDE or raw_value is None:
            continue
        value = raw_value.strip()
        if not value:
            continue
        managed.append(key)
        if _secret_exists(gcloud, key):
            current = _secret_latest_value(gcloud, key)
            if current == value:
                skipped.append(key)
            else:
                print(f"actualizando secret {key} (valor difiere)...")
                _add_secret_version(gcloud, key, value)
                updated.append(key)
        else:
            print(f"creando secret {key}...")
            _create_secret(gcloud, key, value)
            created.append(key)
        _grant_accessor(gcloud, key)

    print()
    print(f"Creados ({len(created)}): {', '.join(created) or '—'}")
    print(f"Actualizados ({len(updated)}): {', '.join(updated) or '—'}")
    print(f"Sin cambios ({len(skipped)}): {', '.join(skipped) or '—'}")

    if not managed:
        print("No hay claves que montar; salgo.")
        return 0

    set_secrets = ",".join(f"{k}={k}:latest" for k in managed)
    remove_env_vars = ",".join(managed)
    print(f"\nActualizando Cloud Run {SERVICE} ({REGION})...")
    subprocess.run(
        [
            gcloud, "run", "services", "update", SERVICE,
            f"--project={PROJECT_ID}",
            f"--region={REGION}",
            f"--remove-env-vars={remove_env_vars}",
            f"--update-secrets={set_secrets}",
        ],
        check=True,
    )
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
