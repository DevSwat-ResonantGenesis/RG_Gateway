"""Agent Engine Routes - Proxy to agent_engine_service."""
import asyncio
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
import httpx
from .config import settings

router = APIRouter(prefix="/agents", tags=["agents"])

AGENT_ENGINE_URL = settings.AGENT_ENGINE_URL


async def proxy_to_agent_engine(path: str, request: Request) -> Response:
    """Proxy request to agent engine service."""
    user_id = request.headers.get("x-user-id", "anonymous")
    org_id = request.headers.get("x-org-id", "")
    
    # Agent engine uses /agents prefix for its routes
    # The gateway router has /agents prefix, so we don't double it
    target_path = f"agents/{path}" if path else "agents"
    
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            # Forward all x-* identity headers so agent engine sees roles & privileges
            forwarded = {
                "x-user-id": user_id,
                "x-org-id": org_id,
                "x-user-role": request.headers.get("x-user-role", "user"),
                "x-is-superuser": request.headers.get("x-is-superuser", ""),
                "x-unlimited-credits": request.headers.get("x-unlimited-credits", ""),
                "content-type": request.headers.get("content-type", "application/json"),
            }
            resp = await client.request(
                method=request.method,
                url=f"{AGENT_ENGINE_URL}/{target_path}",
                headers=forwarded,
                content=await request.body() if request.method in ["POST", "PUT", "PATCH"] else None,
                params=request.query_params,
            )
            
            # Debug logging
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"[AGENT-PROXY] URL: {AGENT_ENGINE_URL}/{target_path}")
            logger.info(f"[AGENT-PROXY] Status: {resp.status_code}")
            logger.info(f"[AGENT-PROXY] Content length: {len(resp.content)}")
            logger.info(f"[AGENT-PROXY] Content preview: {resp.content[:200]}")
            logger.info(f"[AGENT-PROXY] Response: {resp.content}")
            
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=dict(resp.headers),
            )
    except Exception as e:
        return Response(
            content=f"Agent Engine service unavailable: {str(e)}".encode(),
            status_code=503,
        )


@router.get("/health")
async def agent_health(request: Request):
    """Proxy to agent engine health check."""
    return await proxy_to_agent_engine("health", request)


# SSE streaming for agent sessions
@router.get("/sessions/{session_id}/sse")
async def sse_session_stream_proxy(session_id: str, request: Request):
    """SSE streaming proxy for agent session progress."""
    user_id = request.headers.get("x-user-id", "anonymous")
    org_id = request.headers.get("x-org-id", "")
    target_url = f"{AGENT_ENGINE_URL}/agents/sessions/{session_id}/sse"

    async def _stream():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(320.0, connect=10.0)) as client:
                async with client.stream(
                    "GET",
                    target_url,
                    headers={"x-user-id": user_id, "x-org-id": org_id, "accept": "text/event-stream"},
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        except Exception as e:
            import json
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n".encode()

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# Catch-all route for all agent engine paths
@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def agent_engine_catchall(path: str, request: Request):
    """Catch-all proxy for all agent engine routes."""
    return await proxy_to_agent_engine(path, request)
