"""Hash Sphere API v1 - Public Hash Sphere API endpoints."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
import httpx

router = APIRouter(prefix="/api/v1/state-physics", tags=["state-physics-v1"])


import os
STATE_PHYSICS_URL = os.getenv("STATE_PHYSICS_URL") or os.getenv("HASH_SPHERE_URL", "http://rg_users_invarients_sim:8091")


def _rewrite_state_physics_html(html: str) -> str:
    replacements = {
        "fetch('/api/": "fetch('api/",
        'fetch("/api/': 'fetch("api/',
        "fetch(`/api/": "fetch(`api/",
    }

    for old, new in replacements.items():
        html = html.replace(old, new)

    return html


@router.get("/ui", response_class=HTMLResponse)
async def state_physics_ui_v1(request: Request):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{STATE_PHYSICS_URL}/", timeout=10.0)
            rewritten = _rewrite_state_physics_html(resp.text)
            return HTMLResponse(content=rewritten, status_code=resp.status_code)
    except Exception as e:
        return HTMLResponse(
            content=f"<html><body><h1>State Physics Service Unavailable</h1><p>{str(e)}</p></body></html>",
            status_code=503,
        )


@router.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_state_physics_api_v1(request: Request, path: str):
    url = f"{STATE_PHYSICS_URL}/api/{path}"

    async with httpx.AsyncClient() as client:
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
        user_id = request.headers.get("x-user-id")
        org_id = request.headers.get("x-org-id")
        universe_id = request.headers.get("x-universe-id")
        state_physics_universe_id = request.headers.get("x-state-physics-universe-id")
        user_plan = request.headers.get("x-user-plan")
        if user_id:
            headers["x-user-id"] = user_id
        if org_id:
            headers["x-org-id"] = org_id
        if universe_id:
            headers["x-universe-id"] = universe_id
        if state_physics_universe_id:
            headers["x-state-physics-universe-id"] = state_physics_universe_id
        if user_plan:
            headers["x-user-plan"] = user_plan
        body = await request.body()
        resp = await client.request(
            request.method,
            url,
            params=request.query_params,
            content=body if body else None,
            headers=headers,
            timeout=60.0,
        )

        return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))


@router.get("/state")
async def get_state_v1():
    """Get Hash Sphere state (v1 API)."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{STATE_PHYSICS_URL}/api/state", timeout=10.0)
            return resp.json()
    except Exception as e:
        return {"error": str(e), "nodes": [], "edges": []}


@router.post("/identity")
async def create_identity_v1(identity: dict):
    """Create identity in Hash Sphere (v1 API)."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{STATE_PHYSICS_URL}/api/identity",
                json=identity,
                timeout=10.0
            )
            return resp.json()
    except Exception as e:
        return {"error": str(e)}


@router.post("/simulate")
async def simulate_v1(params: dict):
    """Run Hash Sphere simulation (v1 API)."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{STATE_PHYSICS_URL}/api/simulate",
                json=params,
                timeout=30.0
            )
            return resp.json()
    except Exception as e:
        return {"error": str(e)}
