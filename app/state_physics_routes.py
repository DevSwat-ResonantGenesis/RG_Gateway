"""Hash Sphere Routes - Hash Sphere visualization and state endpoints."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
import httpx

router = APIRouter(prefix="/state-physics", tags=["state-physics"])

import os
STATE_PHYSICS_URL = os.getenv("STATE_PHYSICS_URL") or os.getenv("HASH_SPHERE_URL", "http://rg_users_invarients_sim:8091")


@router.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_state_physics_api(request: Request, path: str):
    """Proxy /state-physics/api/* to the State Physics service /api/* (UI-relative API calls)."""
    user_id = request.headers.get("x-user-id", "anonymous")
    org_id = request.headers.get("x-org-id", "")
    universe_id = request.headers.get("x-universe-id", "")
    state_physics_universe_id = request.headers.get("x-state-physics-universe-id", "")

    url = f"{STATE_PHYSICS_URL}/api/{path}"
    async with httpx.AsyncClient() as client:
        headers = dict(request.headers)
        headers["x-user-id"] = user_id
        headers["x-org-id"] = org_id
        if universe_id:
            headers["x-universe-id"] = universe_id
        if state_physics_universe_id:
            headers["x-state-physics-universe-id"] = state_physics_universe_id

        body = await request.body()
        resp = await client.request(
            request.method,
            url,
            params=request.query_params,
            content=body if body else None,
            headers=headers,
            timeout=30.0,
        )

        # Return JSON when possible, otherwise pass through text
        ctype = resp.headers.get("content-type", "")
        if "application/json" in ctype:
            return resp.json()
        return HTMLResponse(content=resp.text, status_code=resp.status_code)


@router.get("/", response_class=HTMLResponse)
async def state_physics_viewer(request: Request):
    """Serve State Physics visualization with user context."""
    user_id = request.headers.get("x-user-id", "anonymous")
    org_id = request.headers.get("x-org-id", "")
    universe_id = request.headers.get("x-universe-id", "")
    state_physics_universe_id = request.headers.get("x-state-physics-universe-id", "")
    
    try:
        async with httpx.AsyncClient() as client:
            # Forward request to State Physics service with user context
            resp = await client.get(
                f"{STATE_PHYSICS_URL}/",
                headers={
                    "x-user-id": user_id,
                    "x-org-id": org_id,
                    "x-universe-id": universe_id,
                    "x-state-physics-universe-id": state_physics_universe_id,
                },
                timeout=10.0
            )
            return HTMLResponse(content=resp.text, status_code=resp.status_code)
    except Exception as e:
        return HTMLResponse(
            content=f"<html><body><h1>State Physics Service Unavailable</h1><p>{str(e)}</p></body></html>",
            status_code=503
        )


@router.get("/state")
async def get_state(request: Request):
    """Get Hash Sphere state for current user."""
    user_id = request.headers.get("x-user-id", "anonymous")
    org_id = request.headers.get("x-org-id", "")
    universe_id = request.headers.get("x-universe-id", "")
    state_physics_universe_id = request.headers.get("x-state-physics-universe-id", "")
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{STATE_PHYSICS_URL}/api/state",
                headers={
                    "x-user-id": user_id,
                    "x-org-id": org_id,
                    "x-universe-id": universe_id,
                    "x-state-physics-universe-id": state_physics_universe_id,
                },
                timeout=10.0
            )
            return resp.json()
    except Exception as e:
        return {"error": str(e), "nodes": [], "edges": []}


@router.get("/nodes")
async def list_nodes(request: Request):
    """List Hash Sphere nodes for current user."""
    user_id = request.headers.get("x-user-id", "anonymous")
    org_id = request.headers.get("x-org-id", "")
    universe_id = request.headers.get("x-universe-id", "")
    state_physics_universe_id = request.headers.get("x-state-physics-universe-id", "")
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{STATE_PHYSICS_URL}/api/nodes",
                headers={
                    "x-user-id": user_id,
                    "x-org-id": org_id,
                    "x-universe-id": universe_id,
                    "x-state-physics-universe-id": state_physics_universe_id,
                },
                timeout=10.0
            )
            return resp.json()
    except Exception as e:
        return {"error": str(e), "nodes": []}


@router.post("/nodes")
async def create_node(node: dict, request: Request):
    """Create a Hash Sphere node for current user."""
    user_id = request.headers.get("x-user-id", "anonymous")
    org_id = request.headers.get("x-org-id", "")
    universe_id = request.headers.get("x-universe-id", "")
    state_physics_universe_id = request.headers.get("x-state-physics-universe-id", "")
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{STATE_PHYSICS_URL}/api/identity",
                json=node,
                headers={
                    "x-user-id": user_id,
                    "x-org-id": org_id,
                    "x-universe-id": universe_id,
                    "x-state-physics-universe-id": state_physics_universe_id,
                },
                timeout=10.0
            )
            return resp.json()
    except Exception as e:
        return {"error": str(e)}


@router.get("/metrics")
async def get_metrics(request: Request):
    """Get Hash Sphere metrics for current user."""
    user_id = request.headers.get("x-user-id", "anonymous")
    org_id = request.headers.get("x-org-id", "")
    universe_id = request.headers.get("x-universe-id", "")
    state_physics_universe_id = request.headers.get("x-state-physics-universe-id", "")
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{STATE_PHYSICS_URL}/api/metrics",
                headers={
                    "x-user-id": user_id,
                    "x-org-id": org_id,
                    "x-universe-id": universe_id,
                    "x-state-physics-universe-id": state_physics_universe_id,
                },
                timeout=10.0
            )
            return resp.json()
    except Exception as e:
        return {"error": str(e), "metrics": {}}
