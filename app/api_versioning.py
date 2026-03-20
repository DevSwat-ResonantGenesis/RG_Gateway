"""API Versioning Support.

Provides /v1/* prefix routing and version negotiation.
"""

from fastapi import APIRouter, Request
from starlette.responses import Response


# Version 1 router - wraps all existing routes
v1_router = APIRouter(prefix="/v1", tags=["v1"])


# Version info endpoint
@v1_router.get("")
async def get_api_version():
    """Get API version information."""
    return {
        "version": "1.0.0",
        "status": "stable",
        "deprecated": False,
        "sunset_date": None,
        "documentation": "/docs",
        "changelog": "/v1/changelog",
    }


@v1_router.get("/changelog")
async def get_changelog():
    """Get API changelog."""
    return {
        "versions": [
            {
                "version": "1.0.0",
                "date": "2025-12-15",
                "changes": [
                    "Initial stable API release",
                    "Full endpoint coverage for all services",
                    "Rate limiting with per-endpoint limits",
                    "Idempotency key support",
                    "Audit logging",
                ],
            }
        ],
    }


# API version headers middleware helper
def add_version_headers(response: Response) -> Response:
    """Add API version headers to response."""
    response.headers["X-API-Version"] = "1.0.0"
    response.headers["X-API-Deprecated"] = "false"
    return response


# Supported versions
SUPPORTED_VERSIONS = {"1", "1.0", "1.0.0"}
CURRENT_VERSION = "1.0.0"


def parse_version_header(accept_version: str) -> str:
    """Parse Accept-Version header and return appropriate version."""
    if not accept_version:
        return CURRENT_VERSION
    
    # Clean and check version
    version = accept_version.strip().lstrip("v")
    
    if version in SUPPORTED_VERSIONS:
        return CURRENT_VERSION
    
    # Default to current version
    return CURRENT_VERSION
