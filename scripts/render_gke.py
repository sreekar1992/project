"""Render the GKE release with immutable images and environment configuration."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(os.environ.get("GKE_RENDER_DIR", "/tmp/ecg-gke-release"))
NAMESPACE = "ecg-clinical-research"


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def write_yaml(name: str, documents: list[dict]) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(yaml.safe_dump_all(documents, sort_keys=False))


def main() -> None:
    api_image = required("API_IMAGE")
    frontend_image = required("FRONTEND_IMAGE")
    for image in (api_image, frontend_image):
        if not re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", image):
            raise SystemExit(f"Expected immutable Artifact Registry digest: {image}")

    config = {
        "OBJECT_STORAGE_BUCKET": required("OBJECT_STORAGE_BUCKET"),
        "AI_MODEL_VERSION": required("AI_MODEL_VERSION"),
        "CORS_ORIGINS": required("CORS_ORIGINS"),
        "ECG_PLATFORM_TRUSTED_HOSTS": required("ECG_PLATFORM_TRUSTED_HOSTS"),
    }
    release_id = required("RELEASE_ID")
    workload_service_account = required("GCP_WORKLOAD_SERVICE_ACCOUNT")
    if not re.fullmatch(r"[a-z0-9-]{1,35}", release_id):
        raise SystemExit("RELEASE_ID must be lowercase letters, digits or hyphens")

    rendered = subprocess.check_output(
        ["kubectl", "kustomize", str(ROOT / "k8s/platform")], text=True
    )
    documents = [item for item in yaml.safe_load_all(rendered) if item]
    for item in documents:
        kind = item["kind"]
        name = item["metadata"]["name"]
        if kind == "ConfigMap" and name == "ecg-platform-config":
            item["data"].update(config)
            item["data"]["OBJECT_STORAGE_BACKEND"] = "gcs"
            item["data"].pop("OBJECT_STORAGE_ENDPOINT", None)
            item["data"].pop("OBJECT_STORAGE_REGION", None)
            item["data"].pop("REDIS_URL", None)
        elif kind == "Deployment" and name in {"ecg-platform-api", "ecg-platform-worker"}:
            pod = item["spec"]["template"]["spec"]
            pod["serviceAccountName"] = "ecg-platform"
            pod["containers"][0]["image"] = api_image
            pod["volumes"][0] = {"name": "model", "emptyDir": {}}
            pod["initContainers"] = [{
                "name": "fetch-model",
                "image": api_image,
                "command": ["python", "-c", (
                    "import os; from google.cloud import storage; "
                    "storage.Client().bucket(os.environ['OBJECT_STORAGE_BUCKET'])"
                    ".blob('models/model.pt').download_to_filename('/models/model.pt')"
                )],
                "envFrom": [{"configMapRef": {"name": "ecg-platform-config"}}],
                "volumeMounts": [{"name": "model", "mountPath": "/models"}],
                "securityContext": {"runAsUser": 10001, "runAsGroup": 10001,
                                    "allowPrivilegeEscalation": False},
                "resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                              "limits": {"cpu": "500m", "memory": "256Mi"}},
            }]

    frontend = list(yaml.safe_load_all((ROOT / "k8s/gke/frontend.yaml").read_text()))
    frontend[0]["spec"]["template"]["spec"]["containers"][0]["image"] = frontend_image
    documents.extend(frontend)

    service_account = yaml.safe_load((ROOT / "k8s/gke/service-account.yaml").read_text())
    service_account["metadata"]["annotations"]["iam.gke.io/gcp-service-account"] = workload_service_account
    documents.insert(1, service_account)

    migration = yaml.safe_load((ROOT / "k8s/gke/migration.yaml").read_text())
    migration["metadata"]["name"] = f"ecg-platform-migrate-{release_id}"
    migration["spec"]["template"]["spec"]["containers"][0]["image"] = api_image
    migration["spec"]["template"]["spec"]["serviceAccountName"] = "ecg-platform"

    config_doc = next(item for item in documents if item["kind"] == "ConfigMap")
    write_yaml("config.yaml", [config_doc])
    write_yaml("service-account.yaml", [service_account])
    write_yaml("migration.yaml", [migration])
    write_yaml("deployment.yaml", documents)
    print(f"Rendered release in {OUTPUT} for namespace {NAMESPACE}")


if __name__ == "__main__":
    main()
