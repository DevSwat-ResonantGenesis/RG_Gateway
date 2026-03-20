"""Agent Engine Routes - Proxy to agent_engine_service."""
import asyncio
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
import httpx

router = APIRouter(prefix="/agents", tags=["agents"])

AGENT_ENGINE_URL = "http://agent_engine_service:8000"


async def proxy_to_agent_engine(path: str, request: Request) -> Response:
    """Proxy request to agent engine service."""
    user_id = request.headers.get("x-user-id", "anonymous")
    org_id = request.headers.get("x-org-id", "")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Forward request with user context
            resp = await client.request(
                method=request.method,
                url=f"{AGENT_ENGINE_URL}/{path}",
                headers={
                    "x-user-id": user_id,
                    "x-org-id": org_id,
                    "content-type": request.headers.get("content-type", "application/json"),
                },
                content=await request.body() if request.method in ["POST", "PUT", "PATCH"] else None,
                params=request.query_params,
            )
            
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


@router.api_route("/autonomous/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def autonomous_routes(path: str, request: Request):
    """Proxy autonomous agent routes."""
    return await proxy_to_agent_engine(f"autonomous/{path}", request)


@router.get("/docs")
async def agent_docs(request: Request):
    """Proxy to agent engine docs."""
    return await proxy_to_agent_engine("docs", request)


@router.get("/openapi.json")
async def agent_openapi(request: Request):
    """Proxy to agent engine OpenAPI spec."""
    return await proxy_to_agent_engine("openapi.json", request)


@router.get("/health")
async def agent_health(request: Request):
    """Proxy to agent engine health check."""
    return await proxy_to_agent_engine("health", request)


# ============== SSE Streaming Proxy ==============

@router.get("/sessions/{session_id}/sse")
async def sse_session_stream_proxy(session_id: str, request: Request):
    """SSE streaming proxy for agent session progress.
    
    Streams step-by-step progress from agent_engine_service as Server-Sent Events.
    Uses httpx streaming to avoid buffering the full response.
    """
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


# ============== Catch-all Session & Tool Routes ==============

@router.api_route("/sessions/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def session_routes(path: str, request: Request):
    """Proxy all /agents/sessions/* routes to agent_engine_service."""
    return await proxy_to_agent_engine(f"agents/sessions/{path}", request)


@router.api_route("/tools/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def tools_routes(path: str, request: Request):
    """Proxy all /agents/tools/* routes to agent_engine_service."""
    return await proxy_to_agent_engine(f"agents/tools/{path}", request)


@router.api_route("/tools", methods=["GET", "POST"])
async def tools_base(request: Request):
    """Proxy /agents/tools to agent_engine_service."""
    return await proxy_to_agent_engine("agents/tools", request)


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def catch_all_agent_routes(path: str, request: Request):
    """Catch-all proxy for any /agents/* routes to agent_engine_service."""
    return await proxy_to_agent_engine(f"agents/{path}", request)
