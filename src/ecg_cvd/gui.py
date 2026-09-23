"""Local, single-page ECG workbench. Run with ``ecg-gui``."""
from __future__ import annotations

import argparse
import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Lock, Timer
import time
import uuid
import webbrowser
import zipfile

import numpy as np
import torch
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import Flask, jsonify, request, send_file
from scipy.io import loadmat
from scipy.io.matlab import MatReadError
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from .crypto import decrypt_bytes, encrypt_bytes, decrypt_password, encrypt_password
from .data import model_input, preprocess
from .explain import grad_cam
from .model import RAMNV2
from .care import care_information
from .single_attack import evaluate_single_attack
from .raw_camouflage import generate_camouflage
from .batch_crypto import (encrypt_dataset, list_batches, list_records, decrypt_record, save_result,
                           batch_path, camouflage_preview, encrypted_record_preview,
                           render_ciphertext_preview, render_waveform, KEY_FILE, INDEX_FILE)

CLASS_NAMES = {
    "NSR": "Normal sinus rhythm", "APB": "Atrial premature beat",
    "AFL": "Atrial flutter", "AFIB": "Atrial fibrillation",
    "SVTA": "Supraventricular tachyarrhythmia", "WPW": "Wolff-Parkinson-White pattern",
    "PVC": "Premature ventricular contraction", "Bigeminy": "Ventricular bigeminy",
    "Trigeminy": "Ventricular trigeminy", "VT": "Ventricular tachycardia",
    "IVR": "Idioventricular rhythm", "VFL": "Ventricular flutter",
    "Fusion": "Fusion of ventricular and normal beat", "LBBBB": "Left bundle branch block beat",
    "RBBBB": "Right bundle branch block beat", "SDHB": "Second-degree heart block",
    "PR": "Pacemaker rhythm",
}


def environment_flag(name: str, default: bool) -> bool:
    """Read a conservative boolean deployment setting from the environment."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def trusted_hosts() -> list[str]:
    """Use localhost by default; deployments must explicitly opt in hosts."""
    configured = os.getenv("ECG_TRUSTED_HOSTS", "")
    hosts = [host.strip() for host in configured.split(",") if host.strip()]
    return hosts or ["localhost", "127.0.0.1", "[::1]"]


def class_name(label: str) -> str:
    return CLASS_NAMES.get(label.split(" ", 1)[-1], label)


def read_signal(data: bytes, filename: str) -> np.ndarray:
    """Read a single uploaded ECG without writing it to the project."""
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".mat":
            arrays = {k: np.asarray(v).squeeze() for k, v in loadmat(BytesIO(data)).items()
                      if not k.startswith("__") and isinstance(v, np.ndarray)
                      and np.issubdtype(v.dtype, np.number)}
            if "val" in arrays:
                raw = arrays["val"]
            else:
                candidates = [v for v in arrays.values() if v.ndim == 1 and v.size == 3600]
                if len(candidates) != 1:
                    raise ValueError("Use a MAT file with one 3,600-sample 'val' waveform.")
                raw = candidates[0]
        elif suffix == ".csv":
            raw = np.loadtxt(BytesIO(data), delimiter=",").squeeze()
        else:
            raise ValueError("Upload a .mat or .csv ECG signal. Images can be encrypted in the security panel.")
        if raw.ndim != 1 or raw.size != 3600 or np.iscomplexobj(raw):
            raise ValueError("Expected one real-valued waveform with 3,600 samples (10 seconds at 360 Hz).")
        raw = raw.astype(np.float32)
        if not np.isfinite(raw).all() or float(np.ptp(raw)) == 0:
            raise ValueError("ECG data must contain finite values and a non-flat waveform.")
        return raw
    except (ValueError, TypeError, NotImplementedError, OSError, MatReadError) as exc:
        raise ValueError(f"Cannot read this ECG: {exc}") from exc


def parse_key(data: bytes) -> bytes:
    try:
        key = bytes.fromhex(data.decode("ascii").strip())
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Upload a key file containing 64 hexadecimal characters.") from exc
    if len(key) != 32:
        raise ValueError("An AES-256 key must contain 64 hexadecimal characters.")
    return key


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def create_app(project_root: Path | str | None = None) -> Flask:
    root = Path(project_root or os.getenv("ECG_PROJECT_ROOT") or Path.cwd()).resolve()
    app = Flask(__name__, static_folder=None)
    app.config.update(
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
        TRUSTED_HOSTS=trusted_hosts(),
        ECG_ENABLE_BACKGROUND_JOBS=environment_flag("ECG_ENABLE_BACKGROUND_JOBS", True),
        ECG_ENABLE_TRAINING=environment_flag("ECG_ENABLE_TRAINING", True),
        ECG_REQUIRE_MODEL_FOR_READY=environment_flag("ECG_REQUIRE_MODEL_FOR_READY", False),
    )
    compute_lock = Lock()
    # Scrypt deliberately consumes memory; serialize password operations locally.
    password_lock = Lock()
    jobs_lock = Lock()
    jobs: dict[str, dict] = {}
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ecg-job")
    app.extensions["ecg_executor"] = executor
    model_cache: dict = {}

    def model_paths() -> dict[str, Path]:
        paths = list(root.glob("artifacts*/model.pt")) + list(root.glob("artifacts_gui/*/model.pt"))
        return {p.relative_to(root).as_posix(): p for p in sorted(paths)
                if p.is_file() and p.resolve().is_relative_to(root)}

    def sample_paths() -> dict[str, Path]:
        folder = root / "MLII"
        return {p.relative_to(root).as_posix(): p for p in sorted(folder.rglob("*.mat"))
                if p.is_file() and p.resolve().is_relative_to(folder.resolve())}

    def resolve_model(identifier: str) -> Path:
        found = model_paths().get(identifier)
        if found is None:
            raise ValueError("Select an available local model checkpoint.")
        return found

    def load_model(path: Path):
        signature = (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        if model_cache.get("signature") != signature:
            checkpoint = torch.load(path, map_location="cpu", weights_only=True)
            mapping = checkpoint["label_map"]
            count = checkpoint["num_classes"]
            if sorted(mapping.values()) != list(range(count)):
                raise ValueError("Model has an invalid class mapping.")
            model = RAMNV2(count)
            model.load_state_dict(checkpoint["state_dict"])
            model.eval()
            model_cache.update(signature=signature, model=model,
                               labels=[label for label, _ in sorted(mapping.items(), key=lambda item: item[1])])
        return model_cache["model"], model_cache["labels"]

    def prediction(raw, name, reference_label, model_path, render_image=True):
        """Compute a result and PNG in memory. Caller holds compute_lock."""
        model, labels = load_model(model_path)
        signal = model_input(preprocess(raw[None]))[0]
        with torch.no_grad():
            probabilities = torch.softmax(model(torch.from_numpy(signal[None, None])), dim=1)[0].numpy()
        # Stable lowest-index tie handling matches torch.argmax in evaluation.
        order = np.argsort(-probabilities, kind="stable")
        predicted = labels[int(order[0])]
        image = BytesIO()
        if render_image:
            grad_cam(model, signal, int(order[0]), image, fs=90.0)
        report = {"sample_name": name, "label": predicted, "label_name": class_name(predicted),
                  "confidence": float(probabilities[order[0]]), "reference_label": reference_label,
                  "model": str(model_path.relative_to(root)), "samples": 3600, "sampling_rate_hz": 360,
                  "duration_seconds": 10, "model_sampling_rate_hz": 90,
                  "top_predictions": [{"label": labels[int(i)], "label_name": class_name(labels[int(i)]),
                                       "probability": float(probabilities[i])} for i in order[:3]],
                  "note": "Research classification. Model score is not a calibrated probability of disease."}
        return report, image.getvalue()

    def secure_directory(category):
        directory = root / "artifacts_secure" / category
        if not directory.resolve().is_relative_to(root):
            raise ValueError("Encrypted storage must remain inside this project.")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return directory

    def save_encrypted(payload, category, filename, extension=""):
        identifier = uuid.uuid4().hex
        path = secure_directory(category) / (identifier + extension + ".ecgenc")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags, 0o600), "wb") as handle:
            handle.write(payload)
        return {"artifact_id": identifier, "filename": filename,
                "ciphertext": encode(payload), "saved_path": path.relative_to(root).as_posix()}

    def encrypted_source():
        file = request.files.get("file")
        identifier = request.form.get("artifact_id", "")
        uploaded = bool(file and file.filename)
        if int(uploaded) + int(bool(identifier)) != 1:
            raise ValueError("Choose one encrypted recording: a saved artifact or an uploaded file.")
        if uploaded:
            return file.read()
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("Invalid encrypted recording identifier.")
        directory = root / "artifacts_secure" / "inputs"
        path = directory / (identifier + ".ecgenc")
        if (not directory.resolve().is_relative_to(root) or path.is_symlink()
                or not path.resolve().is_relative_to(directory.resolve()) or not path.is_file()):
            raise ValueError("Encrypted recording is unavailable. Upload its .ecgenc file again.")
        if path.stat().st_size > app.config["MAX_CONTENT_LENGTH"]:
            raise ValueError("Encrypted recording is too large.")
        return path.read_bytes()

    def unpack_signal(plaintext):
        try:
            package = json.loads(plaintext)
            if (not isinstance(package, dict) or package.get("version") != 1
                    or package.get("kind") != "ecg-signal"):
                raise ValueError
            name = package["filename"]
            reference = package.get("reference_label")
            if (not isinstance(name, str) or not name or len(name) > 255
                    or Path(name).name != name or "\\" in name
                    or (reference is not None and (not isinstance(reference, str) or len(reference) > 100))):
                raise ValueError
            data = base64.b64decode(package["signal_file"], validate=True)
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise ValueError("This is not an encrypted ECG recording created by this workbench.") from exc
        return data, name, reference

    def patch_job(identifier, **values):
        with jobs_lock:
            jobs[identifier].update(values)

    def start_job(operation):
        with jobs_lock:
            if any(job["status"] in ("queued", "running") for job in jobs.values()):
                return jsonify(error="A model job is already running. Let it finish before starting another."), 409
            identifier = uuid.uuid4().hex
            # Avoid retaining an unbounded history during a long local session.
            if len(jobs) >= 30:
                jobs.pop(next(iter(jobs)))
            jobs[identifier] = {"status": "queued", "progress": 0, "log": [], "result": None, "error": None}

        def run():
            try:
                with compute_lock:
                    patch_job(identifier, status="running")
                    result = operation(identifier)
                patch_job(identifier, status="done", progress=100, result=result)
            except Exception as exc:
                patch_job(identifier, status="error", error=str(exc))
        executor.submit(run)
        return jsonify(job_id=identifier), 202

    @app.before_request
    def local_origin_only():
        # No cross-origin API access: the local service has no network authentication.
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if request.headers.get("Sec-Fetch-Site") == "cross-site" or (
                origin and origin.rstrip("/") != request.host_url.rstrip("/")
            ):
                return jsonify(error="Open this interface directly on localhost to use it."), 403

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.errorhandler(Exception)
    def api_error(exc):
        if isinstance(exc, InvalidTag):
            return jsonify(error="Decryption failed: the password/key is incorrect or the encrypted file was modified."), 400
        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description), exc.code
        if isinstance(exc, (ValueError, KeyError)):
            return jsonify(error=str(exc)), 400
        app.logger.exception("ECG operation failed")
        return jsonify(error="This operation failed. Check the server terminal for details."), 500

    @app.get("/")
    def index():
        return send_file(Path(__file__).parent / "web" / "index.html")

    @app.get("/favicon.ico")
    def favicon():
        return "", 204

    @app.get("/healthz")
    def healthz():
        """Cheap process-health endpoint suitable for liveness/startup probes."""
        return jsonify(status="ok")

    @app.get("/readyz")
    def readyz():
        """Report whether this instance has the assets required for inference."""
        available_models = len(model_paths())
        if app.config["ECG_REQUIRE_MODEL_FOR_READY"] and not available_models:
            return jsonify(status="not_ready", reason="No model checkpoint is mounted."), 503
        return jsonify(status="ready", models=available_models)

    @app.get("/api/state")
    def state():
        models = []
        for identifier, path in model_paths().items():
            try:
                metrics = json.loads((path.parent / "metrics.json").read_text())
            except (OSError, ValueError):
                metrics = {}
            models.append({"id": identifier, "name": path.parent.name, "metrics": metrics})
        samples = [{"id": identifier, "name": path.name, "label": path.parent.name}
                   for identifier, path in sample_paths().items()]
        return jsonify(models=models, samples=samples,
                       default_model="artifacts_final/model.pt" if "artifacts_final/model.pt" in model_paths()
                       else (models[0]["id"] if models else None),
                       dataset={"records": len(samples), "classes": len({s["label"] for s in samples})},
                       capabilities={"background_jobs": app.config["ECG_ENABLE_BACKGROUND_JOBS"],
                                     "training": (app.config["ECG_ENABLE_BACKGROUND_JOBS"]
                                                  and app.config["ECG_ENABLE_TRAINING"])})

    @app.post("/api/analyze")
    def analyze():
        started = time.monotonic()
        model_path = resolve_model(request.form.get("model_id", ""))
        file = request.files.get("file")
        reference_label = None
        if file and file.filename:
            name = secure_filename(file.filename) or "uploaded.mat"
            raw = read_signal(file.read(), name)
        else:
            sample = sample_paths().get(request.form.get("sample_id", ""))
            if sample is None:
                raise ValueError("Choose a dataset sample or upload an ECG file.")
            name, reference_label = sample.name, sample.parent.name
            raw = read_signal(sample.read_bytes(), name)
        if not compute_lock.acquire(blocking=False):
            return jsonify(error="A model job is running. Analysis will be available when it finishes."), 409
        try:
            report, image = prediction(raw, name, reference_label, model_path)
        finally:
            compute_lock.release()
        return jsonify(**report, report=report, explanation=encode(image),
                       duration_ms=round(1000 * (time.monotonic() - started)))

    @app.post("/api/secure/encrypt-signal")
    def encrypt_signal():
        file = request.files.get("file")
        sample_id = request.form.get("sample_id", "")
        uploaded = bool(file and file.filename)
        if int(uploaded) + int(bool(sample_id)) != 1:
            raise ValueError("Choose one dataset sample or one uploaded MAT/CSV recording.")
        reference = None
        if uploaded:
            name = secure_filename(file.filename) or "uploaded.mat"
            data = file.read()
        else:
            sample = sample_paths().get(sample_id)
            if sample is None:
                raise ValueError("Choose an available MLII dataset sample.")
            name, reference, data = sample.name, sample.parent.name, sample.read_bytes()
        read_signal(data, name)  # Reject malformed signals before creating an artifact.
        package = json.dumps({"version": 1, "kind": "ecg-signal", "filename": name,
                              "reference_label": reference, "signal_file": encode(data)},
                             separators=(",", ":")).encode("utf-8")
        if len(package) + 128 > app.config["MAX_CONTENT_LENGTH"]:
            raise ValueError("Recording is too large for an encrypted upload. Use a smaller MAT/CSV file.")
        with password_lock:
            payload = encrypt_password(package, request.form.get("password", ""))
        artifact = save_encrypted(payload, "inputs", name + ".ecgenc")
        return jsonify(**artifact, algorithm="AES-256-GCM + scrypt", status="encrypted")

    @app.post("/api/secure/analyze")
    def analyze_encrypted():
        started = time.monotonic()
        payload = encrypted_source()
        password = request.form.get("password", "")
        with password_lock:
            plaintext = decrypt_password(payload, password)
        data, name, reference = unpack_signal(plaintext)
        raw = read_signal(data, name)
        # No model evaluation, model loading or output files until authentication passes.
        model_path = resolve_model(request.form.get("model_id", ""))
        if not compute_lock.acquire(blocking=False):
            return jsonify(error="A model job is running. Prediction is available when it finishes."), 409
        try:
            report, image = prediction(raw, name, reference, model_path)
            with password_lock:
                encrypted_image = encrypt_password(image, password)
                encrypted_report = encrypt_password(json.dumps(report, indent=2).encode("utf-8"), password)
            # Both outputs are already ciphertext before any disk write or success response.
            image_artifact = save_encrypted(encrypted_image, "outputs", "ecg_explanation.png.ecgenc", ".png")
            report_artifact = save_encrypted(encrypted_report, "outputs", "ecg_analysis.json.ecgenc", ".json")
        finally:
            compute_lock.release()
        return jsonify(**report, report=report, encrypted_image=image_artifact,
                       encrypted_report=report_artifact,
                       security={"algorithm": "AES-256-GCM + scrypt", "status": "encrypted",
                                 "note": "Signal decrypted in memory; image and report saved encrypted. Password not persisted."},
                       duration_ms=round(1000 * (time.monotonic() - started)))

    @app.post("/api/secure/decrypt")
    def decrypt_password_file():
        file = request.files.get("file")
        if not file or not file.filename:
            raise ValueError("Choose a password-encrypted .ecgenc file.")
        with password_lock:
            plain = decrypt_password(file.read(), request.form.get("password", ""))
        name = (secure_filename(file.filename) or "restored-file").removesuffix(".ecgenc")
        mime = "application/octet-stream"
        if plain.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        else:
            try:
                decoded = json.loads(plain)
            except (ValueError, UnicodeError):
                decoded = None
            if isinstance(decoded, dict) and decoded.get("kind") == "ecg-signal":
                plain, name, _ = unpack_signal(plain)
            elif decoded is not None:
                mime = "application/json"
        return jsonify(plaintext=encode(plain), filename=name, mime=mime)

    @app.get("/api/dataset/state")
    def dataset_state():
        batches = list_batches(root)
        return jsonify(batches=batches, default_batch=batches[0]["batch_id"] if batches else None)

    @app.get("/api/dataset/records")
    def dataset_records():
        return jsonify(records=list_records(root, request.args.get("batch_id", "")))

    @app.post("/api/dataset/encrypt")
    def encrypt_all_dataset():
        if not app.config["ECG_ENABLE_BACKGROUND_JOBS"]:
            raise ValueError("Dataset encryption jobs are disabled in this deployment. Run a Kubernetes Job instead.")
        if not sample_paths():
            raise ValueError("No MAT recordings found in MLII.")
        body = request.get_json(silent=True)
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON configuration.")
        mode = body.get("mode", "aes")
        if mode not in ("aes", "camouflage"):
            raise ValueError("Choose AES-only or camouflage mode.")
        model_path = None
        epsilon, steps = body.get("camouflage_epsilon", 0.5), body.get("camouflage_steps", 20)
        if mode == "camouflage":
            if (isinstance(epsilon, bool) or not isinstance(epsilon, (int, float))
                    or not 0 < epsilon <= 1 or not math.isfinite(epsilon)):
                raise ValueError("Camouflage epsilon must be a number greater than 0 and at most 1.")
            if type(steps) is not int or not 1 <= steps <= 40:
                raise ValueError("Camouflage steps must be an integer between 1 and 40.")
            model_path = resolve_model(body.get("model_id", ""))

        def operation(identifier):
            patch_job(identifier, log=["Creating protected copies of every MAT recording. Originals are preserved."])
            def progress(done, total, message):
                patch_job(identifier, progress=min(99, round(100 * done / max(total, 1))), log=[message])
            generator = configuration = None
            if mode == "camouflage":
                model, labels = load_model(model_path)
                configuration = {"model": str(model_path.relative_to(root)),
                                 "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
                                 "epsilon": float(epsilon), "steps": steps}
                def generator(mat_bytes, reference_label):
                    signal = read_signal(mat_bytes, "recording.mat")
                    transformed, details = generate_camouflage(model, labels, signal, reference_label,
                                                               epsilon=float(epsilon), steps=steps)
                    details.update(model=configuration["model"], model_sha256=configuration["model_sha256"])
                    return transformed, details
            with password_lock:
                return encrypt_dataset(root, progress=progress, camouflage_generator=generator,
                                       camouflage_config=configuration)
        return start_job(operation)

    @app.get("/api/dataset/preview")
    def preview_camouflaged_record():
        # Ciphertext and decoys are public. No original signal or recovery key is returned.
        batch_id, record_id = request.args.get("batch_id", ""), request.args.get("record_id", "")
        encrypted_preview = encode(encrypted_record_preview(root, batch_id, record_id))
        preview_fields = {
            "encrypted_preview": encrypted_preview,
            "encrypted_preview_note": "Noise-like visualization of the selected recording's encrypted image bytes, not an ECG waveform. Original data remain locked; authenticity is checked during password decryption.",
        }
        public = camouflage_preview(root, batch_id, record_id)
        if public is None:
            return jsonify(camouflage=None, **preview_fields)
        details = public["report"]
        predicted = details.get("camouflaged_prediction")
        if isinstance(predicted, dict):
            predicted = {**predicted, "label_name": class_name(str(predicted.get("label", ""))),
                         "top_predictions": [{**item, "label_name": class_name(str(item.get("label", "")))}
                                             for item in predicted.get("top_predictions", []) if isinstance(item, dict)]}
        return jsonify(camouflage={"image": encode(render_waveform(public["signal"])),
                                   "prediction": predicted, "filename": public["filename"],
                                   "model": details.get("model"), "label_changed": details.get("label_changed"),
                                   "note": "Visible altered decoy, not encrypted waveform values. Saved prediction metadata is unverified until password authentication. This does not prove attack prevention.",
                                   "metadata_authenticated": False}, **preview_fields)

    @app.get("/api/dataset/camouflaged")
    def download_camouflaged_record():
        public = camouflage_preview(root, request.args.get("batch_id", ""), request.args.get("record_id", ""))
        if public is None:
            raise ValueError("This AES-only batch has no camouflaged MAT. Create a camouflage batch first.")
        return send_file(BytesIO(public["mat_bytes"]), mimetype="application/octet-stream", as_attachment=True,
                         download_name=public["filename"])

    @app.post("/api/dataset/decrypt")
    def decrypt_dataset_record():
        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON configuration.")
        include_attack = body.get("include_attack", False)
        if not isinstance(include_attack, bool):
            raise ValueError("include_attack must be true or false.")
        attack_epsilon = body.get("attack_epsilon", 0.05)
        if include_attack and (isinstance(attack_epsilon, bool)
                               or not isinstance(attack_epsilon, (int, float))
                               or not 0 <= attack_epsilon <= 1
                               or not math.isfinite(attack_epsilon)):
            raise ValueError("Attack epsilon must be a finite number between 0 and 1.")
        if not compute_lock.acquire(blocking=False):
            return jsonify(error="A processing job is running. Wait for it to finish."), 409
        try:
            with password_lock:
                restored = decrypt_record(root, body.get("batch_id", ""), body.get("record_id", ""),
                                          body.get("password", ""))
            # Authentication and manifest/file integrity checks happen before model loading.
            metadata = restored["metadata"]
            raw = read_signal(restored["mat_bytes"], metadata["name"])
            model_path = resolve_model(body.get("model_id", ""))
            report, explanation = prediction(raw, metadata["name"], metadata["label"], model_path)
            original_data = restored.get("original_mat_bytes")
            original_image = restored.get("original_image_bytes")
            original_prediction = None
            mse = maximum = None
            if original_data is not None:
                original_raw = read_signal(original_data, metadata["name"])
                original_prediction, _ = prediction(original_raw, metadata["name"], metadata["label"],
                                                     model_path, render_image=False)
                difference = original_raw.astype(np.float64) - raw.astype(np.float64)
                mse = float(np.mean(difference ** 2))
                maximum = float(np.max(np.abs(difference)))
            def prediction_summary(value):
                return {field: value[field] for field in
                        ("label", "label_name", "confidence", "top_predictions")} if value else None

            before = prediction_summary(original_prediction)
            after = prediction_summary(report)
            comparison = {
                "bytes_identical": original_data == restored["mat_bytes"] if original_data is not None else None,
                "images_identical": original_image == restored["image_bytes"] if original_image is not None else None,
                "max_absolute_error": maximum, "mse": mse,
                "original_sha256": hashlib.sha256(original_data).hexdigest() if original_data is not None else None,
                "decrypted_sha256": hashlib.sha256(restored["mat_bytes"]).hexdigest(),
                "original_prediction": before, "decrypted_prediction": after,
                "score_difference": abs(before["confidence"] - after["confidence"]) if before else None,
                "source_unchanged": original_data == restored["mat_bytes"] if original_data is not None else None,
                "encrypted_prediction": {"status": "unavailable",
                                         "reason": "AES ciphertext is not a valid ECG model input. Decrypt before inference."},
            }
            # General educational information refers only to the unperturbed recording.
            # It is never a treatment decision or based on an adversarially changed label.
            care = care_information(report["label"])
            camouflage = camouflage_image = None
            stored_camouflage = restored.get("camouflage")
            if stored_camouflage is not None:
                camouflaged_raw = stored_camouflage["signal"]
                decoy_report, _ = prediction(camouflaged_raw, metadata["name"], metadata["label"],
                                             model_path, render_image=False)
                delta = camouflaged_raw.astype(np.float64) - raw.astype(np.float64)
                camouflage_image = render_waveform(camouflaged_raw)
                camouflage = {
                    "prediction": prediction_summary(decoy_report),
                    "creation_report": stored_camouflage["report"],
                    "filename": stored_camouflage["filename"], "model": report["model"],
                    "label_changed": decoy_report["label"] != report["label"],
                    "recovery_bytes_identical": True,
                    "original_sha256": metadata["hashes"]["mat_plaintext_sha256"],
                    "restored_sha256": hashlib.sha256(restored["mat_bytes"]).hexdigest(),
                    "max_absolute_raw_change": float(np.max(np.abs(delta))), "mse": float(np.mean(delta ** 2)),
                    "note": "The visible val array is an altered decoy, not ciphertext. Password authentication recovers the exact original MAT from its encrypted recovery payload; it does not subtract noise. A changed model label is not proof of privacy, attack prevention, or a clinical change.",
                }
            adversarial = attack_image = encrypted_attack_image = None
            if include_attack:
                model, labels = load_model(model_path)
                signal = model_input(preprocess(raw[None]))[0]
                adversarial, attack_image = evaluate_single_attack(
                    model, labels, signal, metadata["label"], float(attack_epsilon))
                for key in ("clean_prediction", "attacked_prediction"):
                    value = adversarial[key]
                    value["label_name"] = class_name(value["label"])
                    for item in value["top_predictions"]:
                        item["label_name"] = class_name(item["label"])
                encrypted_attack_image = save_result(root, body["batch_id"], body["record_id"],
                                                     "attack_image", attack_image, restored["key"])
            encrypted_image = save_result(root, body["batch_id"], body["record_id"], "image", explanation, restored["key"])
            encrypted_report = save_result(root, body["batch_id"], body["record_id"], "report",
                                            json.dumps({"prediction": report, "comparison": comparison,
                                                        "care_information": care, "adversarial": adversarial,
                                                        "camouflage": camouflage},
                                                       indent=2).encode(),
                                            restored["key"])
            # Same ciphertext diagnostic shown while locked; decryption does not change it.
            preview = render_ciphertext_preview(restored["image_ciphertext"])
        finally:
            compute_lock.release()
        if adversarial is not None:
            adversarial = {**adversarial, "image": encode(attack_image), "encrypted_image": encrypted_attack_image}
        return jsonify(**report, report=report, comparison=comparison,
                       care_information=care, adversarial=adversarial,
                       camouflage={**camouflage, "image": encode(camouflage_image)} if camouflage else None,
                       restored_mat={"plaintext": encode(restored["mat_bytes"]), "filename": metadata["name"]}
                       if camouflage else None,
                       original_image=encode(original_image) if original_image is not None else None,
                       decrypted_image=encode(restored["image_bytes"]), encrypted_preview=encode(preview),
                       encrypted_image=encrypted_image, encrypted_report=encrypted_report,
                       warning="Fixed-password demonstration only. Existing source files are not encrypted or removed.")

    @app.get("/api/dataset/download")
    def download_encrypted_dataset():
        identifier = request.args.get("batch_id", "")
        available = {batch["batch_id"]: batch for batch in list_batches(root)}
        if identifier not in available:
            raise ValueError("Choose an available completed encrypted dataset.")
        directory = batch_path(root, identifier)
        record_ids = {record["record_id"] for record in list_records(root, identifier)}
        archive = BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            for path in sorted(directory.rglob("*")):
                if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                    raise ValueError("Dataset archive contains an unsafe path.")
                if path.is_file():
                    camouflage_match = re.fullmatch(r"([0-9a-f]{32})\.camouflaged\.mat", path.name)
                    if camouflage_match:
                        record_id = camouflage_match.group(1)
                        if (available[identifier].get("mode") != "camouflage" or record_id not in record_ids
                                or path.parent != directory):
                            continue
                        public = camouflage_preview(root, identifier, record_id)
                        if public is None:
                            raise ValueError("The dataset catalog changed while creating the archive.")
                        # Validate container layout; never include an arbitrary plaintext MAT by suffix.
                        bundle.writestr(str(Path(identifier) / path.relative_to(directory)), public["mat_bytes"])
                        continue
                    if path.suffix != ".ecgenc" and path.name not in (KEY_FILE, INDEX_FILE):
                        continue
                    bundle.write(path, Path(identifier) / path.relative_to(directory))
        archive.seek(0)
        return send_file(archive, mimetype="application/zip", as_attachment=True,
                         download_name=f"ecg_encrypted_dataset_{identifier[:8]}.zip")

    @app.post("/api/encrypt")
    def encrypt():
        file = request.files.get("file")
        if file is None or not file.filename:
            raise ValueError("Choose a file or create an ECG explanation first.")
        key_file = request.files.get("key")
        new_key = not (key_file and key_file.filename)
        key = AESGCM.generate_key(bit_length=256) if new_key else parse_key(key_file.read(1024))
        payload = encrypt_bytes(file.read(), key)
        return jsonify(ciphertext=encode(payload), key=key.hex() if new_key else None,
                       filename=(secure_filename(file.filename) or "ecg-file") + ".ecgenc")

    @app.post("/api/decrypt")
    def decrypt():
        file, key_file = request.files.get("file"), request.files.get("key")
        if not file or not key_file or not file.filename or not key_file.filename:
            raise ValueError("Select both the encrypted file and its key file.")
        plain = decrypt_bytes(file.read(), parse_key(key_file.read(1024)))
        name = secure_filename(file.filename) or "restored-file"
        name = name.removesuffix(".ecgenc")
        mime = "image/png" if plain.startswith(b"\x89PNG\r\n\x1a\n") else "application/octet-stream"
        return jsonify(plaintext=encode(plain), filename=name, mime=mime)

    @app.get("/api/jobs/<identifier>")
    def job(identifier):
        with jobs_lock:
            if identifier not in jobs:
                return jsonify(error="This job is no longer available. Start a new run."), 404
            return jsonify(jobs[identifier])

    @app.post("/api/robustness")
    def robustness():
        if not app.config["ECG_ENABLE_BACKGROUND_JOBS"]:
            raise ValueError("Robustness jobs are disabled in this deployment. Run a Kubernetes Job instead.")
        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON configuration.")
        model_path = resolve_model(body.get("model_id", ""))
        epsilon = float(body.get("epsilon", .05))
        if not math.isfinite(epsilon) or not 0 <= epsilon <= 1:
            raise ValueError("Choose an epsilon between 0 and 1.")

        def operation(identifier):
            from .robustness import evaluate_robustness
            patch_job(identifier, log=["Evaluating clean and perturbed ECGs on the validation split."])
            result = evaluate_robustness(root / "MLII", model_path, epsilon,
                                        progress=lambda done, total: patch_job(identifier, progress=int(done / total * 95)))
            result["model_id"] = model_path.relative_to(root).as_posix()
            return result
        return start_job(operation)

    @app.post("/api/train")
    def train():
        if not app.config["ECG_ENABLE_BACKGROUND_JOBS"] or not app.config["ECG_ENABLE_TRAINING"]:
            raise ValueError("Training is disabled in this deployment. Run a dedicated Kubernetes training Job instead.")
        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON configuration.")
        epochs = int(body.get("epochs", 30))
        batch = int(body.get("batch_size", 32))
        epsilon = float(body.get("adversarial_epsilon", 0))
        if not 1 <= epochs <= 200 or batch not in (8, 16, 32, 64):
            raise ValueError("Use 1-200 epochs and a batch size of 8, 16, 32, or 64.")
        if not math.isfinite(epsilon) or not 0 <= epsilon <= 1:
            raise ValueError("Choose an adversarial epsilon between 0 and 1.")
        if not sample_paths():
            raise ValueError("Place the extracted dataset in the project's MLII folder first.")

        def operation(identifier):
            directory = root / "artifacts_gui" / (datetime.now().strftime("%Y%m%d_%H%M%S_") + identifier[:6])
            command = [sys.executable, "-u", "-m", "ecg_cvd.train", "--data", str(root / "MLII"),
                       "--epochs", str(epochs), "--batch-size", str(batch), "--device", "cpu",
                       "--artifacts", str(directory), "--adversarial-epsilon", str(epsilon)]
            env = os.environ.copy()
            env.update(MPLBACKEND="Agg", OMP_NUM_THREADS="2", MKL_NUM_THREADS="2", PYTHONUNBUFFERED="1")
            log = []
            with subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True) as process:
                for line in process.stdout:
                    line = line.strip()
                    if line:
                        log.append(line)
                        patch_job(identifier, log=log[-100:])
                        match = re.match(r"epoch\s+(\d+)/", line)
                        if match:
                            patch_job(identifier, progress=min(95, int(int(match[1]) / epochs * 95)))
                returncode = process.wait()
            if returncode:
                raise RuntimeError("Training failed: " + "\n".join(log[-8:]))
            result = json.loads((directory / "metrics.json").read_text())
            result["model_id"] = (directory / "model.pt").relative_to(root).as_posix()
            return result
        return start_job(operation)

    return app


def main():
    parser = argparse.ArgumentParser(description="Run the local ECG Workbench single-page GUI.")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the workbench in your default browser")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use a port between 1024 and 65535.")
    torch.set_num_threads(2)
    app = create_app(args.project)
    url = f"http://127.0.0.1:{args.port}"
    if args.open:
        Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"ECG Workbench: {url}\nProject: {args.project.resolve()}\nPress Ctrl+C to stop.", flush=True)
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
