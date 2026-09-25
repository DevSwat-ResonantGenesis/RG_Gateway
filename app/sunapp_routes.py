"""
SunApp Backend Routes
Integrates the SunApp backend for Tech AI Audio Podcasts
"""

from fastapi import APIRouter, Request
from .reverse_proxy import proxy

# Create router for sunapp backend
sunapp_router = APIRouter(prefix="/sunapp", tags=["sunapp"])

# Health check for sunapp
@sunapp_router.get("/health")
async def sunapp_health():
    """Health check for sunapp backend"""
    return {"status": "ok", "service": "sunapp_backend"}

# Public routes (no auth required)
@sunapp_router.get("/public/{path:path}")
@sunapp_router.post("/public/{path:path}")
async def proxy_sunapp_public(path: str, request: Request):
    """Proxy public sunapp routes (no auth)"""
    target_url = f"http://sunapp_backend:8000/public/{path}"
    return await proxy(request, target_url)

# Authenticated routes (requires auth)
@sunapp_router.get("/{path:path}")
@sunapp_router.post("/{path:path}")
@sunapp_router.put("/{path:path}")
@sunapp_router.delete("/{path:path}")
@sunapp_router.patch("/{path:path}")
async def proxy_sunapp(path: str, request: Request):
    """Proxy authenticated sunapp routes"""
    target_url = f"http://sunapp_backend:8000/{path}"
    return await proxy(request, target_url)