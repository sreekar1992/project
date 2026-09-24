"""Flask application factory for the authenticated ECG health platform."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import re
import uuid

from flask import Flask, g, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from .ai_adapter import ECGModelAdapter
from .config import Settings
from .db import Database
from .security import APIError
from .services import initialize_reference_data, model_version_for
from .storage import build_storage


REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def create_app(overrides: dict | None = None) -> Flask:
    """Create the platform API without modifying the legacy `ecg-gui` app."""
    settings = Settings.from_environment(overrides)
    app = Flask("ecg_health_platform")
    app.config.from_mapping(settings.flask_config())
    app.config["TRUSTED_HOSTS"] = [host.strip() for host in os.getenv(
        "ECG_PLATFORM_TRUSTED_HOSTS", "localhost,127.0.0.1,[::1]").split(",") if host.strip()]
    app.extensions["ecg_platform_settings"] = settings

    db = Database(settings.database_url)
    db.init_app(app)
    storage = build_storage(settings)
    app.extensions["ecg_platform_storage"] = storage
    if settings.ai_model_path is not None:
        app.extensions["ecg_platform_adapter"] = ECGModelAdapter(settings.ai_model_path, settings.ai_model_version)

    # This memory limiter is intentionally a local fallback. Deployments should
    # set a shared Redis-backed limiter through the reverse proxy/application
    # configuration before handling sensitive clinical traffic.
    app.extensions["ecg_platform_limiter"] = Limiter(
        key_func=get_remote_address, app=app, default_limits=["1000 per day"], storage_uri="memory://",
    )

    @app.before_request
    def request_context():
        supplied = request.headers.get("X-Request-ID", "")
        g.request_id = supplied if REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
        origin = request.headers.get("Origin")
        if origin and origin not in settings.cors_origins:
            raise APIError("CORS_ORIGIN_DENIED", "This browser origin is not authorized for the API.", 403)
        if request.method == "OPTIONS":
            return "", 204
        return None

    @app.after_request
    def secure_headers(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", uuid.uuid4().hex)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        if not settings.development:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        origin = request.headers.get("Origin")
        if origin and origin in settings.cors_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Request-ID"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
        return response

    @app.errorhandler(APIError)
    def platform_error(error: APIError):
        return jsonify(error={"code": error.code, "message": error.message,
                              "request_id": getattr(g, "request_id", "unknown")}), error.status_code

    @app.errorhandler(ValidationError)
    def validation_error(_error: ValidationError):
        return jsonify(error={"code": "VALIDATION_ERROR", "message": "The request data is invalid.",
                              "request_id": getattr(g, "request_id", "unknown")}), 400

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException):
        code = "NOT_FOUND" if error.code == 404 else "HTTP_ERROR"
        return jsonify(error={"code": code, "message": error.description,
                              "request_id": getattr(g, "request_id", "unknown")}), error.code

    @app.errorhandler(Exception)
    def unexpected_error(error: Exception):
        # Do not put raw tracebacks, storage paths, or private data into client responses.
        app.logger.exception("Platform request failed [request_id=%s]", getattr(g, "request_id", "unknown"))
        return jsonify(error={"code": "INTERNAL_ERROR", "message": "The request could not be completed.",
                              "request_id": getattr(g, "request_id", "unknown")}), 500

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", service="ecg-health-platform")

    @app.get("/readyz")
    def readyz():
        model_ready = settings.ai_model_path is not None and settings.ai_model_path.is_file()
        if not model_ready:
            return jsonify(status="not_ready", reason="A compatible model artifact is not configured."), 503
        return jsonify(status="ready", model_configured=True)

    with app.app_context():
        # Production uses `alembic upgrade head`; local development and tests
        # remain runnable without a separately installed database server.
        if settings.development or bool((overrides or {}).get("AUTO_CREATE_SCHEMA")):
            db.create_all()
            initialize_reference_data(db.session())
        if settings.ai_model_path is not None and settings.ai_model_path.is_file():
            try:
                model_version_for(app.extensions["ecg_platform_adapter"], db.session())
                db.session().commit()
            except Exception:
                db.session().rollback()
                app.logger.warning("Configured ECG model could not be registered at startup; analysis stays unavailable.")

    from .api import create_api_blueprint
    app.register_blueprint(create_api_blueprint(app.extensions["ecg_platform_limiter"]))
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ECG health-platform API (research/CDS only).")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="Repository root containing model artifacts")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use a port from 1024 through 65535.")
    app = create_app({"PROJECT_ROOT": args.project, "ENVIRONMENT": "development"})
    logging.getLogger("werkzeug").setLevel(logging.INFO)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
