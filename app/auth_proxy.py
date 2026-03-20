"""
Auth Service Proxy Module
CASCADE-clean - no side effects, pure functions
"""
import os
import httpx
from fastapi import Request, Response

AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth_service:8000")

async def proxy_auth(path: str, request: Request):
    """Proxy requests to auth service - pure function"""
    headers = dict(request.headers)
    headers.pop("host", None)  # Remove host header
    
    async with httpx.AsyncClient() as client:
        resp = await client.request(
            request.method,
            f"{AUTH_SERVICE_URL}/{path}",
            headers=headers,
            content=await request.body()
        )
    
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )
