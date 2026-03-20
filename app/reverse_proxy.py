"""
Production-grade reverse proxy for Genesis2026 Gateway.

This module provides secure, isolated proxy functions for forwarding requests
to backend microservices within the Docker network.

Security Features:
- Cookie forwarding for authentication
- Header translation (RG-* to X-*)
- Internal URL filtering (prevents Docker hostnames leaking to browser)
- Hop-by-hop header filtering
"""

import os
import httpx
import logging
from fastapi import Request, Response

from .config import SERVICE_MAP

V8_GATEWAY_SECRET = os.getenv("GATEWAY_SECRET", "v8-gw-internal-2026")

logger = logging.getLogger(__name__)

# Headers that should never be forwarded (hop-by-hop headers)
HOP_BY_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding",
    "content-length"
})

# Internal Docker hostnames that should never be exposed to clients
INTERNAL_HOSTNAMES = frozenset({
    "agent_engine_service", "auth_service", "chat_service", "memory_service",
    "llm_service", "cognitive_service", "storage_service", "ide_platform_service"
})


async def proxy(service: str, path: str, request: Request) -> Response:
    """Proxy authenticated requests to backend services.
    
    Args:
        service: Service key from SERVICE_MAP (e.g., 'agents', 'auth')
        path: Target path on the backend service
        request: Incoming FastAPI request
        
    Returns:
        Response from the backend service
        
    Security:
        - Forwards authentication cookies
        - Injects user context from auth middleware
        - Filters internal URLs from responses
    """
    if service not in SERVICE_MAP:
        logger.warning(f"Unknown service requested: {service}")
        return Response(status_code=404, content=b"Unknown service")

    target = f"{SERVICE_MAP[service]}/{path}" if path else SERVICE_MAP[service]

    # Filter out host header to avoid conflicts
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    
    # Ensure cookies are forwarded (critical for auth)
    if "cookie" not in headers and request.cookies:
        cookie_str = "; ".join([f"{k}={v}" for k, v in request.cookies.items()])
        headers["cookie"] = cookie_str
    
    # Translate frontend headers to backend headers
    if "rg-org-id" in headers:
        headers["x-org-id"] = headers["rg-org-id"]
    if "rg-role" in headers:
        headers["x-user-role"] = headers["rg-role"]
    
    # Inject user context from auth middleware
    if hasattr(request.state, "user_id") and request.state.user_id:
        headers["x-user-id"] = request.state.user_id
    if hasattr(request.state, "org_id") and request.state.org_id:
        headers["x-org-id"] = request.state.org_id
    if hasattr(request.state, "role") and request.state.role:
        headers["x-user-role"] = request.state.role

    # Inject V8 gateway secret for V8 service authentication
    if service == "v8-api":
        headers["X-Gateway-Secret"] = V8_GATEWAY_SECRET

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            resp = await client.request(
                method=request.method,
                url=target,
                content=await request.body(),
                headers=headers,
                params=request.query_params,
            )
    except httpx.RequestError:
        return Response(status_code=502, content=b"Upstream service unavailable")

    set_cookie_headers = resp.headers.get_list("set-cookie")

    # Filter response headers - remove hop-by-hop and internal URLs
    response_headers = {}
    for k, v in resp.headers.items():
        if k.lower() in HOP_BY_HOP_HEADERS:
            continue
        # Preserve all Set-Cookie headers separately (multiple cookies are common)
        if k.lower() == "set-cookie":
            continue
        # Don't pass internal Docker URLs to the browser
        if k.lower() == "location":
            if any(hostname in v for hostname in INTERNAL_HOSTNAMES):
                continue
        response_headers[k] = v

    response = Response(content=resp.content, status_code=resp.status_code, headers=response_headers)
    for cookie_value in set_cookie_headers:
        response.headers.append("set-cookie", cookie_value)

    return response


async def proxy_public(service: str, path: str, request: Request) -> Response:
    """Proxy for public endpoints - no auth required."""
    if service not in SERVICE_MAP:
        return Response(status_code=404, content=b"Unknown service")

    target = f"{SERVICE_MAP[service]}/{path}" if path else SERVICE_MAP[service]

    # For public endpoints, only pass safe headers
    safe_headers = {
        "content-type": request.headers.get("content-type", "application/json"),
        "accept": request.headers.get("accept", "*/*"),
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.request(
                method=request.method,
                url=target,
                content=await request.body(),
                headers=safe_headers,
                params=request.query_params,
            )
    except httpx.RequestError:
        return Response(status_code=502, content=b"Upstream service unavailable")

    set_cookie_headers = resp.headers.get_list("set-cookie")

    response_headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower() not in ("content-encoding", "transfer-encoding", "content-length", "set-cookie")
    }

    response = Response(content=resp.content, status_code=resp.status_code, headers=response_headers)
    for cookie_value in set_cookie_headers:
        response.headers.append("set-cookie", cookie_value)

    return response
