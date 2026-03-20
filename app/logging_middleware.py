"""Logging and monitoring middleware for API Gateway."""

import json
import logging
import time
import uuid
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("gateway")


class RequestMetrics:
    """Simple in-memory metrics collector."""

    def __init__(self):
        self.total_requests = 0
        self.total_errors = 0
        self.latency_sum = 0.0
        self.status_codes = {}
        self.endpoints = {}

    def record(
        self,
        path: str,
        method: str,
        status_code: int,
        latency_ms: float,
    ):
        """Record a request."""
        self.total_requests += 1
        self.latency_sum += latency_ms

        if status_code >= 400:
            self.total_errors += 1

        # Count status codes
        self.status_codes[status_code] = self.status_codes.get(status_code, 0) + 1

        # Count endpoints
        endpoint_key = f"{method} {path}"
        if endpoint_key not in self.endpoints:
            self.endpoints[endpoint_key] = {"count": 0, "errors": 0, "latency_sum": 0}
        self.endpoints[endpoint_key]["count"] += 1
        self.endpoints[endpoint_key]["latency_sum"] += latency_ms
        if status_code >= 400:
            self.endpoints[endpoint_key]["errors"] += 1

    def get_stats(self) -> dict:
        """Get current statistics."""
        avg_latency = (
            self.latency_sum / self.total_requests if self.total_requests > 0 else 0
        )
        error_rate = (
            self.total_errors / self.total_requests if self.total_requests > 0 else 0
        )

        return {
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "error_rate": round(error_rate, 4),
            "avg_latency_ms": round(avg_latency, 2),
            "status_codes": self.status_codes,
            "top_endpoints": self._get_top_endpoints(10),
        }

    def _get_top_endpoints(self, limit: int) -> list:
        """Get top endpoints by request count."""
        sorted_endpoints = sorted(
            self.endpoints.items(),
            key=lambda x: x[1]["count"],
            reverse=True,
        )[:limit]

        return [
            {
                "endpoint": endpoint,
                "count": data["count"],
                "errors": data["errors"],
                "avg_latency_ms": round(
                    data["latency_sum"] / data["count"] if data["count"] > 0 else 0, 2
                ),
            }
            for endpoint, data in sorted_endpoints
        ]


metrics = RequestMetrics()


class LoggingMiddleware(BaseHTTPMiddleware):
    """Request logging and metrics middleware."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Generate request ID
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        # Extract request info
        method = request.method
        path = request.url.path
        client_ip = self._get_client_ip(request)
        user_id = request.headers.get("x-user-id", "-")

        # Log request
        logger.info(
            f"[{request_id}] --> {method} {path} | IP: {client_ip} | User: {user_id}"
        )

        try:
            # Process request
            response = await call_next(request)

            # Calculate latency
            latency_ms = (time.time() - start_time) * 1000

            # Record metrics
            metrics.record(path, method, response.status_code, latency_ms)

            # Log response
            logger.info(
                f"[{request_id}] <-- {response.status_code} | {latency_ms:.2f}ms"
            )

            # Add request ID header
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            metrics.record(path, method, 500, latency_ms)

            logger.error(
                f"[{request_id}] !!! Error: {str(e)} | {latency_ms:.2f}ms"
            )
            raise

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP address."""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"


def get_metrics() -> dict:
    """Get current metrics."""
    return metrics.get_stats()
