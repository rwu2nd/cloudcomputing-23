# syntax=docker/dockerfile:1
# Backend image (FastAPI). Build context is the repository root, e.g.:
#   docker build -f docker/backend.Dockerfile -t smart-locker-backend:local .

# ---- Stage 1: generate the gRPC client stubs from the shared .proto ----
FROM python:3.12-slim AS proto
RUN pip install --no-cache-dir grpcio-tools==1.85.1
WORKDIR /build
COPY proto/ proto/
RUN mkdir gen && python -m grpc_tools.protoc -I proto \
      --python_out=gen --grpc_python_out=gen --pyi_out=gen \
      proto/smartlocker/v1/locker_controller.proto

# ---- Stage 2: runtime ----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Run as a non-root user. The numeric UID lets Kubernetes verify `runAsNonRoot`.
RUN useradd --system --uid 10001 --no-create-home appuser

# Install dependencies first so this layer is cached until requirements.txt changes.
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

# Copy the rest of the backend folder (app code, plus anything teammates add,
# e.g. migrations/). The app package is /app/app, so the entrypoint is app.main:app.
COPY backend/ ./
# Generated stubs land next to the app package: /app/smartlocker/v1/...
COPY --from=proto /build/gen/ ./

USER 10001
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
