#!/usr/bin/env python3
"""
Production Gateway - Real Auth Integration
Uses real auth service for authentication with CASCADE edge capture
"""

import sys
import os
import json
from pathlib import Path
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import time

from .reverse_proxy import proxy
# Add CASCADE edge capture
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
try:
    from cascade_edge_schema import edge_capture_decorator, capture_external_call, EdgeCapturingHTTPClient
except ImportError:
    # Fallback if cascade_edge_schema not available
    def edge_capture_decorator(service_name, operation_name):
        def decorator(func):
            return func
        return decorator
    
    def capture_external_call(*args, **kwargs):
        pass
    
    EdgeCapturingHTTPClient = httpx.Client

# Deterministic sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Single service entrypoint
app = FastAPI(
    title="Gateway Service",
    description="API Gateway for Genesis2026 - Real Auth",
    version="1.0.0",
    redirect_slashes=False  # Disable automatic 307 redirects for trailing slashes
)

# Add authentication middleware to protect all routes
from .auth_middleware import AuthMiddleware

app.add_middleware(AuthMiddleware)

# Add multi-tenant isolation middleware (Phase 4.3)
from .tenant_isolation import TenantIsolationMiddleware
app.add_middleware(TenantIsolationMiddleware)

# Add rate limiting middleware to prevent DoS attacks
from .rate_limiter import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

# Add CORS middleware - Support both production and development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # Production domains
        "https://dev-swat.com",
        "https://www.dev-swat.com",
        "https://api.dev-swat.com",
        "https://resonantgenesis.xyz",
        "https://www.resonantgenesis.xyz",
        "https://api.resonantgenesis.xyz",
        # Development domains (for local testing)
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        # HTTP versions for testing
        "http://dev-swat.com",
        "http://www.dev-swat.com",
        "http://api.dev-swat.com",
        "http://resonantgenesis.xyz",
        "http://www.resonantgenesis.xyz",
        "http://api.resonantgenesis.xyz",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With", "Accept", "Origin", "X-API-Key"],
)

# Add essential service routes BEFORE other routers to ensure they're registered first

# Memory service routes
@app.api_route("/api/v1/memory/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
@edge_capture_decorator("gateway", "memory_proxy")
async def memory_proxy_v1(request: Request, path: str):
    """Proxy memory service requests"""
    memory_service_url = "http://memory_service:8000"
    
    # Build the full URL
    url = f"{memory_service_url}/memory/{path}"
    
    # Get request data
    headers = dict(request.headers)
    headers.pop("host", None)

    # Inject user context from auth middleware (critical for per-user data)
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        headers["x-user-id"] = user_id
    org_id = getattr(request.state, "org_id", None)
    if org_id:
        headers["x-org-id"] = org_id
    role = getattr(request.state, "role", None)
    if role:
        headers["x-user-role"] = role
    plan = getattr(request.state, "plan", None)
    if plan:
        headers["x-plan"] = plan
    
    # Make request to memory service
    try:
        async with httpx.AsyncClient() as http_client:
            if request.method == "GET":
                response = await http_client.get(url, headers=headers, params=request.query_params)
            elif request.method == "POST":
                body = await request.body()
                response = await http_client.post(url, headers=headers, content=body)
            elif request.method == "PUT":
                body = await request.body()
                response = await http_client.put(url, headers=headers, content=body)
            elif request.method == "DELETE":
                response = await http_client.delete(url, headers=headers)
            else:
                raise HTTPException(status_code=405, detail="Method not allowed")
        
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers)
        )
    except Exception as e:
        return {"error": f"Memory service unavailable: {str(e)}"}, 503

# Memory service health endpoint
@app.get("/api/v1/memory/health")
@edge_capture_decorator("gateway", "memory_health")
async def memory_health():
    """Memory service health through gateway"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://memory_service:8000/health")
            return response.json()
    except Exception as e:
        return {"error": f"Memory service unavailable: {str(e)}"}, 503

# Owner authentication routes - proxy to auth_service
@app.api_route("/owner/auth/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
@edge_capture_decorator("gateway", "owner_auth_proxy")
async def owner_auth_proxy(request: Request, path: str):
    """Proxy owner authentication requests to auth_service"""
    # Use deployment color prefix for service name
    deployment_color = os.getenv("DEPLOYMENT_COLOR", "")
    auth_service_url = f"http://auth_service:8000" if not deployment_color else f"http://{deployment_color}_auth_service:8000"
    url = f"{auth_service_url}/owner/auth/{path}"
    
    headers = dict(request.headers)
    headers.pop("host", None)
    
    try:
        async with httpx.AsyncClient() as http_client:
            if request.method == "GET":
                response = await http_client.get(url, headers=headers, params=request.query_params)
            elif request.method == "POST":
                body = await request.body()
                response = await http_client.post(url, headers=headers, content=body)
            elif request.method == "PUT":
                body = await request.body()
                response = await http_client.put(url, headers=headers, content=body)
            elif request.method == "DELETE":
                response = await http_client.delete(url, headers=headers)
            else:
                raise HTTPException(status_code=405, detail="Method not allowed")
        
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers)
        )
    except Exception as e:
        return Response(
            content=json.dumps({"error": f"Auth service unavailable: {str(e)}"}),
            status_code=503,
            media_type="application/json"
        )

# Chat service routes
@app.api_route("/api/v1/chat/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
@edge_capture_decorator("gateway", "chat_proxy")
async def chat_proxy(request: Request, path: str):
    """Proxy chat service requests"""
    chat_service_url = "http://chat_service:8000"
    
    # Build the full URL
    url = f"{chat_service_url}/{path}"
    
    # Get request data
    headers = dict(request.headers)
    headers.pop("host", None)
    
    # Use HTTP client
    http_client = httpx.AsyncClient()
    
    # Make request to chat service
    try:
        if request.method == "GET":
            response = await http_client.get(url, headers=headers, params=request.query_params)
        elif request.method == "POST":
            body = await request.body()
            response = await http_client.post(url, headers=headers, content=body)
        elif request.method == "PUT":
            body = await request.body()
            response = await http_client.put(url, headers=headers, content=body)
        elif request.method == "DELETE":
            response = await http_client.delete(url, headers=headers)
        else:
            raise HTTPException(status_code=405, detail="Method not allowed")
        
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers)
        )
    except Exception as e:
        return {"error": f"Chat service unavailable: {str(e)}"}, 503

# IDE completions streaming proxy — must be BEFORE chat proxy to avoid buffering
@app.api_route("/api/v1/ide/{path:path}", methods=["POST"])
@edge_capture_decorator("gateway", "ide_proxy")
async def ide_proxy(request: Request, path: str):
    """Streaming proxy for local IDE completions → chat_service /ide/*"""
    import asyncio

    chat_service_url = "http://chat_service:8000"
    url = f"{chat_service_url}/ide/{path}"

    headers = dict(request.headers)
    headers.pop("host", None)
    body = await request.body()

    async def stream_from_backend():
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST", url, headers=headers, content=body,
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        stream_from_backend(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

# Chat service health endpoint
@app.get("/api/v1/chat/health")
@edge_capture_decorator("gateway", "chat_health")
async def chat_health():
    """Chat service health through gateway"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://chat_service:8000/health")
            return response.json()
    except Exception as e:
        return {"error": f"Chat service unavailable: {str(e)}"}, 503

@app.api_route("/scan/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@app.api_route("/api/v1/scan/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "ast_analysis_scan_proxy")
async def ast_analysis_scan_proxy(request: Request, path: str):
    """Proxy scan requests to standalone RG AST Analysis service."""
    def _build_base_urls() -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()

        def add(url: str) -> None:
            u = (url or "").strip().rstrip("/")
            if not u or u in seen:
                return
            seen.add(u)
            urls.append(u)

        # Standalone RG AST Analysis service (preferred)
        add(os.getenv("AST_ANALYSIS_SERVICE_URL") or "")
        # Legacy env vars (fallback)
        add(os.getenv("GATEWAY_CODE_VISUALIZER_URL") or "")
        add(os.getenv("CODE_VISUALIZER_URL") or "")

        # Docker service hostname
        hosts: list[str] = [
            "rg_ast_analysis",
        ]

        for host in hosts:
            add(f"http://{host}:8000")

        return urls

    base_urls = _build_base_urls()

    async with httpx.AsyncClient() as client:
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

        # Ensure identity headers are present for downstream AST Analysis service.
        if "x-user-id" not in {k.lower() for k in headers.keys()} and getattr(request.state, "user_id", None):
            headers["x-user-id"] = str(request.state.user_id)
        if "x-org-id" not in {k.lower() for k in headers.keys()} and getattr(request.state, "org_id", None):
            headers["x-org-id"] = str(request.state.org_id)
        if "x-user-role" not in {k.lower() for k in headers.keys()} and getattr(request.state, "role", None):
            headers["x-user-role"] = str(request.state.role)
        if "x-user-plan" not in {k.lower() for k in headers.keys()} and getattr(request.state, "plan", None):
            headers["x-user-plan"] = str(request.state.plan)

        secret = (os.getenv("AST_ANALYSIS_GATEWAY_SECRET") or os.getenv("CODE_VISUALIZER_GATEWAY_SECRET") or "").strip()
        if secret:
            headers["x-ast-analysis-gateway-secret"] = secret
            headers["x-code-visualizer-gateway-secret"] = secret

        body = await request.body()

        last_exc = None
        for base_url in base_urls:
            try:
                resp = await client.request(
                    request.method,
                    f"{base_url}/api/v1/scan/{path}",
                    params=request.query_params,
                    content=body if body else None,
                    headers=headers,
                    timeout=900.0,
                )
                return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
            except Exception as e:
                last_exc = e
                continue

        raise HTTPException(status_code=503, detail=f"AST Analysis service unavailable: {last_exc}")

# Import and include all API routers (AFTER our essential routes)
from .code_routes import router as code_router
from .terminal_routes import router as terminal_router
from .anchors_routes import router as anchors_router
from .policies_routes import router as policies_router
from .state_physics_routes import router as state_physics_router
from .state_physics_api_v1 import router as state_physics_api_v1_router
from .code_visualizer_routes import router as code_visualizer_router
from .rara_routes import router as rara_router
from .node_routes import router as node_router
from .routers import router as api_router
from .usage_routes import router as usage_router
from .predictions_routes import router as predictions_router
from .git_routes import github_router, git_router

# Include code routes FIRST (before catch-all router)
app.include_router(github_router, prefix="/api/v1", tags=["github"])
app.include_router(git_router, prefix="/api/v1", tags=["git"])
app.include_router(code_router, prefix="/api/v1", tags=["code"])
app.include_router(terminal_router, prefix="/api/v1/terminal", tags=["terminal"])
app.include_router(anchors_router, prefix="/api/v1/anchors", tags=["anchors"])
app.include_router(policies_router, prefix="/api/v1/policies", tags=["policies"])
app.include_router(rara_router, prefix="/api/v1")
app.include_router(node_router, prefix="/api/v1")

# State Physics UI + API (must be before catch-all)
app.include_router(state_physics_router)
app.include_router(state_physics_api_v1_router)

# AST Analysis UI + API (formerly Code Visualizer, now standalone rg_ast_analysis)
app.include_router(code_visualizer_router)

# Predictions routes (HashSphere → V8 Engine proxy, must be before catch-all)
app.include_router(predictions_router, tags=["predictions"])

# Include catch-all router LAST
app.include_router(api_router, prefix="/api/v1", tags=["api"])
app.include_router(usage_router)

# Catch-all removed - specific routers handle all routes

# ============================================
# AUTH SETTINGS AGENTS ROUTES (Legacy frontend compatibility)
# ============================================
# Frontend calls /auth/settings/agents/* but these are now in agent_engine_service
# This route proxies to agent_engine_service for backward compatibility

from .reverse_proxy import proxy

@app.api_route("/auth/settings/agents/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "auth_settings_agents_proxy")
async def auth_settings_agents_proxy(request: Request, path: str):
    """Proxy /auth/settings/agents/* to agent_engine_service."""
    return await proxy("agents", f"agents/settings/{path}", request)


@app.api_route("/autonomy/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "autonomy_root_proxy")
async def autonomy_root_proxy(request: Request, path: str):
    """Proxy /autonomy/* (no /api/v1 prefix) to agent_engine_service.

    Frontend Monitor panel calls these routes directly.
    """
    return await proxy("agents", f"autonomy/{path}", request)


@app.api_route("/autonomy", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "autonomy_root_base")
async def autonomy_root_base_proxy(request: Request):
    """Proxy base /autonomy (no /api/v1 prefix) to agent_engine_service."""
    return await proxy("agents", "autonomy", request)


@app.api_route("/auth/settings/agents", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "auth_settings_agents_base")
async def auth_settings_agents_base_proxy(request: Request):
    """Proxy base /auth/settings/agents to agent_engine_service."""
    return await proxy("agents", "agents/settings/", request)


# Storage backward-compat route (/api/storage/... used by rabbit post image_urls)
@app.api_route("/api/storage/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def api_storage_compat_route(path: str, request: Request):
    """Backward-compat: proxy /api/storage/* to storage service."""
    return await proxy("storage", f"storage/{path}", request)


# Auth service health proxy
@app.get("/auth/health")
@edge_capture_decorator("gateway", "auth_health")
async def auth_health():
    """Auth service health through gateway"""
    try:
        # Get deployment color from environment
        deployment_color = os.getenv("DEPLOYMENT_COLOR", "")
        auth_service_name = f"{deployment_color}_auth_service"
        auth_url = f"http://{auth_service_name}:8000"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{auth_url}/health")
            return response.json()
    except Exception as e:
        return {"error": f"Auth service unavailable: {str(e)}"}, 503

# User service health proxy
@app.get("/user/health")
@edge_capture_decorator("gateway", "user_health")
async def user_health():
    """User service health through gateway"""
    try:
        # Get deployment color from environment
        deployment_color = os.getenv("DEPLOYMENT_COLOR", "")
        user_service_name = f"{deployment_color}_user_service"
        user_url = f"http://{user_service_name}:8000"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{user_url}/health")
            return response.json()
    except Exception as e:
        return {"error": f"User service unavailable: {str(e)}"}, 503

# Health check endpoint
@app.get("/health")
@edge_capture_decorator("gateway", "health")
async def health_check():
    return {"status": "healthy", "service": "gateway"}

# Root endpoint
@app.get("/")
@edge_capture_decorator("gateway", "root")
async def root():
    return {"message": "Genesis2026 Gateway - Production Ready"}

# Revocation management endpoints
@app.post("/admin/revoke/user/{user_id}")
async def revoke_user_tokens(user_id: str, reason: str = "Security policy"):
    """Revoke all tokens for a user."""
    from .revocation_manager_redis import revocation_manager
    await revocation_manager.revoke_user_tokens(user_id, reason, "admin")
    return {"message": f"All tokens revoked for user {user_id}"}

@app.post("/admin/revoke/org/{org_id}")
async def revoke_org_tokens(org_id: str, reason: str = "Security policy"):
    """Revoke all tokens for an organization."""
    from .revocation_manager_redis import revocation_manager
    await revocation_manager.revoke_org_tokens(org_id, reason, "admin")
    return {"message": f"All tokens revoked for organization {org_id}"}

@app.post("/admin/revoke/role/{role}")
async def revoke_role_tokens(role: str, reason: str = "Security policy"):
    """Revoke all tokens for a role."""
    from .revocation_manager_redis import revocation_manager
    await revocation_manager.revoke_role_tokens(role, reason, "admin")
    return {"message": f"All tokens revoked for role {role}"}

@app.post("/admin/revoke/all")
async def revoke_all_tokens(reason: str = "Security policy"):
    """Revoke all tokens globally."""
    from .revocation_manager_redis import revocation_manager
    await revocation_manager.revoke_all_tokens(reason, "admin")
    return {"message": "All tokens revoked globally"}

@app.get("/admin/test")
async def admin_test():
    """Simple admin test endpoint."""
    return {"message": "Admin endpoint working"}

@app.get("/admin/revocation/status")
async def get_revocation_status():
    """Get current revocation status."""
    from .revocation_manager_redis import revocation_manager
    return await revocation_manager.get_revocation_status()

@app.get("/debug/middleware")
async def debug_middleware(request: Request):
    """Debug endpoint to test middleware."""
    return {
        "headers": dict(request.headers),
        "method": request.method,
        "url": str(request.url),
        "client": {
            "host": request.client.host if request.client else None,
            "port": request.client.port if request.client else None
        }
    }

# Legacy agents proxy (no /api/v1 prefix) - for frontend compatibility
@app.api_route("/agents/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "agents_proxy_legacy")
async def agents_proxy_legacy(path: str, request: Request):
    """Proxy agents requests without /api/v1 prefix for frontend compatibility."""
    return await proxy("agents", f"agents/{path}", request)


@app.api_route("/agents", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "agents_base_proxy_legacy")
async def agents_base_proxy_legacy(request: Request):
    """Proxy base /agents requests without /api/v1 prefix for frontend compatibility."""
    return await proxy("agents", "agents/", request)


# Legacy billing proxy (no /api/v1 prefix)
@app.api_route("/billing/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "billing_proxy_legacy")
async def billing_proxy_legacy(path: str, request: Request):
    """Proxy billing requests without /api/v1 prefix for legacy clients."""
    return await proxy("billing-user", f"billing/{path}", request)


# Legacy resonant-chat proxy (no /api/v1 prefix) - for frontend compatibility
@app.api_route("/resonant-chat/analytics", methods=["GET", "POST", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "resonant_chat_analytics_proxy_legacy")
async def resonant_chat_analytics_proxy_legacy(request: Request):
    """Proxy resonant-chat analytics to chat service analytics endpoint."""
    return await proxy("chat", "analytics", request)



@app.api_route("/resonant-chat/message/stream", methods=["POST", "OPTIONS"])
@edge_capture_decorator("gateway", "resonant_chat_stream_proxy")
async def resonant_chat_stream_proxy(request: Request):
    """SSE streaming proxy for resonant-chat message streaming."""
    chat_service_url = "http://chat_service:8000"
    url = f"{chat_service_url}/resonant-chat/message/stream"

    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    if hasattr(request.state, "user_id") and request.state.user_id:
        headers["x-user-id"] = request.state.user_id
    if hasattr(request.state, "org_id") and request.state.org_id:
        headers["x-org-id"] = request.state.org_id
    if hasattr(request.state, "role") and request.state.role:
        headers["x-user-role"] = request.state.role

    body = await request.body()

    async def stream_from_chat():
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST", url, headers=headers, content=body,
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        stream_from_chat(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.api_route("/resonant-chat/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "resonant_chat_proxy_legacy")
async def resonant_chat_proxy_legacy(path: str, request: Request):
    """Proxy resonant-chat requests without /api/v1 prefix for frontend compatibility."""
    return await proxy("chat", f"resonant-chat/{path}", request)

# Skills API proxy - routes to chat_service skills router
@app.api_route("/skills/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "skills_proxy")
async def skills_proxy(path: str, request: Request):
    """Proxy skills requests to chat service."""
    return await proxy("chat", f"skills/{path}", request)

@app.api_route("/api/skills/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "api_skills_proxy")
async def api_skills_proxy(path: str, request: Request):
    """Proxy /api/skills requests to chat service."""
    return await proxy("chat", f"skills/{path}", request)

# Build service proxy
@app.api_route("/api/v1/project-builder/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "project_builder_proxy")
async def project_builder_proxy(path: str, request: Request):
    """Proxy project-builder requests to build service."""
    return await proxy("build", f"project-builder/{path}", request)

@app.api_route("/api/v1/project-builder", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "project_builder_base_proxy")
async def project_builder_base_proxy(request: Request):
    """Proxy base project-builder requests."""
    return await proxy("build", "project-builder/", request)


# API chat proxy - for Vite proxy compatibility (/api/chat/resonant-chat/...)
@app.api_route("/api/chat/resonant-chat/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "api_chat_resonant_proxy")
async def api_chat_resonant_proxy(path: str, request: Request):
    """Proxy /api/chat/resonant-chat requests for Vite proxy compatibility."""
    return await proxy("chat", f"resonant-chat/{path}", request)


# API resonant-chat proxy - for frontend /api/resonant-chat/... calls
@app.api_route("/api/resonant-chat/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "api_resonant_chat_proxy")
async def api_resonant_chat_proxy(path: str, request: Request):
    """Proxy /api/resonant-chat requests for frontend compatibility."""
    return await proxy("chat", f"resonant-chat/{path}", request)


# Analytics routes - proxy to chat service
# Note: chat service analytics router has prefix="/analytics", so we proxy directly
@app.api_route("/analytics", methods=["GET", "OPTIONS"])
@app.api_route("/analytics/{path:path}", methods=["GET", "POST", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "analytics_proxy")
async def analytics_proxy(request: Request, path: str = ""):
    """Proxy analytics requests to chat service."""
    # Chat service has /analytics prefix in router, so we pass the full path
    return await proxy("chat", f"analytics/{path}" if path else "analytics", request)


# API Analytics routes - for /api/analytics calls from frontend
@app.api_route("/api/analytics", methods=["GET", "OPTIONS"])
@app.api_route("/api/analytics/{path:path}", methods=["GET", "POST", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "api_analytics_proxy")
async def api_analytics_proxy(request: Request, path: str = ""):
    """Proxy /api/analytics requests to chat service."""
    return await proxy("chat", f"analytics/{path}" if path else "analytics", request)


# Conversations routes - proxy to chat service
@app.api_route("/conversations", methods=["GET", "POST", "DELETE", "OPTIONS"])
@app.api_route("/conversations/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "conversations_proxy")
async def conversations_proxy(request: Request, path: str = ""):
    """Proxy conversations requests to chat service."""
    return await proxy("chat", f"resonant-chat/conversations/{path}" if path else "resonant-chat/conversations", request)


# Message routes - proxy to chat service
@app.api_route("/message", methods=["POST", "OPTIONS"])
@app.api_route("/message/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "message_proxy")
async def message_proxy(request: Request, path: str = ""):
    """Proxy message requests to chat service."""
    return await proxy("chat", f"resonant-chat/message/{path}" if path else "resonant-chat/message", request)


# Legacy chat proxy (no /api/v1 prefix)
@app.api_route("/chat/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "chat_proxy_legacy")
async def chat_proxy_legacy(path: str, request: Request):
    """Proxy chat requests without /api/v1 prefix for frontend compatibility."""
    return await proxy("chat", f"resonant-chat/{path}", request)


# OAuth proxy (no /api/v1 prefix) - routes to auth service
@app.api_route("/oauth/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "oauth_proxy")
async def oauth_proxy(path: str, request: Request):
    """Proxy OAuth requests to auth service."""
    return await proxy("auth", f"oauth/{path}", request)


# Public auth endpoints (signup, login) - no auth required
@app.api_route("/v1/public/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "public_auth_proxy")
async def public_auth_proxy(path: str, request: Request):
    """Proxy public auth requests (signup, login) to auth service."""
    return await proxy("auth", f"v1/public/{path}", request)


# Legacy auth proxy (no /api/v1 prefix)
@app.api_route("/auth/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "auth_proxy_legacy")
async def auth_proxy_legacy(path: str, request: Request):
    """Proxy auth requests without /api/v1 prefix for frontend compatibility."""
    return await proxy("auth", f"auth/{path}", request)


# Legacy usage proxy (no /api/v1 prefix) - routes to billing service
@app.api_route("/usage/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "usage_proxy_legacy")
async def usage_proxy_legacy(path: str, request: Request):
    """Proxy usage requests without /api/v1 prefix for frontend compatibility."""
    return await proxy("billing-user", f"billing/usage/{path}", request)


# Legacy RAG proxy (no /api/v1 prefix) - routes to memory service
@app.api_route("/rag/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "rag_proxy_legacy")
async def rag_proxy_legacy(path: str, request: Request):
    """Proxy RAG requests without /api/v1 prefix for frontend compatibility."""
    return await proxy("memory", f"memory/rag/{path}", request)


# User API Keys (BYOK) - proxy to auth_service
# Frontend calls /user/api-keys but auth_service serves /auth/user/api-keys
@app.api_route("/user/api-keys/validate", methods=["POST", "OPTIONS"])
@edge_capture_decorator("gateway", "user_api_keys_validate_proxy")
async def user_api_keys_validate_proxy(request: Request):
    """Proxy user API key validation to auth_service."""
    return await proxy("auth", "auth/user/api-keys/validate", request)


@app.api_route("/user/api-keys/by-provider/{provider}", methods=["DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "user_api_keys_delete_by_provider_proxy")
async def user_api_keys_delete_by_provider_proxy(request: Request, provider: str):
    """Proxy user API key deletion by provider name to auth_service."""
    return await proxy("auth", f"auth/user/api-keys/by-provider/{provider}", request)


@app.api_route("/user/api-keys/{key_id}", methods=["DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "user_api_keys_delete_proxy")
async def user_api_keys_delete_proxy(request: Request, key_id: str):
    """Proxy user API key deletion to auth_service."""
    return await proxy("auth", f"auth/user/api-keys/{key_id}", request)


@app.api_route("/user/api-keys", methods=["GET", "POST", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "user_api_keys_proxy")
async def user_api_keys_proxy(request: Request):
    """Proxy user API keys CRUD to auth_service."""
    return await proxy("auth", "auth/user/api-keys", request)


# Legacy user preferences proxy
@app.api_route("/preferences", methods=["GET", "POST", "PUT", "OPTIONS"])
@app.api_route("/preferences/{path:path}", methods=["GET", "POST", "PUT", "OPTIONS"])
@edge_capture_decorator("gateway", "preferences_proxy")
async def preferences_proxy(request: Request, path: str = ""):
    """Proxy preferences requests to user_service."""
    return await proxy("user", f"preferences/{path}" if path else "preferences", request)


# Agent teams - proxy to agent_engine_service
@app.api_route("/agent-teams", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@app.api_route("/agent-teams/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "agent_teams_proxy")
async def agent_teams_proxy(request: Request, path: str = ""):
    """Proxy agent-teams requests to agent_engine_service."""
    return await proxy("agent_engine", f"agent-teams/{path}" if path else "agent-teams", request)


# Hash Sphere endpoints - proxy to memory_service
@app.api_route("/hash-sphere/stats", methods=["GET", "OPTIONS"])
@edge_capture_decorator("gateway", "hash_sphere_stats")
async def hash_sphere_stats(request: Request):
    """Hash Sphere stats - proxy to memory_service."""
    return await proxy("memory", "memory/stats", request)


@app.api_route("/hash-sphere/search", methods=["GET", "POST", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "hash_sphere_search")
async def hash_sphere_search(request: Request):
    """Hash Sphere search - proxy to memory_service."""
    return await proxy("memory", "memory/hash-sphere/search", request)


@app.api_route("/hash-sphere/anchors", methods=["GET", "POST", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "hash_sphere_anchors")
async def hash_sphere_anchors(request: Request):
    """Hash Sphere anchors - proxy to memory_service."""
    return await proxy("memory", "memory/hash-sphere/anchors", request)


# Legacy agents endpoints - REMOVED: agent_engine_service is now running
# Routes are handled by routers.py which proxies to agent_engine_service


# ============================================
# REAL PROXY ENDPOINTS - Phase 3: Replace all stubs
# ============================================

# User preferences - proxy to user_service
@app.api_route("/user/preferences", methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"])
@app.api_route("/user/preferences/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "user_preferences_proxy")
async def user_preferences_proxy(request: Request, path: str = ""):
    """Proxy user preferences to user_service."""
    return await proxy("user", f"preferences/{path}" if path else "preferences", request)


# Autonomy endpoints - proxy to agent_engine_service
@app.api_route("/autonomy/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "autonomy_proxy")
async def autonomy_proxy(request: Request, path: str):
    """Proxy autonomy requests to agent_engine_service."""
    return await proxy("agent_engine", f"autonomy/{path}", request)


# Memory endpoints - proxy to memory_service
@app.api_route("/memory/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "memory_proxy")
async def memory_proxy(request: Request, path: str):
    """Proxy memory requests to memory_service."""
    return await proxy("memory", f"memory/{path}", request)


# ML endpoints - proxy to ml_service
@app.api_route("/ml/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "ml_proxy")
async def ml_proxy(request: Request, path: str):
    """Proxy ML requests to ml_service."""
    return await proxy("ml", path, request)


# Marketplace endpoints - proxy to marketplace_service
@app.api_route("/marketplace/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "marketplace_proxy")
async def marketplace_proxy(request: Request, path: str):
    """Proxy marketplace requests to marketplace_service."""
    return await proxy("marketplace", path, request)


# Execution endpoints - proxy to agent_engine_service
@app.api_route("/execution", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@app.api_route("/execution/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@edge_capture_decorator("gateway", "execution_proxy")
async def execution_proxy(request: Request, path: str = ""):
    """Proxy execution requests to agent_engine_service."""
    if path:
        return await proxy("agents", f"execution/{path}", request)
    return await proxy("agents", "execution", request)

# Agentic Chat SSE streaming - proxy to standalone rg_agentic_chat service
@app.api_route("/api/v1/agentic-chat/{path:path}", methods=["GET", "POST", "DELETE", "OPTIONS"])
@app.api_route("/agentic-chat/{path:path}", methods=["GET", "POST", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "agentic_chat_proxy")
async def agentic_chat_proxy(request: Request, path: str):
    """SSE streaming proxy for agentic chat to standalone RG_Registered_Users_Agentic_Chat service."""
    from fastapi.responses import StreamingResponse
    import httpx

    agentic_chat_url = os.environ.get("AGENTIC_CHAT_SERVICE_URL", "http://rg_agentic_chat:8000")
    target = f"{agentic_chat_url}/agentic-chat/{path}"

    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    if hasattr(request.state, "user_id") and request.state.user_id:
        headers["x-user-id"] = str(request.state.user_id)
    if hasattr(request.state, "org_id") and request.state.org_id:
        headers["x-org-id"] = str(request.state.org_id)
    if hasattr(request.state, "role") and request.state.role:
        headers["x-user-role"] = str(request.state.role)
    if hasattr(request.state, "is_superuser"):
        headers["x-is-superuser"] = "true" if request.state.is_superuser else "false"
    if hasattr(request.state, "unlimited_credits"):
        headers["x-unlimited-credits"] = "true" if request.state.unlimited_credits else "false"
    if hasattr(request.state, "plan") and request.state.plan:
        headers["x-user-plan"] = str(request.state.plan)

    body = await request.body()

    async def _stream_sse():
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                method=request.method,
                url=target,
                content=body,
                headers=headers,
                params=request.query_params,
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        _stream_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Public Guest Agentic Chat — proxy to standalone rg_public_guest_chat service
@app.api_route("/api/v1/public/agentic-chat/{path:path}", methods=["GET", "POST", "OPTIONS"])
@app.api_route("/public/agentic-chat/{path:path}", methods=["GET", "POST", "OPTIONS"])
@edge_capture_decorator("gateway", "public_guest_chat_proxy")
async def public_guest_chat_proxy(request: Request, path: str):
    """SSE streaming proxy for public guest chat to standalone RG_Public-Guest-Agentic_Chat service."""
    from fastapi.responses import StreamingResponse
    import httpx

    guest_chat_url = os.environ.get("GUEST_CHAT_SERVICE_URL", "http://rg_public_guest_chat:8010")
    target = f"{guest_chat_url}/public/agentic-chat/{path}"

    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    body = await request.body()

    if request.method == "GET":
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(target, headers=headers, params=request.query_params)
            try:
                return JSONResponse(content=resp.json(), status_code=resp.status_code)
            except Exception:
                from fastapi.responses import Response
                return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type", "application/json"))

    async def _stream_sse():
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                method=request.method,
                url=target,
                content=body,
                headers=headers,
                params=request.query_params,
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        _stream_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Workflow endpoints - proxy to workflow_service
@app.api_route("/workflow/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "workflow_proxy")
async def workflow_proxy(request: Request, path: str):
    """Proxy workflow requests to workflow_service."""
    return await proxy("workflow", path, request)


# Admin endpoints - proxy to appropriate service based on path
@app.api_route("/admin/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "admin_proxy")
async def admin_proxy(request: Request, path: str):
    """Proxy admin requests to user_service (handles admin operations)."""
    return await proxy("user", f"admin/{path}", request)


# Blockchain endpoints - proxy to blockchain_service
@app.api_route("/blockchain/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "blockchain_proxy")
async def blockchain_proxy(request: Request, path: str):
    """Proxy blockchain requests to blockchain_service."""
    return await proxy("blockchain", f"blockchain/{path}", request)


# Audit endpoints - proxy to blockchain_service
@app.api_route("/audit/{path:path}", methods=["GET", "POST", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "audit_proxy")
async def audit_proxy(request: Request, path: str):
    """Proxy audit requests to blockchain_service."""
    return await proxy("blockchain", f"audit/{path}", request)


# Policies endpoint - proxy to user_service or cognitive_service
@app.api_route("/policies", methods=["GET", "OPTIONS"])
@app.api_route("/policies/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "policies_proxy")
async def policies_proxy(request: Request, path: str = ""):
    """Proxy policies requests to cognitive_service."""
    return await proxy("cognitive", f"policies/{path}" if path else "policies", request)


# Organizations endpoints - proxy to user_service
@app.api_route("/orgs", methods=["GET", "POST", "DELETE", "OPTIONS"])
@app.api_route("/orgs/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "orgs_proxy")
async def orgs_proxy(request: Request, path: str = ""):
    """Proxy organization requests to user_service."""
    return await proxy("user", f"orgs/{path}" if path else "orgs", request)


# Users endpoints - proxy to user_service
@app.api_route("/users", methods=["GET", "OPTIONS"])
@app.api_route("/users/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "users_proxy")
async def users_proxy(request: Request, path: str = ""):
    """Proxy users requests to user_service."""
    return await proxy("user", f"users/{path}" if path else "users", request)


# Terminal endpoints - proxy to ide_platform_service
@app.api_route("/terminal/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "terminal_proxy")
async def terminal_proxy(request: Request, path: str):
    """Proxy terminal requests to ide_platform_service."""
    return await proxy("ide", f"terminal/{path}", request)


# IDE LOC tracking - proxy to ide_platform_service
@app.api_route("/api/v1/ide/loc/{path:path}", methods=["GET", "POST", "OPTIONS"])
@edge_capture_decorator("gateway", "ide_loc_proxy")
async def ide_loc_proxy(request: Request, path: str):
    """Proxy IDE LOC tracking requests to ide_platform_service."""
    return await proxy("ide", f"loc/{path}", request)


# IDE updates - proxy to ide_platform_service
@app.api_route("/api/v1/ide/updates/{path:path}", methods=["GET", "OPTIONS"])
@edge_capture_decorator("gateway", "ide_updates_proxy")
async def ide_updates_proxy(request: Request, path: str):
    """Proxy IDE update check requests to ide_platform_service."""
    return await proxy("ide", f"updates/{path}", request)


# AI endpoints - proxy to llm_service
@app.api_route("/ai/{path:path}", methods=["GET", "POST", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "ai_proxy")
async def ai_proxy(request: Request, path: str):
    """Proxy AI requests to llm_service."""
    return await proxy("llm", f"ai/{path}", request)


# Advanced blockchain endpoints - proxy to blockchain_service
@app.api_route("/advanced/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "advanced_proxy")
async def advanced_proxy(request: Request, path: str):
    """Proxy advanced blockchain requests to blockchain_service."""
    return await proxy("blockchain", f"advanced/{path}", request)


# Settings endpoints - proxy to user_service
@app.api_route("/settings/{path:path}", methods=["GET", "POST", "PUT", "OPTIONS"])
@edge_capture_decorator("gateway", "settings_proxy")
async def settings_proxy(request: Request, path: str):
    """Proxy settings requests to user_service."""
    return await proxy("user", f"settings/{path}", request)


# Public endpoints - proxy to appropriate services
@app.api_route("/public/{path:path}", methods=["GET", "POST", "DELETE", "OPTIONS"])
@edge_capture_decorator("gateway", "public_proxy")
async def public_proxy(request: Request, path: str):
    """Proxy public requests to appropriate service."""
    # Hash sphere public endpoints go to memory_service
    if "hash-sphere" in path:
        return await proxy("memory", f"public/{path}", request)
    # Default to user_service for other public endpoints
    return await proxy("user", f"public/{path}", request)


# Code routes - project builder generate + templates
from .code_routes import router as code_router
app.include_router(code_router, prefix="/api/v1")

# Local LLM endpoints - direct integration with Ollama
from .api.v1.endpoints.local_llm import router as local_llm_router
app.include_router(local_llm_router, prefix="/api/v1/local-llm", tags=["Local LLM"])

# Agent Chat endpoints - Knowledge Daemon for admin agent communication
from .api.v1.endpoints.agent_chat import router as agent_chat_router
app.include_router(agent_chat_router, prefix="/api/v1/admin/agent-chat", tags=["Agent Chat"])

# Owner Dashboard - Daemon Control Routes
from .daemon_routes import router as daemon_router
app.include_router(daemon_router)

# Owner Dashboard - System Metrics Routes
from .system_routes import router as system_router
app.include_router(system_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

# ============================================
# OWNER DASHBOARD RARA STATUS
# ============================================
@app.get("/owner/dashboard/rara/status")
@edge_capture_decorator("gateway", "owner_dashboard_rara_status")
async def owner_dashboard_rara_status(request: Request):
    """Owner dashboard RARA status endpoint - public access."""
    import httpx
    rara_url = os.getenv('RARA_SERVICE_URL', 'http://rg_internal_invarients_sim:8093')
    try:
        async with httpx.AsyncClient() as client:
            health_resp = await client.get(f"{rara_url}/health", timeout=10.0)
            status_resp = await client.get(f"{rara_url}/status", timeout=10.0)
            health_data = health_resp.json()
            status_data = status_resp.json()
            
            return {
                'running': health_data.get('status') == 'ok',
                'online': health_data.get('status') == 'ok',
                'status': health_data.get('status', 'unknown'),
                'service': health_data.get('service', 'rara'),
                'verified_agents': status_data.get('agents_registered', 0),
                'state': status_data.get('state', 'unknown'),
                'uptime_seconds': status_data.get('uptime_seconds', 0)
            }
    except Exception as e:
        return {
            'running': False,
            'online': False,
            'status': 'offline',
            'error': str(e),
            'verified_agents': 0
        }
