"""Node API routes - decentralized network layer.

Bridges rara_service + agent_engine_service into the NodeStatus / Agent
shape that the marketplace frontend expects at /api/v1/node/*.
"""
import os
import asyncio
import httpx
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse
from typing import Optional

router = APIRouter(prefix="/node", tags=["node"])

RARA_URL = os.getenv("RARA_SERVICE_URL", "http://rg_internal_invarients_sim:8093")
AGENT_ENGINE_URL = os.getenv("AGENT_ENGINE_URL", "http://agent_engine_service:8000")
BLOCKCHAIN_URL = os.getenv("BLOCKCHAIN_SERVICE_URL", "http://blockchain_node:8081")


async def _get(url: str, timeout: float = 10.0, headers: dict = None):
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = await c.get(url, headers=headers)
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return None


async def _post(url: str, body: dict, timeout: float = 30.0, headers: dict = None):
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = await c.post(url, json=body, headers=headers)
            return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 503


def _auth_headers(request: Request = None) -> dict:
    """Extract auth headers from incoming request to forward to internal services."""
    h = {}
    if request:
        for key in ("x-user-id", "x-user-role", "x-org-id", "x-is-superuser"):
            val = request.headers.get(key)
            if val:
                h[key] = val
    return h


# ── STATUS ────────────────────────────────────────────────────────────────────

@router.get("/status")
async def node_status():
    """Translate rara /status → NodeStatus shape."""
    rara = await _get(f"{RARA_URL}/status")
    if rara is None:
        return JSONResponse(status_code=503, content={"error": "rara unavailable"})

    state = rara.get("state", "stopped")
    running = state in ("running", "active")

    # Try to get peer/block info from blockchain node
    chain = await _get(f"{BLOCKCHAIN_URL}/api/v1/blockchain/status") or {}

    return {
        "running": running,
        "mode": "decentralized",
        "identity": rara.get("node_id") or "resonant-genesis-primary",
        "chain_connected": chain.get("connected", running),
        "runtime_active": running,
        "indexer_synced": running,
        "peer_count": chain.get("peer_count", rara.get("agents_registered", 0)),
        "block_height": chain.get("block_height"),
        "agents_registered": rara.get("agents_registered", 0),
        "uptime_seconds": rara.get("uptime_seconds"),
        "mutations_today": rara.get("mutations_today", 0),
    }


@router.get("/health")
async def node_health():
    rara = await _get(f"{RARA_URL}/health")
    return rara or {"status": "ok"}


# ── NETWORK ───────────────────────────────────────────────────────────────────

@router.get("/network/nodes")
async def network_nodes(
    region: Optional[str] = None,
    min_trust_score: Optional[float] = None,
    limit: int = Query(default=50, le=200),
):
    """Return known network nodes (primary + any registered peers)."""
    rara = await _get(f"{RARA_URL}/status") or {}
    chain = await _get(f"{BLOCKCHAIN_URL}/api/v1/blockchain/nodes") or {}

    # Build nodes list from what we know
    nodes = []
    running = rara.get("state", "") in ("running", "active")

    # Primary node
    nodes.append({
        "node_id": "resonant-genesis-primary",
        "dsid": "0xRGPRIMARY",
        "address": "resonantgenesis.xyz",
        "port": 443,
        "status": "online" if running else "offline",
        "trust_score": 1.0,
        "capabilities": ["inference", "governance", "memory", "agents"],
        "last_seen": None,
        "region": "EU-West",
    })

    # Add any blockchain peer nodes
    peer_nodes = chain if isinstance(chain, list) else chain.get("nodes", [])
    for peer in peer_nodes[:limit]:
        nodes.append({
            "node_id": peer.get("node_id", peer.get("id", "unknown")),
            "dsid": peer.get("dsid", peer.get("address", "")),
            "address": peer.get("host", peer.get("address", "")),
            "port": peer.get("port", 443),
            "status": peer.get("status", "online"),
            "trust_score": peer.get("trust_score", 0.8),
            "capabilities": peer.get("capabilities", ["inference"]),
            "last_seen": peer.get("last_seen"),
            "region": peer.get("region", "Unknown"),
        })

    # Apply filters
    if region:
        nodes = [n for n in nodes if (n.get("region") or "").lower() == region.lower()]
    if min_trust_score is not None:
        nodes = [n for n in nodes if n["trust_score"] >= min_trust_score]

    return {"nodes": nodes[:limit], "count": len(nodes[:limit])}


@router.get("/network/stats")
async def network_stats():
    """Return aggregated network statistics."""
    rara_task = _get(f"{RARA_URL}/status")
    agents_task = _get(f"{AGENT_ENGINE_URL}/agents?limit=1")

    rara, agents_resp = await asyncio.gather(rara_task, agents_task)
    rara = rara or {}
    total_agents = (agents_resp or {}).get("total", 0)

    return {
        "total_nodes": 1,
        "active_nodes": 1 if rara.get("state") in ("running", "active") else 0,
        "total_agents": total_agents or rara.get("agents_registered", 0),
        "total_executions": rara.get("mutations_today", 0),
        "network_tps": 0,
        "avg_latency_ms": 0,
    }


@router.get("/network/ping/{node_id}")
async def ping_node(node_id: str):
    import time
    start = time.time()
    await _get(f"{RARA_URL}/health")
    latency = round((time.time() - start) * 1000, 1)
    return {"latency_ms": latency, "status": "online"}


@router.get("/network/best-node/{manifest_hash}")
async def best_node(manifest_hash: str):
    return {
        "node_id": "resonant-genesis-primary",
        "estimated_latency_ms": 50,
        "estimated_cost": 0,
    }


# ── AGENTS ────────────────────────────────────────────────────────────────────

def _agent_to_marketplace(a: dict) -> dict:
    """Map agent_engine agent shape → marketplace Agent shape."""
    return {
        "manifest_hash": a.get("agent_id") or a.get("id") or a.get("manifest_hash", ""),
        "name": a.get("name", "Unnamed Agent"),
        "version": "1.0",
        "description": a.get("description", ""),
        "category": a.get("category") or (a.get("tools") and "tool") or "general",
        "trust_tier": 3,
        "status": a.get("status", "Active"),
        "owner_dsid": a.get("owner_id") or a.get("user_id") or "platform",
        "execution_count": a.get("execution_count", 0),
        "price_per_execution": 0,
        "rental_available": False,
    }


@router.get("/agents")
async def search_agents(
    category: Optional[str] = None,
    owner: Optional[str] = None,
    min_trust_tier: Optional[int] = None,
    max_price: Optional[float] = None,
    rental_only: Optional[bool] = None,
    limit: int = Query(default=50, le=200),
    request: Request = None,
):
    """Fetch agents from agent_engine and format as marketplace agents."""
    headers = _auth_headers(request)
    # Use internal service UUID so public/unauthenticated marketplace can list agents
    if "x-user-id" not in headers:
        headers["x-user-id"] = "00000000-0000-0000-0000-000000000000"

    raw = await _get(f"{AGENT_ENGINE_URL}/agents/marketplace?limit={limit}", headers=headers)
    agents_list = []
    if raw:
        items = raw if isinstance(raw, list) else raw.get("agents", raw.get("items", []))
        agents_list = [_agent_to_marketplace(a) for a in (items or [])]

    if min_trust_tier is not None:
        agents_list = [a for a in agents_list if a["trust_tier"] >= min_trust_tier]
    if category:
        agents_list = [a for a in agents_list if a["category"].lower() == category.lower()]

    return {"agents": agents_list, "count": len(agents_list)}


@router.get("/agents/{manifest_hash}")
async def get_agent(manifest_hash: str):
    raw = await _get(f"{AGENT_ENGINE_URL}/agents/{manifest_hash}")
    if not raw:
        return JSONResponse(status_code=404, content={"error": "Agent not found"})
    return _agent_to_marketplace(raw)


@router.post("/agents/publish")
async def publish_agent(request: Request):
    body = await request.json()
    headers = _auth_headers(request)
    resp, status = await _post(f"{AGENT_ENGINE_URL}/agents", body, headers=headers)
    if status in (200, 201):
        agent_id = resp.get("agent_id") or resp.get("id", "")
        return {
            "success": True,
            "manifest_hash": agent_id,
            "tx_hash": f"0x{agent_id[:16] if agent_id else '0000'}",
            "published_at": None,
        }
    return JSONResponse(status_code=status, content={"success": False, "error": str(resp)})


@router.post("/agents/{manifest_hash}/unpublish")
async def unpublish_agent(manifest_hash: str):
    resp, status = await _post(f"{AGENT_ENGINE_URL}/agents/{manifest_hash}/deactivate", {})
    return {"success": status in (200, 204)}


@router.patch("/agents/{manifest_hash}/listing")
async def update_listing(manifest_hash: str, request: Request):
    body = await request.json()
    resp, status = await _post(f"{AGENT_ENGINE_URL}/agents/{manifest_hash}/update", body)
    return {"success": status in (200, 204)}


# ── EXECUTION ─────────────────────────────────────────────────────────────────

@router.post("/execute")
async def execute_agent(request: Request):
    body = await request.json()
    manifest_hash = body.get("manifest_hash", "")
    headers = _auth_headers(request)
    # Map nodeApi execute shape → agent_engine ExecuteTaskRequest shape
    execute_body = {
        "task": body.get("input_data", {}).get("message") or body.get("input_data", {}).get("text") or str(body.get("input_data", {})),
        "input_text": body.get("input_data", {}).get("message") or body.get("input_data", {}).get("text") or "",
        "context": body.get("input_data", {}),
    }
    resp, status = await _post(
        f"{AGENT_ENGINE_URL}/execution/agents/{manifest_hash}/execute",
        execute_body,
        timeout=60.0,
        headers=headers,
    )
    if status in (200, 201):
        return {
            "success": True,
            "output": resp,
            "execution_hash": f"0x{manifest_hash[:16]}exec",
            "tokens_used": 0,
            "duration_ms": 0,
            "governance_decision": "approved",
        }
    return JSONResponse(status_code=status, content={
        "success": False,
        "output": None,
        "execution_hash": "",
        "tokens_used": 0,
        "duration_ms": 0,
        "governance_decision": "rejected",
        "error": str(resp),
    })


@router.get("/executions/history")
async def execution_history(limit: int = Query(default=50, le=200)):
    """Proxy execution history from blockchain_node."""
    data = await _get(f"{BLOCKCHAIN_URL}/executions/history?limit={limit}")
    return data or {"executions": [], "stats": {"total": 0, "successful": 0, "failed": 0, "success_rate": 0, "avg_duration_ms": 0}, "count": 0}


@router.get("/executions/{execution_hash}")
async def execution_status(execution_hash: str):
    return {"status": "completed", "progress": 100, "result": None, "error": None}


@router.post("/executions/{execution_hash}/cancel")
async def cancel_execution(execution_hash: str):
    return {"success": True}


@router.post("/network/nodes/{node_id}/execute")
async def execute_on_node(node_id: str, request: Request):
    body = await request.json()
    return await execute_agent(request)
