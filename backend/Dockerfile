# syntax=docker/dockerfile:1
# Glance API image. Multi-stage: the builder installs dependencies, the runtime
# stage only carries the finished virtualenv (smaller, fewer tools for an
# attacker to use). A `test` stage (not used by compose) runs the suite on the
# exact locked versions.

# ---------- builder ----------
FROM python:3.11-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /src

# Exact versions from requirements.lock (see its header to regenerate). Only
# that file is copied first, so this ~770 MB layer is cached until it changes.
# rapidocr-onnxruntime pulls the full opencv-python, which needs libGL; the
# server never opens a window, so swap in the headless build at the same
# version and the runtime image needs no GL libraries.
COPY requirements.lock ./
RUN pip install -r requirements.lock \
    && VERSION="$(pip show opencv-python | sed -n 's/^Version: //p')" \
    && pip uninstall -y opencv-python \
    && pip install --no-deps "opencv-python-headless==$VERSION" \
    && python -c "import cv2, rapidocr_onnxruntime; print(cv2.__version__)"

# ---------- test (not used by compose) ----------
FROM builder AS test
RUN pip install "pytest>=8.3,<9.0" "pytest-asyncio>=0.24,<1.0" "respx>=0.21,<1.0"
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY tests ./tests
CMD ["python", "-m", "pytest", "-m", "not slow", "-q"]

# ---------- runtime ----------
FROM python:3.11-slim-bookworm AS runtime

# libglib2.0-0: needed by the OpenCV bindings even in the headless build.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANONYMIZED_TELEMETRY=False \
    PATH="/opt/venv/bin:$PATH"

COPY --from=builder /opt/venv /opt/venv

# Do not run as root inside the container.
RUN useradd --create-home --uid 1000 glance \
    && mkdir -p /app/data \
    && chown -R glance:glance /app
WORKDIR /app
COPY --chown=glance:glance app ./app
USER glance

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status == 200 else 1)"

# ONE worker on purpose: Chroma's PersistentClient and FastAPI BackgroundTasks
# both live inside the process, so a second worker would be a second process
# writing the same Chroma files and running its own job queue. This limit is
# temporary: it is lifted when vectors move to pgvector and jobs to a queue/worker.
# --proxy-headers: Azure Container Apps terminates HTTPS in front of the
# container; this makes uvicorn trust X-Forwarded-* so scheme/client IP are right.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]
