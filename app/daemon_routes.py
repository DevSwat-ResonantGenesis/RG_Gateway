"""
Owner Dashboard - Daemon Control Routes
Provides endpoints for monitoring and controlling internal platform daemons.
"""
import os
import logging
import httpx
from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/owner/dashboard/daemons", tags=["Owner Dashboard - Daemons"])

# Service URLs
RARA_URL = os.getenv("RARA_SERVICE_URL", "http://rg_internal_invarients_sim:8093")
AGENT_ENGINE_URL = os.getenv("GATEWAY_AGENT_ENGINE_URL", "http://agent_engine_service:8000")
CHAT_SERVICE_URL = os.getenv("GATEWAY_CHAT_URL", "http://chat_service:8000")


async def _check_service(url: str, timeout: float = 5.0) -> Dict[str, Any]:
    """Check if a service is reachable and return health info."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url}/health")
            if resp.status_code == 200:
                data = resp.json()
                return {"online": True, "status": "running", "data": data}
            return {"online": False, "status": "unhealthy", "code": resp.status_code}
    except Exception as e:
        return {"online": False, "status": "offline", "error": str(e)}


@router.get("/status")
async def get_all_daemon_status():
    """Get status of all internal daemons."""
    from .services.local_llm import local_llm_service

    # Check Knowledge Daemon (LLM availability)
    llm_healthy = await local_llm_service.health_check()
    knowledge_status = {
        "name": "Knowledge Daemon",
        "id": "knowledge_daemon",
        "status": "running" if llm_healthy else "degraded",
        "online": True,
        "llm_available": llm_healthy,
        "provider": "ollama" if getattr(local_llm_service, '_ollama_available', False) else "groq-fallback",
        "description": "Agent chat backend with LLM integration",
    }

    # Check RARA Service
    rara_check = await _check_service(RARA_URL)
    rara_status = {
        "name": "RARA Governance",
        "id": "rara_service",
        "status": rara_check.get("status", "offline"),
        "online": rara_check.get("online", False),
        "description": "Runtime governance, kill switch, policy enforcement",
    }
    if rara_check.get("data"):
        rara_status.update({
            "verified_agents": rara_check["data"].get("verified_agents", 0),
            "uptime_seconds": rara_check["data"].get("uptime_seconds", 0),
        })

    # Check Autonomous Agent Daemon
    autonomous_env = os.getenv("AGENT_ENGINE_ENABLE_AUTONOMOUS_DAEMON", "false")
    agent_engine_check = await _check_service(AGENT_ENGINE_URL)
    autonomous_status = {
        "name": "Autonomous Agent Daemon",
        "id": "autonomous_daemon",
        "status": "configured" if autonomous_env.lower() == "true" else "disabled",
        "online": agent_engine_check.get("online", False),
        "enabled_in_env": autonomous_env.lower() == "true",
        "description": "Self-triggering autonomous agents with goal pursuit",
    }

    # Check Self-Healing Daemon
    self_healing_status = {
        "name": "Self-Healing Daemon",
        "id": "self_healing_daemon",
        "status": "configured",
        "online": False,
        "description": "Auto-recovery, service health monitoring, smart auto-fix",
    }

    # Check WebSocket Provider Status
    chat_check = await _check_service(CHAT_SERVICE_URL)
    ws_provider_status = {
        "name": "WebSocket Provider Status",
        "id": "ws_provider_status",
        "status": "running" if chat_check.get("online") else "offline",
        "online": chat_check.get("online", False),
        "description": "Live provider latency monitoring via WebSocket",
    }

    daemons = [
        knowledge_status,
        rara_status,
        autonomous_status,
        self_healing_status,
        ws_provider_status,
    ]

    return {
        "daemons": daemons,
        "total": len(daemons),
        "online": sum(1 for d in daemons if d["online"]),
        "degraded": sum(1 for d in daemons if d["status"] == "degraded"),
        "offline": sum(1 for d in daemons if not d["online"]),
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/{daemon_id}/status")
async def get_daemon_status(daemon_id: str):
    """Get detailed status of a specific daemon."""
    all_status = await get_all_daemon_status()
    for d in all_status["daemons"]:
        if d["id"] == daemon_id:
            return d
    raise HTTPException(status_code=404, detail=f"Daemon '{daemon_id}' not found")


@router.get("/health")
async def daemons_health():
    """Quick health check for daemon monitoring."""
    status = await get_all_daemon_status()
    healthy = status["online"] > 0
    return {
        "status": "healthy" if healthy else "unhealthy",
        "online_count": status["online"],
        "total_count": status["total"],
    }


# ============================================
# DAEMON CONTROL ACTIONS
# ============================================

@router.post("/autonomous_daemon/start")
async def start_autonomous_daemon():
    """Start the autonomous agent daemon via agent_engine_service."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{AGENT_ENGINE_URL}/autonomous/daemon/start")
            if resp.status_code == 200:
                return {"success": True, "message": "Autonomous daemon starting", "data": resp.json()}
            return {"success": False, "message": f"Agent engine returned {resp.status_code}", "detail": resp.text}
    except Exception as e:
        logger.error(f"Failed to start autonomous daemon: {e}")
        return {"success": False, "message": str(e)}


@router.post("/autonomous_daemon/stop")
async def stop_autonomous_daemon():
    """Stop the autonomous agent daemon via agent_engine_service."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{AGENT_ENGINE_URL}/autonomous/daemon/stop")
            if resp.status_code == 200:
                return {"success": True, "message": "Autonomous daemon stopping", "data": resp.json()}
            return {"success": False, "message": f"Agent engine returned {resp.status_code}", "detail": resp.text}
    except Exception as e:
        logger.error(f"Failed to stop autonomous daemon: {e}")
        return {"success": False, "message": str(e)}


@router.get("/autonomous_daemon/detail")
async def get_autonomous_daemon_detail():
    """Get detailed autonomous daemon status from agent_engine_service."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{AGENT_ENGINE_URL}/autonomous/daemon/status")
            if resp.status_code == 200:
                return {"success": True, "data": resp.json()}
            return {"success": False, "message": f"Agent engine returned {resp.status_code}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/rara_service/kill_switch")
async def toggle_rara_kill_switch(activate: bool = True):
    """Toggle the RARA kill switch."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{RARA_URL}/control/kill-switch",
                json={"activate": activate},
            )
            if resp.status_code == 200:
                return {"success": True, "kill_switch_active": activate, "data": resp.json()}
            return {"success": False, "message": f"RARA returned {resp.status_code}", "detail": resp.text}
    except Exception as e:
        logger.error(f"Failed to toggle RARA kill switch: {e}")
        return {"success": False, "message": str(e)}


@router.post("/knowledge_daemon/chat")
async def knowledge_daemon_chat(request: Request):
    """Send a message to the Knowledge Daemon and get a response."""
    from .services.local_llm import local_llm_service

    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id", "owner-console")

    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    try:
        result = await local_llm_service.chat(
            messages=[
                {"role": "system", "content": "You are the Knowledge Daemon, an internal AI assistant for the ResonantGenesis platform. Help the platform owner with system administration, daemon management, and technical questions."},
                {"role": "user", "content": message},
            ],
            temperature=0.7,
            max_tokens=1024,
        )
        # Extract response content
        content = ""
        if "choices" in result:
            content = result["choices"][0].get("message", {}).get("content", "")
        elif "response" in result:
            content = result["response"]

        return {
            "success": True,
            "response": content,
            "session_id": session_id,
            "model": result.get("model", "unknown"),
        }
    except Exception as e:
        logger.error(f"Knowledge Daemon chat error: {e}")
        return {"success": False, "response": f"Error: {str(e)}", "session_id": session_id}
