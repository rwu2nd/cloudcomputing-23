"""Exit 0 if the local controller reports SERVING over the gRPC health protocol.

Used by the Docker Compose healthcheck (Kubernetes uses its built-in gRPC probe).
"""

import os
import sys

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc


def main() -> int:
    port = os.getenv("GRPC_PORT", "50051")
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            reply = health_pb2_grpc.HealthStub(channel).Check(
                health_pb2.HealthCheckRequest(service=""), timeout=2
            )
    except grpc.RpcError as exc:
        print(f"health check failed: {exc.code().name}", file=sys.stderr)
        return 1
    return 0 if reply.status == health_pb2.HealthCheckResponse.SERVING else 1


if __name__ == "__main__":
    sys.exit(main())
