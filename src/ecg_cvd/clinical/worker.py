"""RQ worker entry point for asynchronous ECG analysis jobs."""
from __future__ import annotations

import argparse
from pathlib import Path
import uuid

from flask import g
from redis import Redis
from rq import Queue, Worker

from .app import create_app
from .db import get_session
from .models import AIAnalysis, BackgroundJob
from .security import APIError, Principal
from .services import ClinicalService


QUEUE_NAME = "ecg-analysis"


def enqueue_analysis(analysis_uuid: uuid.UUID, organization_id: uuid.UUID) -> str:
    """Queue work after its durable analysis row exists; no ECG payload enters Redis."""
    from .security import settings

    if not settings().redis_url:
        raise APIError("QUEUE_CONFIGURATION_ERROR", "Asynchronous analysis requires REDIS_URL.", 503)
    connection = Redis.from_url(settings().redis_url)
    job = Queue(QUEUE_NAME, connection=connection).enqueue(run_analysis_job, str(analysis_uuid), job_timeout=900,
                                                           result_ttl=3600, failure_ttl=86400)
    session = get_session()
    session.add(BackgroundJob(organization_id=organization_id, job_type="ECG_ANALYSIS", status="QUEUED",
                              related_resource_id=str(analysis_uuid), queue_job_id=job.id))
    session.commit()
    return job.id


def run_analysis_job(analysis_uuid: str) -> None:
    """RQ task invoked in a worker process with a fresh app/database session."""
    app = create_app()
    with app.app_context(), app.test_request_context("/internal/analysis-worker"):
        g.request_id = f"worker-{uuid.uuid4().hex[:16]}"
        session = get_session()
        analysis = session.get(AIAnalysis, uuid.UUID(analysis_uuid))
        if analysis is None:
            return
        job = session.query(BackgroundJob).filter_by(related_resource_id=analysis_uuid, job_type="ECG_ANALYSIS").first()
        if job:
            job.status = "PROCESSING"
            session.commit()
        # Audit events deliberately have a null user for an asynchronous system operation.
        principal = Principal(user_id=uuid.uuid4(), organization_id=analysis.organization_id,
                              roles=frozenset({"SYSTEM"}), patient_id=None)
        try:
            ClinicalService(principal).run_analysis(analysis.ai_analysis_uuid)
            if job:
                job.status = "COMPLETED"
                session.commit()
        except Exception:
            if job:
                job.status = "FAILED"
                job.error_message = "Analysis worker failed; inspect authorized operational logs."
                session.commit()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ECG analysis RQ worker.")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    args = parser.parse_args()
    app = create_app({"PROJECT_ROOT": args.project})
    with app.app_context():
        app_settings = app.extensions["ecg_platform_settings"]
        if not app_settings.redis_url:
            parser.error("REDIS_URL is required to run the asynchronous worker.")
        connection = Redis.from_url(app_settings.redis_url)
        Worker([QUEUE_NAME], connection=connection).work()


if __name__ == "__main__":
    main()
