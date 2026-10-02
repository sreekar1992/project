"""Create the first cluster Secret after Terraform apply, without logging values."""
from __future__ import annotations

import json
from pathlib import Path
import secrets
import subprocess
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
TF_DIR = ROOT / "infra/gke"
NAMESPACE = "ecg-clinical-research"


def run(*args: str, input_text: str | None = None) -> str:
    result = subprocess.run(args, input=input_text, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def output(name: str) -> str:
    return run("terraform", f"-chdir={TF_DIR}", "output", "-raw", name)


def main() -> None:
    project = output("project_id")
    run("gcloud", "container", "clusters", "get-credentials", output("cluster_name"),
        "--region", output("cluster_location"), "--project", project)
    run("kubectl", "apply", "-f", str(ROOT / "k8s/platform/namespace.yaml"))
    existing = subprocess.run(
        ("kubectl", "-n", NAMESPACE, "get", "secret", "ecg-platform-secrets"),
        capture_output=True, text=True,
    )
    if existing.returncode == 0:
        print("ecg-platform-secrets already exists; preserving existing credentials.")
        return
    if "NotFound" not in existing.stderr:
        raise RuntimeError("Cannot determine whether ecg-platform-secrets exists: " + existing.stderr.strip())
    password = run("gcloud", "secrets", "versions", "access", "latest",
                   "--secret", output("database_password_secret"), "--project", project)
    database_url = (f"postgresql+psycopg://ecg_app:{quote(password, safe='')}"
                    f"@{output('database_private_ip')}:5432/ecg_platform")
    manifest = {
        "apiVersion": "v1", "kind": "Secret",
        "metadata": {"name": "ecg-platform-secrets", "namespace": NAMESPACE},
        "type": "Opaque",
        "stringData": {
            "DATABASE_URL": database_url,
            "SECRET_KEY": secrets.token_urlsafe(48),
            "JWT_SECRET": secrets.token_urlsafe(48),
            "REDIS_URL": f"redis://{output('redis_host')}:6379/0",
        },
    }
    run("kubectl", "apply", "-f", "-", input_text=json.dumps(manifest))
    print("Created ecg-platform-secrets in", NAMESPACE)


if __name__ == "__main__":
    main()
