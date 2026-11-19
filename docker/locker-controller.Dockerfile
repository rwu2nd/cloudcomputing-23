# syntax=docker/dockerfile:1
# Locker controller image (gRPC). Build context is the repository root, e.g.:
#   docker build -f docker/locker-controller.Dockerfile -t smart-locker-controller:local .

# ---- Stage 1: generate Python stubs from the .proto contract ----
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
RUN useradd --system --uid 10001 --no-create-home appuser

COPY locker-controller/requirements.txt ./
RUN pip install -r requirements.txt

COPY locker-controller/ ./
COPY --from=proto /build/gen/ ./

USER 10001
# 50051 = gRPC, 9100 = Prometheus metrics
EXPOSE 50051 9100

CMD ["python", "-m", "controller.server"]
