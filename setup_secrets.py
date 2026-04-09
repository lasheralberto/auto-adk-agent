"""
secrets.py — gestión de secretos en GCP Secret Manager para strava-api

Lee los valores directamente desde .env y los sube a Secret Manager.

Uso:
  python secrets.py          → crea/actualiza todos los secretos e IAM
  python secrets.py --update → solo actualiza versiones (no crea de cero)
  python secrets.py --iam    → solo configura permisos IAM

Requiere: gcloud CLI autenticado con permisos de secretmanager.admin y run.viewer
"""

import subprocess
import sys
import os

PROJECT = "strava-chat"
REGION = "europe-west1"
SERVICE = "strava-api"
ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")

SECRETS = [
    "INTERNAL_PIPELINE_BASE_URL"
]


def load_env(path: str) -> dict:
    values = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def run(cmd: str, silent: bool = False, stdin_data: str = None) -> str:
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True,
        input=stdin_data,
    )
    if not silent:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="")
    return (result.stdout + result.stderr).strip()


def secret_exists(name: str) -> bool:
    out = run(f"gcloud secrets describe {name} --project={PROJECT} 2>&1", silent=True)
    return "name:" in out


def get_service_account() -> str:
    sa = run(
        f"gcloud run services describe {SERVICE} --region={REGION} --project={PROJECT} "
        f'--format="value(spec.template.spec.serviceAccountName)" 2>&1',
        silent=True,
    )
    if not sa or "ERROR" in sa:
        number = run(
            f'gcloud projects describe {PROJECT} --format="value(projectNumber)"',
            silent=True,
        )
        return f"{number}-compute@developer.gserviceaccount.com"
    return sa


def create_or_update_secrets(env: dict, only_update: bool):
    print(f"\n📦 Leyendo valores desde {ENV_FILE}\n")

    for name in SECRETS:
        value = env.get(name)
        if not value:
            print(f"⚠️  {name} no encontrado en .env — omitido\n")
            continue

        exists = secret_exists(name)

        if not exists and only_update:
            print(f"⚠️  {name} no existe en Secret Manager — ejecuta sin --update para crearlo\n")
            continue

        if not exists:
            print(f"   → creando {name}...")
            run(f"gcloud secrets create {name} --data-file=- --project={PROJECT}", stdin_data=value)
        else:
            print(f"   → actualizando {name}...")
            run(f"gcloud secrets versions add {name} --data-file=- --project={PROJECT}", stdin_data=value)

        print(f"   ✅ {name} listo\n")


def configure_iam():
    print("\n🔐 Obteniendo service account de Cloud Run...")
    sa = get_service_account()
    print(f"   → {sa}\n")

    for name in SECRETS:
        if not secret_exists(name):
            print(f"   ⚠️  {name} no existe, saltando IAM")
            continue
        print(f"   → IAM para {name}...")
        run(
            f"gcloud secrets add-iam-policy-binding {name} "
            f'--member="serviceAccount:{sa}" '
            f"--role=roles/secretmanager.secretAccessor "
            f"--project={PROJECT}"
        )
        print(f"   ✅ {name}\n")


def main():
    args = sys.argv[1:]
    only_update = "--update" in args
    only_iam = "--iam" in args

    print("🚀 strava-api — Secret Manager setup")
    print(f"   Proyecto : {PROJECT}")
    print(f"   Región   : {REGION}")
    print(f"   Servicio : {SERVICE}")

    env = load_env(ENV_FILE)

    if not only_iam:
        create_or_update_secrets(env, only_update)

    configure_iam()

    print("\n✅ Todo listo. Lanza el trigger de Cloud Build para desplegar.")


if __name__ == "__main__":
    main()
