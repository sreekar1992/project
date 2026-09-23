# syntax=docker/dockerfile:1
# The image deliberately contains application code only. Mount MLII recordings,
# checkpoints, and encrypted artifacts at /data instead of baking them into it.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

# The PyTorch CPU index keeps the inference image small enough for CPU-only
# Kubernetes nodes. Build a separate accelerator image for GPU training.
RUN python -m pip install --upgrade pip \
    && python -m pip install --extra-index-url https://download.pytorch.org/whl/cpu . "gunicorn==23.0.0"

RUN groupadd --gid 10001 ecg \
    && useradd --uid 10001 --gid ecg --create-home --shell /usr/sbin/nologin ecg

WORKDIR /data
USER 10001:10001

EXPOSE 8765

# One worker is intentional: the current workbench keeps model/job state in
# process and is not safe to horizontally scale until that state is externalized.
CMD ["gunicorn", "--bind=0.0.0.0:8765", "--workers=1", "--threads=4", "--timeout=120", "--worker-tmp-dir=/tmp", "--access-logfile=-", "--error-logfile=-", "ecg_cvd.wsgi:app"]
