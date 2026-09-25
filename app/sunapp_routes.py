"""
SunApp Backend Routes
Integrates the SunApp backend for Tech AI Audio Podcasts
"""

from fastapi import APIRouter, Request
from .reverse_proxy import proxy
from .config import settings

# Create router for sunapp backend
sunapp_router = APIRouter(prefix="/sunapp", tags=["sunapp"])

# Health check for sunapp
@sunapp_router.get("/health")
async def sunapp_health():
    """Health check for sunapp backend"""
    return {"status": "ok", "service": "sunapp_backend"}

# Public routes (no auth required) - audio generation and explore
@sunapp_router.get("/api/audio")
@sunapp_router.post("/api/audio")
async def proxy_sunapp_audio_root(request: Request):
    """Proxy public audio routes (no auth)"""
    target_url = f"{settings.SUNAPP_URL}/api/audio"
    return await proxy_request(request, target_url)

@sunapp_router.get("/api/audio/{path:path}")
@sunapp_router.post("/api/audio/{path:path}")
async def proxy_sunapp_audio(path: str, request: Request):
    """Proxy public audio routes (no auth)"""
    target_url = f"{settings.SUNAPP_URL}/api/audio/{path}"
    return await proxy_request(request, target_url)

@sunapp_router.get("/api/explore")
async def proxy_sunapp_explore_root(request: Request):
    """Proxy public explore routes (no auth)"""
    target_url = f"{settings.SUNAPP_URL}/api/explore"
    return await proxy_request(request, target_url)

@sunapp_router.get("/api/explore/{path:path}")
async def proxy_sunapp_explore(path: str, request: Request):
    """Proxy public explore routes (no auth)"""
    target_url = f"{settings.SUNAPP_URL}/api/explore/{path}"
    return await proxy_request(request, target_url)

# Authenticated routes (requires auth) - admin routes
@sunapp_router.get("/api/admin/{path:path}")
@sunapp_router.post("/api/admin/{path:path}")
@sunapp_router.put("/api/admin/{path:path}")
@sunapp_router.delete("/api/admin/{path:path}")
@sunapp_router.patch("/api/admin/{path:path}")
async def proxy_sunapp_admin(path: str, request: Request):
    """Proxy authenticated admin routes"""
    target_url = f"{settings.SUNAPP_URL}/api/admin/{path}"
    return await proxy_request(request, target_url)

# Catch-all for other routes
@sunapp_router.get("/{path:path}")
@sunapp_router.post("/{path:path}")
@sunapp_router.put("/{path:path}")
@sunapp_router.delete("/{path:path}")
@sunapp_router.patch("/{path:path}")
async def proxy_sunapp(path: str, request: Request):
    """Proxy other sunapp routes"""
    target_url = f"{settings.SUNAPP_URL}/{path}"
    return await proxy_request(request, target_url)


async def proxy_request(request: Request, target_url: str):
    """Simple proxy function for sunapp requests"""
    import httpx
    from fastapi import Response
    
    # Filter out host header
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    
    # Ensure cookies are forwarded
    if "cookie" not in headers and request.cookies:
        cookie_str = "; ".join([f"{k}={v}" for k, v in request.cookies.items()])
        headers["cookie"] = cookie_str
    
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            resp = await client.request(
                method=request.method,
                url=target_url,
                content=await request.body(),
                headers=headers,
                params=request.query_params,
            )
    except httpx.RequestError:
        return Response(status_code=502, content=b"Upstream service unavailable")
    
    # Filter response headers
    response_headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower() not in ("content-encoding", "transfer-encoding", "content-length", "set-cookie")
    }
    
    response = Response(content=resp.content, status_code=resp.status_code, headers=response_headers)
    for cookie_value in resp.headers.get_list("set-cookie"):
        response.headers.append("set-cookie", cookie_value)
    
    return response